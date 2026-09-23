#!/usr/bin/env python3
"""Freeze the real-user-question ruler (resolve-real-v1).

Only items that survived BOTH independent critic passes are allowed into the
scored split. Everything else is preserved in `candidates.jsonl` with its reason,
because the yield chain is itself the finding: it says how many trustworthy real
memory questions exist in the corpus and where the rest were lost.

The manifest records the exact protocol, the audit chain, and the limitations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def skel(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="benchmarks/fixtures/resolve-real-v1")
    ap.add_argument("--gated", default=".work/real-q-gated.jsonl")
    ap.add_argument("--pack", default=".work/real-q-critic-pack.json")
    ap.add_argument("--pack2", default=".work/critic2-pack.json")
    ap.add_argument("--verdicts-a", default=".work/critic2-verdicts-A.json")
    ap.add_argument("--verdicts-b", default=".work/critic2-verdicts-B.json")
    ap.add_argument("--merged", default=".work/critic-merged.json")
    ap.add_argument("--multi", default=".work/real-q-multi.jsonl")
    ap.add_argument("--db", default="/mnt/zer0models/sft-svlm/data/agentsview/sessions.db")
    a = ap.parse_args()

    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)

    pack2 = {x["id"]: x for x in json.loads((ROOT / a.pack2).read_text())}
    import sqlite3

    db = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)

    def resolve_pointer(session_id: str, ordinal: int) -> str:
        """The miner records session-relative ordinals; the archive is keyed by
        global message ids. A pointer a reviewer cannot resolve is not a citation."""
        row = db.execute(
            "select id from messages where session_id=? and ordinal=? limit 1",
            (session_id, ordinal),
        ).fetchone()
        return f"messages#{row[0]}" if row else f"session:{session_id}#{ordinal}"

    A = {v["id"]: v for v in json.loads((ROOT / a.verdicts_a).read_text())["verdicts"]}
    B = {v["id"]: v for v in json.loads((ROOT / a.verdicts_b).read_text())["verdicts"]}
    merged = json.loads((ROOT / a.merged).read_text())["verdicts"]
    gated = [json.loads(l) for l in (ROOT / a.gated).read_text().splitlines() if l.strip()]

    def bad(v: dict) -> bool:
        return (
            (not v["slot_matches"])
            or (not v["evidence_answers"])
            or (not v["unique_enough"])
            or bool(v.get("relies_on_hidden_context"))
        )

    # ground truth for the S items came from the first critic pass; the second
    # pass only re-judged the *pair* (question, chosen answer).
    src_of = {x["id"]: x.get("_src") for x in pack2.values()}
    rows: list[dict] = []
    for iid, item in pack2.items():
        if bad(A[iid]) or bad(B[iid]):
            continue
        src = src_of.get(iid)
        # locate the original multi record by (question, chosen answer)
        rec = None
        for m in (json.loads(l) for l in (ROOT / a.multi).read_text().splitlines() if l.strip()):
            if skel(m["question"]) == skel(item["question"]) and merged.get(src, {}).get("chosen_answer") in m["multi_values"]:
                rec = m
                break
        rec = rec or {}
        ptr = item["cited_evidence"]["pointer"]
        if ptr.startswith("messages#") and rec.get("session_id"):
            ptr = resolve_pointer(rec["session_id"], int(ptr.split("#", 1)[1]))
        rows.append(
            {
                "id": f"real-{iid}",
                "question": item["question"],
                "slot_kind": item["slot_kind"],
                "expected_value": item["proposed_answer"],
                "evidence_pointer": ptr,
                "session_ordinal": (rec or {}).get("answer_ordinal"),
                "source_tool": item["cited_evidence"].get("tool", ""),
                "harness": (rec or {}).get("agent", ""),
                "session_id": (rec or {}).get("session_id", ""),
                "project": (rec or {}).get("project", ""),
                "family": "real_user_turn",
                "candidate_count": len(item["candidates"]),
                "rival_values": [c for c in item["candidates"] if c != item["proposed_answer"]],
                "ground_truth_source": "independent_critic_2_of_2",
                "quality_gate": "real-v1",
            }
        )

    # de-duplicate near-copies (same question skeleton + same answer)
    seen: set[tuple] = set()
    uniq: list[dict] = []
    for r in rows:
        k = (skel(r["question"]), r["expected_value"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)

    # Split by session lineage. With this few distinct questions a train/sealed
    # split would be theatre, so the manifest says so rather than pretending.
    dev = uniq
    train: list[dict] = []
    sealed: list[dict] = []

    for name, rs in (("train", train), ("dev", dev), ("sealed", sealed)):
        (out / f"{name}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rs))
    (out / "candidates.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in gated)
    )

    blob = "".join(json.dumps(r, sort_keys=True) + "\n" for r in dev)
    sha = hashlib.sha256(blob.encode()).hexdigest()
    reasons = Counter(r["gate"] for r in gated)
    multi = [json.loads(l) for l in (ROOT / a.multi).read_text().splitlines() if l.strip()]
    manifest = {
        "version": "resolve-real-v1",
        "purpose": "primary real-user-question ruler; replaces value-token-template generation for this task family",
        "provenance": "mined from AgentsView sessions.db user turns (real, unmodified), bound to later source evidence",
        "binding_signal": "last assistant *narration* message in the turn naming exactly one slot-shaped candidate that is also present in tool evidence; NOT topical overlap and NOT the provenance signal under test",
        "independent_critique": {
            "pass_1": "4 independent agents, 76 items (10 mechanically accepted + 66 ambiguity cases), no binding logic shown",
            "pass_2": "2 further independent agents re-judged the 10 accepted pairs plus the 4 ambiguity survivors",
            "pass1_to_pass2_agreement_on_accepted_pairs": "10/10 rejected by critic A, 7/10 by critic B",
            "pass2_inter_agent_agreement": "11/14 items",
            "rule": "an item enters the scored split only if BOTH pass-2 critics accept it",
        },
        "counts": {"train": len(train), "dev": len(dev), "sealed": len(sealed)},
        "dataset_sha256": sha,
        "yield_chain": {
            "sessions_scanned_with_tools": 5587,
            "user_turns_scanned": 22683,
            "utterances_after_naturalness_filter": 3833,
            "slot_cued_real_requests": 913,
            "mechanically_bound_pairs": 50,
            "passed_deterministic_gate": 50,
            "distinct_after_near_duplicate_collapse": 10,
            "bound_pairs_surviving_independent_critique": 0,
            "ambiguity_bucket": 70,
            "ambiguity_items_surviving_both_critics": 37,
            "distinct_questions_in_final_ruler": len(dev),
            "note": "the 4 scored items come from the adjudicated ambiguity bucket, not from the mechanically bound set; every mechanically bound pair was rejected by the independent critics",
        },
        "gate_reasons": dict(reasons),
        "ambiguity_cases": len(multi),
        "known_limitations": [
            "n=4 and only 3 distinct question texts: this measures the 'where is the link' class, not arbitrary lookups",
            "the mechanically bound set (50 pairs, 10 distinct) had a 0% survival rate under independent critique: the binder's truth signal is satisfied by incidental mentions",
            "the frozen champion falls back on all 4 items, so this ruler currently separates nothing about the champion",
            "no train/sealed split is meaningful at this size; they are empty rather than padded",
            "the corpus contains very few real slot-lookup questions (913 of 35657 user turns), and the mechanical binder produced 0 items that both critics accepted",
            "harness coverage is skewed: surviving items come from codex/hermes sessions only",
            "all four scored items come from ONE session in the private archive: the ruler is single-lineage and cannot measure session-level generalisation",
            "rows are not committed: they contain real user turns, private hosts and session ids, and this repository is public. The ruler is versioned by generator + dataset_sha256 instead.",
        ],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"dev": len(dev), "sha": sha, "gate_reasons": dict(reasons)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
