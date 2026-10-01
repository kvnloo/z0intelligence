"""Benchmark the Claude Code Stop hook: old (in-hook parse) vs new (detached, incremental).

  python scripts/bench_claude_code_stop_hook.py TRANSCRIPT.jsonl --old-src OLD/src [--new-src src]

Copies the transcript (and its subagents/ dir) under ~/.cache/z0int-bench-stop-hook, never
/tmp; appends only synthetic count-only rows; prints median wall seconds (3 reps).
hook_* = the hook process Claude Code waits on; async_* = the detached stop-async child.
"""
import argparse, fcntl, json, os, subprocess, sys, time, shutil, pathlib, statistics
ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument('transcript', type=pathlib.Path)
ap.add_argument('--old-src', required=True)
ap.add_argument('--new-src', default=str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
ap.add_argument('--scratch', type=pathlib.Path, default=pathlib.Path.home() / '.cache' / 'z0int-bench-stop-hook')
a = ap.parse_args()
S = a.transcript.stem
PY = sys.executable
TREES = {'old': a.old_src, 'new': a.new_src}
B = a.scratch
shutil.rmtree(B / 't', ignore_errors=True)
(B / 't').mkdir(parents=True)
shutil.copy2(a.transcript, B / 't' / a.transcript.name)
if a.transcript.with_suffix('').is_dir():
    shutil.copytree(a.transcript.with_suffix(''), B / 't' / S)
n_append = [0]
def append(path):
    n_append[0] += 1
    k = n_append[0]
    with open(path, 'a') as fh:
        fh.write(json.dumps({'type': 'user', 'promptId': f'bench{k}', 'message': {'content': 'bench'}}) + '\n')
        fh.write(json.dumps({'type': 'assistant', 'message': {'id': f'bench-msg-{k}', 'model': 'claude-opus-5-5',
                 'usage': {'input_tokens': 1, 'output_tokens': 1}, 'content': [{'type': 'text', 'text': 'ok'}]}}) + '\n')
def run(tree, event, home, transcript):
    env = {**os.environ, 'PYTHONPATH': TREES[tree], 'Z0INT_HOME': str(home)}
    hook = json.dumps({'session_id': S, 'transcript_path': str(transcript), 'prompt_id': 'bench'})
    t = time.perf_counter()
    subprocess.run([PY, '-m', 'z0int.claude_code', event], input=hook.encode(), env=env, check=True,
                   stdout=subprocess.DEVNULL)
    return time.perf_counter() - t
res = {}
for tree in ('old', 'new'):
    for rep in range(3):
        work = B / f'w-{tree}-{rep}'; shutil.rmtree(work, ignore_errors=True)
        shutil.copytree(B / 't', work)
        tr = work / f'{S}.jsonl'; home = work / 'home'
        if tree == 'old':
            cold = run(tree, 'stop', home, tr); append(tr); inc = run(tree, 'stop', home, tr)
            res.setdefault(tree, []).append({'hook_cold_s': cold, 'hook_incremental_s': inc})
        else:
            hook_cold = run(tree, 'stop', home, tr)  # returns after spawning the child
            time.sleep(0.2)
            # wait for the detached child by taking the session lock
            lock = home / 'state' / 'claude-code' / f'{S}.lock'
            t = time.perf_counter()
            while not lock.exists(): time.sleep(0.05)
            with open(lock) as fh: fcntl.flock(fh, fcntl.LOCK_EX)
            shutil.rmtree(home)  # measure the async part directly and synchronously
            async_cold = run(tree, 'stop-async', home, tr)
            append(tr)
            hook_inc = run(tree, 'stop', home, tr); time.sleep(0.5)
            with open(lock) as fh: fcntl.flock(fh, fcntl.LOCK_EX)
            append(tr)
            async_inc = run(tree, 'stop-async', home, tr)
            res.setdefault(tree, []).append({'hook_cold_s': hook_cold, 'hook_incremental_s': hook_inc,
                                             'async_cold_s': async_cold, 'async_incremental_s': async_inc})
        shutil.rmtree(work)
out = {t: {k: round(statistics.median(r[k] for r in rows), 3) for k in rows[0]} for t, rows in res.items()}
out['load_avg'] = os.getloadavg(); out['transcript_bytes'] = (B / 't' / f'{S}.jsonl').stat().st_size
out['subagent_files'] = len(list((B / 't' / S / 'subagents').glob('*.jsonl'))) if (B / 't' / S / 'subagents').is_dir() else 0
print(json.dumps(out, indent=1))
