#!/usr/bin/env python3
"""Part 7: ONE deterministic, provenance-based disambiguation challenger.

Hypothesis under test:
    "answerhood" is better indicated by interaction/causal evidence (the
    candidate was read, edited, or passed as a tool argument after the question)
    than by lexical topicality.

This script does NOT touch `DEFAULT_POLICY` or any resolver code. It builds the
candidate field independently and scores it with the deterministic signals the
brief names, then compares the pick against the critic-frozen ground truth -- and
against what the frozen champion returns unchanged.

A tie at the top score resolves to NO ANSWER, because ambiguity must stay
unresolved rather than be broken by a coin flip.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from mine_real_questions import (  # noqa: E402
    DEFAULT_DB,
    EDIT_TOOLS,
    READ_TOOLS,
    content_tokens,
    load_session,
    skeleton,
    slot_values,
)

#: The smallest score that uses only interaction/causal evidence. Weights encode
#: the brief's ordering: an edit is stronger than a read, a read stronger than a
#: mention in output.
W = {
    "edited_after_question": 3,
    "read_after_question": 2,
    "in_tool_args": 2,
    "in_explicit_result": 1,
    "earliest": 1,
}


def rank(cands: list[dict]) -> tuple[str | None, list[dict]]:
    ranked = sorted(cands, key=lambda c: -c["score"])
    if not ranked:
        return None, []
    if len(ranked) > 1 and ranked[0]["score"] == ranked[1]["score"]:
        return None, ranked  # unresolved by construction
    return ranked[0]["value"], ranked


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frozen", default="benchmarks/fixtures/resolve-real-v1/dev.jsonl")
    ap.add_argument("--out", default=".work/provenance-disambig.json")
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--field", default="plausible", choices=("plausible", "wide"),
                    help="plausible = the critic-identified candidate set; wide = every slot-shaped value after the question")
    a = ap.parse_args()

    conn = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [json.loads(l) for l in (ROOT / a.frozen).read_text().splitlines() if l.strip()]

    from z0int.context_resolve import resolve_fast

    results = []
    for row in rows:
        sid = row["session_id"]
        msgs, tcs = load_session(conn, sid)
        q_skel = skeleton(row["question"])
        q_ord = next((m["ordinal"] for m in msgs
                      if m["role"] == "user" and skeleton(m["content"] or "") == q_skel), None)
        if q_ord is None:
            q_ord = next((m["ordinal"] for m in msgs
                          if m["role"] == "user" and q_skel[:60] in skeleton(m["content"] or "")), 0)

        values: list[str] = [row["expected_value"], *row["rival_values"]]
        if a.field == "wide":
            # every slot-shaped value seen after the question: what the resolver's
            # own extractor would put in front of a selector
            for t in tcs:
                if t["ordinal"] <= q_ord:
                    continue
                for v in slot_values("\n".join([t["file_path"] or "", t["input"] or "", t["result"] or ""]),
                                     row["slot_kind"]):
                    if v not in values:
                        values.append(v)

        qt = content_tokens(row["question"])
        cands = []
        first_seen: dict[str, int] = {}
        for t in tcs:
            if t["ordinal"] <= q_ord:
                continue
            for v in values:
                if v in (t["file_path"] or "") or v in (t["input"] or "") or v in (t["result"] or ""):
                    first_seen[v] = min(first_seen.get(v, 10**9), t["ordinal"])
        for v in values:
            edited = read = args = result = False
            for t in tcs:
                if t["ordinal"] <= q_ord:
                    continue
                in_fp = v in (t["file_path"] or "")
                in_args = v in (t["input"] or "")
                in_res = v in (t["result"] or "")
                if in_fp or in_args:
                    if EDIT_TOOLS.search(t["tool_name"]):
                        edited = True
                    if READ_TOOLS.search(t["tool_name"]):
                        read = True
                args |= in_args
                result |= in_res
            score = (
                W["edited_after_question"] * edited
                + W["read_after_question"] * read
                + W["in_tool_args"] * args
                + W["in_explicit_result"] * result
                + W["earliest"] * (1 if first_seen.get(v) == min(first_seen.values(), default=None) else 0)
            )
            cands.append(
                {
                    "value": v,
                    "is_truth": v == row["expected_value"],
                    "score": score,
                    "edited": edited,
                    "read": read,
                    "in_tool_args": args,
                    "in_result": result,
                    "first_ordinal": first_seen.get(v),
                    "topical_overlap": len(qt & content_tokens(v)),
                }
            )
        pick, _ = rank(cands)
        champ = resolve_fast(row["question"], allow_model=False, emit=False)
        champ_vals = [s.value for s in champ.slots if s.resolved]
        results.append(
            {
                "id": row["id"],
                "question": row["question"],
                "truth": row["expected_value"],
                "field_size": len(cands),
                "rivals": len(cands) - 1,
                "challenger_pick": pick,
                "challenger_correct": pick == row["expected_value"],
                "champion_vals": champ_vals,
                "champion_correct": any(
                    row["expected_value"].lower() in (v or "").lower()
                    or (v or "").lower() in row["expected_value"].lower() for v in champ_vals),
                "champion_fallback": champ.fallback_used,
                "candidates": cands,
            }
        )

    n = len(results)
    out = {
        "n": n,
        "challenger_correct": sum(1 for r in results if r["challenger_correct"]),
        "challenger_unresolved": sum(1 for r in results if r["challenger_pick"] is None),
        "challenger_wrong": sum(1 for r in results
                                if r["challenger_pick"] is not None and not r["challenger_correct"]),
        "champion_correct": sum(1 for r in results if r["champion_correct"]),
        "champion_fallbacks": sum(1 for r in results if r["champion_fallback"]),
        "weights": W,
        "results": results,
    }
    Path(ROOT / a.out).write_text(json.dumps(out, indent=2))
    print(json.dumps({k: v for k, v in out.items() if k != "results"}, indent=2))
    for r in results:
        print(f"\n{r['id']} field={r['field_size']} {r['question'][:64]!r}")
        print(f"   truth      : {r['truth']}")
        print(f"   challenger : {r['challenger_pick']}  {'OK' if r['challenger_correct'] else 'MISS'}")
        print(f"   champion   : {r['champion_vals']}  fallback={r['champion_fallback']}")
        for c in sorted(r["candidates"], key=lambda x: -x["score"])[:4]:
            print(f"     score={c['score']:2d} edited={int(c['edited'])} read={int(c['read'])} "
                  f"args={int(c['in_tool_args'])} res={int(c['in_result'])} "
                  f"topical={c['topical_overlap']} {'<== TRUTH' if c['is_truth'] else ''} {c['value']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
