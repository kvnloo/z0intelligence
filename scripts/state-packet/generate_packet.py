#!/usr/bin/env python3
"""Generate a bounded, source-linked orientation view. No model calls or source edits.

All factual packet text comes from current source text or observed runtime metadata.
The unified-memory/INDEX.md source-pointer convention is retained. The source hashes
make a packet reproducible from its inputs, not an authority grant. Gold questions
and experiment responses are never inputs. Run under nice/ionice.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from datetime import datetime, timezone

OPS = Path('/mnt/zer0models/workspace/zer0/data/operations')
HERE = OPS / 'repo-distill'
DEFAULT_REPO = Path('/mnt/zer0models/z0-wt/z0intelligence')
EXCLUDE = {'repo-distill'}


def cmd(args: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(args, cwd=cwd, text=True, capture_output=True, timeout=25)
    return proc.stdout.strip() if proc.returncode == 0 else ''


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean(text: str, limit: int = 700) -> str:
    text = re.sub(r'\s+', ' ', text).strip().replace('`', "'")
    return text if len(text) <= limit else text[:limit] + ' [excerpt]'


def atomic_write(path: Path, text: str) -> None:
    """Publish complete files on the same filesystem, never a partial packet."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--repo', type=Path, default=DEFAULT_REPO)
    ap.add_argument('--out', type=Path, default=Path.home() / '.local/state/z0int/STATE-PACKET.md')
    ap.add_argument('--offline', action='store_true', help='Use saved issue/PR snapshots, do not call GitHub.')
    args = ap.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    sources: dict[str, str] = {}
    sections: dict[str, list[str]] = {}

    def extract(path: Path, pattern: str, cap: int, limit: int = 700) -> list[str]:
        if not path.is_file():
            return []
        sources[str(path)] = digest(path)
        found = []
        for n, line in enumerate(path.read_text(errors='replace').splitlines(), 1):
            if re.search(pattern, line, re.I):
                found.append(f'- `{path}:{n}` → {clean(line, limit)}')
        return found[:cap]

    def section(name: str, rows: list[str]) -> None:
        sections[name] = rows

    section('North-star goals — repository quotations',
        extract(args.repo / 'README.md', r'^\*\*A provenance|^z0intelligence turns', 2) +
        extract(args.repo / 'ROADMAP.md', r'^>.*Optimize for|^Success means|^z0int is not trying', 3))
    section('Invariants and method — source quotations',
        extract(args.repo / 'README.md', r'^> Core invariant', 1) +
        extract(args.repo / 'ROADMAP.md', r'^- (state construction|evidence,|execution completion|learned systems|promotion is|every promoted|memory is|cross-harness)', 8) +
        extract(args.repo / 'docs/METHOD.md', r'^The reranker system|^- A forced|^- Softmax', 3) +
        extract(args.repo / 'AGENTS.md', r'never|must|free-first|privacy|authority', 5))

    policy_rows = []
    policy_root = Path(os.environ.get('Z0INT_POLICY_ROOT', '/mnt/zer0models/z0-wt/bend-policy-laws'))
    capacity_source = policy_root / 'src/z0int/fsm/policy_guard.py'
    if capacity_source.is_file():
        sources[str(capacity_source)] = digest(capacity_source)
        policy_rows.append(f'- `{capacity_source}:179-200` → Small local models: max options per decision = policy.option_capacity allowed_k; default 2 until ChoiceLadder findings land (law O13, z0int #212).')
    for relative in ('src/z0int/fsm/ops.py', 'bend/ops_fsm/LAWS.bend'):
        policy_rows += extract(policy_root / relative, r'allowed_k|option_capacity|o10_phone|o13_|no_agent_resource_caps', 4)
    serving = Path('/home/kvn/.z0int/config/serving.json')
    if serving.is_file():
        sources[str(serving)] = digest(serving)
        value = json.loads(serving.read_text())
        policy_rows.append(f'- `{serving}#/supervisor` → {clean(json.dumps(value.get("supervisor", {}), sort_keys=True))}')
    policy_rows += extract(OPS / 'unified-memory/INDEX.md', r'^- S22 core policy|^- Phone worker lane|^- Native SLM|^- Owner-busy phone|^- Goal vs scripts|^- Phone knowledge loading|^- Retrieval boundary', 7)
    section('Policy declarations and cross-harness pointers', policy_rows)

    # Observe actual cwd and configured unit targets without reading environment/secrets.
    live = []
    for item in sorted(Path('/proc').iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else -1):
        if not item.name.isdigit():
            continue
        try:
            cwd = (item / 'cwd').resolve(strict=True)
            argv_parts = (item / 'cmdline').read_bytes().decode(errors='replace').split('\0')
            argv = ' '.join(p for p in argv_parts if p)
        except (OSError, PermissionError):
            continue
        if not any(re.fullmatch(r'z0int(?:\.[\w]+)+', p) or
                   (p.startswith('/') and 'z0int' in p and p.endswith('.py'))
                   for p in argv_parts):
            continue
        if 'generate_packet.py' in argv or any(p in ('-c', '-p', '--prompt') for p in argv_parts):
            continue
        root = cmd(['git', 'rev-parse', '--show-toplevel'], cwd)
        sha = cmd(['git', 'rev-parse', 'HEAD'], cwd) if root else ''
        branch = cmd(['git', 'branch', '--show-current'], cwd) if root else ''
        live.append({'pid': int(item.name), 'cwd': str(cwd), 'git_root': root, 'sha': sha, 'branch': branch, 'command': clean(argv, 320)})
    units = []
    for scope in (['--user'], []):
        names = cmd(['systemctl', *scope, 'list-unit-files', '--no-legend', '--no-pager']).splitlines()
        for line in names:
            name = line.split()[0] if line.split() else ''
            if not re.search(r'z0int|z0-loop|shadow-learn|quackles|qk-arbiter', name) or not name.endswith('.service'):
                continue
            text = cmd(['systemctl', *scope, 'show', name, '--no-pager', '-p', 'ExecStart', '-p', 'WorkingDirectory', '-p', 'MainPID', '-p', 'ActiveState', '-p', 'FragmentPath', '-p', 'DropInPaths'])
            if text:
                paths = []
                for prop in text.splitlines():
                    if prop.startswith(('FragmentPath=', 'DropInPaths=')):
                        paths.extend(Path(x) for x in prop.split('=', 1)[1].split())
                config = []
                for path in paths:
                    config += extract(path, r'^(ExecStart|WorkingDirectory)=|^Environment=.*PYTHONPATH=', 5, 500)
                units.append({'scope': 'user' if scope else 'system', 'name': name, 'properties': text.splitlines(), 'configuration': config})
    links = []
    ext = Path('/home/kvn/.omp/agent/extensions')
    if ext.is_dir():
        for p in sorted(ext.iterdir()):
            if p.is_symlink() and ('z0' in str(p.resolve()) or 'cognition' in p.name):
                target = p.resolve()
                links.append({'path': str(p), 'target': str(target), 'sha': cmd(['git', 'rev-parse', 'HEAD'], target), 'branch': cmd(['git', 'branch', '--show-current'], target)})
    runtime = {'processes': live, 'units': units, 'extension_links': links}
    runtime_path = args.out.with_name('packet-runtime.json')
    atomic_write(runtime_path, json.dumps(runtime, indent=2, sort_keys=True) + '\n')
    sources[str(runtime_path)] = digest(runtime_path)
    rows = [f'- `{runtime_path}#/processes/{i}` → PID {r["pid"]}, cwd {r["cwd"]}, branch {r["branch"] or "unresolved"}, HEAD {r["sha"] or "unresolved"}, command {r["command"]}' for i, r in enumerate(live)]
    rows += [f'- `{runtime_path}#/units/{i}` → {r["scope"]} {r["name"]}: {clean(" | ".join(r["properties"]), 1100)}' for i, r in enumerate(units)]
    rows += [row for r in units for row in r['configuration']]
    rows += [f'- `{runtime_path}#/extension_links/{i}` → {r["path"]} → {r["target"]}, HEAD {r["sha"]}, branch {r["branch"]}' for i, r in enumerate(links)]
    section('What is live where — observed, not inferred from branch names', rows[:35])

    pilot_rows = []
    for model, folder in (('qwen25-05', 'pilot-v1-qwen25-05'), ('qwen3-06', 'pilot-v1-qwen3-06')):
        path = OPS / 'choice-ladder' / folder / model / 'rows.jsonl'
        if not path.is_file():
            continue
        sources[str(path)] = digest(path)
        groups = {}
        pooled = {}
        for line in path.read_text().splitlines():
            row = json.loads(line)
            pooled_good, pooled_n = pooled.get(row['k'], (0, 0))
            pooled[row['k']] = (pooled_good + int(row.get('correct') is True), pooled_n + 1)
            if row.get('task') != 'T1':
                continue
            k = row['k']
            good, n = groups.get(k, (0, 0))
            groups[k] = (good + int(row.get('correct') is True), n + 1)
        if model == 'qwen25-05':
            measured = ', '.join(f'k{k} {pooled[k][0]}/{pooled[k][1]}' for k in (2, 3, 8, 32) if k in pooled)
            pilot_rows.append(f'- `{path}` → qwen2.5-0.5b pilot v1: {measured}. Pooled synthetic partial pilot, excluded from final capacity gates; shows collapse past 3, not a general capability maximum.')
        for k, (good, n) in sorted(groups.items()):
            pilot_rows.append(f'- `{path}` → Excluded protocol-v1 synthetic T1 pilot: {model}, k={k}, {good}/{n} ({100*good/n:.1f}%) correct. Not a final capability gate.')

    section('Measured findings — atomic source excerpts',
        extract(OPS / 'omp-memory/REPORT.md', r'^\*\*The startup|^At the census|^An aggregate scan|^\*\*The process', 4) +
        pilot_rows[:12] +
        extract(OPS / 'choice-ladder/REPORT.md', r'^\*\*Status:|^Final-protocol', 2) +
        extract(OPS / 'choice-ladder/REPORT.md', r'^These are controlled|^\*\*.*pilot', 2) +
        extract(OPS / 'shadow-learner/CONTEXT-FIX.txt', r'overflow|token|live|exact|context|objective|candidate', 7) +
        extract(OPS / 'shadow-learner/REPORT.txt', r'^CURRENT UPDATE|^Post-cutover|^Quackles arbiter|^service resolves|^executor Service|^DISPATCHER|^Transport base|^70,601|^Those are NOT|^Collision', 10) +
        extract(OPS / 'ask-audit/REPORT.md', r'^- Eligible|^- Reviewed:|^\| (prior-knowledge|stale-policy|relay-paraphrase|scope-substitution|task-drift)', 7) +
        extract(OPS / 'edge-probe/REPORT.md', r'avrea|Genie|NPU|blocked|verified|measured', 5))

    issue_data = None
    pr_data = None
    if not args.offline:
        for name, command in [('packet-issues-live.json', ['gh','issue','list','-R','kvnloo/z0intelligence','--state','open','--limit','1000','--json','number,title,labels,url,body,updatedAt']), ('packet-prs-live.json', ['gh','pr','list','-R','kvnloo/z0intelligence','--state','open','--limit','1000','--json','number,title,labels,url,body,baseRefName,headRefName,headRefOid,isDraft'])]:
            text = cmd(command)
            if text:
                value = json.loads(text)
                atomic_write(HERE / name, json.dumps(value, indent=2, sort_keys=True) + '\n')
    for name in ('packet-issues-live.json', 'issues.json'):
        p = HERE / name
        if p.is_file():
            value = json.loads(p.read_text())
            issue_data = value if isinstance(value, list) else value.get('issues', [])
            sources[str(p)] = digest(p)
            break
    for name in ('packet-prs-live.json', 'prs.json'):
        p = HERE / name
        if p.is_file():
            value = json.loads(p.read_text())
            pr_data = value if isinstance(value, list) else value.get('prs', [])
            sources[str(p)] = digest(p)
            break
    prs = sorted(pr_data or [], key=lambda p: p['number'])
    by_head = {p.get('headRefName'): p['number'] for p in prs}
    def deps(p: dict) -> set[int]:
        result = set()
        if p.get('baseRefName') in by_head:
            result.add(by_head[p['baseRefName']])
        for phrase in re.findall(r'(?i)(?:depends on|blocked by|after merge of|requires)[:\s]+([^\n.]+)', p.get('body', '')):
            result.update(int(n) for n in re.findall(r'#(\d+)', phrase) if int(n) in {q['number'] for q in prs})
        return result - {p['number']}
    ordered = []
    pending = list(prs)
    while pending:
        ready = [p for p in pending if deps(p) <= {r['number'] for r in ordered}]
        if not ready:
            ready = pending[:]
        for p in ready:
            ordered.append(p)
            pending.remove(p)
    rows = []
    for p in ordered:
        labels = ','.join(x.get('name','') if isinstance(x,dict) else str(x) for x in p.get('labels',[]))
        rows.append(f'- [{p["number"]}: {clean(p["title"], 200)}]({p.get("url", "")}) → base {p.get("baseRefName")}, head {p.get("headRefName")}@{p.get("headRefOid", "")[:12]}, draft={p.get("isDraft")}, labels={labels or "none"}, predecessors={sorted(deps(p)) or "none declared"}. Source: `{HERE / "packet-prs-live.json"}`.')
    section('In-flight PRs — dependency-first order, independent PRs have no established total order', rows[:50])
    issue_rows = []
    for issue in sorted(issue_data or [], key=lambda p:p['number']):
        if re.search(r'option|capacity|law|policy|north.star|context|memory|provenance', issue.get('title',''), re.I):
            labels = ','.join(x.get('name','') if isinstance(x,dict) else str(x) for x in issue.get('labels',[]))
            issue_rows.append(f'- [{issue["number"]}: {clean(issue["title"], 220)}]({issue.get("url", "")}) → labels={labels or "none"}. Source: `{HERE / "packet-issues-live.json"}`.')
    section('Issue title/label retrieval pointers — not deployment proof', issue_rows[:22])
    section('Known-wrong past decisions — explicit corrections, not silent replacement',
        extract(args.repo / 'ROADMAP.md', r'^RFCs #|^Accordingly,', 2) +
        extract(OPS / 'shadow-learner/REPORT.txt', r'^LEGACY LIMITATION|^Those are NOT|^Collision', 3) +
        extract(OPS / 'choice-ladder/REPORT.md', r'^- Qwen2.5-0.5B actual|^\*\*Status:', 2) +
        extract(OPS / 'unified-memory/INDEX.md', r'^- Installed MCP freshness|^- Retrieval limits|^- Pass-2 S22 distinction', 3))
    # Header sweep is mandatory input, but only bounded pointers enter model context.
    report_headers = []
    for p in sorted(OPS.glob('*/REPORT.md')):
        if p.parent.name in EXCLUDE:
            continue
        report_headers += extract(p, r'^#{1,2} ', 2, 240)
    section('Operations report header index', report_headers[:30])

    repo_sha = cmd(['git', 'rev-parse', 'HEAD'], args.repo)
    generated_at = datetime.now(timezone.utc).isoformat()
    generator_sha = digest(Path(__file__))
    provenance = f'`{Path(__file__).resolve()}`'
    lines = [
        f'{provenance} → # STATE-PACKET — generated evidence, not authority',
        f'{provenance} → generated_at: {generated_at}',
        f'{provenance} → generator_sha256: {generator_sha}',
        f'`{args.repo}` → repository_sha: {repo_sha}',
        f'{provenance} → Re-observe live state before action. No gold questions or experiment answers are inputs.',
    ]
    # Reserve space for every section. Headers and facts all carry source paths.
    caps = [5,17,12,30,38,42,22,10,30]
    for (name, rows), cap in zip(sections.items(), caps):
        lines += [f'{provenance} → ## {name}', *rows[:cap]]
    if len(lines) > 223:
        raise RuntimeError(f'Packet exceeds 223 lines: {len(lines)}')
    text = '\n'.join(lines) + '\n'
    if len(text.encode()) > 65536:
        raise RuntimeError('Packet exceeds 64 KiB')
    atomic_write(args.out, text)
    manifest = {'generated_at': generated_at, 'generator_sha256': generator_sha, 'sources': sources, 'line_count': len(lines), 'packet_sha256': hashlib.sha256(text.encode()).hexdigest(), 'runtime_sha256': digest(runtime_path), 'issues_present': issue_data is not None, 'prs_present': pr_data is not None, 'github_mode': 'offline snapshots' if args.offline else 'live with snapshot fallback', 'line_limit':223, 'report_headers_seen':len(report_headers)}
    atomic_write(args.out.with_name('packet-manifest.json'), json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'packet': str(args.out), 'lines': len(lines), 'source_files': len(sources), 'processes':len(live), 'units':len(units), 'prs':len(prs)}))

if __name__ == '__main__':
    main()
