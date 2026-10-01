"""Pre-registered analysis for claude-code-savings-v1b (see PREREG.md). Reuses v1's statistics
(ratio of totals, task-cluster bootstrap, Wilcoxon on log ratios, McNemar quality guard) and writes
aggregate-only results (no prompts, answers, session ids or paths).

    .venv/bin/python benchmarks/claude_code_v1b/analyze.py RAW/qa-main.jsonl RAW/repo-main.jsonl \
        [--probe RAW/route-probe.jsonl] --out benchmarks/claude_code_v1b/results
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'claude_code_v1'))
import analyze as A  # noqa: E402  (v1 statistics, unchanged)

A.ARMS = ['lean', 'lean+packet', 'lean+packet-gated']
SESSION, GATED, LEAN = 'lean+packet', 'lean+packet-gated', 'lean'
NI_MARGIN = 0.05
CONTRASTS = [  # (id, A, B, suite, role)
    ('P1', SESSION, GATED, 'qa', 'primary: non-inferiority, CI upper < +5%'),
    ('P2', SESSION, GATED, 'repo', 'primary: superiority, CI upper < 0 and Wilcoxon p < 0.05'),
    ('S1', LEAN, GATED, 'qa', 'secondary: gated keeps the qa saving vs lean'),
    ('S2', LEAN, GATED, 'repo', 'secondary: gated removes the repo overhead vs lean (equivalence +-5%)'),
    ('S3', LEAN, SESSION, 'qa', 'secondary: replication of v1 C3 qa (-35.5%)'),
    ('S4', LEAN, SESSION, 'repo', 'secondary: replication of v1 C3 repo (+10.8%)'),
]
STRATA = {'qa': A.STRATA['qa'], 'repo': A.STRATA['repo'], 'repo_cold': A.STRATA['repo_cold'],
          'repo_warm': A.STRATA['repo_warm'], 'repo_short': A.STRATA['repo_short'],
          'repo_gate_fired': lambda r: r['suite'] == 'repo' and r['task'] in GATE_FIRED_REPO,
          'repo_gate_silent': lambda r: r['suite'] == 'repo' and r['task'] not in GATE_FIRED_REPO}
# Frozen prediction (PREREG.md): repo tasks whose prompt names a packet fact family.
GATE_FIRED_REPO = {'evo-feature-capacity-max-calls', 'evo-nav-capacity-plan', 'tok-fix-trace-aggregate-doublecount',
                   'zint-long-backend-review', 'zint-nav-plugin-hooks'}


def verdicts(block):
    p1, p2 = block['qa']['P1'], block['repo']['P2']
    ci1, ci2 = p1['tokens_change_ci95_cluster_boot'], p2['tokens_change_ci95_cluster_boot']
    h1 = ci1[1] < NI_MARGIN and p1['quality_guard'] == 'PASS'
    h2 = ci2[1] < 0 and (p2['wilcoxon_p_logratio'] or 1) < 0.05 and p2['quality_guard'] == 'PASS'
    s2 = block['repo']['S2']
    ci = s2['tokens_change_ci95_cluster_boot']
    return {'H1_qa_noninferior': 'PASS' if h1 else 'FAIL', 'H2_repo_better': 'PASS' if h2 else 'FAIL',
            'primary': 'PASS' if h1 and h2 else 'FAIL',
            'S2_repo_equivalent_to_lean_within_5pct': 'YES' if (-NI_MARGIN < ci[0] and ci[1] < NI_MARGIN) else 'NO'}


def gate_table(rows):
    out = {}
    for arm in A.ARMS:
        for s in ('qa', 'repo'):
            rs = [r for r in rows if r['arm'] == arm and r['suite'] == s]
            if rs:
                out[f'{s}/{arm}'] = {'trials': len(rs), 'gate_fired': sum(bool(r.get('gate_families')) for r in rs),
                                     'gate_injected': sum(bool(r.get('gate_injected')) for r in rs),
                                     'gate_chars_median_when_injected': statistics.median(
                                         [r['gate_chars'] for r in rs if r.get('gate_injected')] or [0]),
                                     'sessionstart_packet_seen': sum(r['packet_hook_bytes'] > 0 for r in rs)}
    return out


def probe_summary(path):
    rows = [json.loads(l) for l in Path(path).expanduser().read_text().splitlines() if l.strip()]
    return {'n': len(rows), 'mcp_config_passed': sum(r['argv_has_mcp_config'] for r in rows),
            'route_worker_listed': sum(bool(r['route_tools_listed']) for r in rows),
            'listed_tool_names': sorted({t for r in rows for t in r['route_tools_listed']}),
            'mcp_servers_seen': sorted({json.dumps(m, sort_keys=True) for r in rows for m in r['mcp_servers'] or []}),
            'sessions_that_called_route_worker': sum(r['route_worker_calls'] > 0 for r in rows),
            'route_results': [x for r in rows for x in r['route_results']],
            'cost_usd_total': round(sum(r['cost_usd'] or 0 for r in rows), 3),
            'note': 'exploratory availability/callability check; no savings claim'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('raw', nargs='+')
    ap.add_argument('--tag', default='main')
    ap.add_argument('--probe', default=None)
    ap.add_argument('--out', default=None)
    args = ap.parse_args()
    rows = A.load(args.raw, args.tag)
    rng = random.Random(A.SEED)
    res = {'schema': 'z0int.claude_code_savings.v1b.results', 'n_trials': len(rows), 'arms': A.arm_table(rows),
           'gate': gate_table(rows), 'contrasts': {}, 'per_task': A.per_task(rows)}
    for sname, pred in STRATA.items():
        block = {}
        for cid, a, b, suite, role in CONTRASTS:
            if not sname.startswith(suite):
                continue
            c = A.contrast(A.pairs(rows, a, b, lambda r, p=pred, s=suite: p(r) and r['suite'] == s), rng)
            c.update({'A': a, 'B': b, 'role': role})
            block[cid] = c
        res['contrasts'][sname] = block
    res['verdicts'] = verdicts(res['contrasts'])
    if args.probe:
        res['route_probe'] = probe_summary(args.probe)
    text = json.dumps(res, indent=1)
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / 'results.json').write_text(text + '\n')
        with (out / 'trials.csv').open('w') as fh:
            cols = ['suite', 'task', 'repo', 'family', 'arm', 'rep', 'warm', 'verified', 'has_result', 'is_error',
                    'billed_total', 'input', 'cache_read', 'cache_creation', 'output', 'cost_usd', 'num_turns',
                    'tool_calls', 'packet_seen', 'gate_families', 'gate_injected', 'gate_chars', 'wall_s']
            fh.write(','.join(cols) + '\n')
            for r in sorted(rows, key=lambda r: (r['suite'], r['task'], r['arm'], r['rep'])):
                t = r['tokens']
                vals = [r['suite'], r['task'], r['repo'], r['family'], r['arm'], r['rep'], int(r['warm']), int(r['verified']),
                        int(r['has_result']), int(bool(r.get('is_error'))), t['billed_total'], t['input'], t['cache_read'],
                        t['cache_creation'], t['output'], r['cost_usd'] if r['cost_usd'] is not None else '',
                        r['num_turns'] or 0, sum(r['tool_calls'].values()), int(r['packet_hook_bytes'] > 0),
                        '|'.join(r.get('gate_families') or []), '|'.join(r.get('gate_injected') or []),
                        r.get('gate_chars', 0), r['wall_s']]
                fh.write(','.join(map(str, vals)) + '\n')
    for sname, block in res['contrasts'].items():
        print(f'\n== {sname}')
        for cid, c in block.items():
            if not c.get('n_pairs'):
                continue
            print(f"{cid} {c['A']:>12} -> {c['B']:<18} n={c['n_pairs']:>3} tok {c['tokens_change_ratio_of_totals']:+.1%} "
                  f"CI{c['tokens_change_ci95_cluster_boot']} med {c['tokens_median_paired_change']:+.1%} "
                  f"cheaper {c['pairs_B_cheaper']}/{c['n_pairs']} p={c['wilcoxon_p_logratio']} "
                  f"cost {c['cost_change_ratio_of_totals']} succ {c['success_A']}->{c['success_B']} guard={c['quality_guard']}")
    print('\nverdicts', json.dumps(res['verdicts']))
    print('gate', json.dumps(res['gate'], indent=1))
    if args.probe:
        print('probe', json.dumps(res['route_probe'], indent=1))


if __name__ == '__main__':
    main()
