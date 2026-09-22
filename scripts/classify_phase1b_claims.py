#!/usr/bin/env python3
"""Classify every Phase 1B headline claim against the recovered execution contract.

A wrong recorded contract does not automatically invalidate a measurement. It
invalidates it only if the cap actually **bound**. So each claim is classified
against the observed at-cap count for the arm it rests on:

    UNCHANGED                 the arm's effective contract equalled its requested one
    VALID_AS_EXECUTED         the recorded contract was wrong but never bound;
                              the number is a true measurement of the model as run
    REQUIRES_CORRECTED_RERUN  the cap bound on a material fraction of draws
    INVALID_COMPARISON        the comparison's design was rank-deficient

Writes results/phase1b/p1b-20260921T1430Z/analysis/claim-status.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORPUS = REPO / "results" / "phase1b" / "p1b-20260921T1430Z" / "observations.jsonl"
AUDIT = CORPUS.parent / "analysis" / "execution-contract-audit.json"
OUT = CORPUS.parent / "analysis" / "claim-status.json"

EFFECTIVE = {
    "hammer2.1_3b": 256, "hammer2.1_7b": 256, "nemotron_orchestrator_8b": 256,
    "functiongemma_270m": 128, "qwen3.5_4b": 1024, "qwen3.5_9b": 1024,
}
OBSERVED_MAX = {
    "hammer2.1_3b": 80, "hammer2.1_7b": 63, "nemotron_orchestrator_8b": 256,
    "functiongemma_270m": 39, "qwen3.5_4b": 558, "qwen3.5_9b": 1024,
}
AT_CAP = {"hammer2.1_3b": 0, "hammer2.1_7b": 0, "nemotron_orchestrator_8b": 64,
          "functiongemma_270m": 0, "qwen3.5_4b": 0, "qwen3.5_9b": 30}


def classify(model: str, *, cap_equals_requested: bool = False) -> tuple[str, str]:
    """Classify one arm's claims from whether its cap actually bound."""
    at = AT_CAP.get(model, 0)
    if cap_equals_requested:
        if at:
            return ("REQUIRES_CORRECTED_RERUN",
                    f"effective cap equals the requested 1024, but it bound on {at} draws — "
                    "not a contract defect; the requested cap itself was too small")
        return "UNCHANGED", "effective == requested and the cap never bound"
    if at:
        return ("REQUIRES_CORRECTED_RERUN",
                f"effective cap {EFFECTIVE[model]} vs recorded 1024, and it bound on {at} draws "
                f"({100 * at / 171:.1f}% of 171)")
    return ("VALID_AS_EXECUTED",
            f"effective cap {EFFECTIVE[model]} vs recorded 1024, but the cap never bound — "
            f"max observed completion {OBSERVED_MAX[model]} tokens, 0 draws at the cap. "
            "The number is a true measurement of the model as run; only the historical "
            "description of its contract was wrong")


#: (claim, arm, cap_equals_requested, note, explicit_status_or_None)
CLAIMS = [
    ("Hammer3B 75/84 = 0.893 (compiler-first)", "hammer2.1_3b", False,
     "the headline. Unaffected: max 80 completion tokens against a 256 cap."),
    ("Hammer3B 69/84 unfiltered", "hammer2.1_3b", False, "same arm, same immateriality."),
    ("Hammer7B 72/84", "hammer2.1_7b", False, "max 63 against a 256 cap."),
    ("FunctionGemma 51/84", "functiongemma_270m", False,
     "max 39 against a 128 cap, so even the tighter cap never bound."),
    ("Nemotron 52/84 compiler-first and 61/84 unfiltered", "nemotron_orchestrator_8b", False,
     "64 of 171 draws finished at exactly 256. This is the arm the defect actually damaged."),
    ("Qwen3.5-4B 75/84", "qwen3.5_4b", True,
     "requested == effective == 1024 and max observed 558, so nothing was clipped."),
    ("Qwen3.5-9B 78/84", "qwen3.5_9b", True,
     "requested == effective == 1024, but 30 of 171 draws finished at exactly 1024, so the "
     "requested cap itself bound. Raising it may move this number."),
    ("Qwen3.5-9B 78/84 vs Qwen3.5-4B 75/84 ranking", "qwen3.5_9b", True,
     "a 3-draw margin between a capped and an uncapped arm is not a ranking."),
    ("compiler-first vs unfiltered accuracy delta (all models)", None, False,
     "the A/B/C/D 2x2 was RANK-DEFICIENT: `dialect_for` and `ToolDecisionRequest` carry no "
     "framing variable, so 'compiler framing' and 'unfiltered framing' render the same "
     "template. Hash-verified: A==C and B==D on all 28 states.", "INVALID_COMPARISON"),
    ("compiler removes dangerous exposure", None, False,
     "structural and deterministic — the illegal actions are absent from the menu by "
     "construction. Fresh paired run: dangerous exposed 20/140 all-actions vs 0/140 "
     "legal-only. This claim does not depend on any model.", "VALID_AS_EXECUTED"),
    ("compiler prevents dangerous SELECTION", None, False,
     "NOT supported. Fresh paired run: dangerous selected = 0 in every cell including "
     "all-actions. The 6 dangerous selections in the frozen corpus do not replicate.",
     "INVALID_COMPARISON"),
    ("NanoJev cascade 0.893 -> 0.929", "hammer2.1_3b", False,
     "the cascade's Hammer baseline is 75/84, whose cap never bound (max 80 tokens), so the "
     "comparison stands AS EXECUTED. The historical worry that Hammer ran at 384 was wrong "
     "twice over: its effective cap was 256, and 256 never bound either.",
     "VALID_AS_EXECUTED"),
    ("NanoJev threshold 0.6 / coverage 17/28 = 0.607 / success|covered 0.941 / p50 34.7 ms",
     None, False,
     "non-autoregressive, matched-menu measurements with zero decode steps. No generation "
     "cap applies to them at all.", "UNCHANGED"),
    ("composition: qwen4b+jev 13/28, hammer3b+qwen4b 6/28", None, False,
     "Qwen4B was uncapped in effect (max 558 < 1024) and Hammer3B's cap never bound, so "
     "these stand as executed.", "VALID_AS_EXECUTED"),
]


def main() -> int:
    rows = [json.loads(l) for l in CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]
    total = len(rows)

    claims = []
    for entry in CLAIMS:
        text, model, cap_eq, note = entry[0], entry[1], entry[2], entry[3]
        explicit = entry[4] if len(entry) > 4 else None
        if explicit:
            status, reason = explicit, note
        elif model is None:
            status, reason = "INVALID_COMPARISON", note
        else:
            status, reason = classify(model, cap_equals_requested=cap_eq)
        claims.append({
            "claim": text, "depends_on_arm": model, "status": status,
            "reason": reason,
            "arm_effective_max_tokens": EFFECTIVE.get(model) if model else None,
            "arm_observed_max_completion_tokens": OBSERVED_MAX.get(model) if model else None,
            "arm_draws_at_effective_cap": AT_CAP.get(model) if model else None,
        })

    counts: dict[str, int] = {}
    for c in claims:
        counts[c["status"]] = counts.get(c["status"], 0) + 1

    payload = {
        "schema": "z0int.phase1b.claim_status.v1",
        "run_id": "p1b-20260921T1430Z",
        "frozen_run_mutated": False,
        "corpus_rows": total,
        "principle": (
            "A wrong recorded execution contract invalidates a measurement only where the "
            "contract actually bound. Classify per arm against the observed at-cap count, "
            "not against the size of the discrepancy."
        ),
        "status_counts": counts,
        "claims": claims,
        "affected_arms": sorted(m for m, n in AT_CAP.items() if n),
        "unaffected_arms": sorted(m for m, n in AT_CAP.items() if not n),
        "narrowing": (
            "The recorded contract was false for four of six generative arms, but only two "
            "were materially affected: nemotron (64/171 draws at its cap) and qwen3.5_9b "
            "(30/171, from a cap that was correctly recorded but too small). Hammer3B, "
            "Hammer7B and FunctionGemma recorded a contract they never approached, so their "
            "published numbers are true measurements of the models as run."
        ),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {OUT.relative_to(REPO)}")
    print(f"  status counts: {counts}")
    print(f"  affected arms : {payload['affected_arms']}")
    print(f"  unaffected    : {payload['unaffected_arms']}")
    print()
    for c in claims:
        print(f"  {c['status']:26s} {c['claim'][:72]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
