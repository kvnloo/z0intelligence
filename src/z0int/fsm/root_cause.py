"""R1–R4 report-only decisions and local evidence records; never target execution."""
from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
from contextlib import contextmanager

DAY = 86400


def _flags(facts, keys):
    return isinstance(facts, dict) and all(type(facts.get(k)) is bool for k in keys)


def r1(facts):
    keys = ('target', 'before_metric', 'functional_probe', 'expiry_valid')
    return 'escalate' if not _flags(facts, keys) else 'allow' if all(facts[k] for k in keys) else 'refuse'


def r2(facts):
    keys = ('same_metric', 'in_bounds_twice', 'separated_24h')
    return 'escalate' if not _flags(facts, keys) else 'allow' if all(facts[k] for k in keys) else 'refuse'


def r3(facts):
    keys = ('probe_passed', 'owned', 'leased')
    if not _flags(facts, keys):
        return 'escalate'
    return 'allow' if facts['probe_passed'] else 'rollback' if facts['owned'] and facts['leased'] else 'escalate'


def r4(facts):
    keys = ('same_window', 'share_at_least_30')
    return 'escalate' if not _flags(facts, keys) else 'allow' if all(facts[k] for k in keys) else 'refuse'


def _number(value):
    try:
        finite = type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError('finite numeric value required')
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('nonempty text required')
    return value


def _id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', value):
        raise ValueError('invalid record id')
    return value


def _bounds(record):
    low, high = _number(record['lower']), _number(record['upper'])
    if low > high:
        raise ValueError('lower exceeds upper')
    return low, high


def lease_decision(record, now):
    """Validate a proposed lease, including its original symptom measurement."""
    try:
        now = _number(now)
        before = record['before']
        _text(before['metric'])
        _text(before['units'])
        _number(before['value'])
        measured = _number(before['at'])
        expiry = _number(record['expires_at'])
        created = _number(record['created_at'])
        return r1({'target': bool(_text(record['target'])), 'before_metric': measured <= created,
                   'functional_probe': bool(_text(record['functional_probe'])),
                   'expiry_valid': created <= now < expiry <= created + DAY})
    except (ValueError, KeyError, TypeError):
        return 'escalate'


def close_decision(incident):
    try:
        _text(incident['metric'])
        _text(incident['units'])
        low, high = _bounds(incident)
        opened = _number(incident['opened_at'])
        observations = incident['observations']
        if not isinstance(observations, list):
            raise ValueError('observation list required')
        valid = []
        for row in observations:
            at, value = _number(row['at']), _number(row['value'])
            if at < opened or row['metric'] != incident['metric'] or row['units'] != incident['units']:
                return 'refuse'
            valid.append((at, low <= value <= high))
        valid.sort()
        # A recurrence invalidates all earlier healthy observations.
        healthy = []
        for at, in_bounds in valid:
            if not in_bounds:
                healthy.clear()
            else:
                healthy.append(at)
        return r2({'same_metric': True, 'in_bounds_twice': len(healthy) >= 2,
                   'separated_24h': len(healthy) >= 2 and healthy[-1] - healthy[0] >= DAY})
    except (ValueError, KeyError, TypeError):
        return 'escalate'


def probe_decision(lease, passed):
    """Rollback eligibility is not execution or an authorization receipt."""
    return r3({'probe_passed': passed, 'owned': lease.get('owner') == 'z0-guard' and lease.get('status') == 'applied',
               'leased': lease.get('guard_applied') is True and lease.get('schema') == 'z0.mitigation.v1'})


def expiry_decision(lease, now, incidents=()):
    try:
        if _number(now) < _number(lease['expires_at']):
            return 'allow'
        if any(i.get('status') == 'closed' and lease['id'] in i.get('mitigations', []) and close_decision(i) == 'allow'
               for i in incidents):
            return 'allow'
        return probe_decision(lease, False)
    except (KeyError, TypeError, ValueError):
        return 'escalate'


def attribute_decision(samples):
    """Typed cumulative counters: identical units, endpoints and PID/starttime.

    System counters must include disk and swap in the declared units. No block
    conversion or inferred window alignment is permitted. These are submitted
    observations, not a claim that this process performed a live collection.
    """
    try:
        process, system = samples['process'], samples['system']
        if type(process['pid']) is not int or process['pid'] <= 0:
            raise ValueError('positive PID required')
        starttime = _number(process['starttime'])
        if type(starttime) is not int or starttime < 0 or type(process['end_starttime']) is not int or process['end_starttime'] != starttime:
            raise ValueError('process identity changed')
        units = _text(process['units'])
        if units not in ('bytes', 'blocks') or system['units'] != units:
            return 'escalate'
        if system['includes_swap'] is not True:
            return 'escalate'
        for row in (process, system):
            start, end = _number(row['window_start']), _number(row['window_end'])
            before, after = _number(row['before']), _number(row['after'])
            if start < 0 or end <= start or before < 0 or after < before:
                raise ValueError('invalid counter window')
        same = (process['window_start'], process['window_end']) == (system['window_start'], system['window_end'])
        numerator, denominator = process['after'] - process['before'], system['after'] - system['before']
        if not math.isfinite(numerator) or not math.isfinite(denominator) or denominator <= 0 or numerator > denominator:
            return 'escalate'
        return r4({'same_window': same, 'share_at_least_30': numerator >= denominator * .30})
    except (KeyError, TypeError, ValueError):
        return 'escalate'


@contextmanager
def _locked(store):
    root = Path(store)
    root.mkdir(parents=True, exist_ok=True)
    with (root / '.root-cause.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield root


def _read(path):
    with path.open() as stream:
        row = json.load(stream, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    if not isinstance(row, dict):
        raise ValueError('record must be an object')
    return row


def _save(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(row, sort_keys=True, allow_nan=False) + '\n'
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.record-')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def open_incident(store, incident):
    row = dict(incident)
    ident = _id(row['id'])
    _text(row['metric'])
    row.setdefault('units', 'count')
    _text(row['units'])
    _bounds(row)
    row.setdefault('opened_at', time.time())
    _number(row['opened_at'])
    row.update(schema='z0.incident.v1', status='open', observations=[])
    with _locked(store) as root:
        path = root / 'incidents' / (ident + '.json')
        if path.exists():
            existing = _read(path)
            if existing.get('status') != 'open':
                raise ValueError('incident already closed')
            if any(existing.get(k) != row.get(k) for k in ('metric', 'units', 'lower', 'upper')):
                raise ValueError('incident identity conflicts with existing metric contract')
            return existing
        _save(path, row)
    return row


def propose_mitigation(store, proposal, now=None):
    now = time.time() if now is None else _number(now)
    row = dict(proposal)
    ident = _id(row['id'])
    row.setdefault('created_at', now)
    row.setdefault('expires_at', row['created_at'] + DAY)
    decision = lease_decision(row, now)
    if decision != 'allow':
        return {'decision': decision, 'mode': 'report', 'applied': False}
    # Caller-supplied ownership can never promote a proposed lease to applied.
    row.update(schema='z0.mitigation.v1', status='proposed', owner=None, guard_applied=False)
    with _locked(store) as root:
        path = root / 'mitigations' / (ident + '.json')
        if path.exists():
            raise ValueError('mitigation id already exists')
        _save(path, row)
    return {'decision': decision, 'mode': 'report', 'applied': False, 'lease': row}


def observe_incident(store, ident, observation, now=None):
    now = time.time() if now is None else _number(now)
    row = dict(observation)
    _number(row['value'])
    at = _number(row['at'])
    if at > now:
        raise ValueError('future observation')
    with _locked(store) as root:
        path = root / 'incidents' / (_id(ident) + '.json')
        incident = _read(path)
        if incident['status'] != 'open':
            raise ValueError('incident is not open')
        if row['metric'] != incident['metric'] or row['units'] != incident['units'] or at < incident['opened_at']:
            raise ValueError('observation does not match original metric contract')
        incident['observations'].append(row)
        _save(path, incident)
    return incident


def close_incident(store, ident):
    with _locked(store) as root:
        path = root / 'incidents' / (_id(ident) + '.json')
        incident = _read(path)
        decision = close_decision(incident)
        if decision == 'allow':
            incident['status'] = 'closed'
            _save(path, incident)
    return {'decision': decision, 'mode': 'report', 'incident': incident}


def record_probe(store, ident, observation, now=None):
    """Record submitted probe evidence; never run the probe or rollback."""
    now = time.time() if now is None else _number(now)
    at = _number(observation['at'])
    if type(observation.get('passed')) is not bool or at > now:
        raise ValueError('dated Boolean functional probe outcome required')
    _text(observation['evidence'])
    with _locked(store) as root:
        path = root / 'mitigations' / (_id(ident) + '.json')
        lease = _read(path)
        if at < _number(lease['created_at']):
            raise ValueError('probe predates lease')
        decision = probe_decision(lease, observation['passed'])
        lease.setdefault('probe_observations', []).append(dict(observation))
        _save(path, lease)
    return {'decision': decision, 'mode': 'report', 'executed': False,
            'lease': lease, 'intended_action': decision}


def audit_leases(store, now=None):
    """Revisit every persisted lease; expiry raises evidence, never changes targets."""
    now = time.time() if now is None else _number(now)
    findings = []
    with _locked(store) as root:
        incidents = []
        incident_errors = []
        for path in sorted((root / 'incidents').glob('*.json')):
            try:
                incidents.append(_read(path))
            except (OSError, ValueError, TypeError) as exc:
                incident_errors.append(str(exc))
        for path in sorted((root / 'mitigations').glob('*.json')):
            try:
                lease = _read(path)
                ident = _id(lease['id'])
                expired = now >= _number(lease['expires_at'])
                decision = 'escalate' if incident_errors else expiry_decision(lease, now, incidents)
                probes = lease.get('probe_observations', [])
                if decision == 'allow' and not expired and not probes:
                    decision = 'escalate'
                if decision == 'allow' and probes and probes[-1].get('passed') is not True:
                    decision = probe_decision(lease, probes[-1].get('passed'))
                findings.append({'id': ident, 'decision': decision, 'expired': expired,
                                 'reason': 'expiry or functional evidence review', 'executed': False})
            except (OSError, ValueError, TypeError, KeyError) as exc:
                findings.append({'id': path.stem, 'decision': 'escalate', 'reason': str(exc), 'executed': False})
    # Avoid nested file locks: raise report incidents only after the snapshot lock.
    for finding in findings:
        if finding['decision'] != 'allow':
            try:
                open_incident(store, {'id': 'lease-review-' + _id(finding['id'])[:110],
                              'metric': 'mitigation.review', 'units': 'count', 'lower': 0, 'upper': 0,
                              'opened_at': now, 'evidence': finding})
            except (OSError, ValueError, TypeError, KeyError) as exc:
                finding['incident_error'] = str(exc)
    return {'decision': 'escalate' if any(f['decision'] == 'escalate' for f in findings)
            else 'rollback' if any(f['decision'] == 'rollback' for f in findings) else 'allow',
            'mode': 'report', 'executed': False, 'findings': findings}


def import_manual(store, receipt_path, now=None):
    """Import z0.manual_mitigation.v1 evidence, never apply the described change.

    Required fields: id, owner='Hermes', target, created_at, expires_at, before,
    functional_probe, approval={approved_by:'Kevin', reference}, artifacts with
    baseline/probe {path, sha256}, and probe_observation={at, passed, evidence}.
    Artifact paths are relative to the receipt directory. Optional resource
    metadata is retained verbatim for exact read-only inventory correlation.
    """
    now = time.time() if now is None else _number(now)
    source = Path(receipt_path).resolve(strict=True)
    raw = source.read_bytes()
    row = json.loads(raw)
    if not isinstance(row, dict) or row.get('schema') != 'z0.manual_mitigation.v1' or row.get('owner') != 'Hermes':
        raise ValueError('typed Hermes manual mitigation receipt required')
    ident = _id(row['id'])
    if row['approval']['approved_by'] != 'Kevin':
        raise ValueError('explicit Kevin approval receipt required')
    _text(row['approval']['reference'])
    if lease_decision(row, now) != 'allow':
        raise ValueError('valid baseline, target, probe and unexpired <=24h lease required')
    for kind in ('baseline', 'probe'):
        artifact = row['artifacts'][kind]
        relative = Path(_text(artifact['path']))
        target = (source.parent / relative).resolve(strict=True)
        if relative.is_absolute() or not target.is_relative_to(source.parent):
            raise ValueError('artifact must be inside receipt directory')
        payload = target.read_bytes()
        if not payload or hashlib.sha256(payload).hexdigest() != artifact['sha256']:
            raise ValueError('missing or mismatched ' + kind + ' evidence')
    probe = row['probe_observation']
    at = _number(probe['at'])
    _text(probe['evidence'])
    if type(probe.get('passed')) is not bool or not row['created_at'] <= at <= now:
        raise ValueError('dated immediate functional outcome required')
    row.update(schema='z0.mitigation.v1', status='manual', guard_applied=False,
               probe_observations=[probe],
               source_receipt={'path': str(source), 'sha256': hashlib.sha256(raw).hexdigest()})
    with _locked(store) as root:
        path = root / 'mitigations' / (ident + '.json')
        if path.exists():
            raise ValueError('mitigation id already exists')
        _save(path, row)
    return {'decision': probe_decision(row, probe['passed']), 'mode': 'report',
            'executed': False, 'imported': True, 'lease': row}


def command(args):
    try:
        if args.root_action == 'attribute':
            result = {'decision': attribute_decision(_read(Path(args.input))), 'mode': 'report'}
        elif args.root_action == 'mitigate':
            if args.mitigation_action == 'audit':
                result = audit_leases(args.store)
            elif args.mitigation_action == 'probe':
                result = record_probe(args.store, args.id, _read(Path(args.input)))
            elif args.mitigation_action == 'import-manual':
                result = import_manual(args.store, args.input)
            else:
                result = propose_mitigation(args.store, _read(Path(args.input)))
        elif args.incident_action == 'open':
            result = {'decision': 'allow', 'mode': 'report', 'incident': open_incident(args.store, _read(Path(args.input)))}
        elif args.incident_action == 'observe':
            result = {'decision': 'allow', 'mode': 'report', 'incident': observe_incident(args.store, args.id, _read(Path(args.input)))}
        else:
            result = close_incident(args.store, args.id)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result = {'decision': 'escalate', 'mode': 'report', 'reason': str(exc)}
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return {'allow': 0, 'refuse': 3, 'escalate': 4, 'rollback': 3}[result['decision']]


def register(subparsers):
    """Register JSON-input report-only CLI commands on z0-guard's parser."""
    default_store = str(Path.home() / '.local/state/z0-guard/root-cause')
    for name in ('mitigate', 'attribute'):
        parser = subparsers.add_parser(name, help='R1–R4 report-only evidence decision')
        parser.add_argument('--input', required=name == 'attribute')
        if name == 'mitigate':
            parser.add_argument('mitigation_action', choices=('propose', 'probe', 'audit', 'import-manual'))
            parser.add_argument('--id')
        parser.add_argument('--store', default=default_store)
        parser.set_defaults(fn=command, root_action=name)
    incident = subparsers.add_parser('incident', help='Persist incident observations only')
    actions = incident.add_subparsers(dest='incident_action', required=True)
    for name in ('open', 'observe', 'close'):
        parser = actions.add_parser(name)
        parser.add_argument('--store', default=default_store)
        if name != 'open':
            parser.add_argument('--id', required=True)
        if name != 'close':
            parser.add_argument('--input', required=True)
        parser.set_defaults(fn=command, root_action='incident')
