"""Law H: read-only agent placement census; never move, signal, or kill."""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import subprocess

AGENTS = frozenset(('omp', 'hermes', 'claude', 'codex', 'opencode', 'pi', 'unii', 'llama-server'))


def decide(available, agent, scoped):
    """Unknown evidence cannot establish placement compliance."""
    if any(type(value) is not bool for value in (available, agent, scoped)) or not available:
        return 'unknown'
    return 'violation' if agent and not scoped else 'pass'


def _agent_token(token):
    name = Path(token).name
    return name in AGENTS or name in {f'{agent}.{ext}' for agent in AGENTS for ext in ('js', 'mjs', 'py')}


def _interpreter(name):
    return name in ('bun', 'node', 'nodejs') or re.fullmatch(r'python(?:[23](?:\.[0-9]+)?)?', name) is not None


def _process(root, pid):
    directory = root / str(pid)
    def identity():
        fields = (directory / 'stat').read_text().rsplit(')', 1)[1].split()
        return int(fields[1]), int(fields[19])
    ppid, start = identity()
    exe = str((directory / 'exe').readlink())
    comm = (directory / 'comm').read_text().strip()
    argv = [part.decode(errors='replace') for part in (directory / 'cmdline').read_bytes().split(b'\0') if part]
    groups = [line.split(':', 2)[2] for line in (directory / 'cgroup').read_text().splitlines() if line.startswith('0::')]
    if len(groups) != 1 or identity() != (ppid, start):
        raise ValueError('missing v2 cgroup or process identity changed')
    name = Path(exe).name.removesuffix(' (deleted)')
    # Match executable/script positions, not arbitrary prompt text mentioning an agent.
    direct = _agent_token(name) or _agent_token(comm) or bool(argv and _agent_token(argv[0]))
    if _interpreter(name) and len(argv) > 1:
        direct = direct or _agent_token(argv[1]) or (len(argv) > 2 and argv[1] == '-m' and argv[2].split('.')[0] in AGENTS)
    return dict(pid=pid, ppid=ppid, exe=exe, name=name, direct=direct,
                interpreter=_interpreter(name), cgroup=groups[0], starttime=start)


def systemd_units():
    """Read manager configuration and runtime cgroups without changing units."""
    result = subprocess.run(
        ['systemctl', '--user', 'show', 'agent-*.scope', 'hermes-gateway.service',
         '-p', 'Id', '-p', 'LoadState', '-p', 'ActiveState', '-p', 'Slice',
         '-p', 'ControlGroup', '-p', 'OOMPolicy'],
        capture_output=True, text=True, timeout=15, check=True)
    return [dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
            for block in result.stdout.strip().split('\n\n') if block.strip()]


def unit_findings(units):
    findings = []
    for unit in units:
        name = unit.get('Id', '')
        if name.startswith('agent-') and name.endswith('.scope'):
            policy = unit.get('OOMPolicy')
            if policy != 'continue':
                findings.append(dict(unit=name, status='violation' if policy else 'unknown',
                                     reason='agent scope OOMPolicy is not continue' if policy else 'OOMPolicy unavailable',
                                     configured_oom_policy=policy, cgroup=unit.get('ControlGroup')))
        if name == 'hermes-gateway.service' and unit.get('ActiveState') == 'active':
            configured, group = unit.get('Slice'), unit.get('ControlGroup')
            if configured == 'agent.slice' and group and 'agent.slice' not in group.split('/'):
                findings.append(dict(unit=name, status='violation',
                                     reason='configured Slice differs from running cgroup; restart not applied',
                                     configured_slice=configured, cgroup=group))
            elif configured == 'agent.slice' and not group:
                findings.append(dict(unit=name, status='unknown', reason='running cgroup unavailable'))
    return findings

def audit(root=Path('/proc'), units=()):
    root = Path(root)
    rows, unknown, violations, compliant = {}, [], [], []
    observed = dt.datetime.now(dt.timezone.utc).isoformat()
    try:
        pids = sorted(int(path.name) for path in root.iterdir() if path.name.isdecimal())
    except OSError as exc:
        pids = []
        unknown.append(dict(reason=type(exc).__name__))
    for pid in pids:
        try:
            rows[pid] = _process(root, pid)
        except (OSError, ValueError, IndexError) as exc:
            unknown.append(dict(pid=pid, reason=type(exc).__name__))
    for row in rows.values():
        chain, seen, candidate, agent, error = [], set(), row, row['direct'], None
        while candidate is not None:
            if candidate['pid'] in seen:
                error = 'ancestry-cycle'
                break
            seen.add(candidate['pid'])
            chain.append(candidate)
            agent = agent or candidate['direct']
            parent = candidate['ppid']
            if parent == 0:
                break
            candidate = rows.get(parent)
            if candidate is None:
                error = 'ancestor-unavailable'
                break
        # Reparenting destroys provenance. Do not call an unidentifiable interpreter safe.
        if not agent:
            if row['interpreter'] and (error or row['ppid'] == 1 or (len(chain) > 1 and chain[1]['name'] == 'systemd')):
                unknown.append(dict(pid=row['pid'], exe=row['exe'], cgroup=row['cgroup'], reason=error or 'orphan-workload-identity-unknown', ancestry=chain))
            continue
        try:
            if any(_process(root, item['pid']) != item for item in chain):
                raise ValueError('identity changed before report')
        except (OSError, ValueError, IndexError) as exc:
            unknown.append(dict(pid=row['pid'], reason=type(exc).__name__))
            continue
        components = row['cgroup'].split('/')
        scoped = 'agent.slice' in components or 'z0-farm-llama.service' in components
        desktop = any(part.startswith('wayland-wm@') and part.endswith('.service') for part in components)
        reason = ('agent workload in desktop service' if desktop else
                  'agent workload outside allowed scope' if not scoped else 'agent workload correctly scoped')
        finding = dict(pid=row['pid'], exe=row['exe'], cgroup=row['cgroup'], ancestry=chain,
                       status=decide(True, True, scoped), reason=reason)
        (compliant if scoped else violations).append(finding)
    unit_rows = unit_findings(units)
    violations.extend(row for row in unit_rows if row['status'] == 'violation')
    unknown.extend(row for row in unit_rows if row['status'] == 'unknown')
    return dict(schema='z0.cgroup_placement.v1', law='H', mode='report-only', enforcement_enabled=False,
                observed_at=observed, status='violation' if violations else 'unknown' if unknown else 'pass',
                violations=violations, unknown=unknown, compliant=compliant, unit_findings=unit_rows,
                counts=dict(violation=len(violations), unknown=len(unknown), compliant=len(compliant), observed=len(rows)))


def command(args):
    report = audit(args.proc_root)
    if args.proc_root == Path('/proc'):
        try:
            findings = unit_findings(systemd_units())
            report['unit_findings'] = findings
            for row in findings:
                report['violations' if row['status'] == 'violation' else 'unknown'].append(row)
        except (OSError, subprocess.SubprocessError) as exc:
            report['unknown'].append(dict(reason='systemd census unavailable', error=type(exc).__name__))
        report['counts']['violation'] = len(report['violations'])
        report['counts']['unknown'] = len(report['unknown'])
        report['status'] = 'violation' if report['violations'] else 'unknown' if report['unknown'] else 'pass'
    print(json.dumps(report, sort_keys=True))
    return 0


def register(subparsers: argparse._SubParsersAction):
    parser = subparsers.add_parser('cgroup-placement', help='Report Law H agent placement; never remediate')
    parser.add_argument('--proc-root', type=Path, default=Path('/proc'))
    parser.set_defaults(fn=command)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proc-root', type=Path, default=Path('/proc'))
    raise SystemExit(command(parser.parse_args()))
