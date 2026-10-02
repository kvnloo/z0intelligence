"""z0int#55 v0: selective autonomy — when may the frontier complete a decision without the user?

Re-scores existing paired trials (pinned held-out cohort; Lane E + haiku runs: what the frontier
actually did, and whether it was right) under four autonomy policies. The frontier always does the
work; the policy only decides whether that completion is AUTONOMOUS or falls back to ASK/ESCALATE.

  escalate_always   never autonomous (the #55 floor)
  frontier          autonomous iff the frontier chose to answer
  gate              autonomous iff deterministic_gate(DecisionOpportunity) == ACT
  gate_and_frontier autonomous iff both agree

Metrics (per #55): coverage, false autonomous completion (acted AND wrong — counted separately
from abstention), selective accuracy, autonomy on non-answer gold items. No composite score.
"""
import argparse
import json
from pathlib import Path

from z0int.decision_opportunity import build_decision_opportunity, deterministic_gate
from z0int.state_packet import build_state_packet

ROOT = Path(__file__).resolve().parents[2]
PINNED = Path('~/.z0int/research/claude-code-overnight/pinned').expanduser()
POLICIES = ('escalate_always', 'frontier', 'gate', 'gate_and_frontier')


def gates():
    qs = json.loads((ROOT / 'benchmarks/state_packet/questions_pinned.json').read_text())['questions']
    packets, out = {}, {}
    for q in qs:
        if q['repo'] not in packets:
            packets[q['repo']] = build_state_packet(PINNED / q['repo'], projects_root=PINNED / 'projects', adapters=('git', 'docs', 'claude_code'))
        opp = build_decision_opportunity(PINNED / q['repo'], q['prompt'], packet=packets[q['repo']])
        out[q['id']] = {'gate': deterministic_gate(opp), 'gold': q['key']['action']}
    return out


def autonomous(policy, frontier_answered, gate):
    return {'escalate_always': False, 'frontier': frontier_answered, 'gate': gate == 'ACT',
            'gate_and_frontier': frontier_answered and gate == 'ACT'}[policy]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('runs', nargs='+')
    args = ap.parse_args()
    g = gates()
    for f in args.runs:
        rows = [json.loads(l) for l in Path(f).read_text().splitlines() if l.strip()]
        rows = [r for r in rows if r.get('arm') and r.get('question_id') in g]
        for arm in sorted({r['arm'] for r in rows}):
            rs = [r for r in rows if r['arm'] == arm]
            print(f"{Path(f).stem} / {arm} (n={len(rs)})")
            for pol in POLICIES:
                acted = [r for r in rs if autonomous(pol, (r.get('score') or {}).get('action') == 'answer', g[r['question_id']]['gate'])]
                wrong = [r for r in acted if not (r.get('score') or {}).get('correct')]
                off_gold = [r for r in acted if g[r['question_id']]['gold'] != 'answer']
                cov = len(acted) / len(rs)
                print(f"  {pol:18s} coverage {len(acted):2d}/{len(rs)} ({cov:.0%})  false_autonomous {len(wrong):2d}"
                      f"  selective_acc {(len(acted) - len(wrong))}/{len(acted) or 1}  acted_on_nonanswer_gold {len(off_gold)}")


if __name__ == '__main__':
    main()
