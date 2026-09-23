#!/usr/bin/env python3
"""Build the independent-critic pack for the real-question ruler (Part 4).

The critic must NOT see the binding logic. Each item therefore contains only:
  question, slot, proposed answer, cited evidence snippet, rival candidates.
For ambiguity cases the critic is asked which candidate (if any) the evidence
actually establishes -- that adjudication is the only model judgement used, and
it is used as *ground truth*, never as the retrieval operator.

Output: .work/real-q-critic-pack.json  (list of items, batched by the caller)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from gate_real_questions import snippet_for  # noqa: E402
from mine_real_questions import DEFAULT_DB  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gated", default=".work/real-q-gated.jsonl")
    ap.add_argument("--multi", default=".work/real-q-multi.jsonl")
    ap.add_argument("--out", default=".work/real-q-critic-pack.json")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--multi-limit", type=int, default=66)
    a = ap.parse_args()

    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    items: list[dict] = []

    for r in (json.loads(l) for l in Path(a.gated).read_text().splitlines() if l.strip()):
        if r["gate"] != "ACCEPT":
            continue
        items.append(
            {
                "id": f"R{len(items):03d}",
                "kind": "accepted",
                "question": r["question"],
                "slot_kind": r["slot_kind"],
                "proposed_answer": r["value"],
                "cited_evidence": {
                    "pointer": r["evidence_pointer"],
                    "tool": r.get("tool_name", ""),
                    "snippet": snippet_for(conn, r["evidence_pointer"], r["value"]),
                },
                "candidates": [r["value"]],
            }
        )

    # Ambiguity cases: real questions whose final answer named several slot-shaped
    # values, so the mechanical binder refused to bind. The critic adjudicates.
    n = 0
    for r in (json.loads(l) for l in Path(a.multi).read_text().splitlines() if l.strip()):
        if n >= a.multi_limit:
            break
        vals = r["multi_values"][:8]
        items.append(
            {
                "id": f"M{len(items):03d}",
                "kind": "ambiguous",
                "question": r["question"],
                "slot_kind": r["slot_kind"],
                "proposed_answer": None,
                "cited_evidence": {
                    "pointer": f"messages#{r['answer_ordinal']}",
                    "tool": "",
                    "snippet": r["answer_excerpt"][:1200],
                },
                "candidates": vals,
            }
        )
        n += 1

    Path(a.out).write_text(json.dumps(items, indent=2))
    print(json.dumps({"items": len(items), "accepted": sum(1 for i in items if i['kind'] == 'accepted'),
                      "ambiguous": sum(1 for i in items if i['kind'] == 'ambiguous')}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
