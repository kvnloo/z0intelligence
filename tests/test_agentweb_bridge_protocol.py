import json
from pathlib import Path

from z0int.agentweb_bridge_protocol import (
    failure_disposition,
    validate_request_v1,
    validate_response_v1,
)


FIXTURES = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "agentweb-z0-bridge-v1.json"
)


def test_agentweb_bridge_fixture_corpus():
    corpus = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert corpus["schema"] == "agentweb.z0.bridge.fixtures.v1"

    for case in corpus["cases"]:
        errors = validate_request_v1(case["request"])
        if case["valid"]:
            assert errors == [], (case["name"], errors)
        else:
            assert case["error"] in errors, (case["name"], errors)


def test_shadow_timeout_is_never_ambiguous_execution():
    assert failure_disposition("shadow", "timeout") == {
        "status": "not_executed",
        "reconcile_required": False,
        "retry": "caller_may_fallback",
    }


def test_active_timeout_requires_same_identity_reconciliation():
    assert failure_disposition("active", "timeout") == {
        "status": "unknown",
        "reconcile_required": True,
        "retry": "same_identity_only",
    }


def test_trace_conflict_is_rejected_without_second_execution():
    assert failure_disposition("active", "trace_conflict") == {
        "status": "rejected",
        "reconcile_required": False,
        "retry": "none",
    }


def test_shadow_response_cannot_claim_application_or_unknown_execution():
    errors = validate_response_v1(
        {
            "schema": "agentweb.z0.bridge.v1",
            "operation_id": "fixture-op-1",
            "trace_id": "a" * 64,
            "mode": "shadow",
            "status": "unknown",
            "applied": True,
            "replayed": False,
            "reconcile_required": True,
            "retry": "same_identity_only",
        }
    )

    assert "shadow response cannot be applied" in errors
    assert "shadow response cannot have unknown execution status" in errors
    assert "shadow response cannot require reconciliation" in errors
