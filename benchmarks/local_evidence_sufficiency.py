"""Score a local DecisionBackend on the frozen evidence_sufficiency set Jev was admitted on.

Same 48 items (benchmarks/data/authored144.jsonl, family evidence_interpretation), same
three options, same question, same metric (three-way accuracy). Output rows mirror
manifests/capability-evidence/jev-bench-authored144.jsonl so an admitted local route can
cite this file (path + sha256) as its eval_source.

  python benchmarks/local_evidence_sufficiency.py --backend decider_2b --out FILE.jsonl
"""
import argparse
import json
from pathlib import Path
import sys
import time

from z0int.backends import registry
from z0int.backends.base import DecisionOption, DecisionQuestion, DecisionRequest

ROOT = Path(__file__).resolve().parents[1]


def items():
    rows = [json.loads(l) for l in (ROOT / 'benchmarks/data/authored144.jsonl').read_text().splitlines() if l.strip()]
    return [r for r in rows if r['family'] == 'evidence_interpretation' and r['split'] == 'test']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--backend', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--limit', type=int)
    args = ap.parse_args()
    out = Path(args.out)
    done = {json.loads(l)['id'] for l in out.read_text().splitlines()} if out.exists() else set()  # resumable
    backend = registry.create_backend(args.backend)
    todo = [r for r in items() if r['id'] not in done][:args.limit]
    with out.open('a') as fh:
        for r in todo:
            q = DecisionQuestion(id='evidence', type='choice', instructions=r['question'],
                                 options=tuple(DecisionOption(o['id'], o['description']) for o in r['options']))
            t = time.perf_counter()
            res = backend.evaluate(DecisionRequest(state=r['state'], questions=(q,)))
            ans = res.answers[0]
            gold = r['options'][r['label']]['id']
            fh.write(json.dumps({'id': r['id'], 'family': r['family'], 'split': r['split'], 'label': r['label'], 'gold_id': gold,
                                 'option_ids': [o['id'] for o in r['options']], 'probabilities': ans.probabilities,
                                 'pred_id': ans.value, 'ms': (time.perf_counter() - t) * 1000, 'model': res.model,
                                 'backend': args.backend}) + '\n')
            fh.flush()
            print(r['id'], gold, ans.value, round((time.perf_counter() - t), 1), 's', flush=True)
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    print(f"{args.backend}: {sum(x['pred_id'] == x['gold_id'] for x in rows)}/{len(rows)} three-way accuracy")


if __name__ == '__main__':
    sys.exit(main())
