"""claude-code-savings-v1 runner: stock / lean / lean+packet / lean+packet+obspack, real `claude -p`.

Pre-registered in PREREG.md (committed before any measured run). Two suites:

  qa    the v0 pinned held-out current-work set (benchmarks/state_packet/questions_pinned.json),
        run in place in the pinned fixture repos with read-only tools, scored against the frozen key.
  repo  fresh repo tasks (tasks/<id>/task.json) on pinned sources from several z0 repos; each
        (task, arm) gets one persistent directory: rep 0 is a cold session, later reps reset the
        tree to the exact fixture commit and run warm (Claude Code's prefix cache is keyed by cwd
        + startup git snapshot). Verified by check.py, never by the model's own claim.

Every arm disables the user-installed z0 plugins (enabledPlugins=false) so z0 enters only via
--plugin-dir. Sessions are not persisted (no cross-rep transcript leakage into the packet).
Raw JSONL is create-only and kept OUT of git (session ids, local paths, model text).

    .venv/bin/python benchmarks/claude_code_v1/run.py validate
    .venv/bin/python benchmarks/claude_code_v1/run.py run --suite repo --reps 3 --jobs 4 --out ~/.z0int/research/claude-code-savings-v1/repo.jsonl
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import check  # noqa: E402

PY = ROOT / '.venv' / 'bin' / 'python'
PLUGIN = ROOT / 'harness-adapters' / 'claude-code-z0intelligence'
OBSPACK = ROOT / 'harness-adapters' / 'claude-code-z0-obspack'
LEAN = ['--setting-sources', 'project', '--strict-mcp-config', '--disable-slash-commands']
NO_INSTALLED = json.dumps({'enabledPlugins': {'z0intelligence@z0intelligence': False,
                                              'z0-obspack@z0intelligence': False}})
ARMS = {
    'stock': ([], {}),
    'lean': (LEAN, {}),
    'lean+packet': (LEAN + ['--plugin-dir', str(PLUGIN)], {'Z0INT_CLAUDE_CODE_PACKET': '1'}),
    'lean+packet+obspack': (LEAN + ['--plugin-dir', str(PLUGIN), '--plugin-dir', str(OBSPACK)],
                            {'Z0INT_CLAUDE_CODE_PACKET': '1'}),
}
SCRATCH = Path(os.environ.get('Z0_BENCH_SCRATCH', '~/.cache/z0-savings-v1')).expanduser()
PINNED = Path(os.environ.get('Z0_BENCH_PINNED', '~/.z0int/research/claude-code-overnight/pinned')).expanduser()
FIXED_GIT = {'GIT_AUTHOR_NAME': 'bench', 'GIT_AUTHOR_EMAIL': 'bench@local', 'GIT_COMMITTER_NAME': 'bench',
             'GIT_COMMITTER_EMAIL': 'bench@local', 'GIT_AUTHOR_DATE': '2026-09-30T12:00:00Z',
             'GIT_COMMITTER_DATE': '2026-09-30T12:00:00Z'}


def _sp():
    spec = importlib.util.spec_from_file_location('sp_run', ROOT / 'benchmarks' / 'state_packet' / 'run.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SP = _sp()


# ---------------------------------------------------------------- tasks
def load_repo_tasks(only=None):
    tasks = []
    for f in sorted((HERE / 'tasks').glob('*/task.json')):
        t = json.loads(f.read_text())
        t['_dir'] = str(f.parent)
        t['suite'] = 'repo'
        if only and t['id'] not in only:
            continue
        tasks.append(t)
    return tasks


def load_qa_tasks(only=None):
    spec = json.loads((ROOT / 'benchmarks' / 'state_packet' / 'questions_pinned.json').read_text())
    out = []
    for q in spec['questions']:
        if only and q['id'] not in only:
            continue
        out.append({**q, 'suite': 'qa', 'family': 'qa-' + q['kind'], '_contract': spec['answer_contract'],
                    'effort': 'low'})
    return out


def sabotages(task):
    s = task.get('sabotage') or []
    return [s] if isinstance(s, dict) else s


def fixture(task, work: Path, sabotage=True, oracle=False):
    work.mkdir(parents=True, exist_ok=True)
    src = task['source']
    repo = check.source_repo(src['repo'])
    # optional source.prefix: extract under work/<prefix>/ (for repos whose root is itself a package)
    pre = [f"--prefix={src['prefix'].strip('/')}/"] if src.get('prefix') else []
    archive = subprocess.run(['git', '-C', str(repo), 'archive', *pre, src['sha'], *src.get('paths', [])],
                             capture_output=True, check=True).stdout
    subprocess.run(['tar', '-x', '-C', str(work)], input=archive, check=True)
    for rel in task.get('remove', []):  # e.g. hide the reference implementation's own test
        p = work / rel
        shutil.rmtree(p) if p.is_dir() else p.unlink(missing_ok=True)
    if sabotage:
        for sab in sabotages(task):
            f = work / sab['file']
            text = f.read_text()
            assert text.count(sab['old']) == 1, f"{task['id']}: sabotage anchor must be unique in {sab['file']}"
            f.write_text(text.replace(sab['old'], sab['new']))
    if oracle and task.get('oracle_patch'):
        subprocess.run(['git', 'apply', str(Path(task['_dir']) / task['oracle_patch'])], check=True, cwd=work)
    if oracle:
        for rel, body in (task.get('oracle_files') or {}).items():
            (work / rel).write_text(body if isinstance(body, str) else json.dumps(body))
    if oracle and task['check'].get('kind', '').startswith('answer'):
        c = task['check']
        body = c['expect'] if c['kind'] == 'answer_text' else json.dumps(c['expect'])
        (work / c['file']).write_text(body if isinstance(body, str) else json.dumps(body))
    env = {**os.environ, **FIXED_GIT}
    subprocess.run(['git', 'init', '-q', str(work)], check=True)
    subprocess.run(['git', '-C', str(work), 'add', '-A'], check=True, env=env)
    subprocess.run(['git', '-C', str(work), 'commit', '-qm', 'fixture', '--no-gpg-sign'], check=True, env=env)


def validate(args):
    """Oracle pass + as-given fail for every repo task (run before any measured trial)."""
    base = SCRATCH / 'validate' / time.strftime('%H%M%S')
    bad = 0
    for t in load_repo_tasks(args.only):
        res = {}
        # given = what the model sees; oracle = pristine source (bugfix) + oracle_patch (feature) / key (answer)
        for label, sab, orc in (('given', True, False), ('oracle', False, True)):
            w = base / f"{t['id']}-{label}"
            fixture(t, w, sabotage=sab, oracle=orc)
            ok, why = check.verify(t, w, env=bench_env())
            res[label] = (ok, why)
        good = res['oracle'][0] and not res['given'][0]
        bad += not good
        print(('OK  ' if good else 'BAD ') + t['id'], '| given', res['given'][0], '| oracle', res['oracle'][0],
              '' if good else ('\n   given: ' + res['given'][1][-300:] + '\n   oracle: ' + res['oracle'][1][-300:]))
    shutil.rmtree(base, ignore_errors=True)
    return bad


# ---------------------------------------------------------------- one trial
def bench_env():
    tmp = SCRATCH / 'tmp'
    tmp.mkdir(parents=True, exist_ok=True)
    return {**os.environ, 'TMPDIR': str(tmp), 'PATH': str(ROOT / '.venv' / 'bin') + os.pathsep + os.environ['PATH'],
            'GIT_OPTIONAL_LOCKS': '0'}


def parse_stream(stdout: str) -> dict:
    tool_calls, tool_bytes, packs, packet_bytes, result, init = {}, 0, 0, 0, {}, {}
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        t = ev.get('type')
        if t == 'assistant':
            for c in (ev.get('message') or {}).get('content') or []:
                if isinstance(c, dict) and c.get('type') == 'tool_use':
                    tool_calls[c['name']] = tool_calls.get(c['name'], 0) + 1
        elif t == 'user':
            for c in (ev.get('message') or {}).get('content') or []:
                if isinstance(c, dict) and c.get('type') == 'tool_result':
                    content = c.get('content')
                    s = content if isinstance(content, str) else json.dumps(content)
                    tool_bytes += len(s)
                    packs += s.count('[z0 ObservationPack]')
        elif t == 'system' and ev.get('subtype') == 'init':
            init = {'n_tools': len(ev.get('tools') or []), 'mcp_servers': [m.get('name') for m in ev.get('mcp_servers') or []],
                    'plugins': [p.get('name') for p in ev.get('plugins') or []], 'claude_code_version': ev.get('claude_code_version')}
        elif t == 'system' and 'hook' in str(ev.get('subtype', '')):
            out = json.dumps(ev)
            if 'z0-state-packet' in out:
                packet_bytes = max(packet_bytes, len(ev.get('output') or ''))
        elif t == 'result':
            result = ev
    return {'tool_calls': tool_calls, 'tool_result_bytes': tool_bytes, 'obspack_packs': packs,
            'packet_hook_bytes': packet_bytes, 'result': result, 'init': init}


def billed(result: dict) -> dict:
    mu = result.get('modelUsage') or {}
    if mu:
        s = lambda k: sum(int(v.get(k) or 0) for v in mu.values())  # noqa: E731
        inp, out, cr, cc = s('inputTokens'), s('outputTokens'), s('cacheReadInputTokens'), s('cacheCreationInputTokens')
        src = 'modelUsage'
    else:
        u = result.get('usage') or {}
        inp, out, cr, cc = (int(u.get(k) or 0) for k in ('input_tokens', 'output_tokens', 'cache_read_input_tokens',
                                                          'cache_creation_input_tokens'))
        src = 'usage'
    return {'billed_total': inp + out + cr + cc, 'input': inp, 'output': out, 'cache_read': cr,
            'cache_creation': cc, 'source': src}


def claude_cmd(task, arm, prompt, args):
    flags, _ = ARMS[arm]
    cmd = ['claude', '-p', prompt, '--settings', NO_INSTALLED, '--output-format', 'stream-json', '--verbose',
           '--model', args.model, '--effort', task.get('effort', 'low'), '--no-session-persistence',
           '--max-budget-usd', str(args.max_usd), *flags]
    if task['suite'] == 'qa':
        cmd += ['--allowedTools', *SP.READ_ONLY_TOOLS, '--add-dir', str(PINNED / 'projects'),
                '--append-system-prompt', SP.history_hint()]
    else:
        cmd += ['--permission-mode', 'bypassPermissions']
    return cmd


def run_unit(task, arm, reps, args, emit):
    unit = f"{task['id']}__{arm.replace('+', '-')}"
    home = SCRATCH / 'z0home' / args.tag / unit
    home.mkdir(parents=True, exist_ok=True)
    empty_projects = SCRATCH / 'empty-projects'
    empty_projects.mkdir(parents=True, exist_ok=True)
    env = {**bench_env(), **ARMS[arm][1], 'Z0INT_HOME': str(home), 'Z0INT_PYTHON': str(PY),
           'Z0INT_CLAUDE_PROJECTS': str(PINNED / 'projects' if task['suite'] == 'qa' else empty_projects)}
    if task['suite'] == 'qa':
        SP.PROJECTS = PINNED / 'projects'
        work = PINNED / task['repo']
        prompt = f"{task['prompt']}\n\nFields to fill: {json.dumps(task['fields'])}\n\n{task['_contract']}"
    else:
        work = SCRATCH / 'work' / args.tag / unit
        prompt = task['prompt']
    for rep in reps:
        if task['suite'] == 'repo':
            if not (work / '.git').exists():
                fixture(task, work)
            else:
                subprocess.run(['git', '-C', str(work), 'reset', '-q', '--hard'], check=True)
                subprocess.run(['git', '-C', str(work), 'clean', '-qfdx'], check=True)
        cmd = claude_cmd(task, arm, prompt, args)
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, timeout=args.timeout,
                                  stdin=subprocess.DEVNULL)
            stdout, rc, err = proc.stdout, proc.returncode, proc.stderr[-400:]
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or '')
            rc, err = 'timeout', ''
        wall = time.time() - t0
        ps = parse_stream(stdout)
        res = ps.pop('result')
        if task['suite'] == 'qa':
            ans = SP.parse_answer(res.get('result'))
            sc = SP.score(task, ans)
            verified, note = sc['correct'], json.dumps({'action': sc.get('action'), 'fields': sc.get('fields')})
        else:
            try:
                verified, note = check.verify(task, work, env=env)
            except Exception as exc:  # noqa: BLE001
                verified, note = False, f'check error {exc!r}'
        emit({'type': 'trial', 'study': 'claude-code-savings-v1', 'tag': args.tag, 'suite': task['suite'],
              'task': task['id'], 'repo': task.get('repo') or task['source']['repo'], 'family': task.get('family'),
              'arm': arm, 'rep': rep, 'warm': task['suite'] == 'repo' and rep > 0, 'model': args.model,
              'effort': task.get('effort', 'low'), 'verified': bool(verified), 'check_note': note[-300:],
              'wall_s': round(wall, 2), 'returncode': rc, 'stderr_tail': err if rc else '',
              'cost_usd': res.get('total_cost_usd'), 'tokens': billed(res), 'usage': res.get('usage'),
              'model_usage': res.get('modelUsage'), 'num_turns': res.get('num_turns'),
              'is_error': res.get('is_error'), 'subtype': res.get('subtype'), 'session_id': res.get('session_id'),
              'has_result': bool(res), 'result_text': (res.get('result') or '')[:2000], **ps,
              'z0int_revision': subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'],
                                               capture_output=True, text=True).stdout.strip(),
              'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())})


def run(args):
    out = Path(args.out).expanduser()
    if out.exists():
        sys.exit('output exists (create-only)')
    out.parent.mkdir(parents=True, exist_ok=True)
    tasks = load_qa_tasks(args.only) if args.suite == 'qa' else load_repo_tasks(args.only)
    arms = args.arms
    rng = random.Random(args.seed)
    order = tasks[:]
    rng.shuffle(order)
    # Units = (task, arm); reps run sequentially inside a unit (warm reuse). Arm order rotates per task
    # (Latin-square) so each arm is equally often first/last among a task's concurrently-started units.
    units = []
    for i, t in enumerate(order):
        rot = arms[i % len(arms):] + arms[:i % len(arms)]
        units += [(t, a) for a in rot]
    lock = threading.Lock()
    with out.open('x') as fh:
        fh.write(json.dumps({'type': 'meta', 'args': vars(args), 'arms': {a: ARMS[a][0] for a in arms},
                             'z0int_revision': subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                                                              capture_output=True, text=True).stdout.strip(),
                             'claude_version': subprocess.run(['claude', '--version'], capture_output=True,
                                                              text=True).stdout.strip(),
                             'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}) + '\n')

        def emit(row):
            with lock:
                fh.write(json.dumps(row) + '\n')
                fh.flush()
                tk = row['tokens']
                print(f"{row['task']:28} {row['arm']:20} r{row['rep']} ok={int(row['verified'])} "
                      f"tok={tk['billed_total']:>8} ${row['cost_usd'] or 0:.3f} {row['wall_s']:.0f}s", flush=True)

        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futs = [pool.submit(run_unit, t, a, list(range(args.reps)), args, emit) for t, a in units]
            for f in futs:
                f.result()
        fh.write(json.dumps({'type': 'meta_end', 'ended_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}) + '\n')


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    v = sub.add_parser('validate')
    v.add_argument('--only', nargs='*')
    r = sub.add_parser('run')
    r.add_argument('--suite', choices=['qa', 'repo'], required=True)
    r.add_argument('--arms', nargs='+', default=list(ARMS))
    r.add_argument('--only', nargs='*')
    r.add_argument('--reps', type=int, default=3)
    r.add_argument('--jobs', type=int, default=4)
    r.add_argument('--model', default='sonnet')
    r.add_argument('--max-usd', type=float, default=2.0)
    r.add_argument('--timeout', type=int, default=900)
    r.add_argument('--seed', type=int, default=20260930)
    r.add_argument('--tag', default='main')
    r.add_argument('--out', required=True)
    args = ap.parse_args()
    if args.cmd == 'validate':
        sys.exit(1 if validate(args) else 0)
    run(args)


if __name__ == '__main__':
    main()
