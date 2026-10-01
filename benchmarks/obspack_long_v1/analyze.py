"""Pre-registered analysis for obspack-long-v1 (see PREREG.md). Reads raw run JSONL (private), writes
aggregate-only results (no prompts, answers, session ids or paths).

Primary metric: Claude Code's own estimated cost (`total_cost_usd`), because it is billing-weighted
(cache reads ~0.1x input, cache writes ~1.25x); unweighted token totals are secondary.

    .venv/bin/python benchmarks/obspack_long_v1/analyze.py RAW.jsonl [...] --out benchmarks/obspack_long_v1/results
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

ARMS = ['lean', 'lean+obspack', 'stock', 'stock+obspack']
CONTRASTS = [  # (id, A, B, role)
    ('P1', 'lean', 'lean+obspack', 'primary'),
    ('S1', 'stock', 'stock+obspack', 'secondary'),
    ('S2', 'stock', 'lean+obspack', 'secondary'),
]
ANCHOR = 'zint-long-backend-review'
LONG = {'multi_bug_debug', 'directed_recall'}
BOOT = 10000
SEED = 20260930


def load(paths, tag):
    rows = []
    for p in paths:
        for line in Path(p).expanduser().read_text().splitlines():
            r = json.loads(line)
            if r.get('type') == 'trial' and (tag is None or r.get('tag') == tag):
                rows.append(r)
    best = {}  # a later row replaces an earlier one only if the earlier had no result (pre-registered rerun)
    for r in rows:
        k = (r['task'], r['arm'], r['rep'])
        if k not in best or (not best[k]['has_result'] and r['has_result']):
            best[k] = r
    return list(best.values())


def cost(r):
    return r['cost_usd'] or 0.0


def tok(r):
    return r['tokens']['billed_total']


def pairs(rows, a, b, pred):
    idx = {(r['task'], r['rep'], r['arm']): r for r in rows if pred(r)}
    out = [(ra, idx[(t, rep, b)]) for (t, rep, arm), ra in idx.items() if arm == a and (t, rep, b) in idx]
    return sorted(out, key=lambda p: (p[0]['task'], p[0]['rep']))


def cluster_boot(ps, stat, rng):
    by = defaultdict(list)
    for p in ps:
        by[p[0]['task']].append(p)
    keys = sorted(by)
    vals = []
    for _ in range(BOOT):
        v = stat([q for k in (rng.choice(keys) for _ in keys) for q in by[k]])
        if v is not None and not math.isnan(v):
            vals.append(v)
    vals.sort()
    return [round(vals[int(0.025 * len(vals))], 4), round(vals[int(0.975 * len(vals)) - 1], 4)] if vals else None


def rot(ps, key):
    sa = sum(key(a) for a, _ in ps)
    return sum(key(b) for _, b in ps) / sa if sa else float('nan')


def metric_block(usable, key, rng):
    logs = [math.log(key(b) / key(a)) for a, b in usable]
    try:
        w = wilcoxon(logs).pvalue if len(logs) >= 6 and any(logs) else None
    except ValueError:
        w = None
    ci = cluster_boot(usable, lambda s: rot(s, key), rng)
    return {'total_A': round(sum(key(a) for a, _ in usable), 4), 'total_B': round(sum(key(b) for _, b in usable), 4),
            'change_ratio_of_totals': round(rot(usable, key) - 1, 4),
            'change_ci95_task_cluster_boot': [round(x - 1, 4) for x in ci] if ci else None,
            'median_paired_change': round(statistics.median(math.exp(x) - 1 for x in logs), 4),
            'geomean_ratio_change': round(math.exp(statistics.mean(logs)) - 1, 4),
            'pairs_B_lower': sum(x < 0 for x in logs), 'wilcoxon_p_logratio': None if w is None else float(f'{w:.3g}')}


def contrast(ps, rng):
    usable = [(a, b) for a, b in ps if a['has_result'] and b['has_result'] and cost(a) > 0 and cost(b) > 0]
    n = len(usable)
    if n == 0:
        return {'n_pairs': 0}
    sa, sb = sum(a['verified'] for a, _ in usable), sum(b['verified'] for _, b in usable)
    loss = sum(a['verified'] and not b['verified'] for a, b in usable)
    win = sum(b['verified'] and not a['verified'] for a, b in usable)
    mcn = binomtest(win, win + loss).pvalue if win + loss else 1.0
    dsucc = (sb - sa) / n
    both = [(a, b) for a, b in usable if a['verified'] and b['verified']]
    cc = lambda r: r['tokens']['cache_creation']  # noqa: E731
    cr = lambda r: r['tokens']['cache_read']  # noqa: E731
    calls = lambda r: r.get('n_tool_calls') or sum(r['tool_calls'].values())  # noqa: E731
    return {
        'n_pairs': n, 'n_tasks': len({a['task'] for a, _ in usable}), 'dropped_pairs_no_result': len(ps) - n,
        'cost_usd': metric_block(usable, cost, rng),
        'tokens_billed_total': metric_block(usable, tok, rng),
        'cache_creation_change_ratio_of_totals': round(rot(usable, cc) - 1, 4),
        'cache_read_change_ratio_of_totals': round(rot(usable, cr) - 1, 4),
        'wall_s_change_ratio_of_totals': round(rot(usable, lambda r: r['wall_s']) - 1, 4),
        'tool_calls_mean_A': round(statistics.mean(calls(a) for a, _ in usable), 1),
        'tool_calls_mean_B': round(statistics.mean(calls(b) for _, b in usable), 1),
        'obspack_packs_mean_B': round(statistics.mean(b['obspack_packs'] for _, b in usable), 2),
        'z0obs_recalls_mean_B': round(statistics.mean(b.get('z0obs_recalls', 0) for _, b in usable), 2),
        'success_A': f'{sa}/{n}', 'success_B': f'{sb}/{n}', 'success_diff': round(dsucc, 4),
        'discordant_A_only': loss, 'discordant_B_only': win, 'mcnemar_exact_p': float(f'{mcn:.3g}'),
        'quality_guard': 'PASS' if (dsucc >= -0.05 and not (mcn < 0.05 and loss > win)) else 'FAIL',
        'cost_change_both_verified': round(rot(both, cost) - 1, 4) if both else None, 'n_both_verified': len(both),
        'cost_per_success_A': round(sum(cost(a) for a, _ in usable) / sa, 4) if sa else None,
        'cost_per_success_B': round(sum(cost(b) for _, b in usable) / sb, 4) if sb else None,
    }


STRATA = {
    'long_pooled': lambda r: r['family'] in LONG,  # primary population: the 10 pre-registered long tasks
    'long_cold': lambda r: r['family'] in LONG and r['rep'] == 0,
    'long_warm': lambda r: r['family'] in LONG and r['rep'] > 0,
    'all_tasks': lambda r: True,
    'family_directed_recall': lambda r: r['family'] == 'directed_recall',
    'family_multi_bug_debug': lambda r: r['family'] == 'multi_bug_debug',
    'family_log_forensics': lambda r: r['family'] == 'log_forensics',
    'anchor_v1_long_recall': lambda r: r['task'] == ANCHOR,
}


def arm_table(rows):
    out = {}
    for arm in ARMS:
        rs = [r for r in rows if r['arm'] == arm and r['family'] in LONG]
        if not rs:
            continue
        out[arm] = {'trials': len(rs), 'verified': sum(r['verified'] for r in rs),
                    'no_result': sum(not r['has_result'] for r in rs), 'rate_limited': sum(bool(r.get('rate_limited')) for r in rs),
                    'cost_usd_total': round(sum(cost(r) for r in rs), 3), 'tokens_total': sum(tok(r) for r in rs),
                    'cache_read_total': sum(r['tokens']['cache_read'] for r in rs),
                    'cache_creation_total': sum(r['tokens']['cache_creation'] for r in rs),
                    'output_total': sum(r['tokens']['output'] for r in rs),
                    'tool_calls_median': statistics.median(r['n_tool_calls'] for r in rs),
                    'tool_calls_mean': round(statistics.mean(r['n_tool_calls'] for r in rs), 1),
                    'turns_mean': round(statistics.mean(r['num_turns'] or 0 for r in rs), 1),
                    'obspack_packs_total': sum(r['obspack_packs'] for r in rs),
                    'z0obs_recalls_total': sum(r.get('z0obs_recalls', 0) for r in rs),
                    'wall_s_median': round(statistics.median(r['wall_s'] for r in rs), 1)}
    return out


def per_task(rows):
    out = {}
    for t in sorted({r['task'] for r in rows}):
        cell = {}
        for arm in ARMS:
            rs = [r for r in rows if r['task'] == t and r['arm'] == arm]
            if rs:
                cell[arm] = {'n': len(rs), 'verified': sum(r['verified'] for r in rs),
                             'cost_usd_mean': round(statistics.mean(cost(r) for r in rs), 4),
                             'tokens_mean': round(statistics.mean(tok(r) for r in rs)),
                             'tool_calls_mean': round(statistics.mean(r['n_tool_calls'] for r in rs), 1),
                             'packs_mean': round(statistics.mean(r['obspack_packs'] for r in rs), 1),
                             'recalls_mean': round(statistics.mean(r.get('z0obs_recalls', 0) for r in rs), 1)}
        r0 = next(r for r in rows if r['task'] == t)
        out[t] = {'repo': r0['repo'], 'family': r0['family'], 'arms': cell}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('raw', nargs='+')
    ap.add_argument('--tag', default='main')
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    rows = load(args.raw, args.tag)
    rng = random.Random(SEED)
    res = {'schema': 'z0int.obspack_long_v1.results', 'n_trials': len(rows), 'primary_metric': 'cost_usd',
           'arms': arm_table(rows), 'contrasts': {}, 'per_task': per_task(rows)}
    for sname, pred in STRATA.items():
        res['contrasts'][sname] = {}
        for cid, a, b, role in CONTRASTS:
            c = contrast(pairs(rows, a, b, pred), rng)
            c.update({'A': a, 'B': b, 'role': role})
            res['contrasts'][sname][cid] = c
    text = json.dumps(res, indent=1)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / 'results.json').write_text(text + '\n')
        cols = ['task', 'repo', 'family', 'arm', 'rep', 'warm', 'verified', 'has_result', 'is_error', 'rate_limited', 'cost_usd',
                'billed_total', 'input', 'cache_read', 'cache_creation', 'output', 'num_turns', 'tool_calls', 'obspack_packs',
                'z0obs_recalls', 'wall_s']
        with (out / 'trials.csv').open('w') as fh:
            fh.write(','.join(cols) + '\n')
            for r in sorted(rows, key=lambda r: (r['task'], r['arm'], r['rep'])):
                t = r['tokens']
                vals = [r['task'], r['repo'], r['family'], r['arm'], r['rep'], int(r['warm']), int(r['verified']), int(r['has_result']),
                        int(bool(r.get('is_error'))), int(bool(r.get('rate_limited'))), r['cost_usd'] if r['cost_usd'] is not None else '',
                        t['billed_total'], t['input'], t['cache_read'], t['cache_creation'], t['output'], r['num_turns'] or 0,
                        r['n_tool_calls'], r['obspack_packs'], r.get('z0obs_recalls', 0), r['wall_s']]
                fh.write(','.join(map(str, vals)) + '\n')
    for sname in STRATA:
        print(f'\n== {sname}')
        for cid, c in res['contrasts'][sname].items():
            if not c.get('n_pairs'):
                continue
            k, t = c['cost_usd'], c['tokens_billed_total']
            print(f"{cid} {c['A']:>6} -> {c['B']:<14} n={c['n_pairs']:>3} cost {k['change_ratio_of_totals']:+.1%} "
                  f"CI{k['change_ci95_task_cluster_boot']} lower {k['pairs_B_lower']}/{c['n_pairs']} p={k['wilcoxon_p_logratio']} | "
                  f"tok {t['change_ratio_of_totals']:+.1%} CI{t['change_ci95_task_cluster_boot']} p={t['wilcoxon_p_logratio']} | "
                  f"calls {c['tool_calls_mean_A']}->{c['tool_calls_mean_B']} packs {c['obspack_packs_mean_B']} | "
                  f"succ {c['success_A']}->{c['success_B']} guard={c['quality_guard']}")


if __name__ == '__main__':
    main()
