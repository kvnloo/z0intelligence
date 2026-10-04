import copy
import hashlib

import pytest

from z0int.o8_bridge import (
    BridgeValidationError,
    PROTOCOL_VERSION,
    canonical_request_fingerprint,
    derive_trace_id,
    handle_bridge_request,
    join_o8_observation,
    project_intelligence_args,
    project_state_packet,
    validate_bridge_request,
    validate_o8_observation,
)


def request():
    instance = "o8-instance:0123456789abcdef01234567"
    operation = "judgment:jr-123"
    evidence = "lane has a changed diff and tests have not run"
    return {
        "protocol_version": PROTOCOL_VERSION,
        "mode": "shadow",
        "operation_id": operation,
        "trace_id": derive_trace_id(instance, operation),
        "integration_instance": instance,
        "deadline_unix_ms": 4_102_444_800_000,
        "lane": {
            "id": "lane-123",
            "packet_id": "packet-456",
            "project_id": "project-1",
            "runtime": "codex",
            "revision": 7,
        },
        "capability": {
            "function": "verification_needed",
            "task": "Does this turn require an explicit verification pass?",
            "state": {
                "files_changed": 2,
                "tests_run": False,
                "user_asked_ship": True,
            },
            "expected_parent_tokens": 900,
            "expected_parent_ms": 2000,
            "experimental": True,
            "automatic": False,
            "max_tokens": 128,
        },
        "policy": {
            "allow_remote_context": False,
            "free_only": True,
            "risk_class": "read",
            "approval_state": "not_required",
        },
        "evidence": [
            {
                "source_id": "o8:lane:lane-123",
                "retrieved_at": "2026-10-04T02:00:00Z",
                "sha256": hashlib.sha256(evidence.encode()).hexdigest(),
                "content": evidence,
                "privacy": "LOCAL",
            }
        ],
        "correlation": {
            "judgment_receipt_id": "jr-123",
            "approval_id": "approval-9",
        },
    }


def test_request_validates_and_projects_to_existing_intelligence_contract():
    value = request()
    validate_bridge_request(value, now_unix_ms=1_000)
    projected = project_intelligence_args(value)

    assert projected["harness"] == "codex"
    assert projected["trace_id"] == value["trace_id"]
    assert projected["function"] == "verification_needed"
    assert projected["allow_remote"] is False
    assert projected["free_only"] is True
    assert projected["state"] == value["capability"]["state"]
    assert projected["caller_request_sha256"] == canonical_request_fingerprint(value)
    assert "o8.lane.revision" in projected["context"]


def test_state_packet_is_evidence_not_authority():
    packet = project_state_packet(request())
    assert packet["schema"] == "z0int.o8_state_packet.v1"
    assert packet["authorization"]["can_mutate"] is False
    claims = {row["key"]: row["value"] for row in packet["current_claims"]}
    assert claims["o8.lane.id"] == "lane-123"
    assert claims["o8.lane.revision"] == 7
    assert claims["o8.policy.approval_state"] == "not_required"
    assert packet["evidence"][0]["content"].startswith("lane has")


def test_later_outcome_join_does_not_change_request_fingerprint():
    first = request()
    second = copy.deepcopy(first)
    second["correlation"]["outcome_id"] = "outcome-later"
    assert canonical_request_fingerprint(first) == canonical_request_fingerprint(second)


def test_same_operation_semantic_mutation_changes_fingerprint_not_trace():
    first = request()
    second = copy.deepcopy(first)
    second["capability"]["task"] = "Different question"
    assert first["trace_id"] == second["trace_id"]
    assert canonical_request_fingerprint(first) != canonical_request_fingerprint(second)


def test_raw_credentials_and_session_identity_are_rejected():
    for key in ("api_key", "oauthToken", "sessionKey", "user_id", "password"):
        value = request()
        value["capability"]["state"] = {"nested": {key: "must-not-cross"}}
        with pytest.raises(BridgeValidationError, match="forbidden"):
            validate_bridge_request(value, now_unix_ms=1_000)


def test_active_mutation_requires_o8_approval():
    value = request()
    value["mode"] = "active"
    value["policy"]["risk_class"] = "mutation"
    value["policy"]["approval_state"] = "unknown"
    with pytest.raises(BridgeValidationError, match="approval_state=granted"):
        validate_bridge_request(value, now_unix_ms=1_000)

    value["policy"]["approval_state"] = "granted"
    validate_bridge_request(value, now_unix_ms=1_000)


def test_o8_control_room_is_not_silently_admitted_as_active_aodl_executor():
    value = request()
    value["mode"] = "active"
    value["lane"]["runtime"] = "o8"
    value["policy"]["risk_class"] = "read"
    with pytest.raises(BridgeValidationError, match="control room"):
        validate_bridge_request(value, now_unix_ms=1_000)


def test_remote_context_rejects_local_private_evidence():
    value = request()
    value["policy"]["allow_remote_context"] = True
    with pytest.raises(BridgeValidationError, match="remote-context safe"):
        validate_bridge_request(value, now_unix_ms=1_000)


def test_shadow_route_never_enters_dispatch_and_is_receipt_backed(
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
        lambda args: pytest.fail("shadow entered dispatch"),
    )

    value = request()
    result = handle_bridge_request(value, receipt_root=tmp_path)

    assert result["ok"] is True
    assert result["executed"] is False
    assert result["route"]["kind"] == "PARENT_ONLY"
    assert result["replayed"] is False

    stored = receipt.find_receipt(value["trace_id"], root=tmp_path)
    assert stored["execution"] == "shadow"
    assert stored["action_taken"] == "not_applied"
    assert stored["extra"]["o8_lane_id"] == "lane-123"
    assert stored["extra"]["o8_packet_id"] == "packet-456"
    assert stored["extra"]["o8_judgment_receipt_id"] == "jr-123"
    assert stored["extra"]["physical_call_attempted"] is False


def test_shadow_replay_is_idempotent(monkeypatch, tmp_path):
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
            "reason": "fixture",
            "executed": False,
        }

    monkeypatch.setattr(intelligence, "route", route_once)
    value = request()

    first = handle_bridge_request(value, receipt_root=tmp_path)
    second = handle_bridge_request(value, receipt_root=tmp_path)

    assert first["replayed"] is False
    assert second["replayed"] is True
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
    changed["capability"]["task"] = "semantic mutation"
    with pytest.raises(BridgeValidationError, match="shadow trace conflict"):
        handle_bridge_request(changed, receipt_root=tmp_path)


def observation(value, **overrides):
    base = {
        "protocol_version": PROTOCOL_VERSION,
        "trace_id": value["trace_id"],
        "observation_id": "obs-1",
        "lane_id": value["lane"]["id"],
        "lane_revision": value["lane"]["revision"],
        "state": "completed",
        "outcome": "pr_opened",
        "observed_at": "2026-10-04T02:10:00Z",
    }
    base.update(overrides)
    return base


def _seed_shadow(monkeypatch, tmp_path):
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
    value = request()
    handle_bridge_request(value, receipt_root=tmp_path)
    return value


def test_o8_terminal_outcome_is_execution_evidence_not_verified_success(
    monkeypatch,
    tmp_path,
):
    value = _seed_shadow(monkeypatch, tmp_path)
    joined = join_o8_observation(observation(value), receipt_root=tmp_path)
    assert joined["outcome_tier"] == "execution"
    assert joined["quality_verified"] is False


def test_verified_success_requires_independent_verification_source(
    monkeypatch,
    tmp_path,
):
    value = _seed_shadow(monkeypatch, tmp_path)
    bad = observation(value, verified_success=True)
    with pytest.raises(BridgeValidationError, match="verification_source"):
        validate_o8_observation(bad)

    good = observation(
        value,
        verified_success=True,
        verification_source="frozen_acceptance_tests",
    )
    joined = join_o8_observation(good, receipt_root=tmp_path)
    assert joined["outcome_tier"] == "gold"
    assert joined["quality_verified"] is True


def test_stale_observation_cannot_override_newer_lane_revision(
    monkeypatch,
    tmp_path,
):
    value = _seed_shadow(monkeypatch, tmp_path)
    stale = observation(value, lane_revision=6)
    with pytest.raises(BridgeValidationError, match="stale"):
        join_o8_observation(stale, receipt_root=tmp_path)
