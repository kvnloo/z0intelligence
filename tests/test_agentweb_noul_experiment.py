import json

import pytest

from z0int.decision_experiment import run_noul_experiment


class FakeAdmission:
    def acquire(self, provider, call_id, context):
        return {
            "admitted": True,
            "token": "permit-" + call_id,
            "provider": provider,
            "inflight_at_admission": 1,
            "cap": 1,
            "capped": False,
            "reason": "eligible_unprobed",
        }

    def release(self, permit, status, latency_ms, retry_after=None, attempted=True):
        return None


def request():
    return {
        "harness": "agentweb",
        "trace_id": "stop-op-1",
        "parent_agent": "agentweb:" + "a" * 24,
        "function": "experimental_noul",
        "decision_id": "stop_request_v1",
        "state": {"message": "that's enough for now, please stop"},
        "proposition": "The user's message explicitly asks the assistant to stop, pause, wait, or do no more work right now.",
        "allow_remote": True,
        "experimental": True,
    }


def fake_decision(counter):
    def decide(state, *, proposition):
        counter["calls"] += 1
        assert "stop" in state["message"]
        assert "explicitly asks" in proposition
        return {
            "backend": "jev",
            "model": "jev-1.13.0",
            "revision": "jev-1.13.0",
            "probability": 0.97,
            "latency_ms": 11,
            "usage": {"input_tokens": 21, "output_tokens": 1},
        }
    return decide


def test_noul_replays_one_physical_call_and_never_applies(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.setenv("Z0INT_EXPERIMENTAL_JEV_SHADOW", "1")
    counter = {"calls": 0}

    first = run_noul_experiment(
        request(),
        decision_fn=fake_decision(counter),
        admission_factory=FakeAdmission,
    )
    second = run_noul_experiment(
        request(),
        decision_fn=fake_decision(counter),
        admission_factory=FakeAdmission,
    )

    assert counter["calls"] == 1
    assert first["ok"] is True
    assert first["output"]["probability"] == 0.97
    assert first["applied"] is False
    assert second["replayed"] is True

    rows = [
        json.loads(line)
        for line in (tmp_path / "receipts" / "decisions.jsonl").read_text().splitlines()
    ]
    completed = [
        row for row in rows
        if row.get("capability_id") == "experiment.stop_request_v1"
        and row.get("extra", {}).get("status") == "completed"
    ]
    assert len(completed) == 1
    row = completed[0]
    assert row["execution"] == "shadow"
    assert row["extra"]["decision_kind"] == "noul"
    assert row["extra"]["quality_authoritative"] is False
    assert row["extra"]["applied"] is False
    assert "that's enough" not in json.dumps(row)
    assert "proposition" not in json.dumps(row)


def test_noul_mutation_conflicts_under_same_trace(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.setenv("Z0INT_EXPERIMENTAL_JEV_SHADOW", "1")
    counter = {"calls": 0}
    run_noul_experiment(
        request(),
        decision_fn=fake_decision(counter),
        admission_factory=FakeAdmission,
    )
    changed = request()
    changed["state"] = {"message": "keep going"}

    with pytest.raises(ValueError, match="trace_id reused"):
        run_noul_experiment(
            changed,
            decision_fn=fake_decision(counter),
            admission_factory=FakeAdmission,
        )
    assert counter["calls"] == 1


def test_noul_remote_authorization_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.setenv("Z0INT_EXPERIMENTAL_JEV_SHADOW", "1")
    counter = {"calls": 0}
    args = request()
    args["allow_remote"] = False
    result = run_noul_experiment(
        args,
        decision_fn=fake_decision(counter),
        admission_factory=FakeAdmission,
    )
    assert result["reason"] == "remote_context_not_authorized"
    assert result["applied"] is False
    assert counter["calls"] == 0
