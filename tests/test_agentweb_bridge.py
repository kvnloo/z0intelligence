import copy
import hashlib

import pytest

from z0int.agentweb_bridge import (
    BridgeValidationError,
    PROTOCOL_VERSION,
    canonical_request_fingerprint,
    derive_trace_id,
    failure_semantics,
    handle_bridge_request,
    project_intelligence_args,
    validate_bridge_request,
)


def request():
    parent = "agentweb:b9c84322f82434cb46e239d2"
    operation = "op-1"
    content = "source-backed evidence"
    return {
        "protocol_version": PROTOCOL_VERSION,
        "mode": "shadow",
        "operation_id": operation,
        "trace_id": derive_trace_id(parent, operation),
        "integration_instance": "agentweb-instance:e759e9548f57a639a875c728",
        "parent_agent": parent,
        "deadline_unix_ms": 4_102_444_800_000,
        "capability": {
            "function": "evidence_sufficiency",
            "task": "Check whether the evidence supports the claim.",
            "state": {"question": "Is the claim supported?", "evidence": content},
            "expected_parent_tokens": 800,
            "expected_parent_ms": 1500,
            "automatic": False,
            "experimental": False,
            "max_tokens": 256,
        },
        "policy": {
            "allow_remote_context": False,
            "free_only": True,
            "risk_class": "read",
            "approval_state": "not_required",
        },
        "evidence": [
            {
                "source_id": "agentweb:research:1",
                "retrieved_at": "2026-10-01T06:00:00Z",
                "sha256": hashlib.sha256(content.encode()).hexdigest(),
                "content": content,
            }
        ],
        "correlation": {
            "reliability_event_id": "rel-1",
            "observation_id": "obs-1",
        },
    }


def test_valid_request_projects_to_existing_intelligence_contract():
    value = request()
    validate_bridge_request(value, now_unix_ms=1_000)
    projected = project_intelligence_args(value)
    assert projected == {
        "harness": "agentweb",
        "trace_id": value["trace_id"],
        "parent_agent": value["parent_agent"],
        "function": "evidence_sufficiency",
        "task": "Check whether the evidence supports the claim.",
        "state": {"question": "Is the claim supported?", "evidence": "source-backed evidence"},
        "expected_parent_tokens": 800,
        "expected_parent_ms": 1500,
        "allow_remote": False,
        "experimental": False,
        "automatic": False,
        "integration_instance": value["integration_instance"],
        "free_only": True,
        "caller_request_sha256": "9ca1481d7b9494c7b5ae2d75148722be73f1e99a899e7a1815cd17c5805d269d",
        "max_tokens": 256,
    }


def test_cross_language_semantic_fingerprint_vector():
    assert canonical_request_fingerprint(request()) == (
        "9ca1481d7b9494c7b5ae2d75148722be73f1e99a899e7a1815cd17c5805d269d"
    )


def test_same_operation_changed_request_changes_fingerprint_but_not_trace():
    first = request()
    second = copy.deepcopy(first)
    second["capability"]["task"] = "Different semantic request"
    assert first["trace_id"] == second["trace_id"]
    assert canonical_request_fingerprint(first) != canonical_request_fingerprint(second)


def test_observation_join_id_does_not_change_request_fingerprint():
    first = request()
    second = copy.deepcopy(first)
    second["correlation"]["observation_id"] = "later-observation"
    assert canonical_request_fingerprint(first) == canonical_request_fingerprint(second)


def test_bridge_only_provenance_mutation_reaches_dispatch_fingerprint():
    from z0int import dispatch_authority

    first = request()
    second = copy.deepcopy(first)
    second["evidence"][0]["content"] = "changed provenance payload"
    second["evidence"][0]["sha256"] = hashlib.sha256(
        second["evidence"][0]["content"].encode()
    ).hexdigest()

    first_projected = project_intelligence_args(first)
    second_projected = project_intelligence_args(second)

    assert first_projected["trace_id"] == second_projected["trace_id"]
    assert first_projected["task"] == second_projected["task"]
    assert first_projected["state"] == second_projected["state"]
    assert first_projected["caller_request_sha256"] != second_projected["caller_request_sha256"]
    assert dispatch_authority.fingerprint(first_projected) != dispatch_authority.fingerprint(second_projected)


def test_observation_join_change_does_not_change_dispatch_fingerprint():
    from z0int import dispatch_authority

    first = request()
    second = copy.deepcopy(first)
    second["correlation"]["observation_id"] = "later-observation"

    first_projected = project_intelligence_args(first)
    second_projected = project_intelligence_args(second)
    assert first_projected == second_projected
    assert dispatch_authority.fingerprint(first_projected) == dispatch_authority.fingerprint(second_projected)


def test_shadow_handler_never_enters_dispatch_and_persists_observation(
    monkeypatch,
    tmp_path,
):
    from z0int import intelligence, receipt

    monkeypatch.setattr(
        intelligence,
        "routing_snapshot",
        lambda args: {"fixture": True},
    )
    monkeypatch.setattr(
        intelligence,
        "route",
        lambda args, snapshot: {
            "kind": "PARENT_ONLY",
            "reason": "fixture",
            "executed": False,
        },
    )
    monkeypatch.setattr(
        intelligence,
        "dispatch",
        lambda args: pytest.fail("shadow mode entered physical dispatch"),
    )

    value = request()
    result = handle_bridge_request(value, receipt_root=tmp_path)
    assert result["ok"] is True
    assert result["mode"] == "shadow"
    assert result["executed"] is False
    assert result["reconcile_required"] is False
    assert result["replayed"] is False
    assert result["request_sha256"] == canonical_request_fingerprint(value)
    assert result["route"]["kind"] == "PARENT_ONLY"

    stored = receipt.find_receipt(value["trace_id"], root=tmp_path)
    assert stored is not None
    assert stored["execution"] == "shadow"
    assert stored["route"] == "shadow"
    assert stored["prediction"] == "PARENT_ONLY"
    assert stored["action_taken"] == "not_applied"
    assert "outcome" not in stored
    assert stored["measurement_state"] == "unknown"
    assert stored["extra"]["physical_call_attempted"] is False
    assert stored["extra"]["caller_request_sha256"] == canonical_request_fingerprint(value)
    assert stored["extra"]["observation_id"] == "obs-1"
    assert stored["extra"]["reliability_event_id"] == "rel-1"


def test_shadow_replay_is_idempotent_and_does_not_reroute(monkeypatch, tmp_path):
    from z0int import intelligence, receipt

    calls = {"route": 0}

    monkeypatch.setattr(
        intelligence,
        "routing_snapshot",
        lambda args: {"fixture": True},
    )

    def route_once(args, snapshot):
        calls["route"] += 1
        return {
            "kind": "PARENT_ONLY",
            "reason": "stable fixture",
            "executed": False,
        }

    monkeypatch.setattr(intelligence, "route", route_once)
    value = request()

    first = handle_bridge_request(value, receipt_root=tmp_path)
    second = handle_bridge_request(value, receipt_root=tmp_path)

    assert first["replayed"] is False
    assert second["replayed"] is True
    assert first["route"] == second["route"]
    assert calls["route"] == 1

    rows = receipt.receipts_path(tmp_path).read_text().strip().splitlines()
    assert len(rows) == 1


def test_shadow_same_trace_changed_request_is_conflict(monkeypatch, tmp_path):
    from z0int import intelligence

    monkeypatch.setattr(
        intelligence,
        "routing_snapshot",
        lambda args: {"fixture": True},
    )
    monkeypatch.setattr(
        intelligence,
        "route",
        lambda args, snapshot: {
            "kind": "PARENT_ONLY",
            "reason": "fixture",
            "executed": False,
        },
    )

    first = request()
    handle_bridge_request(first, receipt_root=tmp_path)

    changed = copy.deepcopy(first)
    changed["capability"]["task"] = "semantic mutation under same operation"
    with pytest.raises(BridgeValidationError, match="shadow trace conflict"):
        handle_bridge_request(changed, receipt_root=tmp_path)


def test_trace_must_be_derived_from_parent_and_operation():
    value = request()
    value["trace_id"] = "0" * 64
    with pytest.raises(BridgeValidationError, match="trace_id does not match"):
        validate_bridge_request(value, now_unix_ms=1_000)


def test_raw_identity_and_credentials_are_rejected_recursively():
    for key in ("userId", "session_id", "api_key", "accessToken", "credentials"):
        value = request()
        value["capability"]["state"] = {"nested": {key: "must-not-cross"}}
        with pytest.raises(BridgeValidationError, match="forbidden"):
            validate_bridge_request(value, now_unix_ms=1_000)


def test_expired_deadline_is_rejected_before_execution():
    value = request()
    value["deadline_unix_ms"] = 999
    with pytest.raises(BridgeValidationError, match="deadline_expired"):
        validate_bridge_request(value, now_unix_ms=1_000)


def test_active_mutation_requires_explicit_granted_approval():
    value = request()
    value["mode"] = "active"
    value["policy"]["risk_class"] = "external_side_effect"
    value["policy"]["approval_state"] = "unknown"
    with pytest.raises(BridgeValidationError, match="approval_state=granted"):
        validate_bridge_request(value, now_unix_ms=1_000)

    value["policy"]["approval_state"] = "granted"
    validate_bridge_request(value, now_unix_ms=1_000)


def test_evidence_digest_is_checked():
    value = request()
    value["evidence"][0]["sha256"] = "0" * 64
    with pytest.raises(BridgeValidationError, match="does not match content"):
        validate_bridge_request(value, now_unix_ms=1_000)


def test_protocol_version_is_fail_closed():
    value = request()
    value["protocol_version"] = "agentweb.z0.bridge.v2"
    with pytest.raises(BridgeValidationError, match="unsupported protocol_version"):
        validate_bridge_request(value, now_unix_ms=1_000)


def test_failure_semantics_distinguish_shadow_from_physical_attempts():
    shadow = failure_semantics("shadow")
    assert shadow.execution_state == "not_attempted"
    assert shadow.reconcile_required is False
    assert shadow.incumbent_fallback_allowed is True

    advisory = failure_semantics("advisory")
    assert advisory.execution_state == "attempted_unknown"
    assert advisory.reconcile_required is True
    assert advisory.incumbent_fallback_allowed is True

    active = failure_semantics("active")
    assert active.execution_state == "attempted_unknown"
    assert active.reconcile_required is True
    assert active.incumbent_fallback_allowed is False
