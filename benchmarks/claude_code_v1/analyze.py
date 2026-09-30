"""Pre-registered analysis for claude-code-savings-v1 (see PREREG.md). Reads raw run JSONL (private),
writes aggregate-only results (no prompts, answers, session ids or paths).

    .venv/bin/python benchmarks/claude_code_v1/analyze.py RAW.jsonl [RAW2.jsonl ...] --out benchmarks/claude_code_v1/results
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path

from scipy.stats import binomtest, wilcoxon

ARMS = ['stock', 'lean', 'lean+packet', 'lean+packet+obspack']
CONTRASTS = [  # (id, comparator A, treatment B, role)
    ('C1', 'stock', 'lean+packet+obspack', 'primary'),
    ('C2', 'stock', 'lean', 'confirmatory'),
    ('C3', 'lean', 'lean+packet', 'confirmatory'),
    ('C4', 'lean+packet', 'lean+packet+obspack', 'confirmatory'),
    ('S1', 'stock', 'lean+packet', 'secondary'),
]
BOOT = 10000
SEED = 20260930


def load(paths, tag):
    rows = []
    for p in paths:
        for line in Path(p).expanduser().read_text().splitlines():
            r = json.loads(line)
            if r.get('type') == 'trial' and (tag is None or r.get('tag') == tag):
                rows.append(r)
    # A later row for the same (suite, task, arm, rep) replaces an earlier one only if the earlier one had
    # no result (pre-registered single infrastructure rerun).
    best = {}
    for r in rows:
        k = (r['suite'], r['task'], r['arm'], r['rep'])
        if k not in best or (not best[k]['has_result'] and r['has_result']):
            best[k] = r
    return list(best.values())


def tok(r):
    return r['tokens']['billed_total']


def pairs(rows, a, b, pred=lambda r: True):
    idx = {(r['suite'], r['task'], r['rep'], r['arm']): r for r in rows if pred(r)}
    out = []
    for (s, t, rep, arm), ra in idx.items():
        if arm != a:
            continue
        rb = idx.get((s, t, rep, b))
        if rb is None:
            continue
        out.append((ra, rb))
    return sorted(out, key=lambda p: (p[0]['suite'], p[0]['task'], p[0]['rep']))


def cluster_boot(ps, stat, rng):
    by = defaultdict(list)
    for p in ps:
        by[(p[0]['suite'], p[0]['task'])].append(p)
    keys = sorted(by)
    vals = []
    for _ in range(BOOT):
        sample = [q for k in (rng.choice(keys) for _ in keys) for q in by[k]]
        v = stat(sample)
        if v is not None and not math.isnan(v):
            vals.append(v)
    vals.sort()
    return [round(vals[int(0.025 * len(vals))], 4), round(vals[int(0.975 * len(vals)) - 1], 4)] if vals else None


def ratio_of_totals(ps, key=tok):
    sa = sum(key(a) for a, _ in ps)
    return sum(key(b) for _, b in ps) / sa if sa else float('nan')


def contrast(ps, rng):
    usable = [(a, b) for a, b in ps if a['has_result'] and b['has_result'] and tok(a) > 0 and tok(b) > 0]
    n = len(usable)
    if n == 0:
        return {'n_pairs': 0}
    logs = [math.log(tok(b) / tok(a)) for a, b in usable]
    pct = [tok(b) / tok(a) - 1 for a, b in usable]
    try:
        w = wilcoxon(logs).pvalue if n >= 6 and any(logs) else None
    except ValueError:
        w = None
    sa, sb = sum(a['verified'] for a, _ in usable), sum(b['verified'] for _, b in usable)
    loss = sum(a['verified'] and not b['verified'] for a, b in usable)   # A ok, B fail
    win = sum(b['verified'] and not a['verified'] for a, b in usable)    # A fail, B ok
    mcn = binomtest(win, win + loss).pvalue if win + loss else 1.0
    dsucc = (sb - sa) / n
    cost = lambda r: r['cost_usd'] or 0  # noqa: E731
    both_ok = [(a, b) for a, b in usable if a['verified'] and b['verified']]
    rot = ratio_of_totals(usable)
    guard = dsucc >= -0.05 and not (mcn < 0.05 and loss > win)
    return {
        'n_pairs': n, 'n_tasks': len({(a['suite'], a['task']) for a, _ in usable}),
        'dropped_pairs_no_result': len(ps) - n,
        'tokens_total_A': sum(tok(a) for a, _ in usable), 'tokens_total_B': sum(tok(b) for _, b in usable),
        'tokens_change_ratio_of_totals': round(rot - 1, 4),
        'tokens_change_ci95_cluster_boot': [round(x - 1, 4) for x in cluster_boot(usable, ratio_of_totals, rng)],
        'tokens_median_paired_change': round(statistics.median(pct), 4),
        'tokens_geomean_ratio_change': round(math.exp(statistics.mean(logs)) - 1, 4),
        'pairs_B_cheaper': sum(x < 0 for x in logs),
        'wilcoxon_p_logratio': None if w is None else float(f'{w:.3g}'),
        'cost_usd_A': round(sum(cost(a) for a, _ in usable), 3), 'cost_usd_B': round(sum(cost(b) for _, b in usable), 3),
        'cost_change_ratio_of_totals': round(ratio_of_totals(usable, cost) - 1, 4) if sum(cost(a) for a, _ in usable) else None,
        'success_A': f'{sa}/{n}', 'success_B': f'{sb}/{n}', 'success_diff': round(dsucc, 4),
        'success_diff_ci95_cluster_boot': cluster_boot(
            usable, lambda s: (sum(b['verified'] for _, b in s) - sum(a['verified'] for a, _ in s)) / len(s), rng),
        'discordant_A_only': loss, 'discordant_B_only': win, 'mcnemar_exact_p': float(f'{mcn:.3g}'),
        'quality_guard': 'PASS' if guard else 'FAIL',
        'tokens_change_both_verified': round(ratio_of_totals(both_ok) - 1, 4) if both_ok else None,
        'n_both_verified': len(both_ok),
        'tokens_per_success_A': round(sum(tok(a) for a, _ in usable) / sa) if sa else None,
        'tokens_per_success_B': round(sum(tok(b) for _, b in usable) / sb) if sb else None,
    }


def holm(ps):
    order = sorted(range(len(ps)), key=lambda i: (ps[i] is None, ps[i] or 1))
    adj, run = [None] * len(ps), 0.0
    m = sum(p is not None for p in ps)
    for rank, i in enumerate(order):
        if ps[i] is None:
            continue
        run = max(run, min(1.0, (m - rank) * ps[i]))
        adj[i] = float(f'{run:.3g}')
    return adj


STRATA = {
    'pooled': lambda r: True,
    'qa': lambda r: r['suite'] == 'qa',
    'repo': lambda r: r['suite'] == 'repo',
    'repo_cold': lambda r: r['suite'] == 'repo' and r['rep'] == 0,
    'repo_warm': lambda r: r['suite'] == 'repo' and r['rep'] > 0,
    'repo_short': lambda r: r['suite'] == 'repo' and r['family'] != 'long_horizon_recall',
    'long_horizon': lambda r: r['family'] == 'long_horizon_recall',
}


def arm_table(rows):
    out = {}
    for arm in ARMS:
        for sname in ('qa', 'repo'):
            rs = [r for r in rows if r['arm'] == arm and r['suite'] == sname]
            if not rs:
                continue
            t = [tok(r) for r in rs]
            out[f'{sname}/{arm}'] = {
                'trials': len(rs), 'verified': sum(r['verified'] for r in rs), 'no_result': sum(not r['has_result'] for r in rs),
                'is_error': sum(bool(r.get('is_error')) for r in rs),
                'tokens_total': sum(t), 'tokens_median': statistics.median(t), 'cost_usd_total': round(sum(r['cost_usd'] or 0 for r in rs), 3),
                'cache_read_total': sum(r['tokens']['cache_read'] for r in rs),
                'cache_creation_total': sum(r['tokens']['cache_creation'] for r in rs),
                'input_uncached_total': sum(r['tokens']['input'] for r in rs), 'output_total': sum(r['tokens']['output'] for r in rs),
                'tool_calls_mean': round(statistics.mean(sum(r['tool_calls'].values()) for r in rs), 2),
                'turns_mean': round(statistics.mean(r['num_turns'] or 0 for r in rs), 2),
                'packet_seen': sum(r['packet_hook_bytes'] > 0 for r in rs),
                'obspack_packs_total': sum(r['obspack_packs'] for r in rs),
                'wall_s_median': round(statistics.median(r['wall_s'] for r in rs), 1),
            }
    return out


def per_task(rows):
    out = {}
    for key in sorted({(r['suite'], r['task']) for r in rows}):
        cell = {}
        for arm in ARMS:
            rs = [r for r in rows if (r['suite'], r['task']) == key and r['arm'] == arm]
            if rs:
                cell[arm] = {'n': len(rs), 'verified': sum(r['verified'] for r in rs),
                             'tokens_mean': round(statistics.mean(tok(r) for r in rs)),
                             'cost_usd_mean': round(statistics.mean(r['cost_usd'] or 0 for r in rs), 4)}
        rs0 = [r for r in rows if (r['suite'], r['task']) == key]
        out[f'{key[0]}/{key[1]}'] = {'repo': rs0[0]['repo'], 'family': rs0[0]['family'], 'arms': cell}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('raw', nargs='+')
    ap.add_argument('--tag', default='main')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    rows = load(args.raw, args.tag)
    rng = random.Random(SEED)
    res = {'schema': 'z0int.claude_code_savings.v1.results', 'n_trials': len(rows),
           'arms': arm_table(rows), 'contrasts': {}, 'per_task': per_task(rows)}
    for sname, pred in STRATA.items():
        block = {}
        for cid, a, b, role in CONTRASTS:
            c = contrast(pairs(rows, a, b, pred), rng)
            c.update({'A': a, 'B': b, 'role': role})
            block[cid] = c
        confirm = [c for c in ('C1', 'C2', 'C3', 'C4')]
        adj = holm([block[c].get('wilcoxon_p_logratio') for c in confirm])
        for c, p in zip(confirm, adj):
            block[c]['wilcoxon_p_holm'] = p
        res['contrasts'][sname] = block
    text = json.dumps(res, indent=1)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / 'results.json').write_text(text + '\n')
        # aggregate-only per-trial table (no prompts, answers, session ids or paths)
        with (out / 'trials.csv').open('w') as fh:
            cols = ['suite', 'task', 'repo', 'family', 'arm', 'rep', 'warm', 'verified', 'has_result', 'is_error',
                    'billed_total', 'input', 'cache_read', 'cache_creation', 'output', 'cost_usd', 'num_turns',
                    'tool_calls', 'packet_seen', 'obspack_packs', 'wall_s']
            fh.write(','.join(cols) + '\n')
            for r in sorted(rows, key=lambda r: (r['suite'], r['task'], r['arm'], r['rep'])):
                t = r['tokens']
                vals = [r['suite'], r['task'], r['repo'], r['family'], r['arm'], r['rep'], int(r['warm']), int(r['verified']),
                        int(r['has_result']), int(bool(r.get('is_error'))), t['billed_total'], t['input'], t['cache_read'],
                        t['cache_creation'], t['output'], r['cost_usd'] if r['cost_usd'] is not None else '', r['num_turns'] or 0,
                        sum(r['tool_calls'].values()), int(r['packet_hook_bytes'] > 0), r['obspack_packs'], r['wall_s']]
                fh.write(','.join(map(str, vals)) + '\n')
    for sname in STRATA:
        print(f'\n== {sname}')
        for cid, c in res['contrasts'][sname].items():
            if not c.get('n_pairs'):
                continue
            print(f"{cid} {c['A']:>12} -> {c['B']:<20} n={c['n_pairs']:>3} tok {c['tokens_change_ratio_of_totals']:+.1%} "
                  f"CI{c['tokens_change_ci95_cluster_boot']} med {c['tokens_median_paired_change']:+.1%} "
                  f"cheaper {c['pairs_B_cheaper']}/{c['n_pairs']} p={c['wilcoxon_p_logratio']} holm={c.get('wilcoxon_p_holm')} "
                  f"succ {c['success_A']}->{c['success_B']} guard={c['quality_guard']}")


if __name__ == '__main__':
    main()
