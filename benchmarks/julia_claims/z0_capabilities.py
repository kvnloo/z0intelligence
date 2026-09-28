#!/usr/bin/env python3
"""Phase 1-6 — real z0 capability datasets from trace stores, and sealed comparison.

Trace source
    ~/.z0int/episodes/next_action.jsonl  (76,970 rows, 1,276 sessions)
    produced by the z0 OS episode compiler; each row is one agent turn with the
    session's user request, the previous families, and the tool actually invoked.

Why first-action rows only
    `user` is the SESSION-level request, repeated on every turn: 76,970 rows carry
    only 1,644 distinct prompts, and one prompt appears 10,755 times. Predicting the
    next family from a repeated prompt mostly measures session habit, and rows inside
    a session are not independent. Restricting to the first action of each session
    (prev == []) gives one genuinely decision-time state per session.

Gold
    The family of the tool the agent ACTUALLY invoked (observable behaviour).
    gold-generation rule: gold = majority family among the sessions sharing a
    normalized prompt; 375/382 prompts (98.2%) are single-family, so this is
    effectively "what the agent did with this request".

Ceiling
    Per-prompt-majority ceiling is 97.9%; global majority is 61.9%.
"""
from __future__ import annotations

import collections
import hashlib
import json
import re
from pathlib import Path

EPISODES = Path.home() / ".z0int" / "episodes" / "next_action.jsonl"
REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "results" / "julia-claims"
OUT.mkdir(parents=True, exist_ok=True)

# Frozen label semantics, derived from the declared capability card
# `next_action_family` (output_contract) and the observed tool->family mapping.
LABELS = ("READ_SEARCH", "EDIT", "EXECUTE", "WEB", "DELEGATE", "VERIFY", "RESPOND", "ABSTAIN")
DESCRIPTIONS = {
    "READ_SEARCH": "Locate or read existing information: files, code, skills, sessions, search",
    "EDIT": "Modify an existing file or write a new one",
    "EXECUTE": "Run a command, process, or program",
    "WEB": "Search or browse the web, or analyse an external page or image",
    "DELEGATE": "Create or update tasks, todos, or hand work to another agent",
    "VERIFY": "Check or validate something that was already produced",
    "RESPOND": "Answer the user directly without using a tool",
    "ABSTAIN": "Do nothing, or defer the decision",
}
REPRESENTATIONS = {
    "R0": lambda lab: lab,
    "R1": lambda lab: DESCRIPTIONS[lab],
    "R2": lambda lab: f"{lab} — {DESCRIPTIONS[lab]}",
}
MIN_PER_CLASS = 50


def norm(prompt: str) -> str:
    """Grouping key: whitespace/case-normalised prompt. No content rewriting."""
    return re.sub(r"\s+", " ", prompt.strip().lower())


def build():
    rows = [json.loads(l) for l in EPISODES.read_text().splitlines() if l.strip()]
    first = [r for r in rows if not (r.get("prev") or []) and r["user"].strip()]

    by_prompt = collections.defaultdict(collections.Counter)
    original = {}
    for r in first:
        key = norm(r["user"])
        by_prompt[key][r["family"]] += 1
        # keep the ORIGINAL text as the model input; the normalised form is only a
        # grouping key. Feeding the lowercased key as state is a representation
        # defect, not a property of the task.
        original.setdefault(key, r["user"].strip())

    examples = []
    for key, counter in by_prompt.items():
        gold = counter.most_common(1)[0][0]
        if gold not in LABELS:
            continue
        examples.append({
            "example_id": hashlib.sha256(key.encode()).hexdigest()[:16],
            "group_id": key,
            "state_normalised": key,
            "state": original[key],
            "gold": gold,
            "observations": sum(counter.values()),
            "ambiguous": len(counter) > 1,
        })
    examples.sort(key=lambda e: e["example_id"])
    return examples, first


def split_of(group_id: str) -> str:
    """60/20/20 grouped split. Groups are normalised prompts, so near-identical
    requests never straddle a boundary."""
    h = int(hashlib.sha256(("split:" + group_id).encode()).hexdigest()[:8], 16)
    r = h % 100
    return "dev" if r < 60 else ("validation" if r < 80 else "sealed")


def contract(examples) -> dict:
    counts = collections.Counter(e["gold"] for e in examples)
    present = [l for l in LABELS if counts.get(l, 0) >= MIN_PER_CLASS]
    payload = {
        "capability_id": "tool_family_select",
        "version": "v1",
        "intended_use": ("choose the coarse tool family for an agent's first action on a "
                         "user request, to narrow a tool registry before an expensive router"),
        "input_fields": ["state = the session's opening user request (source truncates at 400 chars)"],
        "ordered_label_ids": list(LABELS),
        "label_descriptions": DESCRIPTIONS,
        "source_of_gold": ("family of the tool the agent actually invoked first in that session "
                           "(~/.z0int/episodes/next_action.jsonl, prev == [])"),
        "gold_generation": "majority family across sessions sharing a normalised prompt",
        "exclusion_criteria": [
            "empty user prompt",
            "rows that are not the session's first action (state would be a repeated prompt)",
            f"families with fewer than {MIN_PER_CLASS} examples",
        ],
        "known_ambiguity_policy": ("prompts with >1 observed family keep their majority family; "
                                   "count and rate are reported"),
        "error_cost": "low: a routing hint that narrows candidates, reversible by the next stage",
        "minimum_evaluation_size": MIN_PER_CLASS,
        "group_key": "normalised prompt (lowercased, whitespace-collapsed)",
        "split": "60% dev / 20% validation / 20% sealed, by group_key",
        "representations": list(REPRESENTATIONS),
        "labels_with_enough_evidence": present,
        "labels_below_minimum": [l for l in LABELS if 0 < counts.get(l, 0) < MIN_PER_CLASS],
        "ceiling_note": "per-prompt-majority ceiling measured at 97.9% of first-action rows",
    }
    payload["schema_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return payload


def main():
    examples, first = build()
    counts = collections.Counter(e["gold"] for e in examples)
    for e in examples:
        e["split"] = split_of(e["group_id"])
    print(f"source rows={len(first)} first-action(prev==[]) with prompt")
    print(f"unique prompts={len(examples)}")
    print(f"gold distribution: {dict(counts.most_common())}")
    amb = sum(1 for e in examples if e["ambiguous"])
    print(f"ambiguous prompts (>1 family observed): {amb}/{len(examples)}")
    for s in ("dev", "validation", "sealed"):
        sub = [e for e in examples if e["split"] == s]
        print(f"  {s:<11} n={len(sub):<4} dist={dict(collections.Counter(x['gold'] for x in sub).most_common())}")

    c = contract(examples)
    (OUT / "capability-tool_family_select.contract.json").write_text(
        json.dumps(c, indent=2, sort_keys=True) + "\n")
    path = OUT / "capability-tool_family_select.jsonl"
    path.write_text("\n".join(json.dumps(e, sort_keys=True) for e in examples) + "\n")
    print(f"\ncontract schema_sha256={c['schema_sha256'][:16]}")
    print(f"labels with >= {MIN_PER_CLASS}: {c['labels_with_enough_evidence']}")
    print(f"labels below minimum: {c['labels_below_minimum']}")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
