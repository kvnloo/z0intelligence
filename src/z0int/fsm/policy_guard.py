"""Read-only O11/O12/O13 receipt/configuration audit and bounded source scan.

Missing evidence is unavailable, not a successful audit. This command never
changes routing or prevents an operator turn from using frontier/Jev.
"""
from __future__ import annotations

import ast
import datetime as dt
import glob
import hashlib
import json
import math
import os
import re
from pathlib import Path

from .ops import risk_option_capacity_allows

STAGES = ('none', 'shadow', 'canary', 'live')
FRONTIER = {'frontier', 'remote_frontier', 'jev', 'bounded_jev'}
PHYSICAL_KEYS = ('model', 'quant', 'engine', 'backend', 'device')
LOADER_FILES = {'src/z0int/policy_runtime.py', 'omp-extensions/local-cognition/index.ts'}
SOURCE_SUFFIXES = {'.py', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.yaml', '.yml', '.json', '.sh'}
SKIP_DIRS = {'.git', '.venv', 'venv', 'node_modules', 'dist', 'build', '__pycache__',
             'tests', 'test', 'fixtures', 'data', 'docs', 'posts', 'studies'}


def timestamp(value) -> dt.datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return dt.datetime.fromtimestamp(value, dt.timezone.utc)
    if not isinstance(value, str):
        raise ValueError('missing receipt timestamp')
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timestamp requires a timezone')
    return parsed.astimezone(dt.timezone.utc)


def duration(value: str) -> dt.timedelta:
    match = re.fullmatch(r'(\d+(?:\.\d+)?)(s|m|h|d)', value)
    if not match:
        raise ValueError('since must be a duration such as 30m, 24h or 7d')
    return dt.timedelta(seconds=float(match[1]) * {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[match[2]])


def _issue(path, line, rule, detail):
    return {'path': str(path), 'line': line, 'rule': rule, 'detail': detail}


def _records(path: Path):
    def unpack(row, inherited=None):
        inherited = {} if inherited is None else inherited
        if isinstance(row, list):
            for child in row:
                yield from unpack(child, inherited)
            return
        if not isinstance(row, dict):
            yield row
            return
        synthetic = inherited.get('synthetic') is True or row.get('synthetic') is True
        row = {**inherited, **row}
        if synthetic:
            row['synthetic'] = True
        # Session custom entries wrap the decision in data and put time outside.
        if isinstance(row.get('data'), dict) and any(
                key in row['data'] for key in ('policy_version', 'route', 'tier', 'policy_families',
                                               'option_count', 'shadow')):
            yield from unpack(row['data'], {key: value for key, value in row.items() if key != 'data'})
            return
        for key in ('receipts', 'decisions'):
            if key in row:
                if not isinstance(row[key], list):
                    raise ValueError('receipt collection must be a list')
                parent = {name: value for name, value in row.items() if name not in ('receipts', 'decisions')}
                for child in row[key]:
                    yield from unpack(child, parent)
                return
        yield row

    text = path.read_text()
    try:
        doc = json.loads(text)
    except ValueError:
        for line, raw in enumerate(text.splitlines(), 1):
            if raw.strip():
                for row in unpack(json.loads(raw)):
                    yield line, row
        return
    for line, row in enumerate(unpack(doc), 1):
        yield line, row


def _arm_matches(actual, named):
    if not isinstance(actual, dict) or not isinstance(named, dict) or not named:
        return False
    if named.get('kind') not in ('rules', 'knn') and not all(
            isinstance(named.get(key), str) and named[key]
            and isinstance(actual.get(key), str) and actual[key] for key in PHYSICAL_KEYS):
        return False
    # kind classifies the finding (slm/rules/engine), not physical deployment.
    identity = {key: value for key, value in named.items() if key != 'kind'}
    if not identity:
        return actual.get('kind') == named.get('kind')
    return all(actual.get(key) == value for key, value in identity.items())


def _deployment_matches(receipt, policy, arm, named):
    if named.get('kind') in ('rules', 'knn'):
        return True
    if not isinstance(arm, dict) or not isinstance(policy.get('serving'), list):
        return False
    observed = receipt.get('observed_identity')
    if not isinstance(observed, dict):
        return False
    for endpoint in policy['serving']:
        if not isinstance(endpoint, dict) or not all(endpoint.get(key) == arm.get(key) for key in PHYSICAL_KEYS):
            continue
        expected = {'model_id': endpoint.get('expected_model_id'),
                    'engine_build': endpoint.get('engine_build'), 'model_path': endpoint.get('model_path')}
        sha = endpoint.get('model_sha256')
        if not all(isinstance(value, str) and value for value in expected.values()):
            continue
        if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
            continue
        if not all(observed.get(key) == value for key, value in expected.items()):
            continue
        if receipt.get('props_model_id') != expected['model_id']:
            continue
        declared = receipt.get('expected_identity')
        if declared is not None and (not isinstance(declared, dict)
                or any(declared.get(key) != value for key, value in {**expected, 'model_sha256': sha}.items())):
            continue
        return True
    return False


def _synthetic_context(policy_path):
    if policy_path is None:
        return None, False
    home = policy_path.expanduser().resolve().parent.parent
    protected = {(Path.home() / '.z0int').resolve()}
    if os.environ.get('Z0INT_LIVE_HOME'):
        protected.add(Path(os.environ['Z0INT_LIVE_HOME']).expanduser().resolve())
    return home, home not in protected and Path('/tmp').resolve() in home.parents


def _abstained(receipt, rule):
    value = receipt.get('abstain')
    if not isinstance(value, dict):
        return False
    confidence, threshold = value.get('confidence'), rule.get('threshold')
    return (isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
            and isinstance(threshold, (int, float)) and not isinstance(threshold, bool)
            and math.isfinite(confidence) and math.isfinite(threshold)
            and 0 <= confidence < threshold <= 1
            and value.get('threshold') == threshold)


def _route(receipt):
    value = receipt.get('route', receipt.get('tier'))
    if isinstance(value, dict):
        return value.get('tier', value.get('kind')), value.get('arm')
    return value, receipt.get('selected_arm', receipt.get('arm'))



def _capacity_scope(row):
    """Normalize producer field names, never infer a missing physical dimension."""
    scope = {}
    for value in (row, row.get('selected_arm'), row.get('arm'), row.get('scope')):
        if isinstance(value, dict):
            scope.update({key: value[key] for key in PHYSICAL_KEYS if key in value})
    if 'model' not in scope and isinstance(row.get('model_id'), str):
        scope['model'] = row['model_id']
    return scope


def _allowed_k(policy, scope, now, risk=None):
    if risk not in ('read', 'mutating_reversible'):
        return 0
    if not all(isinstance(scope.get(key), str) and scope[key] for key in PHYSICAL_KEYS):
        return 0
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', scope['model']):
        return 0
    key = json.dumps({key: scope[key] for key in PHYSICAL_KEYS}, sort_keys=True,
                     separators=(',', ':'), ensure_ascii=False)
    entries = (policy or {}).get('option_capacity', {})
    risks = entries.get(key) if isinstance(entries, dict) else None
    entry = risks.get(risk) if isinstance(risks, dict) else None
    if not isinstance(entry, dict):
        return 0
    k, lower, bar = entry.get('allowed_k'), entry.get('wilson_lower'), entry.get('bar_derived')
    inputs = entry.get('inputs')
    if (type(k) is not int or k < 2 or not isinstance(lower, (int, float))
            or isinstance(lower, bool) or not math.isfinite(lower) or not 0 <= lower <= 1
            or not isinstance(bar, (int, float)) or isinstance(bar, bool)
            or not math.isfinite(bar) or lower <= bar
            or not isinstance(inputs, dict) or inputs.get('blocked') is True
            or inputs.get('status') not in ('measured', 'unmeasured')
            or type(inputs.get('measured')) is not bool
            or inputs['measured'] != (inputs['status'] == 'measured')
            or not isinstance(entry.get('finding_ids'), list) or not entry['finding_ids']
            or not all(isinstance(x, str) and x for x in entry['finding_ids'])
            or not isinstance(entry.get('task'), list) or not entry['task']
            or not all(isinstance(x, str) and x for x in entry['task'])):
        return 0
    costs = [inputs.get(name) for name in
             ('cost_call', 'cost_frontier', 'cost_error', 'abstain_rate')]
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) for value in costs):
        return 0
    call, frontier, error, abstain = costs
    if call < 0 or frontier <= 0 or error <= 0 or not 0 <= abstain <= 1:
        return 0
    expected = 1 - ((1 - abstain) * frontier - call) / error
    if not math.isfinite(expected) or not math.isclose(bar, expected, rel_tol=1e-12, abs_tol=1e-12):
        return 0
    try:
        return k if timestamp(entry.get('expires_at')) > now else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def _bound_refusal(row):
    return (row.get('slm_requested') is False and type(row.get('option_count')) is int
            and row['option_count'] == 0
            and row.get('policy_reason', row.get('reason')) in (
                'option_bound_exceeded', 'option_capacity_exceeded'))


def _configured_options(home):
    """Only read the runtime's known configuration/status inputs; never the ledger."""
    endpoints = []
    serving = home / 'config/serving.json'
    if serving.exists():
        doc = json.loads(serving.read_text())
        if not isinstance(doc, dict) or not isinstance(doc.get('endpoints'), list):
            raise ValueError(f'{serving}: invalid serving endpoints')
        endpoints = doc['endpoints']
        if not all(isinstance(row, dict) for row in endpoints):
            raise ValueError(f'{serving}: endpoint must be an object')

    def walk(node, path, pointer, inherited):
        if isinstance(node, list):
            for index, value in enumerate(node):
                yield from walk(value, path, f'{pointer}/{index}', inherited)
        elif isinstance(node, dict):
            combined = {**inherited, **node}
            # Nested arm/scope fields must be visible when the count is on its parent.
            if 'option_count' in node or 'candidate_k' in node:
                count = node.get('option_count', node.get('candidate_k'))
                identifier = node.get('id', node.get('model_id', pointer or '/'))
                if not _capacity_scope(combined).get('model') and endpoints:
                    for index, endpoint in enumerate(endpoints):
                        yield path, f'{identifier}@{endpoint.get("model_id", index)}', {
                            **endpoint, **combined, 'option_count': count}
                else:
                    yield path, str(identifier), {**combined, 'option_count': count}
            for key, value in node.items():
                if key not in ('option_count', 'candidate_k'):
                    yield from walk(value, path, f'{pointer}/{key}', combined)

    for relative in ('config/serving.json', 'config/loop.json', 'config/z0int.json',
                     'config/shadow.json', 'shadow/config.json', 'loop/shadow-learning-status.json'):
        path = home / relative
        if path.exists():
            doc = json.loads(path.read_text())
            if not isinstance(doc, dict):
                raise ValueError(f'{path}: configuration must be an object')
            # Other z0int/loop settings are not SLM requests.
            if relative in ('config/loop.json', 'config/z0int.json'):
                doc = doc.get('shadow_learning', {})
            yield from walk(doc, path, '', {})

def audit(receipts: str | None, policy_path: Path | None, since: str,
          *, now: dt.datetime | None = None):
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - duration(since)
    counts = dict.fromkeys(('unstamped', 'stale_policy', 'o11_evidence_bound_route',
                            'o12_offload_obligation', 'o13_option_capacity'), 0)
    report = {'schema': 'z0.policy_guard.v1', 'ts': now.isoformat(), 'policy_version': None,
              'window': {'since': cutoff.isoformat(), 'receipts': 0}, 'counts': counts,
              'offload_rate': {}, 'available': False, 'unavailable_reason': None}
    issues, unavailable = [], []
    policy = None
    try:
        if policy_path is None:
            raise ValueError('no policy path')
        policy = json.loads(policy_path.read_text())
        if not isinstance(policy, dict) or policy.get('schema') != 'z0.decision_policy.v1':
            raise ValueError('invalid policy schema')
        body = {key: value for key, value in policy.items() if key != 'version'}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'),
                                          ensure_ascii=False, allow_nan=False).encode()).hexdigest()
        if policy.get('version') != digest:
            raise ValueError('policy content hash mismatch')
        if not isinstance(policy.get('families'), dict):
            raise ValueError('invalid policy families')
        if not isinstance(policy.get('serving'), list):
            raise ValueError('invalid policy serving entries')
        for family, rule in policy['families'].items():
            if not isinstance(rule, dict) or rule.get('stage') not in STAGES:
                raise ValueError(f'invalid policy stage for {family}')
            if not isinstance(rule.get('rule_id'), str) or not rule['rule_id']:
                raise ValueError(f'invalid policy rule for {family}')
            timestamp(rule.get('expires_at'))
            if rule['stage'] != 'none' and (
                    not isinstance(rule.get('arm'), dict) or not rule['arm'].get('kind')):
                raise ValueError(f'invalid policy arm for {family}')
        report['policy_version'] = digest
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError, RuntimeError) as exc:
        unavailable.append(f'policy unavailable: {exc}')
        policy = None
    families = policy['families'] if policy else {}
    try:
        home, synthetic_allowed = _synthetic_context(policy_path)
    except (OSError, RuntimeError) as exc:
        home, synthetic_allowed = None, False
        unavailable.append(f'policy home unavailable: {exc}')
    synthetic_ids, synthetic_rules, synthetic_seen = set(), set(), set()

    def capacity_violation(row, path, line, identifier, *, required=False):
        if _bound_refusal(row):
            return
        scope = _capacity_scope(row)
        risk = row.get('risk_class')
        allowed = _allowed_k(policy, scope, now, risk)
        k = row.get('option_count')
        invalid = type(k) is not int or k < 0
        if not invalid and risk_option_capacity_allows(
                0 if risk == 'read' else 1 if risk == 'mutating_reversible' else 2,
                True, 0, 0, allowed, k):
            return
        if invalid and not required and 'option_count' not in row:
            return
        counts['o13_option_capacity'] += 1
        detail = ('missing/invalid option_count' if invalid else f'option_count {k} exceeds allowed_k {allowed}')
        issues.append({**_issue(path, line, 'o13_option_capacity', detail),
                       'id': str(identifier), 'option_count': k, 'allowed_k': allowed, 'scope': scope})

    for family, rule in families.items():
        if rule['stage'] in ('shadow', 'canary', 'live'):
            capacity_violation(rule, policy_path, 1, rule['rule_id'])
    if home is not None:
        try:
            for path, identifier, row in _configured_options(home):
                capacity_violation(row, path, 1, identifier, required=True)
        except (OSError, UnicodeError, ValueError, TypeError, OverflowError) as exc:
            unavailable.append(f'option configuration unavailable: {exc}')

    def synthetic_violation(path, line, kind, identifier):
        key = (kind, str(identifier))
        if synthetic_allowed or key in synthetic_seen:
            return
        synthetic_seen.add(key)
        counts['o11_evidence_bound_route'] += 1
        issues.append({**_issue(path, line, 'synthetic-evidence-outside-tmp',
                               f'synthetic {kind} outside isolated temporary policy home'), 'id': str(identifier)})

    if home is not None:
        ledger_path = home / 'findings/findings.jsonl'
        if ledger_path.exists():
            try:
                for line, raw in enumerate(ledger_path.read_text().splitlines(), 1):
                    if not raw.strip():
                        continue
                    finding = json.loads(raw)
                    if not isinstance(finding, dict):
                        raise ValueError('finding must be an object')
                    if finding.get('synthetic') is True:
                        identifier = finding.get('id', f'line:{line}')
                        synthetic_ids.add(str(identifier))
                        synthetic_violation(ledger_path, line, 'finding', identifier)
            except (OSError, UnicodeError, ValueError) as exc:
                unavailable.append(f'findings ledger unavailable: {exc}')
    if policy:
        if policy.get('synthetic') is True:
            synthetic_violation(policy_path, 1, 'policy', policy['version'])
        for family, rule in families.items():
            evidence = rule.get('evidence', [])
            if rule.get('synthetic') is True or (
                    isinstance(evidence, list) and any(str(identifier) in synthetic_ids for identifier in evidence)):
                synthetic_rules.add(family)
                synthetic_violation(policy_path, 1, 'rule', rule['rule_id'])
        for index, endpoint in enumerate(policy.get('serving', [])):
            if isinstance(endpoint, dict) and endpoint.get('synthetic') is True:
                synthetic_violation(policy_path, 1, 'serving', endpoint.get('url', f'index:{index}'))
    for family, rule in families.items():
        if rule['stage'] == 'live':
            report['offload_rate'][family] = dict.fromkeys(('live_decisions', 'offloaded', 'abstained', 'frontier'), 0)
    stamped = shadow_audited = 0
    health_needed = health_known = 0
    paths = sorted(set(glob.glob(str(Path(receipts).expanduser()), recursive=True))) if receipts else []
    if not paths:
        unavailable.append('no receipt files matched')
    for name in paths:
        path = Path(name)
        try:
            for line, row in _records(path):
                if not isinstance(row, dict):
                    raise ValueError(f'receipt {line} is not an object')
                value = next((row[k] for k in ('ts', 'at', 'timestamp', 'created_at') if k in row), None)
                at = timestamp(value)
                if at < cutoff or at > now:
                    continue
                report['window']['receipts'] += 1
                if isinstance(row.get('shadow'), list):
                    for index, shadow in enumerate(row['shadow']):
                        if not isinstance(shadow, dict):
                            raise ValueError('shadow attempt must be an object')
                        if shadow.get('kind') in ('rules', 'knn') or shadow.get('model') in ('rules', 'knn'):
                            continue
                        attempt = {**row, **shadow, 'stage': 'shadow'}
                        identifier = shadow.get('id', f'{row.get("trace_id", path)}:shadow:{shadow.get("model_id", shadow.get("model", index))}')
                        capacity_violation(attempt, path, line, identifier, required=True)
                        shadow_audited += 1
                    if not any(key in row for key in ('policy_version', 'route', 'tier', 'policy_families')):
                        continue
                variants = row.get('policy_families')
                if isinstance(variants, dict) and variants:
                    decisions = [{**row, **stamp, 'family': family} for family, stamp in variants.items()
                                 if isinstance(stamp, dict)]
                else:
                    decisions = [row]
                if not decisions:
                    decisions = [row]
                for rec in decisions:
                    family = rec.get('family')
                    policy_family = rec.get('policy_family', family)
                    rule = families.get(policy_family, {}) if isinstance(policy_family, str) else {}
                    if rec.get('synthetic') is True or row.get('synthetic') is True or policy_family in synthetic_rules:
                        identifier = rec.get('id', rec.get('trace_id', rec.get('request_id', f'{path}:{line}')))
                        synthetic_violation(path, line, 'receipt', identifier)
                    _, selected = _route(rec)
                    if (rec.get('stage') in ('shadow', 'canary', 'live')
                            and (rec.get('slm_requested') is not False
                                 or type(rec.get('option_count')) is int and rec['option_count'] > 0)
                            and (rec.get('slm_requested') is True
                                 or isinstance(selected, dict) and selected.get('kind') == 'slm'
                                 or 'option_count' in rec)):
                        capacity_violation(rec, path, line,
                            rec.get('id', rec.get('trace_id', rec.get('request_id', f'{path}:{line}'))),
                            required=True)
                    route, arm = _route(rec)
                    frontier = isinstance(route, str) and route in FRONTIER
                    scope = rec.get('scope', arm if isinstance(arm, dict) else {})
                    named_scope = rule.get('scope', {})
                    applicable = (rule.get('family', policy_family) == family
                                  and isinstance(scope, dict) and isinstance(named_scope, dict)
                                  and (all(scope.get(key) == value for key, value in named_scope.items())
                                       or (frontier and not scope)))
                    required = ('policy_version', 'policy_sha256', 'rule_id', 'stage', 'family')
                    if not all(isinstance(rec.get(k), str) and rec[k] for k in required):
                        counts['unstamped'] += 1
                        issues.append(_issue(path, line, 'unstamped', 'missing decision policy stamp'))
                    else:
                        stamped += 1
                    if policy and (not applicable or rec.get('policy_version') != policy['version']
                                   or rec.get('policy_sha256') != policy['version']
                                   or rec.get('policy_stage', rec.get('stage')) != rule.get('stage', 'none')
                                   or rec.get('rule_id') != rule.get('rule_id', 'none')):
                        counts['stale_policy'] += 1
                        issues.append(_issue(path, line, 'stale_policy', 'receipt differs from current policy'))
                    if route is None:
                        unavailable.append(f'{path}:{line}: missing route/tier')
                        continue
                    if not frontier:
                        expired = False
                        try:
                            expired = timestamp(rule['expires_at']) <= at
                        except (KeyError, ValueError, TypeError):
                            expired = True
                        if (not applicable or rule.get('stage') not in ('canary', 'live') or expired
                                or not _arm_matches(arm, rule.get('arm'))
                                or not _deployment_matches(rec, policy or {}, arm, rule.get('arm', {}))):
                            counts['o11_evidence_bound_route'] += 1
                            issues.append(_issue(path, line, 'o11_evidence_bound_route', 'offload lacks current evidence naming this full arm'))
                    if rule.get('stage') == 'live':
                        rate = report['offload_rate'][policy_family]
                        rate['live_decisions'] += 1
                        rate['frontier' if frontier else 'offloaded'] += 1
                        abstain = _abstained(rec, rule)
                        rate['abstained'] += int(abstain)
                        if frontier:
                            health_needed += 1
                            healthy = rec.get('serving_healthy')
                            if isinstance(healthy, bool):
                                health_known += 1
                            else:
                                issues.append(_issue(path, line, 'serving-health-unknown',
                                                     'live frontier receipt lacks a boolean serving_healthy observation'))
                            if healthy is True and not abstain and not _bound_refusal(rec):
                                counts['o12_offload_obligation'] += 1
                                issues.append(_issue(path, line, 'o12_offload_obligation', 'healthy live family used frontier without below-threshold abstention'))
        except (OSError, UnicodeError, ValueError, TypeError, OverflowError) as exc:
            unavailable.append(f'{path}: {exc}')
    if not stamped and not shadow_audited:
        unavailable.append('no stamped receipts in window')
    if health_needed and not health_known:
        unavailable.append(f'serving health unavailable for all {health_needed} live frontier receipts')
    report['available'] = not unavailable
    report['unavailable_reason'] = '; '.join(dict.fromkeys(unavailable)) or None
    return report, issues


def static_findings(root: Path):
    """Find literal routing overrides, not every mention of a route enum.

    Python uses AST assignment/keyword/dict semantics. Other source languages
    use bounded assignment/property patterns. This is not whole-program analysis.
    """
    if not root.is_dir():
        return [_issue(root, 0, 'static-unavailable', 'root is not a directory')]
    findings = []

    def literal(node):
        return isinstance(node, (ast.List, ast.Set, ast.Tuple)) and all(isinstance(v, ast.Constant) for v in node.elts)

    def check(path, line, key, value):
        key = key.lower() if isinstance(key, str) else ''
        if key == 'credited_families' and literal(value):
            findings.append(_issue(path, line, 'hardcoded-credited-families', 'literal credited_families outside policy loader'))
        route_key = key in ('route', 'tier', 'selected_arm') or key.endswith(('_route', '_tier')) or key.startswith('route_')
        if route_key and isinstance(value, ast.Constant) and isinstance(value.value, str):
            findings.append(_issue(path, line, 'hardcoded-route', 'literal route outside policy loader'))

    def sources():
        for directory, folders, files in os.walk(root):
            folders[:] = sorted(name for name in folders if name not in SKIP_DIRS and not name.startswith('.'))
            for name in sorted(files):
                yield Path(directory) / name

    for path in sources():
        relative = path.relative_to(root)
        if any(part in SKIP_DIRS for part in relative.parts) or relative.as_posix() in LOADER_FILES:
            continue
        if not path.is_file() or path.suffix not in SOURCE_SUFFIXES:
            continue
        try:
            text = path.read_text()
            if path.suffix == '.py':
                for node in ast.walk(ast.parse(text)):
                    if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        for target in targets:
                            key = target.id if isinstance(target, ast.Name) else target.attr if isinstance(target, ast.Attribute) else target.slice.value if isinstance(target, ast.Subscript) and isinstance(target.slice, ast.Constant) else None
                            check(path, node.lineno, key, node.value)
                    elif isinstance(node, ast.keyword):
                        check(path, node.value.lineno, node.arg, node.value)
                    elif isinstance(node, ast.Dict):
                        for key, value in zip(node.keys, node.values):
                            if isinstance(key, ast.Constant):
                                check(path, key.lineno, key.value, value)
            else:
                pattern = re.compile(r'''["']?\b(credited_families|(?:default_)?route|tier|selected_arm)["']?\s*(?::\s*[A-Za-z_][\w<>.,\[\] |]*\s*)?\s*[=:]\s*(\[[^\]]*\]|["'][^"']+["']|(?:remote_frontier|frontier|bounded_jev|tiny_specialist|orchestrator_slm|general_slm|slm)\b)''', re.M | re.I)
                for match in pattern.finditer(text):
                    rule = 'hardcoded-credited-families' if match[1].lower() == 'credited_families' else 'hardcoded-route'
                    findings.append(_issue(path, text.count('\n', 0, match.start()) + 1, rule, 'literal routing override outside policy loader'))
        except (OSError, UnicodeError, SyntaxError) as exc:
            findings.append(_issue(path, 0, 'static-unavailable', type(exc).__name__))
    return findings
