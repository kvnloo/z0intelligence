"""ACT | OBSERVE_MORE | ESCALATE from a State Packet (evolution-lab#24 pilot, z0int#22 cohort).

Can a local decision backend, given the rendered State Packet and the question, choose the
right next move before any frontier call? Gold comes from the drift-proof pinned key
(answer -> ACT, abstain -> OBSERVE_MORE, conflict -> ESCALATE). #24 order: rule baselines
first, then local backends; every row keeps its per-class outcome and confidence.

  python benchmarks/state_packet/action_selector.py --backends julia_1 laya_421m --out FILE.jsonl
"""
import argparse
import json
from pathlib import Path
import time

from z0int.backends import registry
from z0int.backends.base import DecisionOption, DecisionQuestion, DecisionRequest
from z0int.state_packet import build_state_packet, render_additional_context

HERE = Path(__file__).resolve().parent
PINNED = Path('~/.z0int/research/claude-code-overnight/pinned').expanduser()
GOLD = {'answer': 'ACT', 'abstain': 'OBSERVE_MORE', 'conflict': 'ESCALATE'}
OPTIONS = (
    DecisionOption('ACT', 'The supplied state is sufficient: answer directly from it.'),
    DecisionOption('OBSERVE_MORE', 'A required fact is missing or unavailable: say it is unknown or observe more before answering.'),
    DecisionOption('ESCALATE', 'Sources conflict about the answer: surface the conflict instead of picking a winner.'),
)
QUESTION = 'Given this state and the user question, which next move is correct?'


def cohort():
    qs = json.loads((HERE / 'questions_pinned.json').read_text())['questions']
    packets = {}
    for q in qs:
        if q['repo'] not in packets:
            pkt = build_state_packet(PINNED / q['repo'], projects_root=PINNED / 'projects')
            packets[q['repo']] = render_additional_context(pkt, max_tokens=1500)
        yield q, packets[q['repo']]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--backends', nargs='+', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()
    items = list(cohort())
    rows = []
    for q, ctx in items:
        gold = GOLD[q['key']['action']]
        rows.append({'backend': 'rule_always_act', 'id': q['id'], 'gold': gold, 'pred': 'ACT', 'conf': 1.0})
    for name in args.backends:
        backend = registry.create_backend(name)
        for q, ctx in items:
            gold = GOLD[q['key']['action']]
            state = f"{ctx}\n\nUser question: {q['prompt']}"
            t = time.perf_counter()
            try:
                res = backend.evaluate(DecisionRequest(state=state, questions=(DecisionQuestion(
                    id='next', type='choice', instructions=QUESTION, options=OPTIONS),)))
                a = res.answers[0]
                rows.append({'backend': name, 'id': q['id'], 'gold': gold, 'pred': a.value,
                             'conf': max(a.probabilities.values()), 'probs': a.probabilities,
                             's': round(time.perf_counter() - t, 2)})
            except Exception as exc:  # a backend that cannot run is a recorded result, not a crash
                rows.append({'backend': name, 'id': q['id'], 'gold': gold, 'pred': None, 'error': f'{type(exc).__name__}: {exc}'[:200]})
            print(name, q['id'], gold, rows[-1].get('pred'), rows[-1].get('s'), flush=True)
    Path(args.out).write_text(''.join(json.dumps(r) + '\n' for r in rows))
    for name in ['rule_always_act', *args.backends]:
        rs = [r for r in rows if r['backend'] == name]
        acc = sum(r['pred'] == r['gold'] for r in rs)
        per = {g: f"{sum(r['pred'] == g for r in rs if r['gold'] == g)}/{sum(r['gold'] == g for r in rs)}" for g in GOLD.values()}
        confident_wrong = sum(1 for r in rs if r.get('pred') and r['pred'] != r['gold'] and r.get('conf', 0) >= 0.9)
        print(f"{name}: {acc}/{len(rs)} recall {per} confident_errors {confident_wrong}")


if __name__ == '__main__':
    main()
