"""Semantic (DecisionBackend) skill-family selector vs the pre-registered labels.

Scores every prompt once per mode (cached to --cache as JSONL), then sweeps thresholds
offline with exactly the evaluate.py metrics: a recall failure = a labelled skill hidden;
extra = an exposed family not in the label; savings = description chars hidden.

Held-out discipline: the threshold is chosen on dev (rows 0-14) by a rule fixed before
scoring -- the highest threshold with zero dev recall failures -- and then reported on
held-out (rows 15-29) and on all 30.

    Z0INT_DECIDER_DEVICE=cpu python benchmarks/claude_code/skill_select/evaluate_semantic.py \
        --mode family --cache runs/semantic_family.jsonl
"""
import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

from z0int import claude_code_skills as cs

HERE = Path(__file__).parent
THRESHOLDS = [0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5]


def rows():
    return [json.loads(l) for l in (HERE / 'labels.jsonl').read_text().splitlines()]


def score(r, exposed, hidden, skills):
    fams = {cs.family(n, skills[n]) for n in exposed}
    need_skills = {n for n in skills if n.split('-')[0] in r['need']}
    need = {cs.family(n, skills[n]) for n in need_skills}
    missing = need_skills - set(exposed)
    return bool(missing), len(fams - need), sum(len(skills[n]) for n in hidden), sorted(need), sorted(fams)


def summarize(rs, decide, skills):
    total = sum(len(d) for d in skills.values())
    miss = over = 0; hid = 0; fails = []
    for r in rs:
        exposed, hidden, _ = decide(r)
        m, o, h, need, got = score(r, exposed, hidden, skills)
        miss += m; over += o; hid += h
        if m:
            fails.append(r['prompt'][:50])
    return {'n': len(rs), 'miss': miss, 'extra': over, 'hidden_pct': hid / len(rs) / total,
            'hidden_tok': hid / len(rs) / 4, 'fails': fails}


def fmt(s):
    return f"{s['n'] - s['miss']:>2}/{s['n']} recall, {s['miss']} miss, {s['extra']:>2} extra, {s['hidden_pct']:.0%} hidden (~{s['hidden_tok']:.0f} tok)"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=['family', 'skill'], default='family')
    ap.add_argument('--cache', default=str(HERE / 'runs' / 'semantic_family.jsonl'))
    ap.add_argument('--backend', default='decider_2b')
    ap.add_argument('--threads', type=int, default=4)
    args = ap.parse_args()
    skills = cs.load()
    data = rows()
    cache_path = Path(args.cache); cache_path.parent.mkdir(parents=True, exist_ok=True)
    cached = {}
    if cache_path.exists():
        for l in cache_path.read_text().splitlines():
            c = json.loads(l); cached[c['prompt']] = c
    todo = [r for r in data if r['prompt'] not in cached]
    if todo:
        import torch
        torch.set_num_threads(args.threads)
        backend = cs.get_backend(args.backend)
        t0 = time.perf_counter(); h = backend.health(load=True)
        print(f"load {time.perf_counter() - t0:.1f}s: {h.detail}", flush=True)
        with cache_path.open('a') as f:
            for r in todo:
                t0 = time.perf_counter()
                try:
                    scores, res = cs.semantic_scores(r['prompt'], skills, backend, args.mode)
                    err = None; diag = {'input_tokens': res.diagnostics.get('input_tokens'), 'load_ms': res.diagnostics.get('load_ms')}
                except Exception as exc:  # noqa: BLE001
                    scores, err, diag = None, repr(exc), {}
                c = {'prompt': r['prompt'], 'mode': args.mode, 'scores': scores, 'error': err,
                     'wall_s': time.perf_counter() - t0, 'maxrss_mb': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, **diag}
                cached[r['prompt']] = c; f.write(json.dumps(c) + '\n'); f.flush()
                print(f"{c['wall_s']:6.1f}s {r['prompt'][:50]:<50} need={r['need']} " + ' '.join(f'{k}={v:.3f}' for k, v in (scores or {}).items()), flush=True)
    dev, held = data[:15], data[15:]
    lex = lambda r: cs.select(r['prompt'], skills)
    print(f"\nmode={args.mode} backend={args.backend}")
    print(f"lexical v1        dev {fmt(summarize(dev, lex, skills))} | held {fmt(summarize(held, lex, skills))} | all {fmt(summarize(data, lex, skills))}")
    table = []
    for hybrid in (False, True):
        for t in THRESHOLDS:
            dec = lambda r, t=t, h=hybrid: cs.decide_semantic(r['prompt'], skills, cached[r['prompt']]['scores'], t, h)
            s = {k: summarize(v, dec, skills) for k, v in (('dev', dev), ('held', held), ('all', data))}
            table.append((hybrid, t, s))
            print(f"{'hybrid  ' if hybrid else 'semantic'} t={t:<5} dev {fmt(s['dev'])} | held {fmt(s['held'])} | all {fmt(s['all'])}  fails={s['all']['fails']}")
    for hybrid in (False, True):
        ok = [(t, s) for h, t, s in table if h == hybrid and s['dev']['miss'] == 0]
        if ok:
            t, s = max(ok, key=lambda x: x[0])
            print(f"SELECTED ({'hybrid' if hybrid else 'semantic'}) on dev: t={t} -> held {fmt(s['held'])} | all {fmt(s['all'])}")
    walls = sorted(c['wall_s'] for c in cached.values() if c.get('mode') == args.mode)
    if walls:
        print(f"latency/prompt (s): median {walls[len(walls)//2]:.1f} min {walls[0]:.1f} max {walls[-1]:.1f}; "
              f"max RSS {max(c['maxrss_mb'] for c in cached.values()):.0f} MB; errors {sum(1 for c in cached.values() if c['error'])}")


if __name__ == '__main__':
    main()
