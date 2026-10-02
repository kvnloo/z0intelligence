#!/usr/bin/env python3
"""Export the real tool_family_select capability set as bench fixtures.

``results/julia-claims/capability-tool_family_select.jsonl`` (382 first-action
prompts, gold = the tool family the agent actually used) is the only z0 decision
set in the repo with >= 50 labelled examples per split. This renders one split in
the ``z0int backends bench`` fixture format so every roster candidate can be
scored on it with the canonical harness (``--fixtures``), using the same question
as ``z0_capability_compare.py``: instructions "Which tool family should handle
this request?" and the representation selected on DEV there (R1: descriptions).

    python benchmarks/julia_claims/export_bench_fixture.py --split sealed --out /tmp/tfs-sealed.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from z0_capabilities import LABELS, REPRESENTATIONS  # noqa: E402

DATA = HERE.parents[1] / "results" / "julia-claims" / "capability-tool_family_select.jsonl"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", default="sealed", choices=("dev", "validation", "sealed", "all"))
    ap.add_argument("--representation", default="R1", choices=tuple(REPRESENTATIONS))
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    render = REPRESENTATIONS[args.representation]
    options = [{"id": lab, "description": render(lab)} for lab in LABELS]
    n = 0
    with open(args.out, "w", encoding="utf-8") as fh:
        for line in DATA.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if args.split != "all" and row["split"] != args.split:
                continue
            fh.write(json.dumps({
                "id": f"tool_family_select/{row['split']}/{row['example_id']}",
                "capability": "tool_family_select",
                "provenance": f"julia-claims.capability-tool_family_select.v1:{row['split']}:{args.representation}",
                "state": row["state"],
                "question": {"id": "family", "type": "choice",
                             "instructions": "Which tool family should handle this request?",
                             "options": options},
                "gold": row["gold"],
                "allow_abstain": False,
                "abstain_option_id": "ABSTAIN",
            }, ensure_ascii=False) + "\n")
            n += 1
    print(f"wrote {n} fixtures to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
