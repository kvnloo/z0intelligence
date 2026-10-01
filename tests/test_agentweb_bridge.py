import copy
import hashlib

import pytest

from z0int.agentweb_bridge import (
    BridgeValidationError,
    PROTOCOL_VERSION,
    canonical_request_fingerprint,
    derive_trace_id,
    failure_semantics,
    project_intelligence_args,
    validate_bridge_request,
)


def request():
    parent = "agentweb:" + "a" * 24
    operation = "op-1"
    content = "source-backed evidence"
    return {
        "protocol_version": PROTOCOL_VERSION,
        "mode": "shadow",
        "operation_id": operation,
        "trace_id": derive_trace_id(parent, operation),
        "integration_instance": "agentweb-instance:" + "b" * 16,
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
        "max_tokens": 256,
    }


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
