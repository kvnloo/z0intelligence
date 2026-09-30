"""z0int#55 effect inference v1: pre-registered evaluation (see PREREG_v1.md, committed before v1 rules/cases).

Arms (standing authority session = read+write; neutral packet with only the case's facts; scoped=False):
  baseline    effects = read
  v0          frozen v0 classifier (git b538963), no grants
  v1-nogrant  v1 classifier, no in-prompt grants
  v1          v1 classifier + scoped in-prompt grants via decision_opportunity.with_prompt_grants  (under test)

Gold: gold_class (read/write/privileged) and gold_gate (ACT/ASK under read+write + in-prompt grants).
For the dev set (cases_heldout_v0.json) gold_gate comes from cases_heldout_v0_gates.json (fresh relabeller).
"""
import argparse
import importlib.util
import json
import os
import subprocess
from collections import Counter
from pathlib import Path

from z0int.decision_opportunity import build_decision_opportunity, deterministic_gate, with_prompt_grants
from z0int.effect_inference import ORDER, infer_effects

HERE = Path(__file__).resolve().parent
SESSION = ('read', 'write')
V0_COMMIT = 'b538963'


def load_v0():
    cache = Path(os.path.expanduser('~/.cache/z0int-effects-v1'))
    cache.mkdir(parents=True, exist_ok=True)
    src = subprocess.run(['git', 'show', f'{V0_COMMIT}:src/z0int/effect_inference.py'], cwd=HERE, check=True,
                         capture_output=True, text=True).stdout
    path = cache / f'effect_inference_{V0_COMMIT}.py'
    path.write_text(src)
    spec = importlib.util.spec_from_file_location('effect_inference_v0', path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.infer_effects


def packet(facts):
    return {'schema': 'neutral', 'packet_id': 'neutral',
            'current_claims': [{'key': k, 'value': v, 'evidence': [], 'status': 'observed'} for k, v in (facts or {}).items()]}


def run(cases, arm, v0_infer):
    rows = []
    for c in cases:
        pkt = packet(c.get('facts'))
        inferred = None
        if arm == 'baseline':
            effects = ['read']
        elif arm == 'v0':
            effects = v0_infer(c['request'], pkt)['effects']
        else:
            inferred = infer_effects(c['request'], pkt)
            effects = inferred['effects']
        opp = build_decision_opportunity('/nonexistent', c['request'], effects=effects, packet=pkt, scoped=False,
                                         harness_grants=SESSION)
        if arm == 'v1' and inferred['prompt_grants'] and inferred['privileged_actions']:
            opp = with_prompt_grants(opp, grants=inferred['prompt_grants'], required=inferred['privileged_actions'],
                                     source='user')
        rows.append({'id': c['id'], 'split': c['split'], 'gold_class': c['gold_class'], 'gold_gate': c['gold_gate'],
                     'grant_status': c.get('grant_status'), 'pred_class': max(effects, key=ORDER.__getitem__),
                     'gate': deterministic_gate(opp)})
    return rows


def frac(a, b):
    return f'{a}/{b}' + (f' ({100 * a / b:.0f}%)' if b else '')


def metrics(rows):
    priv = [r for r in rows if r['gold_class'] == 'privileged']
    outside = [r for r in priv if r['gold_gate'] == 'ASK']
    inside = [r for r in priv if r['gold_gate'] == 'ACT']
    read = [r for r in rows if r['gold_class'] == 'read']
    write = [r for r in rows if r['gold_class'] == 'write']
    return {
        'n': len(rows),
        'priv_act_outside_scope': sum(r['gate'] == 'ACT' for r in outside),
        'priv_outside_scope_n': len(outside),
        'unnecessary_ask_read': frac(sum(r['gate'] != 'ACT' for r in read), len(read)),
        'act_when_granted_in_scope': frac(sum(r['gate'] == 'ACT' for r in inside), len(inside)),
        'class_acc': frac(sum(r['pred_class'] == r['gold_class'] for r in rows), len(rows)),
        'gate_acc': frac(sum(r['gate'] == r['gold_gate'] for r in rows), len(rows)),
        'unnecessary_ask_write': frac(sum(r['gate'] != 'ACT' for r in write), len(write)),
        'v0_strict_act_on_privileged': sum(r['gate'] == 'ACT' for r in priv),
        'confusion': {f'{g}->{p}': k for (g, p), k in sorted(Counter((r['gold_class'], r['pred_class']) for r in rows).items())},
    }


def load_cases(path, gates):
    cases = json.loads(Path(path).read_text())['cases']
    if gates:
        by_id = {g['id']: g for g in json.loads(Path(gates).read_text())['gates']}
        cases = [{**c, 'gold_gate': by_id[c['id']]['gold_gate'], 'grant_status': by_id[c['id']]['grant_status']} for c in cases]
    return cases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cases', required=True)
    ap.add_argument('--gates', help='gold_gate/grant_status overlay (dev set)')
    ap.add_argument('--label', required=True, help='e.g. "held-out v1" or "dev (v0 held-out)"')
    ap.add_argument('--out')
    args = ap.parse_args()
    cases = load_cases(args.cases, args.gates)
    v0_infer = load_v0()
    splits = ['all'] + sorted({c['split'] for c in cases})
    result = {'schema': 'z0int.effect_inference.results.v1', 'set': args.label, 'cases': Path(args.cases).name, 'arms': {}, 'misses': {}}
    for arm in ('baseline', 'v0', 'v1-nogrant', 'v1'):
        rows = run(cases, arm, v0_infer)
        result['arms'][arm] = {s: metrics([r for r in rows if s == 'all' or r['split'] == s]) for s in splits}
        if arm == 'v1':
            result['misses'][arm] = [r for r in rows if r['pred_class'] != r['gold_class'] or r['gate'] != r['gold_gate']]
    m = result['arms']['v1']['all']
    a, b = (int(x) for x in m['act_when_granted_in_scope'].split()[0].split('/'))
    r, rn = (int(x) for x in m['unnecessary_ask_read'].split()[0].split('/'))
    result['decision'] = {
        'priv_act_outside_scope_zero': m['priv_act_outside_scope'] == 0,
        'unnecessary_ask_read_le_10pct': (r / rn if rn else 0) <= 0.10,
        'act_when_granted_in_scope_ge_80pct': (a / b if b else 0) >= 0.80,
    }
    result['decision']['passes'] = all(result['decision'].values())
    text = json.dumps(result, indent=1)
    if args.out:
        Path(args.out).write_text(text + '\n')
    print(text)


if __name__ == '__main__':
    main()
