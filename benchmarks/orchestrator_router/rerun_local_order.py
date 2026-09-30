"""Addendum A1 (PREREG_addendum_local_order.md): deterministic arm re-scored under host `local_order`.

No model calls. Rebuilds S1/S2 with the current policy, hard-checks frozen inputs against results_v0.json,
then scores deterministic / cheapest / strongest with the v0 scorer and writes results_a1_local_order.json.

  .venv/bin/python benchmarks/orchestrator_router/rerun_local_order.py
"""
from __future__ import annotations

import importlib.util
import json
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('router_v0', HERE / 'run.py')
v0 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v0)
wr = v0.wr

V0 = json.loads((HERE / 'results_v0.json').read_text())
OUT = HERE / 'results_a1_local_order.json'
ARMS = ('deterministic', 'cheapest', 'strongest')
EXPECTED_CHANGED = {'host_override'}


def fail(msg):
    print('FROZEN-INPUT CHECK FAILED:', msg)
    OUT.write_text(json.dumps({'schema': 'z0int.orchestrator_router.results.a1', 'frozen_input_check': 'failed',
                               'reason': msg}, indent=1) + '\n')
    sys.exit(2)


def main():
    policy, providers = wr.configuration()
    s1, _, sizes, s1_files = v0.build_s1(policy, providers)
    s2, _, _ = v0.build_s2()
    for it in s1:
        it['cost_ms'] = dict(it['cost'])
    for it in s2:
        it['cost_ms'] = {c: it['cost'][c] * 1000 for c in it['cands']}
    items = s1 + s2

    # ---- frozen-input check vs v0
    inputs = {'authored144': v0.sha(v0.ROOT / 'benchmarks/data/authored144.jsonl'),
              'questions_pinned': v0.sha(v0.ROOT / 'benchmarks/state_packet/questions_pinned.json'),
              'worker_routing_policy': v0.sha(wr.POLICY_PATH),
              'host_override': v0.sha(v0.Z0 / 'config/worker_routing.local.json'),
              'action_selector_run': v0.sha(v0.ACTION_RUN),
              **{f's1_outcomes:{k}': v0.sha(v0.BENCH / f) for k, f in s1_files.items()}}
    for k, h in inputs.items():
        if k not in EXPECTED_CHANGED and V0['inputs'].get(k) != h:
            fail(f'input sha changed: {k}')
    if [v0.key(c) for c in s1[0]['cands']] != V0['inputs']['s1_legal_candidates']:
        fail('S1 legal candidates differ')
    if {v0.key(c): v for c, v in s1[0]['cost_ms'].items()} != V0['inputs']['s1_cost_ms']:
        fail('S1 costs differ')
    if s2[0]['cands'] != V0['inputs']['s2_legal_candidates'] or s2[0]['cost_ms'] != {k: v for k, v in V0['inputs']['s2_cost_ms'].items()}:
        fail('S2 candidates/costs differ')
    v0_rows = {(r['set'], r['id']): r for r in V0['items'] if r['set'] in ('S1', 'S2')}
    if set(v0_rows) != {(it['set'], it['id']) for it in items}:
        fail('item ids differ')
    for it in items:
        r = v0_rows[(it['set'], it['id'])]
        if r['gold'] != ([v0.key(g) for g in it['gold']] if it['gold'] else None):
            fail(f'gold differs on {it["id"]}')
        if r['passes'] != {v0.key(c): v for c, v in it['passes'].items()}:
            fail(f'passes differ on {it["id"]}')

    # ---- score with the v0 scorer (no raw rows are consulted for these arms)
    scored = v0.score(items, sizes, None, {})['arms']
    arms = {a: scored[a] for a in ARMS}
    v0_arms = {a: {s: V0['arms'][a][s] for s in ('S1', 'S2', 'pooled_labeled') if s in V0['arms'][a]} for a in ARMS}
    det_dist = {}
    for it in s1:
        det_dist[v0.key(it['det'])] = det_dist.get(v0.key(it['det']), 0) + 1
    changed = [it['id'] for it in items if v0.key(it['det']) != v0_rows[(it['set'], it['id'])]['deterministic']]
    d1 = arms['deterministic']['S1']
    reading = {'s1_pass_ge_0.85': d1['pass_rate'] >= 0.85,
               's1_regret_below_v0_cheapest': d1['mean_regret'] < V0['arms']['cheapest']['S1']['mean_regret']}
    reading['fix_confirmed'] = all(reading.values())

    # exploratory only (PREREG addendum): orch_think vs new deterministic, pooled labeled
    orch = [r for r in V0['items'] if r['gold'] and 'orch_think' in r]
    new_det = {(it['set'], it['id']): v0.key(it['det']) for it in items}
    o_vec = [(r['orch_think']['proposal'] or r['deterministic']) in r['gold'] for r in orch]
    d_vec = [new_det[(r['set'], r['id'])] in r['gold'] for r in orch]

    out = {'schema': 'z0int.orchestrator_router.results.a1', 'prereg': 'benchmarks/orchestrator_router/PREREG_addendum_local_order.md',
           'frozen_input_check': 'passed',
           'code_commit': subprocess.run(['git', '-C', str(HERE), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip(),
           'inputs': inputs, 'v0_host_override': V0['inputs']['host_override'],
           'policy_local_order': policy.get('local_order'),
           's1_deterministic_choice_distribution': det_dist, 'items_with_changed_deterministic_choice': len(changed),
           'arms_a1': arms, 'arms_v0': v0_arms, 'reading': reading,
           'exploratory_orch_think_vs_a1_deterministic': v0.mcnemar(o_vec, d_vec)}
    OUT.write_text(json.dumps(out, indent=1) + '\n')
    for a in ARMS:
        for s in ('S1', 'S2', 'pooled_labeled'):
            if s in arms[a]:
                n, o = arms[a][s], v0_arms[a].get(s, {})
                print(f"{a:13s} {s:15s} v0 {o.get('correct')}/{o.get('n')} pass {o.get('pass_rate', 0):.3f} regret {o.get('mean_regret', 0):.3f}"
                      f"  ->  A1 {n['correct']}/{n['n']} pass {n['pass_rate']:.3f} regret {n['mean_regret']:.3f}")
    print('S1 det distribution', det_dist, 'changed', len(changed), 'reading', reading)
    print('exploratory', out['exploratory_orch_think_vs_a1_deterministic'])
    print('wrote', OUT)


if __name__ == '__main__':
    main()
