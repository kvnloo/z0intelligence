import json

import pytest

from z0int.reliability_observation import ingest_observation, validate_observation


def observation():
    return {
        "schema": "z0int.decision_receipt.v1",
        "trace_id": "agentweb-observe-" + "a" * 64,
        "session_id": "agentweb:" + "b" * 24,
        "capability_id": "agentweb.tool.ask_crm",
        "provider": "agentweb",
        "route": "shadow",
        "execution": "log_only",
        "action_taken": "success",
        "latency_ms": 12,
        "measurement_state": "partial",
        "state_reason": "observational_agentweb_tool_outcome_not_quality_verification",
        "outcome": {
            "source": "agentweb_reliability_event",
            "execution_completed": True,
            "tool_ok": True,
            "success": True,
        },
        "extra": {
            "observational": True,
            "tool": "ask_crm",
            "reliability_outcome": "success",
            "platform": "emma_live",
        },
    }


def test_observation_is_idempotent_and_stores_no_quality_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    first = ingest_observation(observation())
    second = ingest_observation(observation())
    assert first == {"ok": True, "receipt_id": observation()["trace_id"], "replayed": False}
    assert second == {"ok": True, "receipt_id": observation()["trace_id"], "replayed": True}

    rows = [
        json.loads(line)
        for line in (tmp_path / "receipts" / "decisions.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["execution"] == "log_only"
    assert row["measurement_state"] == "partial"
    assert row["extra"]["quality_authoritative"] is False
    assert row["extra"]["status"] == "observed"


def test_changed_payload_under_same_observation_trace_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    ingest_observation(observation())
    changed = observation()
    changed["latency_ms"] = 13
    with pytest.raises(ValueError, match="changed payload"):
        ingest_observation(changed)


@pytest.mark.parametrize("field,value", [
    ("session_id", "raw-session-id"),
    ("execution", "live"),
    ("measurement_state", "complete"),
])
def test_observation_rejects_unsafe_boundary_shapes(field, value):
    row = observation()
    row[field] = value
    with pytest.raises(ValueError):
        validate_observation(row)


def test_observation_rejects_attempt_to_mint_verified_quality():
    row = observation()
    row["outcome"]["verified_success"] = True
    with pytest.raises(ValueError, match="quality fields"):
        validate_observation(row)


def test_observation_rejects_freeform_error_text():
    row = observation()
    row["extra"]["error_summary"] = "sensitive text"
    with pytest.raises(ValueError, match="unsupported fields"):
        validate_observation(row)
