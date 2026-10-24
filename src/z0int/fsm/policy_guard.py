"""Read-only O11/O12 receipt audit and bounded source scan.

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

STAGES = ('none', 'shadow', 'canary', 'live')
FRONTIER = {'frontier', 'remote_frontier', 'jev', 'bounded_jev'}
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
        row = {**inherited, **row}
        # Session custom entries wrap the decision in data and put time outside.
        if isinstance(row.get('data'), dict) and any(
                key in row['data'] for key in ('policy_version', 'route', 'tier', 'policy_families')):
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
    # A model name alone cannot establish device/quant/engine/backend identity.
    return isinstance(actual, dict) and isinstance(named, dict) and bool(named) and all(
        actual.get(key) == value for key, value in named.items())


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


def audit(receipts: str | None, policy_path: Path | None, since: str,
          *, now: dt.datetime | None = None):
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - duration(since)
    counts = dict.fromkeys(('unstamped', 'stale_policy', 'o11_evidence_bound_route', 'o12_offload_obligation'), 0)
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
    except (OSError, ValueError, TypeError) as exc:
        unavailable.append(f'policy unavailable: {exc}')
        policy = None
    families = policy['families'] if policy else {}
    for family, rule in families.items():
        if rule['stage'] == 'live':
            report['offload_rate'][family] = dict.fromkeys(('live_decisions', 'offloaded', 'abstained', 'frontier'), 0)
    stamped = 0
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
                    rule = families.get(family, {}) if isinstance(family, str) else {}
                    required = ('policy_version', 'policy_sha256', 'rule_id', 'stage', 'family')
                    if not all(isinstance(rec.get(k), str) and rec[k] for k in required):
                        counts['unstamped'] += 1
                        issues.append(_issue(path, line, 'unstamped', 'missing decision policy stamp'))
                    else:
                        stamped += 1
                    if policy and (rec.get('policy_version') != policy['version']
                                   or rec.get('policy_sha256') != policy['version']
                                   or rec.get('stage') != rule.get('stage', 'none')
                                   or rec.get('rule_id') != rule.get('rule_id', 'none')):
                        counts['stale_policy'] += 1
                        issues.append(_issue(path, line, 'stale_policy', 'receipt differs from current policy'))
                    route, arm = _route(rec)
                    if route is None:
                        unavailable.append(f'{path}:{line}: missing route/tier')
                        continue
                    frontier = route in FRONTIER
                    if not frontier:
                        expired = False
                        try:
                            expired = timestamp(rule['expires_at']) <= at
                        except (KeyError, ValueError, TypeError):
                            expired = True
                        if (rule.get('stage') not in ('canary', 'live') or expired
                                or not _arm_matches(arm, rule.get('arm'))):
                            counts['o11_evidence_bound_route'] += 1
                            issues.append(_issue(path, line, 'o11_evidence_bound_route', 'offload lacks current evidence naming this full arm'))
                    if rule.get('stage') == 'live':
                        rate = report['offload_rate'][family]
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
                            if healthy is True and not abstain:
                                counts['o12_offload_obligation'] += 1
                                issues.append(_issue(path, line, 'o12_offload_obligation', 'healthy live family used frontier without below-threshold abstention'))
        except (OSError, UnicodeError, ValueError, TypeError, OverflowError) as exc:
            unavailable.append(f'{path}: {exc}')
    if not stamped:
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
