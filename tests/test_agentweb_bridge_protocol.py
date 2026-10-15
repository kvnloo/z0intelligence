import json
from pathlib import Path

from z0int.agentweb_bridge_protocol import (
    failure_disposition,
    validate_request_v1,
    validate_response_v1,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "agentweb-z0-bridge-v1.json"


def test_agentweb_bridge_fixture_corpus():
    corpus = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert corpus["schema"] == "agentweb.z0.bridge.fixtures.v1"

    for case in corpus["cases"]:
        errors = validate_request_v1(case["request"])
        if case["valid"]:
            assert errors == [], (case["name"], errors)
        else:
            assert case["error"] in errors, (case["name"], errors)


def test_shadow_plan_timeout_is_not_ambiguous_execution():
    assert failure_disposition("shadow", "timeout", "plan") == {
        "status": "not_executed",
        "reconcile_required": False,
        "retry": "caller_may_fallback",
    }


def test_shadow_decide_timeout_requires_same_identity_reconciliation():
    assert failure_disposition("shadow", "timeout", "decide") == {
        "status": "unknown",
        "reconcile_required": True,
        "retry": "same_identity_only",
    }


def test_proven_overload_is_pre_execution_in_active_and_shadow_decide():
    expected = {
        "status": "not_executed",
        "reconcile_required": False,
        "retry": "caller_may_fallback",
    }
    assert failure_disposition("active", "overload_pre_execution", "dispatch") == expected
    assert failure_disposition("shadow", "overload_pre_execution", "decide") == expected


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


def test_side_effect_free_shadow_response_cannot_claim_unknown_execution():
    errors = validate_response_v1({
        "schema": "agentweb.z0.bridge.v1",
        "operation_id": "fixture-op-1",
        "trace_id": "a" * 64,
        "mode": "shadow",
        "kind": "plan",
        "status": "unknown",
        "applied": True,
        "replayed": False,
        "reconcile_required": True,
        "retry": "same_identity_only",
    })

    assert "shadow response cannot be applied" in errors
    assert "side-effect-free shadow response cannot have unknown execution status" in errors
    assert "side-effect-free shadow response cannot require reconciliation" in errors


def test_shadow_decide_response_can_be_unknown_but_never_applied():
    errors = validate_response_v1({
        "schema": "agentweb.z0.bridge.v1",
        "operation_id": "fixture-op-2",
        "trace_id": "b" * 64,
        "mode": "shadow",
        "kind": "decide",
        "status": "unknown",
        "applied": False,
        "replayed": False,
        "reconcile_required": True,
        "retry": "same_identity_only",
    })
    assert errors == []
