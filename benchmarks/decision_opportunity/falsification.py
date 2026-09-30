"""z0int#53 falsification: does DecisionOpportunity add decision value over the State Packet alone?

Pre-registered (committed before any run): cohort = pinned held-out current-work questions
(benchmarks/state_packet/questions_pinned.json, fixtures under ~/.z0int/.../pinned). No learning, no
model: every arm is deterministic, so this scores STATE SUFFICIENCY independently of any learner.

Arms
  packet_alone  : the packet's own repo-level decision.mode (ACT | OBSERVE)
  do_repo       : deterministic_gate(DecisionOpportunity, scoped=False)
  do_question   : deterministic_gate(DecisionOpportunity, scoped=True)

Gold (strict, primary):  answer -> {ACT};  abstain -> {ASK, ABSTAIN, OBSERVE};  conflict -> {ESCALATE}
Gold (lenient, secondary): as strict, but conflict also accepts ASK (asking the user to resolve).
Decision rule: #53 survives only if do_question beats packet_alone on strict accuracy AND does not
lose any abstain/conflict item that packet_alone got right.
"""
import argparse
import json
from pathlib import Path

from z0int.decision_opportunity import build_decision_opportunity, deterministic_gate
from z0int.state_packet import build_state_packet

ROOT = Path(__file__).resolve().parents[2]
PINNED = Path('~/.z0int/research/claude-code-overnight/pinned').expanduser()
STRICT = {'answer': {'ACT'}, 'abstain': {'ASK', 'ABSTAIN', 'OBSERVE'}, 'conflict': {'ESCALATE'}}
LENIENT = {**STRICT, 'conflict': {'ESCALATE', 'ASK'}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    qs = json.loads((ROOT / 'benchmarks/state_packet/questions_pinned.json').read_text())['questions']
    packets, rows = {}, []
    for q in qs:
        if q['repo'] not in packets:
            packets[q['repo']] = build_state_packet(PINNED / q['repo'], projects_root=PINNED / 'projects')
        pkt = packets[q['repo']]
        gold = q['key']['action']
        preds = {
            'packet_alone': 'ACT' if pkt['decision']['mode'] == 'ACT' else 'OBSERVE',
            'do_repo': deterministic_gate(build_decision_opportunity(PINNED / q['repo'], q['prompt'], packet=pkt, scoped=False)),
        }
        opp = build_decision_opportunity(PINNED / q['repo'], q['prompt'], packet=pkt, scoped=True)
        preds['do_question'] = deterministic_gate(opp)
        rows.append({'id': q['id'], 'kind': q.get('kind'), 'gold': gold, 'preds': preds,
                     'families': opp['scope'].get('families', []), 'scope_mode': opp['scope']['mode']})
    Path(args.out).write_text(''.join(json.dumps(r) + '\n' for r in rows))
    for arm in ('packet_alone', 'do_repo', 'do_question'):
        for name, rule in (('strict', STRICT), ('lenient', LENIENT)):
            ok = [r for r in rows if r['preds'][arm] in rule[r['gold']]]
            per = {g: f"{sum(1 for r in ok if r['gold'] == g)}/{sum(1 for r in rows if r['gold'] == g)}" for g in STRICT}
            print(f"{arm:13s} {name:7s} {len(ok)}/{len(rows)} {per}")


if __name__ == '__main__':
    main()
