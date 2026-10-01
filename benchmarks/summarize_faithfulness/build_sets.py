"""Build the frozen summarize-faithfulness v0 set from z0's own repos and CI (see PREREG.md).

Every item is a real tool invocation run here (pytest on mutated copies of z0intelligence, git on z0 repos,
ruff/mypy/compileall/uv build, read-only gh queries). Gold critical facts are extracted deterministically
from the text the model is shown. Items go to ~/.cache/z0-summ-faith/sets/items.jsonl (never committed);
only counts and sha256 values go to benchmarks/summarize_faithfulness/sets_manifest.json.

  python benchmarks/summarize_faithfulness/build_sets.py
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
WS = Path('~/workspace').expanduser()
CACHE = Path('~/.cache/z0-summ-faith').expanduser()
SETS, WORK = CACHE / 'sets', CACHE / 'build'
PY = ROOT / '.venv/bin/python'
SEED = 20261001
MAX_CHARS = 6000
QUOTA = {'pytest': 40, 'git': 40, 'build': 40, 'gh': 40}
TASK = ('Summarize this {what} output in at most 40 words for an engineer who has not seen it. Keep the key '
        'facts: whether it succeeded or failed, the counts, and the names of what failed or changed. '
        'Return only the summary.')
MAX_TOKENS = 120
GIT_REPOS = ['z0intelligence', 'z0', 'z0evals', 'evolution-lab', 'firstmate', 'kerdoios', 'o8', 'dash']
GH_REPOS = ['kvnloo/z0intelligence', 'kvnloo/z0', 'kvnloo/z0evals', 'kvnloo/evolution-lab', 'kvnloo/o8', 'kvnloo/dash']
PRIOR_SET = Path('~/.cache/z0-speed-offload/sets/summarize_tool_output.jsonl').expanduser()


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def sh(cmd, cwd=None, timeout=180, env=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    return p.returncode, (p.stdout + p.stderr)


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True).stdout


def render(cmd, out, code):
    return f'$ {cmd}\n{out.rstrip()}\n[exit code {code}]'


# --- facts --------------------------------------------------------------------------------------------------
# A fact is {'type': outcome|number|name, 'label', 'value' (outcome: fail|pass; number: int; name: [alternatives]),
# 'tier': critical|secondary}. A name fact is recalled when any alternative is recalled.

def outcome(v, tier='critical'):
    return {'type': 'outcome', 'label': 'outcome', 'value': v, 'tier': tier}


def number(label, n, tier='critical'):
    return {'type': 'number', 'label': label, 'value': int(n), 'tier': tier}


def name(label, alts, tier='critical'):
    alts = sorted({a for a in alts if a})
    return {'type': 'name', 'label': label, 'value': alts, 'tier': tier}


def file_alts(path):
    p = Path(path)
    return [path, p.name] + ([p.stem] if len(p.stem) >= 6 and not p.stem.startswith('__') else [])


def test_alts(nodeid):
    fn = re.sub(r'\[.*$', '', nodeid.split('::')[-1])
    return [nodeid, fn]


def names_facts(label, groups, tier='critical'):
    """<= 3 distinct items: each one is a fact; more: one fact satisfied by naming any of them."""
    groups = list({tuple(g): g for g in groups}.values())
    if not groups:
        return []
    if len(groups) <= 3:
        return [name(label, g, tier) for g in groups]
    return [name(label + '_any', [a for g in groups for a in g], tier)]


def item(kind, key, what, context, facts, meta):
    assert any(f['tier'] == 'critical' for f in facts), key
    return {'id': sha(f'{kind}:{key}')[:16], 'cls': 'summarize_tool_output', 'kind': kind,
            'task': TASK.format(what=what), 'context': context, 'facts': facts, 'max_tokens': MAX_TOKENS,
            'meta': meta}


# --- pytest on mutated copies of z0intelligence -------------------------------------------------------------

MUTATIONS = [(' == ', ' != '), (' != ', ' == '), (' < ', ' <= '), (' <= ', ' < '), (' > ', ' >= '),
             (' >= ', ' > '), ('True', 'False'), ('False', 'True'), (' + 1', ' + 2'), (' - 1', ' - 2'),
             (' and ', ' or '), (' or ', ' and '), ('return True', 'return False'), (' not ', ' ')]


def parse_pytest(out, code):
    facts = [outcome('fail' if code else 'pass')]
    tail = [l for l in out.splitlines() if re.match(r'^=*\s*\d+ (passed|failed|error)', l.strip('= ')) or
            re.search(r'\d+ (passed|failed|errors?) in [\d.]+s', l)]
    counts = {}
    if tail:
        for n, k in re.findall(r'(\d+) (failed|passed|skipped|errors?|xfailed|xpassed|deselected)', tail[-1]):
            counts['error' if k.startswith('error') else k] = int(n)
    failed = [m.group(1) for m in re.finditer(r'^FAILED (\S+)', out, re.M)]
    errored = [m.group(1) for m in re.finditer(r'^ERROR (\S+)', out, re.M)]
    if counts.get('failed'):
        facts.append(number('failed_count', counts['failed']))
    if counts.get('error'):
        facts.append(number('error_count', counts['error']))
    if not counts.get('failed') and not counts.get('error') and counts.get('passed'):
        facts.append(number('passed_count', counts['passed']))
    elif counts.get('passed'):
        facts.append(number('passed_count', counts['passed'], 'secondary'))
    facts += names_facts('failed_test', [test_alts(n) for n in failed])
    facts += names_facts('errored', [file_alts(n.split('::')[0]) if '::' not in n else test_alts(n) for n in errored])
    return facts, counts


def pytest_items(rng):
    wt = WORK / 'mutwt'
    if not wt.exists():
        WORK.mkdir(parents=True, exist_ok=True)
        subprocess.run(['git', '-C', str(ROOT), 'worktree', 'add', '--detach', str(wt), 'HEAD'], check=True,
                       capture_output=True)
    base = git(wt, 'rev-parse', 'HEAD').strip()
    tests = sorted((wt / 'tests').glob('test_*.py'))
    test_text = {t: t.read_text(errors='replace') for t in tests}
    mods = sorted(p for p in (wt / 'src/z0int').glob('*.py') if p.name != '__init__.py')
    users = {}
    for m in mods:
        pat = re.compile(rf'\b(z0int\.{m.stem}\b|from z0int import [^\n]*\b{m.stem}\b)')
        hit = [t for t in tests if pat.search(test_text[t])]
        if hit:
            users[m] = hit
    cands = sorted(users)
    want_fail, want_pass, want_err = 30, 7, 3
    out_items, tries = [], 0
    env = {'PYTHONPATH': str(wt / 'src'), 'PATH': '/usr/bin:/bin', 'HOME': str(Path.home())}
    while len(out_items) < QUOTA['pytest'] and tries < 400:
        tries += 1
        mod = rng.choice(cands)
        files = sorted(rng.sample(users[mod], min(len(users[mod]), rng.randint(1, 3))))
        src = mod.read_text()
        lines = src.splitlines(keepends=True)
        n_err = sum(1 for x in out_items if x['meta']['mutation'] == 'import_error')
        n_pass = sum(1 for x in out_items if x['meta']['exit'] == 0)
        n_fail = len(out_items) - n_pass - n_err
        if n_err < want_err and rng.random() < 0.08:
            mutated, mut = 'import z0int_missing_dependency  # noqa\n' + src, 'import_error'
        else:
            spots = [(i, a, b) for i, l in enumerate(lines) for a, b in MUTATIONS
                     if a in l and not l.lstrip().startswith(('#', '"', "'")) and 'def ' not in l]
            if not spots:
                continue
            i, a, b = rng.choice(spots)
            lines2 = list(lines)
            lines2[i] = lines[i].replace(a, b, 1)
            mutated, mut = ''.join(lines2), f'{mod.name}:{i + 1}:{a.strip()}->{b.strip()}'
        mod.write_text(mutated)
        try:
            rel = [str(f.relative_to(wt)) for f in files]
            for tb in ('short', 'line'):
                cmd = [str(PY), '-m', 'pytest', '-q', '-p', 'no:cacheprovider', f'--tb={tb}', *rel]
                try:
                    code, out = sh(cmd, cwd=wt, timeout=150, env=env)
                except subprocess.TimeoutExpired:
                    code, out = None, ''
                    break
                if len(out) <= MAX_CHARS - 200:
                    break
        finally:
            mod.write_text(src)
        if code is None or len(out) > MAX_CHARS - 200 or code not in (0, 1, 2):
            continue
        kind = 'import_error' if mut == 'import_error' else ('pass' if code == 0 else 'fail')
        if (kind == 'pass' and n_pass >= want_pass) or (kind == 'fail' and n_fail >= want_fail) or \
                (kind == 'import_error' and (n_err >= want_err or code == 0)):
            continue
        key = f'{base}:{mut}:{",".join(rel)}'
        if any(x['meta']['key'] == key for x in out_items):  # the same mutation drawn twice
            continue
        facts, counts = parse_pytest(out, code)
        if code and not any(f['label'] in ('failed_count', 'error_count') for f in facts):
            continue
        shown = 'python -m pytest -q --tb=' + tb + ' ' + ' '.join(rel)
        out_items.append(item('pytest', key, 'pytest', render(shown, out, code), facts,
                              {'mutation': mut, 'exit': code, 'counts': counts, 'base': base, 'key': key}))
        print('pytest', len(out_items), kind, mut, flush=True)
    return out_items


# --- git --------------------------------------------------------------------------------------------------

def shortstat_facts(text, names):
    m = re.search(r'(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?', text)
    facts = [number('files_changed', m.group(1))]
    if m.group(2):
        facts.append(number('insertions', m.group(2), 'secondary'))
    if m.group(3):
        facts.append(number('deletions', m.group(3), 'secondary'))
    groups = [file_alts(n) for n in names]
    facts += names_facts('changed_file', groups) if len(names) <= 2 else [name('changed_file_any', [a for g in groups for a in g])]
    return facts


def git_items(rng):
    prior = set()
    if PRIOR_SET.exists():
        prior = {json.loads(l)['id'] for l in PRIOR_SET.read_text().splitlines() if l.strip()}
    repos = [WS / r for r in GIT_REPOS if (WS / r / '.git').exists()]
    pools = {r: git(r, 'log', '--no-merges', '--format=%H', '-n', '1500').split() for r in repos}
    plan = ['show'] * 15 + ['range'] * 15 + ['patch'] * 10
    out, used = [], set()
    for mode in plan:
        for _ in range(200):
            r = rng.choice(repos)
            h = rng.choice(pools[r])
            if (r.name, h, mode) in used or sha(f'summarize_tool_output:{h}')[:16] in prior:
                continue
            if mode == 'range':
                k = rng.randint(2, 6)
                try:
                    a = git(r, 'rev-parse', f'{h}~{k}').strip()
                except subprocess.CalledProcessError:
                    continue
                spec, cmd = [a[:9] + '..' + h[:9]], f'git diff --stat=200 {a[:9]}..{h[:9]}'
                names = [n for n in git(r, 'diff', '--name-only', a, h).splitlines() if n.strip()]
                text = git(r, 'diff', '--stat=200', a, h)
            elif mode == 'show':
                names = [n for n in git(r, 'show', '--name-only', '--format=', h).splitlines() if n.strip()]
                text, cmd = git(r, 'show', '--stat=200', '--format=medium', h), f'git show --stat {h[:9]}'
            else:
                try:
                    names = [n for n in git(r, 'diff', '--name-only', f'{h}^', h).splitlines() if n.strip()]
                    text = git(r, 'diff', f'{h}^', h)
                except subprocess.CalledProcessError:
                    continue
                cmd = f'git diff {h[:9]}^ {h[:9]}'
            if not 1 <= len(names) <= 25 or len(text) > MAX_CHARS - 200 or len(text) < 80:
                continue
            if mode == 'patch':
                if len(names) > 4 or 'Binary files' in text:
                    continue
                facts = names_facts('changed_file', [file_alts(n) for n in names])
            else:
                m = re.search(r'(\d+) files? changed', text)
                if not m or int(m.group(1)) != len(names):
                    continue
                facts = shortstat_facts(text, names)
            used.add((r.name, h, mode))
            out.append(item('git', f'{r.name}:{mode}:{h}', {'patch': 'git diff', 'range': 'git diff --stat',
                                                            'show': 'git commit'}[mode],
                            render(cmd, text, 0), facts, {'repo': r.name, 'mode': mode, 'commit': h, 'exit': 0}))
            print('git', len(out), mode, r.name, h[:9], flush=True)
            break
    return out


# --- build / lint -----------------------------------------------------------------------------------------

RUFF_SETS = ['E,F,W', 'F', 'B', 'UP', 'SIM', 'E,W', 'F,B', 'PL', 'RET', 'C4', 'I', 'N']


def ruff_facts(out, code):
    facts = [outcome('fail' if code else 'pass')]
    m = re.search(r'Found (\d+) errors?', out)
    if m:
        facts.append(number('error_count', m.group(1)))
    files = sorted({m.group(1) for m in re.finditer(r'^(\S+?\.py):\d+:\d+: ', out, re.M)})
    facts += names_facts('file_with_error', [file_alts(f) for f in files])
    codes = sorted({m.group(1) for m in re.finditer(r'^\S+?\.py:\d+:\d+: ([A-Z]+\d+)', out, re.M)})
    if codes:
        facts.append(name('rule_code_any', codes, 'secondary'))
    return facts


def mypy_facts(out, code):
    facts = [outcome('fail' if code else 'pass')]
    m = re.search(r'Found (\d+) errors? in (\d+) files?', out)
    if m:
        facts += [number('error_count', m.group(1)), number('files_with_errors', m.group(2), 'secondary')]
    files = sorted({m.group(1) for m in re.finditer(r'^(\S+?\.pyi?):\d+: error', out, re.M)})
    facts += names_facts('file_with_error', [file_alts(f) for f in files])
    return facts


def add(out, x):
    """Append unless the same invocation was already drawn."""
    if all(y['id'] != x['id'] for y in out):
        out.append(x)


def build_items(rng):
    out = []
    py_repos = []
    seen = set()
    for p in sorted(WS.glob('*/pyproject.toml')):
        try:
            origin = git(p.parent, 'remote', 'get-url', 'origin').strip()
        except subprocess.CalledProcessError:
            continue
        # z0's own Python repos only: kvnloo origins, not forks of upstream projects
        if 'github.com/kvnloo/' not in origin or origin.endswith(('/hermes-agent.git',)):
            continue
        if origin not in seen and (p.parent / '.git').exists():
            seen.add(origin)
            py_repos.append(p.parent)
    rng.shuffle(py_repos)
    ruff = shutil.which('ruff', path=str(ROOT / '.venv/bin'))
    mypy = shutil.which('mypy', path=str(ROOT / '.venv/bin'))
    pyfiles = {r: [f for f in git(r, 'ls-files', '*.py').splitlines() if (r / f).is_file()] for r in py_repos}
    py_repos = [r for r in py_repos if pyfiles[r]]
    # ruff: 15 (<= 4 passing)
    n_pass = 0
    for _ in range(300):
        if sum(1 for x in out if x['meta']['tool'] == 'ruff') >= 15:
            break
        r = rng.choice(py_repos)
        target = rng.choice(pyfiles[r]) if rng.random() < 0.6 else str(Path(rng.choice(pyfiles[r])).parent)
        sel = rng.choice(RUFF_SETS)
        code, o = sh([ruff, 'check', '--no-cache', '--isolated', '--output-format=concise', '--select', sel, target], cwd=r)
        if code not in (0, 1) or len(o) > MAX_CHARS - 200 or (code == 0 and n_pass >= 4):
            continue
        n_pass += code == 0
        cmd = f'ruff check --output-format=concise --select {sel} {target}'
        add(out, item('build', f'ruff:{r.name}:{sel}:{target}', 'ruff', render(cmd, o, code), ruff_facts(o, code),
                        {'tool': 'ruff', 'repo': r.name, 'exit': code}))
        print('build ruff', len(out), code, flush=True)
    # mypy: 11
    n_pass = 0
    for _ in range(200):
        if sum(1 for x in out if x['meta']['tool'] == 'mypy') >= 11:
            break
        r = rng.choice(py_repos)
        f = rng.choice([x for x in pyfiles[r] if not x.startswith('tests/')] or pyfiles[r])
        try:
            code, o = sh([mypy, '--ignore-missing-imports', '--follow-imports=silent', '--show-error-codes',
                          '--cache-dir', str(WORK / 'mypy-cache'), f], cwd=r, timeout=170)
        except subprocess.TimeoutExpired:
            continue
        if code not in (0, 1) or len(o) > MAX_CHARS - 200 or (code == 0 and n_pass >= 3):
            continue
        n_pass += code == 0
        add(out, item('build', f'mypy:{r.name}:{f}', 'mypy', render(f'mypy --ignore-missing-imports {f}', o, code),
                        mypy_facts(o, code), {'tool': 'mypy', 'repo': r.name, 'exit': code}))
        print('build mypy', len(out), code, flush=True)
    # compileall on a copy with an injected syntax error: 8
    for _ in range(200):
        if sum(1 for x in out if x['meta']['tool'] == 'compileall') >= 8:
            break
        r = rng.choice(py_repos)
        f = rng.choice(pyfiles[r])
        lines = (r / f).read_text(errors='replace').splitlines(keepends=True)
        cand = [i for i, l in enumerate(lines) if re.search(r'\)\s*$', l) and not l.lstrip().startswith('#')]
        if len(lines) < 20 or not cand:
            continue
        i = rng.choice(cand)
        lines[i] = re.sub(r'\)(\s*)$', r'\1', lines[i], count=1)
        d = WORK / 'compile' / r.name
        if d.exists():
            shutil.rmtree(d)
        (d / f).parent.mkdir(parents=True, exist_ok=True)
        (d / f).write_text(''.join(lines))
        code, o = sh([str(PY), '-m', 'compileall', '-q', str(Path(f).parts[0])], cwd=d)
        shutil.rmtree(d)
        if code == 0 or len(o) > MAX_CHARS - 200 or 'Error' not in o:
            continue
        m = re.search(r'line (\d+)', o)
        facts = [outcome('fail'), name('file_with_error', file_alts(f))]
        if m:
            facts.append(number('error_line', m.group(1), 'secondary'))
        et = re.search(r'^(\w*Error): ', o, re.M)
        if et:
            facts.append(name('error_type', [et.group(1)], 'secondary'))
        add(out, item('build', f'compileall:{r.name}:{f}:{i}', 'python -m compileall',
                        render(f'python -m compileall -q {Path(f).parts[0]}', o, code), facts,
                        {'tool': 'compileall', 'repo': r.name, 'exit': code}))
        print('build compileall', len(out), flush=True)
    # uv build: one per distinct repo (6 available; pass or fail as they are)
    for r in py_repos:
        if sum(1 for x in out if x['meta']['tool'] == 'uv-build') >= 6:
            break
        dist = WORK / 'dist' / r.name
        shutil.rmtree(dist, ignore_errors=True)
        try:
            code, o = sh(['uv', 'build', '--no-progress', '--color', 'never', '-o', str(dist)], cwd=r, timeout=300)
        except subprocess.TimeoutExpired:
            continue
        shutil.rmtree(dist, ignore_errors=True)
        o = o.replace(str(dist), 'dist')
        if len(o) > MAX_CHARS - 200:
            o = '\n'.join(o.splitlines()[-60:])
            if len(o) > MAX_CHARS - 200:
                continue
        facts = [outcome('fail' if code else 'pass')]
        arts = re.findall(r'Successfully built (\S+?)(?: and (\S+))?$', o, re.M)
        names = [a for t in arts for a in t if a]
        if names:
            facts += [name('artifact_any', [a for n in names for a in (n, Path(n).name)])]
        add(out, item('build', f'uvbuild:{r.name}:{git(r, "rev-parse", "HEAD").strip()}', 'uv build',
                        render('uv build', o, code), facts, {'tool': 'uv-build', 'repo': r.name, 'exit': code}))
        print('build uv', len(out), code, flush=True)
    return out


# --- gh ---------------------------------------------------------------------------------------------------

def ghj(*args):
    code, o = sh(['gh', *args], timeout=120)
    return json.loads(o) if code == 0 else None


def gh_items(rng):
    out = []
    failed = {}
    for repo in GH_REPOS:
        rows = ghj('run', 'list', '-R', repo, '-L', '200', '--status', 'failure', '--json', 'databaseId,workflowName,createdAt') or []
        failed[repo] = sorted(r['databaseId'] for r in rows)
    pool = [(repo, i) for repo, ids in failed.items() for i in ids]
    rng.shuffle(pool)
    # 16 x log window around the first ##[error]
    k = 0
    for repo, rid in pool:
        if k >= 16:
            break
        code, o = sh(['gh', 'run', 'view', str(rid), '-R', repo, '--log-failed'], timeout=120)
        lines = o.splitlines()
        err = next((i for i, l in enumerate(lines) if '##[error]' in l), None)
        if code != 0 or err is None:
            continue
        win = [l[:300] for l in lines[max(0, err - 40):err + 4]]
        text = '\n'.join(win)
        while len(text) > MAX_CHARS - 300 and len(win) > 10:
            win = win[1:]
            text = '\n'.join(win)
        facts = [outcome('fail')]
        m = re.search(r'Process completed with exit code (\d+)', text)
        if m:
            facts.append(number('exit_code', m.group(1)))
        jobs = sorted({l.split('\t')[0] for l in win if '\t' in l and '##[error]' in l})
        facts += names_facts('failed_job', [[j] for j in jobs])
        tests = sorted({m.group(1) for m in re.finditer(r'FAILED (\S+::\S+)', text)})
        facts += names_facts('failed_test', [test_alts(t) for t in tests])
        cmd = f'gh run view {rid} -R {repo} --log-failed  (window around the first error)'
        out.append(item('gh', f'logfailed:{repo}:{rid}', 'GitHub Actions log', render(cmd, text, 0), facts,
                        {'gh': 'log-failed', 'repo': repo, 'run': rid, 'exit': 0}))
        k += 1
        print('gh log', len(out), flush=True)
    # 8 x run view summary
    k = 0
    for repo, rid in pool[::-1]:
        if k >= 8:
            break
        code, o = sh(['gh', 'run', 'view', str(rid), '-R', repo], timeout=120)
        if code != 0 or len(o) > MAX_CHARS - 200 or any(x['meta'].get('run') == rid for x in out):
            continue
        facts = [outcome('fail')]
        steps = [m.group(1).strip() for m in re.finditer(r'^\s{2}X (.+)$', o, re.M)]
        jobs = [re.sub(r' in \S+ \(ID \d+\)$', '', m.group(1)).strip() for m in re.finditer(r'^X (.+ \(ID \d+\))$', o, re.M)]
        facts += names_facts('failed_step', [[s] for s in steps]) or names_facts('failed_job', [[j] for j in jobs])
        m = re.search(r'Process completed with exit code (\d+)', o)
        if m:
            facts.append(number('exit_code', m.group(1)))
        out.append(item('gh', f'runview:{repo}:{rid}', 'gh run view', render(f'gh run view {rid} -R {repo}', o, 0),
                        facts, {'gh': 'run-view', 'repo': repo, 'run': rid, 'exit': 0}))
        k += 1
        print('gh view', len(out), flush=True)
    # 8 x run list (mixed outcomes)
    k = 0
    for repo in GH_REPOS * 3:
        if k >= 8:
            break
        lim = rng.choice([8, 10, 12, 15])
        rows = ghj('run', 'list', '-R', repo, '-L', '200', '--json', 'databaseId') or []
        if len(rows) < lim + 5:
            continue
        start = rng.randint(0, len(rows) - lim)
        # gh has no offset flag: list start+lim rows and show the last lim of them (the command is rendered that way).
        code, o = sh(['gh', 'run', 'list', '-R', repo, '-L', str(start + lim)], timeout=120)
        page = o.splitlines()[start:start + lim]
        if code != 0 or len(page) < lim:
            continue
        text = '\n'.join(page)
        fails = [l.split('\t') for l in page if l.split('\t')[1:2] == ['failure']]
        if any(x['meta'].get('page') == (repo, start, lim) for x in out) or len(text) > MAX_CHARS - 200:
            continue
        facts = [outcome('fail' if fails else 'pass')]
        facts.append(number('failed_runs', len(fails)) if fails else number('runs_listed', lim))
        if fails:
            facts += names_facts('failed_workflow', [[f[3]] for f in fails if len(f) > 3])
        cmd = f'gh run list -R {repo} -L {start + lim} | sed -n {start + 1},{start + lim}p'
        out.append(item('gh', f'runlist:{repo}:{start}:{lim}', 'gh run list', render(cmd, text, 0), facts,
                        {'gh': 'run-list', 'repo': repo, 'page': (repo, start, lim), 'exit': 0}))
        k += 1
        print('gh list', len(out), flush=True)
    # 8 x pr checks / pr view on z0intelligence
    prs = ghj('pr', 'list', '-R', 'kvnloo/z0intelligence', '--state', 'all', '-L', '80', '--json', 'number') or []
    nums = sorted(p['number'] for p in prs)
    rng.shuffle(nums)
    kc = kv = 0
    for n in nums:
        if kc >= 4 and kv >= 4:
            break
        if kc < 4:
            code, o = sh(['gh', 'pr', 'checks', str(n), '-R', 'kvnloo/z0intelligence'], timeout=120)
            rows = [l.split('\t') for l in o.splitlines() if '\t' in l]
            if rows and len(o) <= MAX_CHARS - 200:
                bad = [r[0] for r in rows if r[1] in ('fail', 'cancel')]
                facts = [outcome('fail' if bad else 'pass')]
                facts += names_facts('failed_check', [[b] for b in bad]) if bad else [number('checks_passed', sum(r[1] == 'pass' for r in rows))]
                out.append(item('gh', f'prchecks:{n}', 'gh pr checks', render(f'gh pr checks {n}', o, code), facts,
                                {'gh': 'pr-checks', 'pr': n, 'exit': code}))
                kc += 1
                print('gh checks', len(out), flush=True)
                continue
        if kv < 4:
            code, o = sh(['gh', 'pr', 'view', str(n), '-R', 'kvnloo/z0intelligence'], timeout=120)
            if code != 0:
                continue
            o = o if len(o) <= MAX_CHARS - 200 else o[:MAX_CHARS - 300] + '\n[... truncated]'
            st = re.search(r'^state:\s*(\w+)', o, re.M)
            if not st:
                continue
            facts = [name('pr_state', [st.group(1), st.group(1).lower()]), number('pr_number', n)]
            out.append(item('gh', f'prview:{n}', 'gh pr view', render(f'gh pr view {n}', o, code), facts,
                            {'gh': 'pr-view', 'pr': n, 'exit': code}))
            kv += 1
            print('gh prview', len(out), flush=True)
    return out


def main():
    from z0int.speed_offload import classify_task
    reg = json.loads((ROOT / 'manifests/task_classes.v0.json').read_text())
    SETS.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    allitems = []
    for kind, fn in [('pytest', pytest_items), ('git', git_items), ('build', build_items), ('gh', gh_items)]:
        part = CACHE / 'sets' / f'part-{kind}.jsonl'
        if part.exists():
            items = [json.loads(l) for l in part.read_text().splitlines() if l.strip()]
        else:
            items = fn(random.Random(f'{SEED}:{kind}'))
            part.write_text(''.join(json.dumps(x, sort_keys=True) + '\n' for x in items))
        allitems += items
    for x in allitems:
        assert classify_task(x['task'], reg)[0] == 'summarize_tool_output', x['task']
    ids = [x['id'] for x in allitems]
    assert len(ids) == len(set(ids))
    body = ''.join(json.dumps(x, sort_keys=True) + '\n' for x in allitems)
    (SETS / 'items.jsonl').write_text(body)
    by = {}
    for x in allitems:
        k = x['kind']
        by.setdefault(k, {'n': 0, 'nonzero_or_failing': 0, 'critical_facts': 0})
        by[k]['n'] += 1
        by[k]['nonzero_or_failing'] += any(f['type'] == 'outcome' and f['value'] == 'fail' for f in x['facts'])
        by[k]['critical_facts'] += sum(f['tier'] == 'critical' for f in x['facts'])
    manifest = {'schema': 'z0int.summarize_faithfulness.sets.v0', 'seed': SEED, 'n': len(allitems),
                'sha256': sha(body), 'task_template_sha256': sha(TASK), 'max_context_chars': max(len(x['context']) for x in allitems),
                'by_kind': by, 'built_from_commit': git(ROOT, 'rev-parse', 'HEAD').strip(),
                'item_ids_sha256': sha('\n'.join(sorted(ids)))}
    (HERE / 'sets_manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    print(json.dumps(manifest, indent=1))


if __name__ == '__main__':
    main()
