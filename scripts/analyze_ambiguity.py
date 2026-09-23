#!/usr/bin/env python3
"""Part 6/7: quantify candidate ambiguity and test whether deterministic
provenance signals can pick the answer out of the plausible set.

Two questions:
  A. What does the candidate set actually look like around a real question?
  B. Do interaction/provenance signals separate the true candidate from rivals,
     or do the rivals arrive through the *same* event?

For (B) the answer only has meaning if the true candidate is known, so this runs
on the critic-frozen ruler; the wider ambiguity corpus is used for (A) only.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from mine_real_questions import (  # noqa: E402
    DEFAULT_DB,
    EDIT_TOOLS,
    READ_TOOLS,
    URL_SPAN,
    content_tokens,
    load_session,
    slot_values,
)

CONTEXT_WINDOW = 140
STOPISH = set("is are was were the a an and or of to in on for with from at by be which what where "
              "did do does we us our use used using file files".split())


def label_terms(text: str, value: str) -> list[str]:
    """Content words near the value -- the local attribution, not provenance."""
    i = text.find(value)
    if i < 0:
        return []
    lo = max(0, i - CONTEXT_WINDOW)
    hi = min(len(text), i + len(value) + CONTEXT_WINDOW)
    return sorted({t.lower() for t in re.findall(r"[A-Za-z][\w.\-]{2,}", text[lo:hi])
                   if t.lower() not in STOPISH})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--multi", default=".work/real-q-multi.jsonl")
    ap.add_argument("--frozen", default="benchmarks/fixtures/resolve-real-v1/dev.jsonl")
    ap.add_argument("--out", default=".work/ambiguity-analysis.json")
    ap.add_argument("--db", default=DEFAULT_DB)
    a = ap.parse_args()

    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    multi = [json.loads(l) for l in (ROOT / a.multi).read_text().splitlines() if l.strip()]
    frozen = [json.loads(l) for l in (ROOT / a.frozen).read_text().splitlines() if l.strip()]

    # ---------- A. shape of the ambiguity corpus -------------------------------
    shape = Counter()
    per_slot: dict[str, Counter] = {}
    for m in multi:
        vals = m["multi_values"]
        shape["cases"] += 1
        shape["total_candidates"] += len(vals)
        ps = per_slot.setdefault(m["slot_kind"], Counter())
        ps["cases"] += 1
        ps["candidates"] += len(vals)

    # ---------- B. can provenance separate truth from rivals? ------------------
    findings = []
    for row in frozen:
        sid = row["session_id"]
        if not sid:
            continue
        msgs, tcs = load_session(conn, sid)
        by_ord = {m["ordinal"]: m for m in msgs}
        # locate the evidence message that carried the answer
        ptr = row["evidence_pointer"]
        kind, _, raw = ptr.partition("#")
        ans_text = ""
        if kind == "messages" and int(raw) in by_ord:
            ans_text = by_ord[int(raw)]["content"] or ""
        else:
            r = conn.execute("select content from messages where id=?", (int(raw),)).fetchone()
            ans_text = (r[0] if r else "") or ""
        cands = [row["expected_value"]] + row["rival_values"]
        prof = []
        for c in cands:
            in_args = in_fp = in_res = False
            first = None
            tools = set()
            for t in tcs:
                blob = "\n".join([t["file_path"] or "", t["input"] or "", t["result"] or ""])
                if c in blob:
                    first = t["ordinal"] if first is None else min(first, t["ordinal"])
                    tools.add(t["tool_name"])
                    in_args |= c in (t["input"] or "")
                    in_fp |= c in (t["file_path"] or "")
                    in_res |= c in (t["result"] or "")
            prof.append(
                {
                    "value": c,
                    "is_truth": c == row["expected_value"],
                    "in_tool_args": in_args,
                    "is_file_path": in_fp,
                    "in_tool_result": in_res,
                    "tool_names": sorted(tools),
                    "first_ordinal": first,
                    "in_answer_text": c in ans_text,
                    "label_terms": label_terms(ans_text, c) if ans_text else [],
                }
            )
        prov_keys = ("in_tool_args", "is_file_path", "in_tool_result", "first_ordinal", "tool_names")
        truth = next(p for p in prof if p["is_truth"])
        rivals = [p for p in prof if not p["is_truth"]]
        # a provenance signal *separates* only if it differs between truth and every rival
        separators = []
        for k in prov_keys:
            if all(truth[k] != r[k] for r in rivals):
                separators.append(k)
        # the same, for the local attribution signal
        q_terms = content_tokens(row["question"])
        truth_label = len(q_terms & set(truth["label_terms"]))
        rival_label = max((len(q_terms & set(r["label_terms"])) for r in rivals), default=0)
        findings.append(
            {
                "id": row["id"],
                "question": row["question"][:120],
                "truth": row["expected_value"],
                "rivals": [r["value"] for r in rivals],
                "provenance_separators": separators,
                "provenance_identical": not separators,
                "same_evidence_pointer": all(
                    truth[k] == r[k] for k in prov_keys for r in rivals
                ),
                "label_overlap_truth": truth_label,
                "label_overlap_best_rival": rival_label,
                "label_separates": truth_label > rival_label,
                "profiles": prof,
            }
        )

    out = {
        "ambiguity_corpus": {
            "cases": shape["cases"],
            "candidates": shape["total_candidates"],
            "mean_candidates": round(shape["total_candidates"] / max(1, shape["cases"]), 2),
            "per_slot": {k: dict(v) for k, v in per_slot.items()},
        },
        "separation_test": {
            "n": len(findings),
            "provenance_identical_for_truth_and_all_rivals": sum(
                1 for f in findings if f["provenance_identical"]),
            "same_evidence_pointer": sum(1 for f in findings if f["same_evidence_pointer"]),
            "label_overlap_separates": sum(1 for f in findings if f["label_separates"]),
        },
        "findings": findings,
    }
    Path(ROOT / a.out).write_text(json.dumps(out, indent=2))
    print(json.dumps(out["ambiguity_corpus"], indent=2))
    print(json.dumps(out["separation_test"], indent=2))
    for f in findings:
        print(f"\n{f['id']} {f['question'][:70]!r}")
        print(f"  truth  {f['truth']}")
        for r in f["rivals"]:
            print(f"  rival  {r}")
        print(f"  provenance separators: {f['provenance_separators'] or 'NONE'}  "
              f"same_pointer={f['same_evidence_pointer']}  "
              f"label overlap truth/rival: {f['label_overlap_truth']}/{f['label_overlap_best_rival']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
