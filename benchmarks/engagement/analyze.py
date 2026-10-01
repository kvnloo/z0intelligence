"""Pre-registered analysis for M2 / M4 / M5 (benchmarks/engagement/PREREG.md). Aggregates only.

  python benchmarks/engagement/analyze.py m2 RUN.jsonl [--recompute] > results_m2.json
  python benchmarks/engagement/analyze.py m4 RUN.jsonl > results_m4.json
  python benchmarks/engagement/analyze.py m5 RUN.jsonl > results_m5.json

--recompute re-derives M2's tokens-to-first-relevant-read from the session transcripts with the
current definition in run_engagement.first_relevant (the definition is frozen in the PREREG commit).
"""
from __future__ import annotations

import argparse, json, random, statistics, sys
from math import comb
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def boot_median_ci(xs, n=10000, seed=0):
    if not xs:
        return None
    rng = random.Random(seed)
    meds = sorted(statistics.median(rng.choices(xs, k=len(xs))) for _ in range(n))
    return [meds[int(0.025 * n)], meds[int(0.975 * n) - 1]]


def sign_test(xs):
    neg, pos = sum(x < 0 for x in xs), sum(x > 0 for x in xs)
    n = neg + pos
    if n == 0:
        return {'neg': 0, 'pos': 0, 'p_two_sided': 1.0}
    k = min(neg, pos)
    p = min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n)
    return {'neg': neg, 'pos': pos, 'p_two_sided': round(p, 6)}


def wilcoxon(xs):
    """Exact two-sided Wilcoxon signed-rank (zeros dropped, average ranks for ties)."""
    d = [x for x in xs if x != 0]
    n = len(d)
    if n == 0:
        return {'n': 0, 'W_plus': 0, 'p_two_sided': 1.0}
    order = sorted(range(n), key=lambda i: abs(d[i]))
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and abs(d[order[j + 1]]) == abs(d[order[i]]):
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    r2 = [int(round(2 * r)) for r in ranks]  # doubled ranks are integers
    wp2 = sum(r for r, x in zip(r2, d) if x > 0)
    total = sum(r2)
    dist = {0: 1}
    for r in r2:
        nxt = dict(dist)
        for s, c in dist.items():
            nxt[s + r] = nxt.get(s + r, 0) + c
        dist = nxt
    lo = min(wp2, total - wp2)
    p = min(1.0, 2 * sum(c for s, c in dist.items() if s <= lo) / 2 ** n)
    return {'n': n, 'W_plus': wp2 / 2, 'p_two_sided': round(p, 6)}


def paired(rows, a, b, key, by=('task', 'rep')):
    idx = {}
    for r in rows:
        idx.setdefault(tuple(r[k] for k in by), {})[r['arm']] = r
    diffs, pct, pairs = [], [], []
    for k, arms in sorted(idx.items()):
        if a in arms and b in arms:
            va, vb = key(arms[a]), key(arms[b])
            if va is None or vb is None:
                continue
            diffs.append(vb - va)
            if va:
                pct.append(100 * (vb - va) / va)
            pairs.append(k)
    return {'n_pairs': len(diffs), 'median_diff': statistics.median(diffs) if diffs else None,
            'median_diff_ci95': boot_median_ci(diffs), 'mean_diff': statistics.mean(diffs) if diffs else None,
            'median_pct': statistics.median(pct) if pct else None, 'median_pct_ci95': boot_median_ci(pct),
            'sign': sign_test(diffs), 'wilcoxon': wilcoxon(diffs)}


def arm_table(rows, arms, extra=()):
    out = {}
    for a in arms:
        rs = [r for r in rows if r['arm'] == a]
        costs = [r['cost_usd'] for r in rs if r['cost_usd'] is not None]
        out[a] = {'n': len(rs), 'verified': sum(bool(r['verified']) for r in rs),
                  'cost_total_usd': round(sum(costs), 4), 'cost_median_usd': round(statistics.median(costs), 5) if costs else None,
                  'errors': sum(bool(r.get('is_error')) or r.get('rc') not in (0,) for r in rs)}
        for name, fn in extra:
            vals = [fn(r) for r in rs if fn(r) is not None]
            out[a][name + '_median'] = statistics.median(vals) if vals else None
    return out


def load(paths):
    rows = []
    for p in paths:
        rows += [json.loads(l) for l in open(p) if l.strip()]
    return rows


def m2(rows, recompute):
    if recompute:
        import run_engagement as R
        spec = {t['id']: t for t in json.loads((HERE / 'tasks_m2.json').read_text())['tasks']}
        for r in rows:
            streams, _ = R.summarize_streams(r['session_id'])
            if streams:
                r['to_first_relevant'] = R.first_relevant(streams, spec[r['task']])
    a, b = 'stock', 'ws-packet'
    prim = lambda r: r['to_first_relevant']['tokens']
    res = {'measure': 'M2', 'arms': arm_table(rows, [a, b], [('tokens_to_first_relevant', prim),
                                                            ('root_billed_in', lambda r: r['root_billed_in'])]),
           'reached_first_relevant': {x: sum(r['to_first_relevant']['reached'] for r in rows if r['arm'] == x) for x in (a, b)},
           'packet_delivered': sum(bool(r['root_ctx'].get('workspace_packet')) for r in rows if r['arm'] == b),
           'stock_contaminated': sum(bool(r['root_ctx'].get('workspace_packet') or r['root_ctx'].get('state_packet')) for r in rows if r['arm'] == a),
           'ws_modified_runs': sum(not r['ws_unchanged'] for r in rows),
           'primary_tokens_to_first_relevant': paired(rows, a, b, prim),
           'secondary_weighted_to_first_relevant': paired(rows, a, b, lambda r: r['to_first_relevant']['weighted']),
           'secondary_cost_usd': paired(rows, a, b, lambda r: r['cost_usd']),
           'exploratory_by_packet_shown': {
               str(s): paired([r for r in rows if r['packet_shown'] == s], a, b, prim) for s in (True, False)},
           'per_task': {t: {x: {'verified': sum(r['verified'] for r in rows if r['task'] == t and r['arm'] == x),
                                'tokens_median': statistics.median([prim(r) for r in rows if r['task'] == t and r['arm'] == x] or [0])}
                            for x in (a, b)} for t in sorted({r['task'] for r in rows})}}
    p = res['primary_tokens_to_first_relevant']
    ci = p['median_diff_ci95']
    pass_ok = res['arms'][b]['verified'] >= res['arms'][a]['verified']
    res['decision'] = {'ci_excludes_zero': bool(ci and (ci[1] < 0 or ci[0] > 0)), 'direction': 'saving' if ci and ci[1] < 0 else ('cost' if ci and ci[0] > 0 else 'none'),
                       'pass_rate_not_lower': pass_ok,
                       'claim_saving': bool(ci and ci[1] < 0 and pass_ok)}
    return res


def m4(rows):
    a, b = 'm4-off', 'm4-on'
    res = {'measure': 'M4', 'arms': arm_table(rows, [a, b], [('sub_billed_in', lambda r: r['sub_billed_in']),
                                                            ('n_subagents', lambda r: r['n_subagents'])]),
           'subagents_total': {x: sum(r['n_subagents'] for r in rows if r['arm'] == x) for x in (a, b)},
           'subagents_with_packet': {x: sum(r['subagents_with_state_packet'] for r in rows if r['arm'] == x) for x in (a, b)},
           'runs_without_subagents': {x: sum(r['n_subagents'] == 0 for r in rows if r['arm'] == x) for x in (a, b)},
           'primary_sub_billed_in': paired(rows, a, b, lambda r: r['sub_billed_in']),
           'secondary_sub_weighted_in': paired(rows, a, b, lambda r: r['sub_weighted_in']),
           'secondary_cost_usd': paired(rows, a, b, lambda r: r['cost_usd']),
           'secondary_total_billed_in': paired(rows, a, b, lambda r: r['sub_billed_in'] + r['root_billed_in'])}
    ci = res['primary_sub_billed_in']['median_diff_ci95']
    pass_ok = res['arms'][b]['verified'] >= res['arms'][a]['verified']
    cost_med = res['secondary_cost_usd']['median_diff']
    res['decision'] = {'sub_tokens_ci_excludes_zero': bool(ci and (ci[1] < 0 or ci[0] > 0)),
                       'direction': 'saving' if ci and ci[1] < 0 else ('cost' if ci and ci[0] > 0 else 'none'),
                       'pass_rate_not_lower': pass_ok, 'cost_median_diff_le_0': cost_med is not None and cost_med <= 0,
                       'recommend_default_on': bool(ci and ci[1] < 0 and pass_ok and cost_med is not None and cost_med <= 0)}
    return res


def m5(rows):
    arms = ['stock', 'settings-lean', 'launcher-lean']
    prefix = lambda r: (r['first_request']['cc'] + r['first_request']['cr'] + r['first_request']['input']) if r.get('first_request') else None
    res = {'measure': 'M5', 'arms': arm_table(rows, arms, [('first_request_prefix_tokens', prefix)])}
    for phase, sel in (('cold', lambda r: not r['warm']), ('warm', lambda r: r['warm'])):
        rs = [r for r in rows if sel(r)]
        res[phase] = {'arms': arm_table(rs, arms),
                      **{f'{b}_vs_stock_cost': paired(rs, 'stock', b, lambda r: r['cost_usd']) for b in arms[1:]},
                      'launcher_vs_settings_lean_cost': paired(rs, 'settings-lean', 'launcher-lean', lambda r: r['cost_usd'])}
        sl, ll = res[phase]['settings-lean_vs_stock_cost']['median_pct'], res[phase]['launcher-lean_vs_stock_cost']['median_pct']
        res[phase]['settings_lean_recovers_fraction'] = (sl / ll) if sl is not None and ll else None
    w = res['warm']
    ci = w['settings-lean_vs_stock_cost']['median_pct_ci95']
    res['decision'] = {'settings_lean_carries_over': bool(ci and ci[1] < 0 and w['arms']['settings-lean']['verified'] >= w['arms']['stock']['verified'])}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('measure', choices=['m2', 'm4', 'm5'])
    ap.add_argument('runs', nargs='+')
    ap.add_argument('--recompute', action='store_true')
    args = ap.parse_args()
    rows = load(args.runs)
    res = m2(rows, args.recompute) if args.measure == 'm2' else m4(rows) if args.measure == 'm4' else m5(rows)
    print(json.dumps(res, indent=1, default=lambda x: round(x, 4) if isinstance(x, float) else x))


if __name__ == '__main__':
    main()
