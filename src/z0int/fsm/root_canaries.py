"""Read-only R7 and operational canaries. Only incident records are written.

No mode changes, refresh requests, daemon starts, or service installations occur.
Unknown measurements are incidents, not evidence of an absent index.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import time

EXPIRY = 72 * 3600


def report_only_expired(report_only: bool, same_finding: bool, age_seconds: int) -> bool:
    return report_only and same_finding and age_seconds >= EXPIRY


def timestamp(value):
    if isinstance(value, (int, float)) and math.isfinite(value):
        return float(value)
    parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp requires timezone')
    return parsed.timestamp()


def result(sensor, status, **evidence):
    return dict(sensor=sensor, status=status, evidence=evidence)


def r7_inventory(decisions: Path, modes: dict, now: float) -> list[dict]:
    """A successful decision clears that gate's prior report findings.

    Finding identity excludes timestamps/mode/status only; different targets or
    reasons remain different findings. Non-report mode transitions reset age.
    """
    pending = {}
    rows = []
    with decisions.open() as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                at = timestamp(row['at'])
                if at > now:
                    raise ValueError('future guard decision')
                rows.append((at, row))
    for at, row in sorted(rows, key=lambda pair: pair[0]):
        gate = row['gate']
        if row.get('mode') != 'report' or row.get('status') != 'would-refuse':
            pending = {key: value for key, value in pending.items() if key[0] != gate}
            continue
        finding = {k: v for k, v in row.items() if k not in {'at', 'mode', 'status'}}
        encoded = json.dumps(finding, sort_keys=True, separators=(',', ':'))
        key = (gate, encoded)
        first, _, count = pending.get(key, (at, at, 0))
        pending[key] = (first, at, count + 1)
    return [dict(gate=gate, finding=json.loads(encoded), first_at=first,
                 last_at=last, count=count, age_seconds=now-first,
                 mode=modes.get(gate, 'unknown'),
                 expired=report_only_expired(modes.get(gate) == 'report', True, now-first))
            for (gate, encoded), (first, last, count) in sorted(pending.items())]


def command(argv, timeout):
    """Never use a shell or infer index absence from timeout/exit status."""
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return result('command', 'unavailable', argv=argv, reason=type(exc).__name__)
    elapsed = time.monotonic() - started
    return result('command', 'pass' if proc.returncode == 0 else 'fail',
                  argv=argv, elapsed=elapsed, returncode=proc.returncode,
                  stdout=proc.stdout, stderr=proc.stderr)


def search_canary(ctx, root, query, samples, timeout):
    measurements = []
    for _ in range(samples):
        observed = command([ctx, '--data-root', str(root), 'search', query,
                            '--refresh', 'off', '--format', 'json', '--limit', '1'], timeout)
        if observed['status'] != 'pass':
            return result('ctx.search', observed['status'], observation=observed)
        try:
            json.loads(observed['evidence']['stdout'])
        except ValueError:
            return result('ctx.search', 'unavailable', reason='invalid JSON response')
        measurements.append(observed['evidence']['elapsed'])
    ordered = sorted(measurements)
    p50 = ordered[math.ceil(len(ordered)*.5)-1]
    p95 = ordered[math.ceil(len(ordered)*.95)-1]
    return result('ctx.search', 'pass' if p50 < 2 and p95 < 5 else 'fail',
                  samples=measurements, p50_seconds=p50, p95_seconds=p95)


def refresh_canary(root, now):
    try:
        path = root / 'daemon/jobs/core-refresh.json'
        job = json.loads(path.read_text())
        finished = float(job['finished_at_ms']) / 1000
        age = now-finished
        status = 'pass' if job['outcome'] == 'completed' and 0 <= age <= 21600 else 'fail'
        return result('ctx.refresh', status, path=str(path), outcome=job['outcome'], age_seconds=age)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return result('ctx.refresh', 'unavailable', reason=str(exc))


def snapshot_canary(ctx, root, timeout):
    observed = command([ctx, '--data-root', str(root), 'status', '--format', 'json'], timeout)
    if observed['status'] != 'pass':
        return result('ctx.snapshots', observed['status'], observation=observed)
    # No orphan counter exists in the inspected ctx source status contract.
    # Do not equate inactive generations/read leases with orphaned snapshots.
    return result('ctx.snapshots', 'unavailable', reason='ctx status exposes no authoritative orphan snapshot count',
                  observation=observed)


def archive_canary(database, raw_roots, timeout):
    """Compare independent raw-root inventories, including files not in the DB.

    Root specs map an AgentsView agent id to directories containing only that
    provider's raw transcript files. Do not infer coverage from indexed paths.
    """
    deadline = time.monotonic() + timeout
    try:
        with sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True,
                             timeout=min(timeout, 1)) as db:
            db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
            indexed = dict(db.execute('SELECT agent, MAX(file_mtime) FROM sessions WHERE deleted_at IS NULL GROUP BY agent'))
        if not raw_roots:
            return result('agentsview.freshness', 'unavailable', reason='no independent raw transcript roots configured')
        agents = {}
        for agent, roots in raw_roots.items():
            newest = None
            for root in roots:
                if not root.is_dir():
                    raise ValueError(f'raw root unavailable: {root}')
                for directory, _, files in os.walk(root):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('raw inventory deadline')
                    for name in files:
                        if name.endswith(('.jsonl', '.json', '.db')):
                            mtime = (Path(directory)/name).stat().st_mtime
                            newest = mtime if newest is None else max(newest, mtime)
            if newest is None or indexed.get(agent) is None:
                agents[agent] = dict(status='unavailable', reason='missing raw or indexed sessions')
            else:
                # AgentsView stores source file_mtime as Unix nanoseconds.
                archived = float(indexed[agent]) / 1e9
                lag = max(0, newest-archived)
                agents[agent] = dict(status='pass' if lag <= 7200 else 'fail', lag_seconds=lag,
                                     raw_latest=newest, indexed_latest=archived)
        missing = sorted(set(indexed)-set(raw_roots))
        status = 'fail' if any(v['status'] == 'fail' for v in agents.values()) else (
            'unavailable' if missing or any(v['status'] == 'unavailable' for v in agents.values()) else 'pass')
        return result('agentsview.freshness', status, agents=agents, missing_raw_roots=missing)
    except (OSError, ValueError, sqlite3.Error, TimeoutError) as exc:
        return result('agentsview.freshness', 'unavailable', reason=str(exc))


def timer_canary(timeout):
    observed = command(['systemctl', '--user', 'show', 'agentsview-sync.service',
                        '--property=Result', '--property=ExecMainStatus',
                        '--property=ExecMainExitTimestampMonotonic'], timeout)
    if observed['status'] != 'pass':
        return result('agentsview.sync', observed['status'], observation=observed)
    fields = dict(line.split('=', 1) for line in observed['evidence']['stdout'].splitlines() if '=' in line)
    try:
        ran = int(fields['ExecMainExitTimestampMonotonic']) > 0
        ok = fields['Result'] == 'success' and fields['ExecMainStatus'] == '0'
        return result('agentsview.sync', 'pass' if ran and ok else 'fail', fields=fields)
    except (ValueError, KeyError):
        return result('agentsview.sync', 'unavailable', fields=fields)


def record_incidents(store, observations, now):
    from .root_cause import open_incident
    for observation in observations:
        if observation['status'] == 'pass':
            continue
        identity = observation['sensor']
        if identity == 'R7' and 'finding' in observation['evidence']:
            identity += ':' + json.dumps(observation['evidence']['finding'], sort_keys=True)
        identifier = 'canary-' + hashlib.sha256(identity.encode()).hexdigest()[:24]
        # Daily identities allow a later recurrence without modifying a closed incident.
        identifier += '-' + str(int(now // 86400))
        incident = dict(id=identifier, metric=observation['sensor'], lower=0, upper=0,
                        opened_at=now, evidence=observation)
        try:
            open_incident(store, incident)
        except ValueError as exc:
            if str(exc) != 'incident already closed':
                raise
            incident['id'] += '-' + str(int(now * 1000000))
            open_incident(store, incident)


def control_inventory(root):
    """Legacy manager drop-ins are observations, never rollback authority."""
    observations = []
    if not root.exists():
        return [result('unleased.inventory', 'unavailable', reason=f'missing {root}')]
    try:
        for path in sorted(root.rglob('*.conf')):
            unit = path.parent.name.removesuffix('.d')
            properties = []
            for line in path.read_text().splitlines():
                text = line.strip()
                if text and not text.startswith(('#', ';', '[')) and '=' in text:
                    key, value = text.split('=', 1)
                    properties.append(dict(property=key, value=value))
            observations.append(result('unleased.' + str(path.relative_to(root)), 'fail',
                                       classification='UN-LEASED', unit=unit,
                                       properties=properties, path=str(path),
                                       mtime=path.stat().st_mtime,
                                       reason='legacy drop-in has no guard-owned lease attribution'))
    except (OSError, UnicodeError) as exc:
        observations.append(result('unleased.inventory', 'unavailable', reason=str(exc)))
    return observations


def agentsview_search_canary(executable, query, timeout):
    """One bounded read-only CLI search; no retry, sync, or service changes."""
    started = time.monotonic()
    observed = command([executable, 'session', 'search', query,
                        '--limit', '1', '--format', 'json'], timeout)
    evidence = dict(elapsed_seconds=time.monotonic() - started,
                    deadline_seconds=timeout,
                    returncode=observed['evidence'].get('returncode'),
                    reason=observed['evidence'].get('reason'))
    status = observed['status']
    if status == 'pass':
        try:
            json.loads(observed['evidence']['stdout'])
        except ValueError:
            status = 'unavailable'
            evidence['reason'] = 'invalid JSON response'
    return result('agentsview.search', status, **evidence)


def run_once(args):
    from . import guard
    now = time.time()
    from .root_cause import audit_leases
    lease_audit = audit_leases(args.store, now=now)
    modes = {g: guard.mode(g) for g in guard.DEFAULT_MODES}
    observations = []
    try:
        inventory = r7_inventory(args.decisions, modes, now)
        observations.extend(result('R7', 'fail', **entry) for entry in inventory if entry['expired'])
        for entry in inventory:
            if entry['mode'] == 'unknown':
                observations.append(result('R7', 'unavailable', **entry))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        inventory = []
        observations.append(result('R7', 'unavailable', reason=str(exc)))
    if getattr(args, 'audit_only', False):
        record_incidents(args.store, observations, now)
        return dict(mode='report', at=now, modes=modes, inventory=inventory, observations=observations, lease_audit=lease_audit)
    roots = {} if args.raw_root else discover_raw_roots(Path.home())
    for item in args.raw_root:
        agent, path = item.split('=', 1)
        roots.setdefault(agent, []).append(Path(path).expanduser())
    observations += control_inventory(args.user_control)
    observations += [search_canary(args.ctx, args.ctx_root, args.query, args.samples, args.timeout),
                     refresh_canary(args.ctx_root, now), snapshot_canary(args.ctx, args.ctx_root, args.timeout),
                     archive_canary(args.agentsview_db, roots, args.timeout), timer_canary(args.timeout),
                     agentsview_search_canary(args.agentsview, args.query, args.timeout)]
    record_incidents(args.store, observations, now)
    return dict(mode='report', at=now, modes=modes, inventory=inventory, observations=observations, lease_audit=lease_audit)


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError('must be positive and finite')
    return number


def cmd_canaries(args):
    try:
        while True:
            print(json.dumps(run_once(args), sort_keys=True), flush=True)
            if not args.continuous:
                return 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 130


def raw_root_spec(value):
    agent, separator, path = value.partition('=')
    if not separator or not agent or not path:
        raise ValueError('expected AGENT=PATH')
    return value


def discover_raw_roots(home):
    """Known provider transcript roots, independent of indexed file paths."""
    candidates = {
        'codex': [home/'.codex/sessions', home/'.codex/archived_sessions'],
        'claude': [home/'.claude/projects', home/'.claude-home/projects'],
        'hermes': list((home/'.hermes/profiles').glob('*/sessions')),
        'omp': [Path('/workspace/kvn-home/.omp/agent/sessions')],
        'grok': [home/'.grok/sessions'],
        'deepseek-harness': [home/'.dsh/sessions'],
        'gemini': [home/'.gemini/tmp'],
        'antigravity-cli': [home/'.gemini/antigravity-cli/conversations'],
        'kimi': [home/'.kimi-code/sessions'],
        'omo': [home/'.omo/agent/sessions'],
    }
    return {agent: existing for agent, roots in candidates.items()
            if (existing := [root for root in roots if root.is_dir()])}


def register(subparsers):
    parser = subparsers.add_parser('canary', help='Read-only R7, ctx and AgentsView canaries; incident records only')
    home = Path.home()
    parser.add_argument('--decisions', type=Path, default=Path(os.environ.get('Z0_GUARD_STATE', home/'.local/state/z0-guard'))/'decisions.jsonl')
    parser.add_argument('--store', type=Path, default=Path(os.environ.get('Z0_GUARD_STATE', home/'.local/state/z0-guard'))/'root-cause')
    parser.add_argument('--ctx', default=str(home/'.local/bin/ctx'))
    parser.add_argument('--ctx-root', type=Path, default=Path('/mnt/zer0models/host-offload/home_kvn_.ctx'))
    parser.add_argument('--agentsview-db', type=Path, default=Path('/mnt/zer0models/sft-svlm/data/agentsview/sessions.db'))
    parser.add_argument('--agentsview', default=str(home/'.local/bin/agentsview'))
    parser.add_argument('--user-control', type=Path, default=home/'.config/systemd/user.control')
    parser.add_argument('--raw-root', type=raw_root_spec, action='append', default=[], metavar='AGENT=PATH', help='Independent provider raw transcript directory; repeat for each provider/root; defaults discover known provider homes')
    parser.add_argument('--query', default='root cause')
    parser.add_argument('--samples', type=int, choices=range(20, 101), default=20)
    parser.add_argument('--timeout', type=positive, default=7.0)
    parser.add_argument('--continuous', action='store_true')
    parser.add_argument('--interval', type=positive, default=3600.0)
    parser.set_defaults(fn=cmd_canaries)
    audit = subparsers.add_parser('report-expiry', help='Report R7 inventory and open expired-finding incidents; never enforce')
    audit.add_argument('--decisions', type=Path, default=Path(os.environ.get('Z0_GUARD_STATE', home/'.local/state/z0-guard'))/'decisions.jsonl')
    audit.add_argument('--store', type=Path, default=Path(os.environ.get('Z0_GUARD_STATE', home/'.local/state/z0-guard'))/'root-cause')
    audit.set_defaults(fn=cmd_canaries, audit_only=True, continuous=False)
