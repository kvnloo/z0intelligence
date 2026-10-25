"""A–E and I state claim audit. Observation and refusal only; never remediation.

The finite predicates mirror bend/state_fsm. Sensors recheck event identities,
not a wall-clock TTL. A pass is neither authorization nor proof of future state.
"""
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import os
import re
import socket
import subprocess
from pathlib import Path

import yaml

from .phone_caps import phone_caps_findings

FIELDS = {
    'A': ('current', 'scope_matches', 'agrees'),
    'B': ('disk_matches', 'process_matches', 'canonical'),
    'C': ('epoch_current', 'no_active_hits', 'coverage_complete'),
    'D': ('typed_receipt', 'output_verified', 'work_positive', 'transport_ok', 'target_current'),
    'E': ('measured', 'scope_matches', 'agrees'),
    'I': ('tracked_source', 'scope_matches', 'agrees'),
}
SCHEMA = 'z0.state_guard.v1'


def evaluate(law: str, facts: dict) -> str:
    """Strict unknown-preserving finite law, shared with the Bend model."""
    if not isinstance(law, str) or law not in FIELDS or not isinstance(facts, dict) or facts.get('available') is not True:
        return 'unknown'
    values = [facts.get(key) for key in FIELDS[law]]
    if any(type(value) is not bool for value in values):
        return 'unknown'
    return 'pass' if all(values) else 'violation'


def check(ident, law, facts, reason, **evidence):
    status = evaluate(law, facts)
    return {'id': ident, 'law': law, 'status': status, 'blocked': status != 'pass',
            'reason': reason, 'facts': facts, 'evidence': evidence,
            'escalate_to': 'Kevin' if status == 'unknown' else None}


def report(checks, *, source):
    counts = {status: sum(row['status'] == status for row in checks)
              for status in ('pass', 'violation', 'unknown')}
    status = 'violation' if counts['violation'] else 'unknown' if counts['unknown'] else 'pass'
    if not checks:
        checks = [check('empty-inventory', 'A', {}, 'No checks were declared')]
        return report(checks, source=source)
    return {'schema': SCHEMA, 'mode': 'report-only', 'enforcement_enabled': False,
            'observed_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'host': socket.gethostname(), 'source': str(source), 'status': status,
            'counts': counts, 'checks': checks,
            'action': 'escalate_to_Kevin' if counts['unknown'] else 'alert' if counts['violation'] else 'none'}


def _path(value):
    return Path(os.path.expandvars(str(value))).expanduser()


def _read(path):
    """Read a stable file snapshot; replacement/mutation becomes unknown."""
    p = _path(path)
    before = p.stat()
    data = p.read_bytes()
    after = p.stat()
    ident = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    if ident(before) != ident(after):
        raise ValueError(f'file changed during observation: {p}')
    return data, {'path': str(p.resolve()), 'sha256': hashlib.sha256(data).hexdigest(),
                  'stat': list(ident(after))}


def _json(path):
    data, binding = _read(path)
    return json.loads(data), binding


def _proc(pid, proc_root=Path('/proc')):
    root = proc_root / str(pid)
    # comm can contain spaces and closing parentheses; field22 follows final ).
    stat = (root / 'stat').read_text().rsplit(')', 1)[1].split()
    return {'pid': int(pid), 'proc_starttime_ticks': int(stat[19]),
            'boot_id': (proc_root / 'sys/kernel/random/boot_id').read_text().strip()}


def _unit(name):
    if not re.fullmatch(r'[A-Za-z0-9_.@:-]+\.(?:service|timer|socket)', name):
        raise ValueError('invalid unit name')
    run = subprocess.run(['systemctl', '--user', 'show', name, '--no-pager',
                          '-p', 'LoadState,ActiveState,SubState,MainPID,InvocationID,UnitFileState,ExecStart,Environment,WorkingDirectory'],
                         capture_output=True, text=True, timeout=10, check=False)
    if run.returncode:
        raise ValueError(f'systemd observation unavailable: {name}')
    row = dict(line.split('=', 1) for line in run.stdout.splitlines() if '=' in line)
    if row.get('LoadState') == 'not-found':
        raise ValueError(f'unit not found: {name}')
    pid = int(row.get('MainPID', '0'))
    row['process'] = _proc(pid) if pid else None
    return row


def sensor_claim(spec):
    ident = spec['id']
    try:
        expected = spec['expected']
        if spec['kind'] == 'unit':
            first = _unit(spec['unit'])
            second = _unit(spec['unit'])
            keys = ('InvocationID', 'MainPID', 'process')
            current = all(first[k] == second[k] for k in keys)
            actual = {key: second.get(key) for key in expected}
            observed = {'unit': spec['unit'], 'state': actual,
                        'invocation_id': second['InvocationID'], 'process': second['process']}
        elif spec['kind'] == 'file':
            first, binding = _read(spec['path'])
            second, rebound = _read(spec['path'])
            current = binding == rebound
            actual = hashlib.sha256(second).hexdigest()
            observed = {'binding': rebound}
        else:
            raise ValueError('unknown claim sensor')
        return check(ident, 'A', {'available': True, 'current': current,
                     'scope_matches': spec.get('host') == socket.gethostname(),
                     'agrees': actual == expected}, 'Observed state compared with the declared claim', **observed)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        return check(ident, 'A', {}, str(exc))


def runtime_subject(args):
    """Identify runtime entrypoints, not arbitrary shell text mentioning the project."""
    if not args:
        return False
    program = Path(args[0]).name
    if program in ('z0int', 'z0int-bridge'):
        return True
    if not re.fullmatch(r'(?:python(?:\d+(?:\.\d+)*)?|pypy\d*)', program):
        return False
    if '-m' in args:
        index = args.index('-m')
        return index + 1 < len(args) and args[index + 1].startswith('z0int.')
    return len(args) > 1 and args[1].endswith('.py') and '/z0int/' in args[1]


def sensor_manifest(spec):
    """Observe installed entrypoints and live process identities against one release."""
    rows = []
    try:
        manifest, binding = _json(spec['manifest'])
        source = _path(manifest['source_root']).resolve()
        sha = manifest.get('integration_sha', manifest.get('source_sha'))
        stamp, stamp_binding = _read(source / 'DEPLOYED_SHA')
        pointer = _path(manifest['current_reference']).resolve()
        approved, approved_binding = _json(spec['hash_manifest'])
        hashes = approved.get('sha256')
        if approved.get('source_sha') != sha or not isinstance(hashes, dict) or not hashes:
            raise ValueError('approved nonempty artifact hash manifest required')
        artifacts = []
        for relative, expected in hashes.items():
            artifact = (source / relative).resolve(strict=True)
            if source not in artifact.parents:
                raise ValueError('artifact escapes approved release root')
            _, artifact_binding = _read(artifact)
            artifacts.append((artifact, artifact_binding, expected))
        stable = (_read(spec['manifest'])[1] == binding
                  and _read(spec['hash_manifest'])[1] == approved_binding
                  and _read(source / 'DEPLOYED_SHA')[1] == stamp_binding
                  and _path(manifest['current_reference']).resolve() == pointer
                  and all(_read(path)[1] == found for path, found, _ in artifacts))
        disk_ok = (pointer == source and stamp.decode().strip() == sha
                   and manifest.get('cutover_performed') is True
                   and all(found['sha256'] == expected for _, found, expected in artifacts))
        rows.append(check('manifest-generation', 'B', {'available': stable, 'disk_matches': disk_ok,
                          'process_matches': True,
                          'canonical': not any(part in str(source) for part in ('/z0-wt/', '/worktrees/'))},
                          'Release pointer, approved artifact hashes, content stamp and completed cutover',
                          manifest=binding, stamp=stamp_binding, hash_manifest=approved_binding))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        rows.append(check('manifest-generation', 'B', {}, str(exc)))
        manifest, source, sha = {}, None, None
    for name in spec.get('units', []):
        try:
            first = _unit(name)
            final = _unit(name)
            stable = all(first[key] == final[key] for key in ('InvocationID', 'MainPID', 'process'))
            paths = ' '.join(final.get(key, '') for key in ('ExecStart', 'Environment', 'WorkingDirectory'))
            worktree = '/z0-wt/' in paths or '/worktrees/' in paths
            if worktree:
                # Positive evidence of forbidden live wiring is not missing evidence.
                facts = {'available': stable, 'disk_matches': True, 'process_matches': False, 'canonical': False}
            else:
                # A disk path alone cannot attest already-imported Python/extension code.
                facts = {'available': False}
            rows.append(check(f'unit:{name}', 'B', facts,
                              'Worktree-linked entrypoint' if worktree else 'Loaded startup digest unavailable; no in-memory claim',
                              unit=name, invocation_id=final['InvocationID'], process=final['process'],
                              worktree_linked=worktree))
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
            rows.append(check(f'unit:{name}', 'B', {}, str(exc)))
    for value in spec.get('entrypoints', []):
        p = _path(value)
        try:
            resolved = p.resolve(strict=True)
            linked = '/z0-wt/' in str(resolved) or '/worktrees/' in str(resolved)
            matches = source is not None and (resolved == source or source in resolved.parents)
            rows.append(check(f'entrypoint:{p}', 'B', {'available': True, 'disk_matches': matches,
                              'process_matches': True, 'canonical': not linked},
                              'Resolved executable/extension binding (disk only)', path=str(p), resolved=str(resolved)))
        except OSError as exc:
            rows.append(check(f'entrypoint:{p}', 'B', {}, str(exc)))
    # Enumerate only relevant live runtimes; never print environment or argv contents.
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        argv = ''
        try:
            before = _proc(int(proc.name))
            args = [item.decode(errors='replace') for item in (proc / 'cmdline').read_bytes().split(b'\0') if item]
            argv = ' '.join(args)
            if not runtime_subject(args):
                continue
            # Exclude only this observer's PID; a worker's arbitrary arguments cannot hide it.
            if int(proc.name) == os.getpid():
                continue
            env = (proc / 'environ').read_bytes().split(b'\0')
            safe_paths = [item.decode(errors='replace') for item in env
                          if item.startswith((b'PYTHONPATH=', b'Z0INT_SOURCE_ROOT='))]
            cwd = str((proc / 'cwd').resolve(strict=True))
            linked = any('/z0-wt/' in value or '/worktrees/' in value for value in [argv, cwd, *safe_paths])
            stable = before == _proc(int(proc.name))
            rows.append(check(f'process:{proc.name}', 'B', {'available': stable if linked else False,
                              'disk_matches': True, 'process_matches': False, 'canonical': not linked},
                              'Live runtime references a feature worktree' if linked else 'No loaded source digest attestation',
                              process=before, cwd=cwd, worktree_linked=linked))
        except (FileNotFoundError, ProcessLookupError):
            continue  # process exited; not a live deployment member
        except (OSError, ValueError, IndexError) as exc:
            if any(t in argv for t in ('z0int', 'shadow_learning')):
                rows.append(check(f'process:{proc.name}', 'B', {}, str(exc)))
    return rows


REVOKED = {
    'phone-disk-floor': re.compile(r'min_disk_free_gb\s*[:=]\s*[1-9]|disk_free_gb\s*<\s*min_disk'),
    'phone-memory-cap': re.compile(r'(?:512\s*(?:MiB|MB|M)\b|(?:MemoryMax|memory_max|memory_pct|min_mem_available_mb|mem_floor_mb|default_mem|mem_max|rss_limit)\s*[:=])', re.I),
    'phone-cpu-cap': re.compile(r'(?:taskset\s+(?:-c\s+)?0-[0-6]|(?:CPUQuota|cpu(?:_mask|set|s))\s*[:=]\s*[\"\']?\d)', re.I),
    'phone-battery-cap': re.compile(r'(?:battery[^\n]{0,35}(?:50\s*%|[<:=]\s*50)|min_battery[^\n]{0,15}50)', re.I),
    'phone-thermal-cap': re.compile(r'(?:max_android_thermal_status|max_temp_c|thermal_limit|temperature_limit)\s*[:=]\s*\d', re.I),
}
TEXT_SUFFIXES = {'.md', '.txt', '.yaml', '.yml', '.json', '.py', '.sh', '.toml', '.conf'}
SKIP = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '.cache'}


def _phone_yaml_lines(text):
    """Only phone node declarations count as phone caps in mixed fleet YAML."""
    root = yaml.compose(text)
    spans = []
    def visit(node):
        if isinstance(node, yaml.MappingNode):
            entries = {key.value: value for key, value in node.value}
            kind = entries.get('kind')
            if isinstance(kind, yaml.ScalarNode) and kind.value == 'phone':
                spans.append((node.start_mark.line + 1, node.end_mark.line + 1))
            else:
                for child in entries.values():
                    visit(child)
        elif isinstance(node, yaml.SequenceNode):
            for child in node.value:
                visit(child)
    visit(root)
    return spans


def sensor_revocations(spec):
    """Scan declared admitted stores; historical/platform blocks are evidence, not policy.

Exempt blocks must use explicit `historical:`/`platform-observation:` and
`end-historical`/`end-platform-observation` lines. No inferred prose exemptions.
    """
    rows, files, errors, hits = [], set(), [], []
    ledger_binding, epoch_current = None, False
    try:
        owner, ledger_binding = _json(spec['policy_ledger'])
        if owner.get('schema') != 'z0.revocations.v1' or type(owner.get('policy_epoch')) is not int:
            raise ValueError('typed owner revocation ledger required')
        ids = {row['id'] for row in owner['revocations']}
        if not set(REVOKED).issubset(ids):
            raise ValueError('revocation ledger does not cover every scanned rule')
        epoch_current = owner['policy_epoch'] == spec['policy_epoch']
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append(f'owner ledger unavailable: {exc}')
    for value in spec.get('roots', []):
        root = _path(value)
        if not root.exists():
            errors.append(str(root))
            continue
        if root.is_file():
            files.add(root)
        else:
            def onerror(exc):
                errors.append(str(exc))
            for directory, dirs, names in os.walk(root, onerror=onerror, followlinks=False):
                dirs[:] = [name for name in dirs if name not in SKIP]
                for name in names:
                    p = Path(directory) / name
                    if p.suffix in TEXT_SUFFIXES or name in ('fleet', 'AGENTS.md', 'SKILL.md'):
                        files.add(p)
                for name in dirs:
                    if (Path(directory) / name).is_symlink():
                        errors.append(f'unscanned linked store: {Path(directory) / name}')
    for p in sorted(files):
        try:
            data, binding = _read(p)
            text = data.decode('utf-8')
            phone_spans = _phone_yaml_lines(text) if p.suffix in ('.yaml', '.yml') and 'fleet' in str(p) else None
            phone_file = any(word in str(p).lower() for word in ('phone', 's22', 's25', 'mobile'))
            o10 = phone_caps_findings(p.parent.parent, [p]) if p.suffix in ('.py', '.sh', '.yaml', '.yml', '.md') or p.name == 'fleet' else []
            if any(finding['rule'] == 'scan-unavailable' for finding in o10):
                errors.append(f'O10 source unavailable: {p}')
            o10_lines = {finding['line'] for finding in o10 if finding['rule'] != 'scan-unavailable'}
            historical = False
            phone_context = phone_file
            for number, line in enumerate(text.splitlines(), 1):
                marker = line.strip().lower()
                if p.suffix == '.md' and line.startswith('#'):
                    phone_context = phone_file or any(word in marker for word in ('phone', 's22', 's25'))
                if marker in ('historical:', 'platform-observation:'):
                    historical = True
                    continue
                if marker in ('end-historical', 'end-platform-observation'):
                    historical = False
                    continue
                if historical:
                    continue
                if phone_spans is not None and not any(start <= number < end for start, end in phone_spans):
                    continue
                if phone_spans is None and not phone_context and number not in o10_lines and not any(word in line.lower() for word in ('phone', 's22', 's25')):
                    continue
                for rule, pattern in REVOKED.items():
                    matched = pattern.search(line) if rule == 'phone-disk-floor' else (
                        number in o10_lines and pattern.search(line))
                    if matched:
                        hit = {'path': str(p), 'line': number, 'revocation_id': rule, 'binding': binding}
                        hits.append(hit)
                        rows.append(check(f'revocation:{p}:{number}:{rule}', 'C',
                                          {'available': ledger_binding is not None, 'epoch_current': epoch_current, 'no_active_hits': False,
                                           'coverage_complete': True}, 'Revoked phone restriction remains loadable', **hit))
        except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
            errors.append(f'{p}: {exc}')
    if ledger_binding is not None:
        try:
            if _read(spec['policy_ledger'])[1] != ledger_binding:
                errors.append('owner policy epoch changed during scan')
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    rows.append(check('revocation-coverage', 'C',
                      {'available': bool(files) and not errors, 'epoch_current': epoch_current,
                       'no_active_hits': not hits, 'coverage_complete': spec.get('inventory_complete') is True},
                      'Declared store inventory coverage; unreadable stores never pass',
                      files_scanned=len(files), unavailable=errors,
                      policy_epoch=spec.get('policy_epoch'), ledger_binding=ledger_binding, revocations=list(REVOKED)))
    return rows


def sensor_outcome(spec):
    binding = None
    try:
        row, binding = _json(spec['path'])
        if row.get('schema') == 'z0int.loop.tick_summary.v0':
            # Existing producer contract: a degraded tick must never be promoted
            # to verified success. A successful summary still needs output proof.
            stable = _read(spec['path'])[1] == binding
            contradicted = row.get('status') in ('degraded', 'partial_measurement') or row.get('exit') not in (0, None)
            facts = {'available': stable and contradicted, 'typed_receipt': False,
                     'output_verified': False, 'work_positive': False,
                     'transport_ok': False, 'target_current': True}
            return check(spec['id'], 'D', facts,
                         'Native tick is non-success; success promotion refused' if contradicted
                         else 'Native tick summary alone lacks independently bound output proof',
                         receipt=binding, observed_schema=row['schema'], observed_status=row.get('status'),
                         observed_exit=row.get('exit'), rows_added=row.get('rows_added'),
                         transport_tick=(row.get('cognition') or {}).get('transport_tick'))
        output = _path(row['output_path'])
        data, output_binding = _read(output)
        typed = row.get('schema') == 'z0.state_outcome.v1' and row.get('outcome') == 'success'
        natural = lambda key: type(row.get(key)) is int and row[key] > 0
        facts = {'available': True, 'typed_receipt': typed,
                 'output_verified': bool(data) and output_binding['sha256'] == row.get('output_sha256'),
                 'work_positive': natural('attempted') and natural('valid_decisions'),
                 'transport_ok': natural('transport_ok') and row.get('exit_code') == 0,
                 'target_current': row.get('target_retired') is False}
        # Stable output and receipt reads bind the assertion, not a reporter boolean.
        if _read(spec['path'])[1] != binding or _read(output)[1] != output_binding:
            facts['available'] = False
        return check(spec['id'], 'D', facts, 'Typed positive output contract', receipt=binding, output=output_binding)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return check(spec['id'], 'D', {}, str(exc), receipt=binding)


def sensor_constant(spec):
    try:
        measurement, binding = _json(spec['measurement'])
        source, source_binding = _read(spec['path'])
        tree = ast.parse(source, filename=str(spec['path']))
        declarations = []
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == spec['name'] for t in node.targets):
                declarations.append(ast.literal_eval(node.value))
        if len(declarations) != 1 or type(declarations[0]) not in (int, float):
            raise ValueError('one numeric top-level constant declaration required')
        date = dt.datetime.fromisoformat(measurement['measured_at'].replace('Z', '+00:00'))
        measured = (measurement.get('schema') == 'z0.measurement.v1' and date.tzinfo is not None
                    and bool(measurement.get('command')) and measurement.get('kind') == 'measurement')
        value = measurement['value']
        if type(value) not in (int, float) or type(spec['value']) not in (int, float):
            raise ValueError('numeric measurement required')
        # Exact same-boundary contract, no invented tolerance or rate conversion.
        agrees = (declarations[0] == value == spec['value']
                  and measurement.get('source_sha256') == source_binding['sha256'])
        return check(spec['id'], 'E', {'available': True, 'measured': measured,
                     'scope_matches': measurement.get('scope') == spec['scope'] and measurement.get('units') == spec['units'],
                     'agrees': agrees}, 'Constant cites a dated same-scope measurement of current source',
                     measurement=binding, source=source_binding)
    except (OSError, ValueError, KeyError, TypeError, SyntaxError) as exc:
        return check(spec['id'], 'E', {}, str(exc))


def sensor_host_drift(spec):
    """Consume a stable report-only host-drift.v1 observation, never repair it."""
    try:
        row, binding = _json(spec['path'])
        if not isinstance(row, dict):
            raise ValueError('host drift report must be an object')
        observed = dt.datetime.fromisoformat(row['observed_at'].replace('Z', '+00:00'))
        if observed.tzinfo is None:
            raise ValueError('timezone-aware host observation required')
        age = (dt.datetime.now(dt.timezone.utc) - observed).total_seconds()
        if not 0 <= age <= 2100 or not isinstance(row.get('host'), str) or not row['host']:
            raise ValueError('current identified host observation required')
        if (row.get('schema') != 'host-drift.v1'
                or row.get('status') not in ('clean', 'drift', 'unknown')
                or not isinstance(row.get('drift'), list)
                or not isinstance(row.get('unknown'), list)):
            raise ValueError('host-drift.v1 report required')
        for item in row['drift'] + row['unknown']:
            if not isinstance(item, dict) or not all(isinstance(item.get(k), str) and item[k]
                                                    for k in ('kind', 'path', 'detail')):
                raise ValueError('typed drift/unknown rows required')
        if row['status'] == 'clean' and (row['drift'] or row['unknown']):
            raise ValueError('clean report contains adverse evidence')
        if row['status'] == 'drift' and not row['drift']:
            raise ValueError('drift report has no drift evidence')
        if row['status'] == 'unknown' or row['unknown']:
            return check('host-tracked-source', 'I', {}, 'Host evidence incomplete', report=binding)
        stable = _read(spec['path'])[1] == binding
        return check('host-tracked-source', 'I',
                     {'available': stable, 'tracked_source': not row['drift'],
                      'scope_matches': row.get('host') == spec['host'],
                      'agrees': not row['drift']},
                     'Live config must have a tracked source; checker report is evidence, not authority',
                     report=binding, drift=row['drift'])
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return check('host-tracked-source', 'I', {}, str(exc))


def audit(config):
    checks = []
    for spec in config.get('claims', []):
        checks.append(sensor_claim(spec))
    if not config.get('claims'):
        checks.append(check('claims-inventory', 'A', {}, 'No structured live claims declared'))
    checks.extend(sensor_manifest(config.get('manifest', {})))
    checks.extend(sensor_revocations(config.get('revocations', {})))
    for spec in config.get('outcomes', []):
        checks.append(sensor_outcome(spec))
    if not config.get('outcomes'):
        checks.append(check('outcomes-inventory', 'D', {}, 'No typed positive-output receipts declared'))
    for spec in config.get('constants', []):
        checks.append(sensor_constant(spec))
    if not config.get('constants'):
        checks.append(check('constants-inventory', 'E', {}, 'No same-scope measurement receipts declared'))
    if config.get('host_drift'):
        checks.append(sensor_host_drift(config['host_drift']))
    return checks


def command(args):
    try:
        path = args.input or args.config
        document, binding = _json(path)
        if not isinstance(document, dict):
            raise ValueError('state inventory must be an object')
        if args.input:
            checks = [check(row.get('id', f'input:{index}'), row.get('law'), row.get('facts'),
                            row.get('reason', 'Finite replay input; not a live observation'),
                            incident=row.get('incident')) for index, row in enumerate(document['checks'])]
        else:
            checks = audit(document)
        result = report(checks, source=path)
        result['inventory_binding'] = binding
    except (OSError, ValueError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        result = report([check('inventory-unavailable', 'A', {}, str(exc))], source=getattr(args, 'config', None))
    print(json.dumps(result, sort_keys=True))
    return 3 if result['counts']['violation'] else 4 if result['counts']['unknown'] else 0
