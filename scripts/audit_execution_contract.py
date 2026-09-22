#!/usr/bin/env python3
"""Audit the Phase 1B execution contract without rewriting the frozen run.

The defect
----------
Every one of the 1,102 Phase 1B receipts records ``max_tokens: 1024``. That was
the *requested campaign setting*. It was never the value that reached the wire.

``LocalSLMBackend.decide`` sends::

    max_tokens=min(request.max_tokens, self._dialect.max_tokens)

and the dialect is resolved from the model's manifest row by
``dialect_for(tool_parser, tool_call_template)``. That resolver tests
``tool_parser`` **first** and returns on the first dialect whose name is a
substring — so ``tool_parser="hermes"`` resolves to ``HERMES_DIALECT`` and the
separate ``tool_call_template`` is never consulted.

The consequence, resolved from source rather than inferred:

    nemotron_orchestrator_8b   tool_parser=hermes        -> HERMES_DIALECT   256
    hammer2.1_3b / _7b         tool_parser=hermes        -> HERMES_DIALECT   256
    functiongemma_270m         tool_parser=functiongemma -> FUNCTIONGEMMA    128
    qwen3.5_4b / _9b           tool_parser=qwen3_coder   -> QWEN_DIALECT    1024

So **every generative arm except the two Qwen arms ran at 256 or 128 tokens while
recording 1024**. The accuracy of those arms is a measurement of the model under a
cap the documentation says it was not run under.

The part that is worse than a config error: ``NEMOTRON_DIALECT`` exists with
``max_tokens=1024``, and its own comment reads

    "Nemotron-Orchestrator emits a <think> block before its call; the measured
     failure mode at 256 tokens was an EMPTY content field with the whole budget
     spent on reasoning."

Someone diagnosed this exact failure, wrote the fix, and it was never reached,
because ``tool_parser="hermes"`` wins the lookup before the template is examined.

This script recovers the facts and emits them with a source and a confidence per
row. It does not touch the corpus.
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

CORPUS = REPO / "results" / "phase1b" / "p1b-20260921T1430Z" / "observations.jsonl"
MANIFEST = REPO / "manifests" / "local_cognition.v1.json"
OUT = CORPUS.parent / "analysis" / "execution-contract-audit.json"

GENERATIVE = (
    "hammer2.1_3b", "hammer2.1_7b", "nemotron_orchestrator_8b",
    "qwen3.5_4b", "qwen3.5_9b", "functiongemma_270m",
)


def resolve_dialect(manifest: dict, model_id: str):
    from z0int.cognition.adapters.dialects import dialect_for

    row = (manifest.get("models") or {}).get(model_id) or {}
    tp, tct = row.get("tool_parser"), row.get("tool_call_template")
    d = dialect_for(tp, tct)
    return {
        "tool_parser": tp,
        "tool_call_template": tct,
        "dialect": d.name,
        "dialect_max_tokens": d.max_tokens,
    }


def observe(rows: list[dict], model_id: str) -> dict:
    """What the corpus actually shows about this model's completion lengths."""
    comps, reqs, arms = [], set(), set()
    for r in rows:
        if r.get("model_id") != model_id:
            continue
        arms.add(r.get("arm"))
        if r.get("max_tokens") is not None:
            reqs.add(int(r["max_tokens"]))
        rd = r.get("raw_decision") or {}
        c = rd.get("completion_tokens")
        if c is None:
            c = r.get("tokens_out")
        if c is not None:
            comps.append(int(c))
    if not comps:
        return {"observed_calls": 0}
    comps.sort()
    at_or_over = {
        "==128": sum(1 for c in comps if c == 128),
        "==256": sum(1 for c in comps if c == 256),
        "==384": sum(1 for c in comps if c == 384),
        ">=1024": sum(1 for c in comps if c >= 1024),
    }
    return {
        "observed_calls": len(comps),
        "arms": sorted(a for a in arms if a),
        "recorded_requested_max_tokens": sorted(reqs),
        "observed_completion_tokens_min": comps[0],
        "observed_completion_tokens_median": comps[len(comps) // 2],
        "observed_completion_tokens_max": comps[-1],
        "observed_histogram_at_caps": at_or_over,
        "observed_max_never_exceeds": comps[-1],
    }


def classify(row: dict) -> dict:
    """Effective cap, with an explicit source and confidence."""
    eff = row["dialect_max_tokens"]
    req = row.get("recorded_requested_max_tokens") or []
    requested = req[0] if len(req) == 1 else (req or None)
    effective = min(requested, eff) if requested else eff
    clipped = bool(requested and requested > eff)
    obs_max = row.get("observed_completion_tokens_max")
    # Cross-check the source-derived cap against what the corpus actually shows.
    if clipped and obs_max is not None:
        consistent = obs_max <= eff
        confidence = "high" if consistent else "low"
        note = (
            f"source: dialect lookup in dialect_for() -> {row['dialect']}({eff}); "
            f"observed max completion {obs_max} {'<=' if consistent else '>'} {eff}"
        )
    elif requested and requested == eff:
        confidence = "high"
        note = f"requested {requested} == dialect cap {eff}; nothing was clipped"
        clipped = False
    else:
        confidence = "medium"
        note = f"dialect cap {eff}; requested value not recovered from the corpus"
    return {
        "requested_max_tokens": requested,
        "dialect_max_tokens": eff,
        "effective_max_tokens": effective,
        "clipped": clipped,
        "confidence": confidence,
        "evidence_source": note,
    }


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = [json.loads(l) for l in CORPUS.read_text(encoding="utf-8").splitlines() if l.strip()]

    models: dict[str, dict] = {}
    for mid in GENERATIVE:
        entry = {"model_id": mid}
        entry.update(resolve_dialect(manifest, mid))
        entry.update(observe(rows, mid))
        entry.update(classify(entry))
        models[mid] = entry

    # Was the fix present but unreachable?
    from z0int.cognition.adapters.dialects import DIALECTS

    nem = DIALECTS.get("nemotron")
    reachable = {
        mid: {
            "dialect_selected": m["dialect"],
            "nemotron_dialect_exists": nem is not None,
            "nemotron_dialect_cap": getattr(nem, "max_tokens", None),
            "nemotron_dialect_used": m["dialect"] == "nemotron",
        }
        for mid, m in models.items()
    }

    clipped_models = [mid for mid, m in models.items() if m["clipped"]]
    payload = {
        "schema": "z0int.phase1b.execution_contract_audit.v1",
        "run_id": "p1b-20260921T1430Z",
        "frozen_run_mutated": False,
        "executed_at": "2026-09-22",
        "summary": {
            "defect": (
                "Every receipt records max_tokens=1024, the requested campaign setting. "
                "The value sent to the wire is min(requested, dialect.max_tokens), and the "
                "dialect is chosen by dialect_for(tool_parser, tool_call_template), which "
                "matches tool_parser first and never consults the template."
            ),
            "models_with_a_clipped_cap": clipped_models,
            "models_unaffected": [m for m in models if m not in clipped_models],
            "headline": (
                f"{len(clipped_models)} of {len(models)} generative arms ran under a cap "
                "smaller than the one recorded on their receipts."
            ),
            "the_fix_existed_and_was_unreachable": (
                "NEMOTRON_DIALECT is defined with max_tokens=1024 and its comment names the "
                "exact 256-token empty-content failure. It is never selected for "
                "nemotron_orchestrator_8b because that row's tool_parser is 'hermes', which "
                "wins the lookup before the template is examined."
            ),
        },
        "models": models,
        "nemotron_dialect_reachability": reachable,
        "recoverability": {
            "finish_reason_in_frozen_corpus": "absent",
            "truncation_flag_in_frozen_corpus": "absent",
            "gold_mentioned_in_reasoning": "absent",
            "note": (
                "The frozen receipts record no finish_reason, no truncation flag and no "
                "reasoning text, so truncation cannot be recovered per row. It can be "
                "inferred only from completion_tokens hitting the effective cap exactly, "
                "which is what the histogram above counts. Per-row recovery requires the "
                "fresh run, which does record finish_reason."
            ),
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {OUT.relative_to(REPO)}")
    print(f"\n{'model':28s} {'requested':>9s} {'dialect':>10s} {'effective':>9s} "
          f"{'clipped':7s} {'obs max':>7s} {'conf':6s}")
    for mid, m in models.items():
        print(f"{mid:28s} {str(m['requested_max_tokens']):>9s} {str(m['dialect_max_tokens']):>10s} "
              f"{str(m['effective_max_tokens']):>9s} {str(m['clipped']):7s} "
              f"{str(m.get('observed_completion_tokens_max')):>7s} {m['confidence']:6s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
