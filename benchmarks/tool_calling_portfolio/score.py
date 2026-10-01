"""Score raw_v0.jsonl against the frozen items (decision rule in PREREG.md).

  python benchmarks/tool_calling_portfolio/score.py
"""
from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import PRIVATE, args_valid, extract_calls, load_items, score_item  # noqa: E402
from run import ARMS, META, RAW  # noqa: E402

COMPARATOR = 'qwen3_8b'
SUITES = ('z0_route', 'bfcl', 'action_selector')


def pct(v, q):
    v = sorted(v)
    if not v:
        return None
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(v[lo] + (v[hi] - v[lo]) * (k - lo), 1)


def mcnemar(a, b):
    """Exact two-sided McNemar on paired booleans. Returns (a_only, b_only, p)."""
    x = sum(1 for i, j in zip(a, b) if i and not j)
    y = sum(1 for i, j in zip(a, b) if j and not i)
    n = x + y
    if n == 0:
        return x, y, 1.0
    p = sum(math.comb(n, k) for k in range(0, min(x, y) + 1)) / 2 ** n * 2
    return x, y, float(f'{min(1.0, p):.3g}')


def main():
    items = {i['id']: i for i in load_items()}
    order = list(items)
    raw = [json.loads(line) for line in RAW.read_text().splitlines() if line.strip()]
    meta = json.loads(META.read_text()) if META.exists() else {}
    rows, by = [], {}
    for r in raw:
        it = items[r['id']]
        calls, path = ([], 'error') if r['error'] or not r['message'] else extract_calls({'choices': [{'message': r['message']}]})
        s = score_item(it, calls)
        t = r.get('timings') or {}
        row = {'arm': r['arm'], 'id': r['id'], 'suite': r['suite'], 'category': r['category'], 'exact': bool(s['exact']),
               'n_calls': len(calls), 'parse_path': path, 'args_valid': args_valid(calls, it['tools']) if calls else None,
               'wall_ms': r['wall_ms'], 'server_ms': round(t.get('prompt_ms', 0) + t.get('predicted_ms', 0), 1) if t else None,
               'prompt_tokens': (r.get('usage') or {}).get('prompt_tokens'),
               'completion_tokens': (r.get('usage') or {}).get('completion_tokens'),
               'finish_reason': r.get('finish_reason'), 'error': bool(r['error'])}
        row.update({k: v for k, v in s.items() if k not in ('exact', 'pred', 'called')})
        if it['suite'] == 'z0_route' and it['category'] == 'delegate_worker':
            # POST-HOC sensitivity (not the pre-registered score): the frozen gold compares `task` with the whole
            # 'Use provider P with model M for this: <subtask>' line; every arm put only <subtask> in `task`.
            alt = score_item({**it, 'request': it['request'].split('for this: ', 1)[1]}, calls)
            row['posthoc_exact'], row['posthoc_form_exact'] = bool(alt['exact']), bool(alt['form_exact'])
        else:
            row['posthoc_exact'], row['posthoc_form_exact'] = row['exact'], row.get('form_exact')
        if it['suite'] == 'action_selector':
            row['pred'] = s.get('pred')
        rows.append(row)
        by[(r['arm'], r['id'])] = row
    arms = [a for a in ARMS if any(k[0] == a for k in by)]
    res = {'schema': 'z0int.tool_calling_portfolio.results.v0', 'comparator': COMPARATOR, 'arms': {}}
    for a in arms:
        rs = [by[(a, i)] for i in order if (a, i) in by]
        d = {'n': len(rs), 'exact': sum(r['exact'] for r in rs)}
        d['exact_acc'] = round(d['exact'] / max(1, d['n']), 3)
        for s in SUITES:
            ss = [r for r in rs if r['suite'] == s]
            d[s] = {'n': len(ss), 'exact': sum(r['exact'] for r in ss), 'by_category': {}}
            for c in sorted({r['category'] for r in ss}):
                cs = [r for r in ss if r['category'] == c]
                d[s]['by_category'][c] = f"{sum(r['exact'] for r in cs)}/{len(cs)}"
        zr = [r for r in rs if r['suite'] == 'z0_route']
        d['z0_route']['form_exact'] = f"{sum(r.get('form_exact', False) for r in zr)}/{len(zr)}"
        d['z0_route']['function_ok_given_form'] = f"{sum(r.get('function_ok', False) for r in zr if r.get('form_exact'))}/{sum(r.get('form_exact', False) for r in zr)}"
        for k in ('tool_ok', 'ids_ok', 'task_ok', 'remote_ok'):
            d['z0_route'][k] = f"{sum(r.get(k, False) for r in zr)}/{len(zr)}"
        d['posthoc_sensitivity'] = {
            'note': 'NOT pre-registered: delegate_worker task fidelity checked against the subtask instead of the full request line',
            'pooled_exact': sum(r['posthoc_exact'] for r in rs),
            'z0_route_exact': sum(r['posthoc_exact'] for r in zr),
            'z0_route_form_exact': f"{sum(bool(r['posthoc_form_exact']) for r in zr)}/{len(zr)}"}
        irr = [r for r in rs if r['category'] == 'irrelevance']
        d['irrelevance_rejection'] = round(sum(r['exact'] for r in irr) / max(1, len(irr)), 3)
        rel = [r for r in rs if r['suite'] == 'bfcl' and r['category'] != 'irrelevance']
        d['bfcl_relevant_no_call'] = sum(r['n_calls'] == 0 for r in rel)
        called = [r for r in rs if r['n_calls']]
        d['args_valid'] = round(sum(bool(r['args_valid']) for r in called) / max(1, len(called)), 3)
        d['args_valid_n'] = f"{sum(bool(r['args_valid']) for r in called)}/{len(called)}"
        d['parse_paths'] = dict(Counter(r['parse_path'] for r in rs))
        d['errors'] = sum(r['error'] for r in rs)
        d['finish_length'] = sum(r['finish_reason'] == 'length' for r in rs)
        w = [r['wall_ms'] for r in rs if not r['error']]
        sv = [r['server_ms'] for r in rs if r['server_ms']]
        d['latency_ms'] = {'client_p50': pct(w, .5), 'client_p95': pct(w, .95), 'server_p50': pct(sv, .5), 'server_p95': pct(sv, .95)}
        ac = [r for r in rs if r['suite'] == 'action_selector']
        d['action_selector']['pred_counts'] = dict(Counter(str(r.get('pred')) for r in ac))
        m = meta.get(a, {})
        d['serving'] = {'cold_start_ms': m.get('cold_start_ms'), 'bench_vram_loaded_mib': m.get('gpu_loaded', {}).get('bench_mib'),
                        'bench_vram_peak_sampled_mib': m.get('bench_peak_mib_sampled'),
                        'gpu_total_before_mib': m.get('gpu_before', {}).get('total_mib'),
                        'gpu_total_loaded_mib': m.get('gpu_loaded', {}).get('total_mib'), 'measured_at': m.get('measured_at'),
                        'model': m.get('model')}
        res['arms'][a] = d
    # paired tests vs comparator
    if COMPARATOR in res['arms']:
        comp = res['arms'][COMPARATOR]
        for a in arms:
            if a == COMPARATOR:
                continue
            tests = {}
            for scope in ('pooled', *SUITES):
                ids = [i for i in order if (a, i) in by and (COMPARATOR, i) in by and (scope == 'pooled' or items[i]['suite'] == scope)]
                x, y, p = mcnemar([by[(a, i)]['exact'] for i in ids], [by[(COMPARATOR, i)]['exact'] for i in ids])
                tests[scope] = {'n': len(ids), 'arm_only': x, 'comparator_only': y, 'p': p,
                                'verdict': 'better' if x > y and p < 0.05 else 'worse' if y > x and p < 0.05 else 'not distinguishable'}
            d = res['arms'][a]
            d['vs_comparator'] = tests
            s = d['serving']
            cheap = (s['bench_vram_loaded_mib'] or 1e9) < (comp['serving']['bench_vram_loaded_mib'] or 0) and \
                (d['latency_ms']['client_p50'] or 1e9) <= (comp['latency_ms']['client_p50'] or 0)
            gates = {'pooled_within_5': d['exact'] >= comp['exact'] - 5,
                     'irrelevance_ge_0.80': d['irrelevance_rejection'] >= 0.80,
                     'z0_form_exact_ge_0.90': int(d['z0_route']['form_exact'].split('/')[0]) >= 0.90 * d['z0_route']['n'],
                     'args_valid_ge_0.95': d['args_valid'] >= 0.95, 'cheaper_vram_and_p50': cheap}
            d['eligibility'] = {'substitute_gates': gates, 'substitute_eligible': all(gates.values()),
                                'better_pooled': tests['pooled']['verdict'] == 'better'}
            d['eligibility']['shadow_eligible'] = d['eligibility']['substitute_eligible'] or d['eligibility']['better_pooled']
    (HERE / 'results_v0.json').write_text(json.dumps(res, indent=1) + '\n')
    (HERE / 'rows_v0.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    hdr = f"{'arm':26} {'pooled':>8} {'z0':>6} {'form':>6} {'bfcl':>6} {'irr':>5} {'act':>5} {'valid':>6} {'p50':>6} {'p95':>6} {'vram':>6}"
    print(hdr)
    for a, d in res['arms'].items():
        print(f"{a:26} {d['exact']:>4}/{d['n']:<3} {d['z0_route']['exact']:>3}/{d['z0_route']['n']:<2} {d['z0_route']['form_exact']:>6} "
              f"{d['bfcl']['exact']:>3}/{d['bfcl']['n']:<3}{d['irrelevance_rejection']:>5} {d['action_selector']['exact']:>2}/{d['action_selector']['n']:<2} "
              f"{d['args_valid']:>6} {d['latency_ms']['client_p50']!s:>6} {d['latency_ms']['client_p95']!s:>6} {d['serving']['bench_vram_loaded_mib']!s:>6}")
    print('private raw:', PRIVATE)


if __name__ == '__main__':
    main()
