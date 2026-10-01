"""Paired Claude Code runs for the engagement measurements M2, M4 and M5 (docs/engagement.md).

Conventions follow benchmarks/claude_code/run_pair.py: same task instance per arm, arms interleaved
and reversed across reps, create-only JSONL, Claude Code's own billed usage, programmatic verifier.
Differences, all pre-registered in benchmarks/engagement/PREREG.md:

* Every arm gets its own ``--settings`` file. Every arm except launcher-lean disables the installed
  z0 plugins via ``enabledPlugins: false``; z0 mechanisms under test are registered only as hooks in
  that per-run settings file (never in the user's settings or plugin).
* Scratch lives under ``~/.cache/z0-savings-engagement`` (counted as the harness cohort by
  ``claude_code_engagement.cohort``); never /tmp.
* Output is ``--output-format stream-json --verbose``; the raw stream and the session transcript are
  kept in scratch (they contain prompts and local paths), only derived numbers go to the JSONL.
* Inherited CLAUDE_* / CLAUDECODE env vars from the launching session are stripped.

  python benchmarks/engagement/run_engagement.py m2 --out ... [--reps 3] [--jobs 3]
  python benchmarks/engagement/run_engagement.py m4 --out ... [--reps 3] [--jobs 3]
  python benchmarks/engagement/run_engagement.py m5 --out ... [--reps 4] [--jobs 3]
"""
from __future__ import annotations

import argparse, json, os, random, re, shutil, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
STACK_VENV = Path.home() / 'workspace/z0intelligence-stack/.venv'
PY = STACK_VENV / 'bin/python'
SCRATCH = Path.home() / '.cache/z0-savings-engagement'
WS = SCRATCH / 'ws'
M4_SRC = Path.home() / 'workspace/z0intelligence-engagement'
M4_SHA = '24a8321'
M4_BRANCH = 'feat/engagement-v0'
PROJECTS = Path.home() / '.claude/projects'
Z0_PLUGINS_OFF = {'z0intelligence@z0intelligence': False, 'z0-obspack@z0intelligence': False}
LEAN_SETTINGS = {'skillListingMaxDescChars': 80, 'skillListingBudgetFraction': 0.005,
                 'disableClaudeAiConnectors': True}  # == claude_code_engagement.lean_settings()['settings']


def hook_cmd(*env: str) -> str:
    # The branch's code (PYTHONPATH) on the stack venv's interpreter; Z0INT_HOME comes from the run env.
    return (f'env PYTHONPATH={ROOT / "src"} {" ".join(env)} {PY} -m z0int.claude_code session-start || true')


def settings_for(arm: str) -> dict | None:
    base = {'enabledPlugins': dict(Z0_PLUGINS_OFF)}
    if arm in ('stock', 'm4-off'):
        if arm == 'm4-off':
            base['hooks'] = {'SessionStart': [{'hooks': [{'type': 'command', 'timeout': 30,
                             'command': hook_cmd('Z0INT_CLAUDE_CODE_PACKET=1')}]}]}
        return base
    if arm == 'ws-packet':
        base['hooks'] = {'SessionStart': [{'hooks': [{'type': 'command', 'timeout': 30,
                         'command': hook_cmd('Z0INT_CLAUDE_CODE_PACKET=1', 'Z0INT_CLAUDE_CODE_OFFLOAD_HINT=0')}]}]}
        return base
    if arm == 'm4-on':
        base['hooks'] = {
            'SessionStart': [{'hooks': [{'type': 'command', 'timeout': 30, 'command': hook_cmd('Z0INT_CLAUDE_CODE_PACKET=1')}]}],
            'SubagentStart': [{'hooks': [{'type': 'command', 'timeout': 30,
                              'command': hook_cmd('Z0INT_CLAUDE_CODE_PACKET=1', 'Z0INT_CLAUDE_CODE_SUBAGENT_PACKET=1')}]}]}
        return base
    if arm == 'settings-lean':
        return {**base, **LEAN_SETTINGS}
    if arm == 'launcher-lean':
        return None
    raise ValueError(arm)


def clean_env(extra: dict) -> dict:
    env = {k: v for k, v in os.environ.items() if not (k.startswith('CLAUDE_') or k == 'CLAUDECODE')}
    env['TMPDIR'] = str(SCRATCH / 'tmp')
    env.update(extra)
    return env


def run_claude(arm, prompt, cwd, trial_dir, args, budget):
    trial_dir.mkdir(parents=True, exist_ok=True)
    z0home = trial_dir / 'z0home'
    env = clean_env({'Z0INT_HOME': str(z0home)})
    flags = ['-p', prompt, '--output-format', 'stream-json', '--verbose', '--model', args.model,
             '--effort', args.effort, '--permission-mode', 'bypassPermissions', '--max-budget-usd', str(budget)]
    st = settings_for(arm)
    if arm == 'launcher-lean':
        env['PYTHONPATH'] = str(ROOT / 'src')
        cmd = [str(STACK_VENV / 'bin/z0int'), 'claude-code', 'launch', '--profile', 'lean', '--no-plugin', '--', *flags]
    else:
        sf = trial_dir / 'settings.json'
        sf.write_text(json.dumps(st, indent=1))
        cmd = ['claude', *flags, '--settings', str(sf)]
        if arm == 'settings-lean':
            # Proposal also removes the user's lavish-axi SessionStart hook; a settings overlay cannot
            # remove a user hook, so this arm shadows the binary with a silent no-op on PATH.
            shim = SCRATCH / 'shim-noaxi'
            shim.mkdir(exist_ok=True)
            (shim / 'lavish-axi').write_text('#!/bin/sh\nexit 0\n'); (shim / 'lavish-axi').chmod(0o755)
            env['PATH'] = f'{shim}{os.pathsep}{env["PATH"]}'
    t0 = time.time()
    try:
        run = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=args.timeout)
        stdout, rc = run.stdout, run.returncode
    except subprocess.TimeoutExpired as e:
        stdout, rc = (e.stdout or b'').decode(errors='replace') if isinstance(e.stdout, bytes) else (e.stdout or ''), 'timeout'
    wall = time.time() - t0
    (trial_dir / 'stream.jsonl').write_text(stdout)
    init, result = {}, {}
    for line in stdout.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get('type') == 'system' and d.get('subtype') == 'init':
            init = d
        elif d.get('type') == 'result':
            result = d
    return init, result, wall, rc


# --------------------------------------------------------------------------- transcripts

def transcript_paths(session_id):
    if not session_id:
        return None, []
    roots = list(PROJECTS.glob(f'*/{session_id}.jsonl'))
    if not roots:
        return None, []
    root = roots[0]
    return root, sorted((root.with_suffix('') / 'subagents').glob('*.jsonl'))


def requests(path):
    """One row per API request (dedup by message.id): timestamp, tokens, tool_use inputs, extras."""
    reqs, order, hookctx = {}, [], []
    try:
        lines = path.read_text(errors='replace').splitlines()
    except OSError:
        return [], []
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get('type') == 'attachment' and (d.get('attachment') or {}).get('type') == 'hook_additional_context':
            hookctx.append(line)
        if d.get('type') != 'assistant':
            continue
        m = d.get('message') or {}
        mid = m.get('id') or d.get('uuid')
        if mid not in reqs:
            u = m.get('usage') or {}
            reqs[mid] = {'ts': d.get('timestamp') or '', 'input': u.get('input_tokens', 0) or 0,
                         'cc': u.get('cache_creation_input_tokens', 0) or 0,
                         'cr': u.get('cache_read_input_tokens', 0) or 0,
                         'out': u.get('output_tokens', 0) or 0, 'tools': []}
            order.append(mid)
        for b in m.get('content') or []:
            if isinstance(b, dict) and b.get('type') == 'tool_use':
                reqs[mid]['tools'].append((b.get('name'), b.get('input') or {}))
    return [reqs[k] for k in order], hookctx


def billed_in(r):
    return r['input'] + r['cc'] + r['cr']


def weighted_in(r):  # cost-weighted input-equivalent tokens (cache write 1.25x, cache read 0.1x)
    return r['input'] + 1.25 * r['cc'] + 0.1 * r['cr']


READ_TOOLS = {'Read', 'Grep', 'Bash', 'NotebookRead'}
CD = re.compile(r'(?:^|&&|;|\|\|)\s*cd\s+([^\s;&|]+)')


def first_relevant(streams, task):
    """Cumulative billed input tokens over all requests (root + subagents, by timestamp) up to and
    including the first request that issued a read of the task's target file in the target repo."""
    files = [re.compile(f) for f in task['files']]
    events = []
    for agent, reqs in streams.items():
        for r in reqs:
            events.append((r['ts'], agent, r))
    events.sort(key=lambda e: e[0])
    cwd = {}
    total = weighted = 0.0
    n = 0
    for ts, agent, r in events:
        total += billed_in(r); weighted += weighted_in(r); n += 1
        for name, inp in r['tools']:
            if name not in READ_TOOLS:
                continue
            s = json.dumps(inp)
            here = cwd.get(agent, '')
            if name == 'Bash':
                cds = CD.findall(inp.get('command', ''))
                for c in cds:  # relative cds accumulate onto the tracked cwd
                    cwd[agent] = os.path.normpath(os.path.join(cwd.get(agent, ''), c))
            if any(f.search(s) for f in files) and (task['repo'] in s or task['repo'] in here):
                return {'reached': True, 'tokens': total, 'weighted': weighted, 'requests': n}
    return {'reached': False, 'tokens': total, 'weighted': weighted, 'requests': n}


def answer_ok(text, accept):
    lines = [l for l in (text or '').splitlines() if re.match(r'\s*\**ANSWER\**\s*:', l)]
    if not lines:
        return False, None
    a = lines[-1].split(':', 1)[1]
    norm = re.sub(r'[\s`"\'*]', '', a).lower().rstrip('.')
    return norm in accept, norm


def summarize_streams(session_id):
    root, subs = transcript_paths(session_id)
    streams, hookctx = {}, {}
    if root:
        streams['root'], hookctx['root'] = requests(root)
        for p in subs:
            streams[p.stem], hookctx[p.stem] = requests(p)
    def tot(keys, fn):
        return sum(fn(r) for k in keys for r in streams.get(k, []))
    sub_keys = [k for k in streams if k != 'root']
    ctx_marks = {k: {'state_packet': any('<z0-state-packet' in l for l in v),
                     'workspace_packet': any('<z0-workspace-packet' in l for l in v),
                     'offload': any('<z0-offload' in l for l in v)} for k, v in hookctx.items()}
    return streams, {
        'transcript_found': bool(root), 'n_subagents': len(sub_keys),
        'root_requests': len(streams.get('root', [])),
        'root_billed_in': tot(['root'], billed_in), 'root_weighted_in': tot(['root'], weighted_in),
        'sub_billed_in': tot(sub_keys, billed_in), 'sub_weighted_in': tot(sub_keys, weighted_in),
        'sub_requests': sum(len(streams[k]) for k in sub_keys),
        'first_request': ({'cc': streams['root'][0]['cc'], 'cr': streams['root'][0]['cr'], 'input': streams['root'][0]['input']}
                          if streams.get('root') else None),
        'root_ctx': ctx_marks.get('root', {}),
        'subagents_with_state_packet': sum(1 for k in sub_keys if ctx_marks.get(k, {}).get('state_packet')),
    }


def base_row(task, arm, rep, args, init, result, wall, rc):
    return {'task': task['id'], 'arm': arm, 'rep': rep, 'model': args.model, 'effort': args.effort,
            'rc': rc, 'wall_s': round(wall, 2), 'cost_usd': result.get('total_cost_usd'),
            'usage': result.get('usage'), 'model_usage': result.get('modelUsage'),
            'num_turns': result.get('num_turns'), 'session_id': result.get('session_id') or init.get('session_id'),
            'is_error': result.get('is_error'), 'subtype': result.get('subtype'),
            'init': {'plugins': [p.get('name') if isinstance(p, dict) else p for p in init.get('plugins') or []],
                     'mcp_servers': len(init.get('mcp_servers') or []), 'tools': len(init.get('tools') or []),
                     'slash_commands': len(init.get('slash_commands') or []), 'skills': len(init.get('skills') or [])}}


# --------------------------------------------------------------------------- measurements

def m2_trial(task, arm, rep, args, spec):
    tdir = SCRATCH / 'runs' / args.tag / f"{task['id']}-{arm}-{rep}"
    before = ws_state()
    init, result, wall, rc = run_claude(arm, task['prompt'] + '\n\n' + spec['suffix'], WS, tdir, args, args.max_usd)
    row = base_row(task, arm, rep, args, init, result, wall, rc)
    streams, s = summarize_streams(row['session_id'])
    ok, norm = answer_ok(result.get('result'), task['accept'])
    row.update(s, verified=ok, answer=norm, packet_shown=task['packet_shown'],
               to_first_relevant=first_relevant(streams, task), ws_unchanged=(ws_state() == before))
    return row


def ws_state():
    out = []
    for r in sorted(WS.iterdir()):
        if (r / '.git').exists():
            s = subprocess.run(['git', '--no-optional-locks', '-C', str(r), 'status', '--porcelain'],
                               capture_output=True, text=True).stdout
            out.append((r.name, s))
    return out


def m4_trial(task, arm, rep, args, spec):
    tdir = SCRATCH / 'runs' / args.tag / f"{task['id']}-{arm}-{rep}"
    work = tdir / 'repo'
    if work.exists():
        shutil.rmtree(work)
    tdir.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'clone', '-q', '--shared', '--no-checkout', str(M4_SRC), str(work)], check=True)
    subprocess.run(['git', '-C', str(work), 'checkout', '-q', '-B', M4_BRANCH, M4_SHA], check=True)
    subprocess.run(['git', '-C', str(work), 'remote', 'remove', 'origin'], check=True)
    init, result, wall, rc = run_claude(arm, task['prompt'] + '\n\n' + spec['suffix'], work, tdir, args, args.max_usd)
    row = base_row(task, arm, rep, args, init, result, wall, rc)
    _, s = summarize_streams(row['session_id'])
    ok, norm = answer_ok(result.get('result'), task['accept'])
    row.update(s, verified=ok, answer=norm)
    return row


def m5_fixture(task, work):
    sys.path.insert(0, str(ROOT / 'benchmarks/claude_code'))
    from run_pair import fixture
    fixture(task, work)


def m5_trial(task, arm, rep, args):
    work = SCRATCH / 'm5' / f"{args.tag}-{task['id']}-{arm}"
    if not (work / '.git').exists():
        work.mkdir(parents=True, exist_ok=True)
        m5_fixture(task, work)
        subprocess.run(['git', '-C', str(work), 'add', '-A'], check=True)
        subprocess.run(['git', '-C', str(work), '-c', 'user.name=bench', '-c', 'user.email=bench@local',
                        'commit', '-qm', 'fixture'], check=True)
    else:
        subprocess.run(['git', '-C', str(work), 'reset', '-q', '--hard'], check=True)
        subprocess.run(['git', '-C', str(work), 'clean', '-qfdx'], check=True)
    tdir = SCRATCH / 'runs' / args.tag / f"{task['id']}-{arm}-{rep}"
    init, result, wall, rc = run_claude(arm, task['prompt'], work, tdir, args, args.max_usd)
    row = base_row(task, arm, rep, args, init, result, wall, rc)
    _, s = summarize_streams(row['session_id'])
    argv = [str(PY) if a == 'python3' else a.replace('{task}', str(task['_dir'])).replace('{work}', str(work)) for a in task['check']]
    check = subprocess.run(argv, cwd=work, capture_output=True, text=True,
                           env={**clean_env({}), 'PYTHONPATH': str(work / 'src')})
    row.update(s, verified=check.returncode == 0, warm=rep > 0)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('measure', choices=['m2', 'm4', 'm5'])
    ap.add_argument('--out', required=True)
    ap.add_argument('--tag', required=True)
    ap.add_argument('--reps', type=int, default=3)
    ap.add_argument('--tasks', nargs='*')
    ap.add_argument('--model', default='sonnet')
    ap.add_argument('--effort', default='low')
    ap.add_argument('--max-usd', type=float, default=1.0)
    ap.add_argument('--timeout', type=int, default=900)
    ap.add_argument('--jobs', type=int, default=3)
    args = ap.parse_args()
    if args.jobs > 3:
        sys.exit('at most 3 concurrent claude processes')
    out = Path(args.out)
    if out.exists():
        sys.exit('output exists (create-only)')
    out.parent.mkdir(parents=True, exist_ok=True)
    (SCRATCH / 'tmp').mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    fh = out.open('x')

    def emit(row):
        with lock:
            fh.write(json.dumps(row) + '\n'); fh.flush()
            print(row['task'], row['arm'], row['rep'], row['verified'], row['cost_usd'], row.get('n_subagents'), flush=True)

    if args.measure in ('m2', 'm4'):
        spec = json.loads((HERE / f'tasks_{args.measure}.json').read_text())
        tasks = [t for t in spec['tasks'] if not args.tasks or t['id'] in args.tasks]
        arms = ['stock', 'ws-packet'] if args.measure == 'm2' else ['m4-off', 'm4-on']
        rng = random.Random(4)
        plan = []
        for rep in range(args.reps):
            for i, task in enumerate(tasks):
                if args.measure == 'm2':
                    order = arms if (rep + i) % 2 == 0 else arms[::-1]
                else:  # M4: randomized (blocked) per-session assignment order, fixed seed
                    order = rng.sample(arms, 2)
                plan += [(task, arm, rep) for arm in order]
        fn = m2_trial if args.measure == 'm2' else m4_trial

        def run(item):
            emit(fn(*item, args, spec))
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            list(pool.map(run, plan))
    else:
        arms = ['stock', 'settings-lean', 'launcher-lean']
        ids = args.tasks or ['add-feature', 'debug-suite', 'fix-bug', 'grep-answer', 'nav-harness']
        tasks = []
        for t in ids:
            d = ROOT / 'benchmarks/claude_code/tasks' / t
            task = json.loads((d / 'task.json').read_text()); task['_dir'] = d; tasks.append(task)

        # One worker per arm; within a (task, arm) the reps run back to back so reps 1.. are warm
        # (rep 0 cold). The three arms of a task run concurrently, so provider drift hits all arms.
        def seq(task, arm):
            for rep in range(args.reps):
                emit(m5_trial(task, arm, rep, args))
        for task in tasks:
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(lambda a: seq(task, a), arms))
    fh.close()


if __name__ == '__main__':
    main()
