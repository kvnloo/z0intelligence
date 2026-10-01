from __future__ import annotations

import copy
import json

import pytest

from z0int.aodl_observation import project_drift, record_drift
from z0int.receipt import receipts_path
from z0int.tokenomics_emit import emit_aodl_admission_once, events_path


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))


def document():
    return {
        "specVersion": "0.2",
        "graphId": "drift-test",
        "revision": 3,
        "intentGraph": {
            "nodes": [{
                "id": "parent",
                "kind": "task",
                "ports": [{"id": "out", "direction": "out", "schema": "Task"}],
                "capabilities": ["execute"],
                "authorityCeiling": ["execute"],
                "lifecycle": "declared",
            }],
            "edges": [],
        },
        "policies": {
            "kinds": ["sequence"],
            "fanIn": "all",
            "dynamic": {"allowed": False, "maxChildren": 0, "maxDepth": 0},
        },
        "constraints": {"budgets": {"tokens": 1000}, "termination": {"on": "done"}},
        "provenance": {"source": "test", "sourceHash": "0" * 64},
    }


def test_drift_projects_stateupdate_without_mutating_intent():
    doc = document()
    before = json.dumps(doc, sort_keys=True)
    result = record_drift(doc, trace_id="trace-drift", observation={"worker_status": "failed"})

    assert json.dumps(doc, sort_keys=True) == before
    projected = result["document"]
    assert projected["intentGraph"] == doc["intentGraph"]
    assert projected["provenance"]["sourceHash"] == doc["provenance"]["sourceHash"] == "0" * 64
    assert projected["eventLog"] == [result["event"]]
    assert result["event"]["type"] == "stateUpdate"
    assert result["event"]["payload"]["drift"] is True
    assert result["semantic_fingerprint_before"] != result["semantic_fingerprint_after"]

    receipt = result["receipt"]
    assert receipt["schema"] == "z0int.decision_receipt.v1"
    assert receipt["capability_id"] == "aodl.observation"
    assert receipt["execution"] == "log_only"
    assert "success" not in receipt
    assert "verified_success" not in receipt
    assert receipt["extra"]["aodl_intent_source_hash"] == "0" * 64


def test_drift_replay_is_idempotent_for_event_and_receipt():
    first = record_drift(document(), trace_id="trace-drift", observation={"worker_status": "failed"})
    second = record_drift(first["document"], trace_id="trace-drift", observation={"worker_status": "failed"})

    assert second["replayed"] is True
    assert second["receipt_replayed"] is True
    assert second["event"] == first["event"]
    assert second["receipt"] == first["receipt"]
    assert second["document"]["eventLog"] == [first["event"]]
    assert len(receipts_path().read_text().splitlines()) == 1


def test_invalid_contract_fails_before_observation_receipt():
    doc = document()
    doc["intentGraph"]["nodes"][0]["kind"] = ["task"]
    with pytest.raises(ValueError, match="invalid AODL"):
        record_drift(doc, trace_id="bad", observation={"x": 1})
    assert not receipts_path().exists()


def test_project_drift_supports_causal_parents_without_reordering_intent():
    doc = document()
    original_graph = copy.deepcopy(doc["intentGraph"])
    result = project_drift(
        doc,
        trace_id="child",
        observation={"state": "stale"},
        causal_parents=("event-b", "event-a"),
    )
    assert result["event"]["causalParents"] == ["event-b", "event-a"]
    assert result["document"]["intentGraph"] == original_graph
    assert doc.get("eventLog") is None


def test_gate_latency_tokenomics_is_one_time_and_non_success():
    admission = {
        "allowed": True,
        "codes": [],
        "latency_us": 125.0,
        "aodl_canon_version": "aodl-canon-1",
        "aodl_semantic_fingerprint": "aodl-canon-1:" + "a" * 64,
        "aodl_intent_source_hash": "0" * 64,
    }
    assert emit_aodl_admission_once(
        receipt_id="aodl-admission-x",
        caller_trace_id="caller",
        session_id="session",
        harness="dsh",
        admission=admission,
    )
    assert not emit_aodl_admission_once(
        receipt_id="aodl-admission-x",
        caller_trace_id="caller",
        session_id="session",
        harness="dsh",
        admission=admission,
    )
    rows = [json.loads(line) for line in events_path().read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["schema"] == "z0int.aodl_gate_latency.v1"
    assert row["latency_ms"] == pytest.approx(0.125)
    assert row["measurement_scope"] == "gate_latency"
    assert row["task_success"] is None
    assert row["verified_success"] is None
