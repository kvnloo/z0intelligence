from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from z0int.gstack_context import attach_context_candidate, normalize_context_bill


def _runner():
    path = Path(__file__).resolve().parents[1] / "benchmarks" / "gstack_context_value" / "run.py"
    spec = importlib.util.spec_from_file_location("gstack_context_value_eval", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_root_cli_exposes_gstack_bill_shadow_command() -> None:
    from z0int.cli import build_parser

    args = build_parser().parse_args(["context", "gstack-bill", "--input", "bill.json"])
    assert args.context_cmd == "gstack-bill"
    assert args.input == "bill.json"


def test_gstack_context_value_fixture_suite() -> None:
    result = _runner().run_cases()
    assert result["passed"] == result["total"]
    assert result["total"] >= 5


def test_declared_benefit_never_becomes_verified_outcome_or_authority() -> None:
    result = normalize_context_bill(
        {
            "tokenSource": "estimate",
            "tokenEstimateErrorPct": 40,
            "skills": [{"name": "qa", "eagerTokens": 100, "estimatedTokenSaving": 10000}],
        }
    )
    candidate = result["candidates"][0]
    assert candidate["value_density"] == 100
    assert candidate["benefit"]["state"] == "declared_estimate"
    assert candidate["outcome"]["verified"] is False
    assert candidate["authority"]["granted"] == []
    assert candidate["traffic_eligible"] is False


def test_receipt_join_is_correlation_only() -> None:
    candidate = normalize_context_bill(
        {
            "tokenSource": "estimate",
            "tokenEstimateErrorPct": 40,
            "skills": [{"name": "qa", "eagerTokens": 100, "estimatedTokenSaving": 200}],
        }
    )["candidates"][0]
    receipt = {"trace_id": "t1", "success": False, "extra": {"experiment_id": "e1"}}
    joined = attach_context_candidate(receipt, candidate)
    assert joined["success"] is False
    assert joined["extra"]["experiment_id"] == "e1"
    assert joined["extra"]["gstack_context_candidate_id"] == candidate["candidate_id"]


def test_invalid_bill_fails_closed() -> None:
    with pytest.raises(ValueError):
        normalize_context_bill({"skills": "not-a-list"})
