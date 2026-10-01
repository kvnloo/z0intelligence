"""obspack-long-v1 runner: does ObservationPack cut the cost of LONG, output-heavy Claude Code sessions?

Pre-registered in PREREG.md (committed before any measured run). Arms: lean vs lean+obspack (primary),
stock vs stock+obspack (secondary). Derived from benchmarks/claude_code_v1/run.py (same isolation:
user-installed z0 plugins disabled via enabledPlugins=false, z0 enters only via --plugin-dir; sessions
not persisted; one persistent dir per (task, arm), rep 0 cold, later reps reset to the fixture commit
and run warm). Verified by check.py, never by the model's claim. Raw JSONL stays out of git.

    .venv/bin/python benchmarks/obspack_long_v1/run.py validate
    .venv/bin/python benchmarks/obspack_long_v1/run.py run --arms lean lean+obspack --reps 3 --jobs 3 --out ~/.cache/z0-obspack-long/raw/main.jsonl
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import random
import re
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
OBS = ['--plugin-dir', str(ROOT / 'harness-adapters' / 'claude-code-z0-obspack')]
NO_INSTALLED = json.dumps({'enabledPlugins': {'z0intelligence@z0intelligence': False,
                                              'z0-obspack@z0intelligence': False}})
ARMS = {
    'lean': (LEAN, {}),
    'lean+obspack': (LEAN + OBS, {}),
    'stock': ([], {}),
    'stock+obspack': (OBS, {}),
}
SCRATCH = Path(os.environ.get('Z0_BENCH_SCRATCH', '~/.cache/z0-obspack-long')).expanduser()
FIXED_GIT = {'GIT_AUTHOR_NAME': 'bench', 'GIT_AUTHOR_EMAIL': 'bench@local', 'GIT_COMMITTER_NAME': 'bench',
             'GIT_COMMITTER_EMAIL': 'bench@local', 'GIT_AUTHOR_DATE': '2026-09-30T12:00:00Z',
             'GIT_COMMITTER_DATE': '2026-09-30T12:00:00Z'}


# ---------------------------------------------------------------- tasks
def load_repo_tasks(only=None):
    tasks = []
    for f in sorted((HERE / 'tasks').glob('*/task.json')):
        t = json.loads(f.read_text())
        t['_dir'] = str(f.parent)
        if only and t['id'] not in only:
            continue
        tasks.append(t)
    return tasks


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
    if task.get('generate'):  # deterministic data generator kept in the task dir (never copied into work)
        subprocess.run([sys.executable, str(Path(task['_dir']) / task['generate']), str(work)], check=True)
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
    tool_calls, tool_bytes, packs, packet_bytes, result, init, recalls, bash_cmds = {}, 0, 0, 0, {}, {}, 0, []
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
                    if c['name'] == 'Bash':
                        bash_cmds.append(str((c.get('input') or {}).get('command', ''))[:300])
                        recalls += 'z0obs' in bash_cmds[-1]
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
    return {'tool_calls': tool_calls, 'n_tool_calls': sum(tool_calls.values()), 'tool_result_bytes': tool_bytes,
            'obspack_packs': packs, 'z0obs_recalls': recalls, 'bash_cmds': bash_cmds, 'result': result, 'init': init}


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
    return ['claude', '-p', prompt, '--settings', NO_INSTALLED, '--output-format', 'stream-json', '--verbose',
            '--model', args.model, '--effort', task.get('effort', 'medium'), '--no-session-persistence',
            '--max-budget-usd', str(args.max_usd), '--permission-mode', 'bypassPermissions', *flags]


def run_unit(task, arm, reps, args, emit):
    unit = f"{task['id']}__{arm.replace('+', '-')}"
    home = SCRATCH / 'z0home' / args.tag / unit
    home.mkdir(parents=True, exist_ok=True)
    env = {**bench_env(), **ARMS[arm][1], 'Z0INT_HOME': str(home), 'Z0INT_PYTHON': str(PY)}
    work = SCRATCH / 'work' / args.tag / unit
    for rep in reps:
        if (task['id'], arm, rep) in args.done:
            continue
        for attempt in range(40):
            row = run_trial(task, arm, rep, work, env, args)
            if not row['rate_limited']:
                emit(row)
                break
            # Quota/session limit: not a trial. Record it, wait for the reset, retry the same rep.
            emit({'type': 'rate_limit', 'task': task['id'], 'arm': arm, 'rep': rep, 'attempt': attempt,
                  'message': row['result_text'][:200], 'ts': row['ts']})
            wait_for_reset(row['result_text'])


LIMIT_RX = re.compile(r"hit your (session|usage|weekly) limit|usage limit|rate limit|limit reached", re.I)
_limit_lock = threading.Lock()


def wait_for_reset(text):
    """Sleep until the reset time Claude Code reports (e.g. 'resets 10:10pm'), +2 min; else 15 min."""
    with _limit_lock:
        m = re.search(r'resets (\d{1,2})(?::(\d{2}))?\s*(am|pm)', text, re.I)
        secs = 900
        if m:
            h, mi, ap = int(m.group(1)) % 12, int(m.group(2) or 0), m.group(3).lower()
            h += 12 if ap == 'pm' else 0
            now = time.localtime()
            target = time.mktime((now.tm_year, now.tm_mon, now.tm_mday, h, mi, 0, 0, 0, -1))
            if target < time.time() - 6 * 3600:  # e.g. 'resets 1am' seen at 11pm
                target += 86400
            secs = max(60, target - time.time() + 120)  # already past (another worker waited it out): 1 min
        print(f'rate limit: sleeping {secs / 60:.0f} min', flush=True)
        time.sleep(secs)


def run_trial(task, arm, rep, work, env, args):
    if not (work / '.git').exists():
        fixture(task, work)
    else:
        subprocess.run(['git', '-C', str(work), 'reset', '-q', '--hard'], check=True)
        subprocess.run(['git', '-C', str(work), 'clean', '-qfdx'], check=True)
    while _limit_lock.locked():  # another worker is waiting out a limit
        time.sleep(10)
    cmd = claude_cmd(task, arm, task['prompt'], args)
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
    try:
        verified, note = check.verify(task, work, env=env)
    except Exception as exc:  # noqa: BLE001
        verified, note = False, f'check error {exc!r}'
    tokens = billed(res)
    limited = bool(LIMIT_RX.search((res.get('result') or '') + err)) and tokens['billed_total'] == 0
    return {'type': 'trial', 'study': 'obspack-long-v1', 'tag': args.tag, 'task': task['id'],
            'repo': task['source']['repo'], 'family': task.get('family'), 'arm': arm, 'rep': rep, 'warm': rep > 0,
            'model': args.model, 'effort': task.get('effort', 'medium'), 'verified': bool(verified),
            'check_note': note[-300:], 'wall_s': round(wall, 2), 'returncode': rc, 'stderr_tail': err if rc else '',
            'rate_limited': limited, 'cost_usd': res.get('total_cost_usd'), 'tokens': tokens,
            'usage': res.get('usage'), 'model_usage': res.get('modelUsage'), 'num_turns': res.get('num_turns'),
            'is_error': res.get('is_error'), 'subtype': res.get('subtype'), 'session_id': res.get('session_id'),
            'has_result': bool(res) and not limited, 'result_text': (res.get('result') or '')[:2000], **ps,
            'z0int_revision': subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'],
                                             capture_output=True, text=True).stdout.strip(),
            'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}


def run(args):
    out = Path(args.out).expanduser()
    if out.exists():
        sys.exit('output exists (create-only)')
    out.parent.mkdir(parents=True, exist_ok=True)
    # --skip: cells (task, arm, rep) that already have a real result in earlier output files are not rerun
    args.done = set()
    for p in args.skip or []:
        for line in Path(p).expanduser().read_text().splitlines():
            r = json.loads(line)
            if r.get('type') == 'trial' and r['has_result'] and r['tokens']['billed_total'] > 0:
                args.done.add((r['task'], r['arm'], r['rep']))
    tasks = load_repo_tasks(args.only)
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
        fh.write(json.dumps({'type': 'meta', 'args': {k: v for k, v in vars(args).items() if k != 'done'}, 'arms': {a: ARMS[a][0] for a in arms},
                             'z0int_revision': subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'],
                                                              capture_output=True, text=True).stdout.strip(),
                             'claude_version': subprocess.run(['claude', '--version'], capture_output=True,
                                                              text=True).stdout.strip(),
                             'started_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}) + '\n')

        def emit(row):
            with lock:
                fh.write(json.dumps(row) + '\n')
                fh.flush()
                if row['type'] != 'trial':
                    print('rate-limited:', row['task'], row['arm'], row['rep'], row['message'][:80], flush=True)
                    return
                tk = row['tokens']
                print(f"{row['task']:28} {row['arm']:14} r{row['rep']} ok={int(row['verified'])} "
                      f"tok={tk['billed_total']:>8} ${row['cost_usd'] or 0:.3f} calls={row['n_tool_calls']} "
                      f"packs={row['obspack_packs']} rec={row['z0obs_recalls']} {row['wall_s']:.0f}s", flush=True)

        with ThreadPoolExecutor(max_workers=min(args.jobs, 3)) as pool:
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
    r.add_argument('--arms', nargs='+', default=['lean', 'lean+obspack'])
    r.add_argument('--only', nargs='*')
    r.add_argument('--reps', type=int, default=3)
    r.add_argument('--jobs', type=int, default=3)
    r.add_argument('--model', default='sonnet')
    r.add_argument('--max-usd', type=float, default=5.0)
    r.add_argument('--timeout', type=int, default=1500)
    r.add_argument('--seed', type=int, default=20260930)
    r.add_argument('--tag', default='main')
    r.add_argument('--out', required=True)
    r.add_argument('--skip', nargs='*', help='earlier output JSONL whose completed cells are not rerun')
    args = ap.parse_args()
    if args.cmd == 'validate':
        sys.exit(1 if validate(args) else 0)
    run(args)


if __name__ == '__main__':
    main()
