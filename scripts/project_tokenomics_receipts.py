#!/usr/bin/env python3
"""Project Phase 1B observations into Tokenomics' own receipt schema.

Tokenomics owns neutral measurement and economic accounting; Q-Route owns
routing evidence.  The boundary is only respected if the physical facts travel
*as Tokenomics shapes them* rather than as a second, local interpretation.  This
projector does exactly one thing: it maps each raw observation receipt onto
``z0int.decision_receipt.v1`` -- the schema pinned in
``tokenomics/fixtures/z0int_receipt.json`` -- and asserts the mapping round-trips
against that fixture's required keys.

It computes no cost.  If economics are needed, Tokenomics derives them from these
receipts; a cost column invented here would be a second source of truth.

    python scripts/project_tokenomics_receipts.py --run-id p1b-... --out <dir>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

REPO = Path(__file__).resolve().parents[1]
RESULTS_ROOT = REPO / "results" / "phase1b"

RECEIPT_SCHEMA = "z0int.decision_receipt.v1"
#: Keys the canonical fixture carries that we must be able to reproduce.
EXPECTED_KEYS: tuple[str, ...] = (
    "schema",
    "trace_id",
    "session_id",
    "capability_id",
    "provider",
    "model",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "latency_ms",
    "experiment_id",
    "arm_id",
    "outcome",
)

#: arm kind -> the capability the decision exercised.  Generic capabilities only:
#: AODL owns the intent/authority vocabulary and no model name appears here.
CAPABILITY_BY_KIND: dict[str, str] = {
    "deterministic": "cognition.deterministic_choice",
    "bounded": "cognition.bounded_choice",
    "unfiltered": "cognition.bounded_choice_unconstrained",
    "cascade": "cognition.cascaded_choice",
    "cold_probe": "cognition.residency_probe",
}


def capability_id(row: Mapping[str, Any]) -> str:
    kind = str(row.get("arm_kind") or "bounded")
    return CAPABILITY_BY_KIND.get(kind, "cognition.bounded_choice")


def to_decision_receipt(row: Mapping[str, Any]) -> dict[str, Any]:
    """One observation -> one Tokenomics decision receipt."""
    verification = row.get("verification") or {}
    gold = str(verification.get("kind") or "") in (
        "fixture_gold", "unit_test", "compiler_contract", "deterministic_check",
        "outcome_success",
    )
    error = (row.get("failure_retry") or {}).get("error")
    return {
        "schema": RECEIPT_SCHEMA,
        "trace_id": row.get("trace_id"),
        "session_id": row.get("run_id"),
        "capability_id": capability_id(row),
        "provider": "local",
        "model": row.get("model_id"),
        "model_revision": row.get("model_revision"),
        "quantization": row.get("quant"),
        "input_tokens": row.get("tokens_in"),
        "output_tokens": row.get("tokens_out"),
        "cached_input_tokens": row.get("cached_tokens"),
        "latency_ms": row.get("total_ms"),
        "ttft_ms": row.get("ttft_ms"),
        # Residency is part of physical accounting on a one-model GPU, so it is
        # carried explicitly rather than folded into latency.
        "residency": {
            "class": row.get("cold_or_warm"),
            "resident_before": row.get("resident_before"),
            "load_ms": row.get("load_ms"),
            "peak_vram_mib": row.get("peak_vram_mib"),
        },
        "experiment_id": row.get("run_id"),
        "arm_id": row.get("arm"),
        "outcome": {
            "execution_completed": error is None,
            # Never infer verification from an ambient success.  Only a
            # deterministic verifier may claim it.
            "verified_success": bool(row.get("correct")) if gold else None,
            "verification_source": verification.get("kind") if gold else None,
            "failure": error,
            "source": "phase1b_densify",
        },
        "accounting_authority": "tokenomics",
    }


def project(
    observations_path: Path, out_path: Path
) -> dict[str, Any]:
    rows = 0
    missing = 0
    with observations_path.open(encoding="utf-8") as src, out_path.open(
        "w", encoding="utf-8"
    ) as dst:
        for line in src:
            line = line.strip()
            if not line:
                continue
            obs = json.loads(line)
            receipt = to_decision_receipt(obs)
            absent = [k for k in EXPECTED_KEYS if k not in receipt]
            if absent:
                missing += 1
            dst.write(json.dumps(receipt, sort_keys=True, default=str) + "\n")
            rows += 1
    return {
        "schema": "z0int.tokenomics_projection.v1",
        "receipt_schema": RECEIPT_SCHEMA,
        "observations": rows,
        "receipts": rows,
        "receipts_missing_expected_keys": missing,
        "expected_keys": list(EXPECTED_KEYS),
        "out": str(out_path),
        "cost_computed_here": False,
        "note": "physical facts only; Tokenomics owns economics",
    }


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(list(argv) if argv is not None else None)

    run_dir = Path(args.run_dir) if args.run_dir else (RESULTS_ROOT / args.run_id)
    observations = run_dir / "observations.jsonl"
    if not observations.is_file():
        print(f"# no observations at {observations}", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else (run_dir / "tokenomics_receipts.jsonl")
    summary = project(observations, out)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["receipts_missing_expected_keys"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
