from z0int.agentweb_bridge_wire import (
    unwrap_agentweb_bridge_request,
    wrap_agentweb_bridge_response,
)


def envelope(*, kind="decide", capability="experiment.report_type_v1", payload=None):
    return {
        "schema": "agentweb.z0.bridge.v1",
        "operation_id": "op-1",
        "trace_id": "a" * 64,
        "parent_agent": "agentweb:" + "b" * 24,
        "mode": "shadow",
        "kind": kind,
        "capability": capability,
        "policy": {
            "allow_remote": True,
            "free_only": True,
            "experimental": True,
            "risk_class": "read",
            "approval_state": "not_required",
        },
        "payload": payload or {
            "function": "experimental_choice",
            "decision_id": "report_type_v1",
            "state": {"filename": "q3.html"},
            "instructions": "Classify.",
            "options": {"creative": "creative", "performance": "metrics"},
        },
    }


def test_legacy_body_passes_through_unchanged():
    body = {"harness": "agentweb", "function": "summarization"}
    bridge, legacy = unwrap_agentweb_bridge_request("/v1/plan", body)
    assert bridge is None
    assert legacy is body


def test_choice_envelope_unwraps_to_existing_decision_contract():
    bridge, legacy = unwrap_agentweb_bridge_request(
        "/v1/experimental/choice",
        envelope(),
    )
    assert bridge is not None
    assert legacy == {
        "function": "experimental_choice",
        "decision_id": "report_type_v1",
        "state": {"filename": "q3.html"},
        "instructions": "Classify.",
        "options": {"creative": "creative", "performance": "metrics"},
        "harness": "agentweb",
        "trace_id": "a" * 64,
        "parent_agent": "agentweb:" + "b" * 24,
        "allow_remote": True,
        "experimental": True,
    }


def test_generic_dispatch_preserves_free_only_policy():
    request = envelope(
        kind="dispatch",
        capability="summarization",
        payload={"function": "summarization", "task": "summarize"},
    )
    request["mode"] = "active"
    bridge, legacy = unwrap_agentweb_bridge_request("/v1/intelligence", request)
    assert bridge is not None
    assert legacy["free_only"] is True
    assert legacy["function"] == "summarization"


def test_wrong_kind_is_rejected_before_authority_code():
    request = envelope(kind="plan")
    try:
        unwrap_agentweb_bridge_request("/v1/experimental/choice", request)
    except ValueError as exc:
        assert "expected 'decide'" in str(exc)
    else:
        raise AssertionError("wrong bridge kind was accepted")


def test_paid_decision_lane_rejects_free_only_policy():
    request = envelope()
    request["policy"]["free_only"] = True
    try:
        unwrap_agentweb_bridge_request("/v1/experimental/choice", request)
    except ValueError as exc:
        assert "free_only=true" in str(exc)
    else:
        raise AssertionError("paid decision lane accepted free_only=true")


def test_response_wrap_preserves_legacy_result_and_shadow_non_application():
    request = envelope()
    legacy_result = {
        "ok": True,
        "executed": True,
        "applied": False,
        "mode": "shadow",
        "replayed": True,
        "output": {"label": "performance"},
        "dispatch_receipt_id": "receipt-1",
    }
    wrapped = wrap_agentweb_bridge_response(request, legacy_result)
    assert wrapped["schema"] == "agentweb.z0.bridge.v1"
    assert wrapped["kind"] == "decide"
    assert wrapped["status"] == "completed"
    assert wrapped["applied"] is False
    assert wrapped["replayed"] is True
    assert wrapped["output"] == legacy_result
    assert wrapped["evidence"] == {"receipt_id": "receipt-1"}
