"""Paired Claude Code arm runner (z0evals-style: same task instance, pinned sources, create-only output).

Each trial copies a fresh fixture, runs headless Claude Code with or without plugin dirs,
records Claude Code's own billed usage/cost, the plugin's Tokenomics receipts, and a
programmatic verifier result. Tool-reported "savings" are never used.
"""
import argparse, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / 'harness-adapters/claude-code-z0intelligence'
# Arm -> extra CLI flags. 'lean' drops user settings/hooks, MCP and skills: the static prefix tax.
ARMS = {
    'off': [],
    'z0-shadow': ['--plugin-dir', str(PLUGIN)],
    'lean': ['--setting-sources', 'project', '--strict-mcp-config', '--disable-slash-commands'],
    'lean+z0': ['--setting-sources', 'project', '--strict-mcp-config', '--disable-slash-commands', '--plugin-dir', str(PLUGIN)],
}


def fixture(task, work):
    src = task['_dir'] / 'repo'
    if src.is_dir():
        shutil.copytree(src, work, dirs_exist_ok=True)
    s = task.get('source')
    if s:
        archive = subprocess.run(['git', '-C', str(ROOT), 'archive', s['sha'], *s['paths']], capture_output=True, check=True).stdout
        subprocess.run(['tar', '-x', '-C', str(work)], input=archive, check=True)
    sab = task.get('sabotage')
    if sab:
        f = work / sab['file']; text = f.read_text()
        assert text.count(sab['old']) == 1, 'sabotage anchor must be unique'
        f.write_text(text.replace(sab['old'], sab['new']))
    subprocess.run(['git', 'init', '-q', str(work)], check=True)


def trial(task, arm, rep, args):
    work = Path(tempfile.mkdtemp(prefix=f"cc-{task['id']}-{arm}-"))
    home = work.parent / (work.name + '-z0home')
    fixture(task, work)
    env = {**os.environ, 'Z0INT_HOME': str(home), 'Z0INT_PYTHON': str(ROOT / '.venv/bin/python'),
           'PATH': str(ROOT / '.venv/bin') + os.pathsep + os.environ['PATH']}
    cmd = ['claude', '-p', task['prompt'], '--output-format', 'json', '--model', args.model,
           '--effort', args.effort, '--permission-mode', 'bypassPermissions', '--max-budget-usd', str(args.max_usd)]
    cmd += ARMS[arm]
    t0 = time.time()
    run = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, timeout=args.timeout)
    wall = time.time() - t0
    try:
        result = json.loads(run.stdout)
    except ValueError:
        result = {'parse_error': run.stdout[-500:], 'stderr': run.stderr[-500:]}
    argv = [str(ROOT / '.venv/bin/python') if a == 'python3' else a.format(task=task['_dir'], work=work) for a in task['check']]
    check = subprocess.run(argv, cwd=work, capture_output=True, text=True, env={**env, 'PYTHONPATH': str(work / 'src')})
    events = home / 'tokenomics' / 'events.jsonl'
    receipts = [json.loads(l) for l in events.read_text().splitlines()] if events.exists() else []
    return {'task': task['id'], 'arm': arm, 'rep': rep, 'model': args.model, 'effort': args.effort,
            'verified': check.returncode == 0, 'wall_s': round(wall, 2),
            'cost_usd': result.get('total_cost_usd'), 'usage': result.get('usage'),
            'model_usage': result.get('modelUsage'), 'num_turns': result.get('num_turns'),
            'session_id': result.get('session_id'), 'is_error': result.get('is_error'),
            'plugin_receipts': receipts, 'workdir': str(work)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tasks', nargs='+', required=True)
    ap.add_argument('--arms', nargs='+', default=['off', 'z0-shadow'])
    ap.add_argument('--reps', type=int, default=1)
    ap.add_argument('--model', default='sonnet')
    ap.add_argument('--effort', default='low')
    ap.add_argument('--max-usd', type=float, default=1.0)
    ap.add_argument('--timeout', type=int, default=900)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    out = Path(args.out)
    if out.exists():
        sys.exit('output exists (create-only)')
    out.parent.mkdir(parents=True, exist_ok=True)
    tasks = []
    for t in args.tasks:
        d = Path(__file__).parent / 'tasks' / t
        task = json.loads((d / 'task.json').read_text()); task['_dir'] = d; tasks.append(task)
    with out.open('x') as fh:
        # Interleave arms within each rep so drift in the provider hits both arms equally.
        for rep in range(args.reps):
            for task in tasks:
                for arm in (args.arms if rep % 2 == 0 else list(reversed(args.arms))):
                    row = trial(task, arm, rep, args)
                    fh.write(json.dumps(row) + '\n'); fh.flush()
                    print(task['id'], arm, rep, row['verified'], row['cost_usd'], row['num_turns'], flush=True)


if __name__ == '__main__':
    main()
