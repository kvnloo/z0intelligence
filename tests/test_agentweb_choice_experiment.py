import json

import pytest

from z0int.decision_experiment import run_choice_experiment


class FakeAdmission:
    def __init__(self):
        self.releases = []

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
        self.releases.append((permit["token"], status, attempted))


def request():
    return {
        "harness": "agentweb",
        "trace_id": "report-op-1",
        "parent_agent": "agentweb:fixture",
        "function": "experimental_choice",
        "decision_id": "report_type_v1",
        "state": {"filename": "q3-performance.html", "excerpt": "ROAS spend CTR CPC"},
        "instructions": "Classify this report.",
        "options": {
            "creative": "Marketing creative",
            "performance": "Paid media metrics",
            "research": "Research and general reports",
        },
        "allow_remote": True,
        "experimental": True,
    }


def fake_decision(counter):
    def decide(state, *, instructions, options):
        counter["calls"] += 1
        assert state["filename"] == "q3-performance.html"
        assert set(options) == {"creative", "performance", "research"}
        return {
            "backend": "jev",
            "model": "jev-1.13.0",
            "revision": "jev-1.13.0",
            "label": "performance",
            "probabilities": {"creative": 0.01, "performance": 0.98, "research": 0.01},
            "confidence": 0.98,
            "latency_ms": 12.5,
            "usage": {"input_tokens": 42, "output_tokens": 3},
        }
    return decide


def test_shadow_choice_requires_explicit_remote_and_experiment_flags(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.setenv("Z0INT_EXPERIMENTAL_JEV_SHADOW", "1")
    counter = {"calls": 0}

    args = request()
    args["allow_remote"] = False
    result = run_choice_experiment(
        args,
        decision_fn=fake_decision(counter),
        admission_factory=FakeAdmission,
    )

    assert result["reason"] == "remote_context_not_authorized"
    assert result["executed"] is False
    assert result["applied"] is False
    assert counter["calls"] == 0


def test_identical_experiment_replays_one_physical_call_and_never_applies(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.setenv("Z0INT_EXPERIMENTAL_JEV_SHADOW", "1")
    counter = {"calls": 0}

    first = run_choice_experiment(
        request(),
        decision_fn=fake_decision(counter),
        admission_factory=FakeAdmission,
    )
    second = run_choice_experiment(
        request(),
        decision_fn=fake_decision(counter),
        admission_factory=FakeAdmission,
    )

    assert counter["calls"] == 1
    assert first["ok"] is True and first["executed"] is True
    assert first["applied"] is False
    assert first["mode"] == "shadow"
    assert second["replayed"] is True
    assert second["applied"] is False

    rows = [
        json.loads(line)
        for line in (tmp_path / "receipts" / "decisions.jsonl").read_text().splitlines()
    ]
    completed = [
        row for row in rows
        if row.get("capability_id") == "experiment.report_type_v1"
        and row.get("extra", {}).get("status") == "completed"
    ]
    assert len(completed) == 1
    row = completed[0]
    assert row["execution"] == "shadow"
    assert row["extra"]["quality_authoritative"] is False
    assert row["extra"]["applied"] is False
    assert "filename" not in json.dumps(row)
    assert row["input_tokens"] == 42
    assert row["output_tokens"] == 3


def test_mutated_request_under_same_trace_is_conflict_not_second_call(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.setenv("Z0INT_EXPERIMENTAL_JEV_SHADOW", "1")
    counter = {"calls": 0}
    original = request()

    run_choice_experiment(
        original,
        decision_fn=fake_decision(counter),
        admission_factory=FakeAdmission,
    )

    changed = request()
    changed["options"] = {**changed["options"], "research": "Mutated definition"}

    with pytest.raises(ValueError, match="trace_id reused"):
        run_choice_experiment(
            changed,
            decision_fn=fake_decision(counter),
            admission_factory=FakeAdmission,
        )

    assert counter["calls"] == 1


def test_paid_shadow_flag_defaults_off(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.delenv("Z0INT_EXPERIMENTAL_JEV_SHADOW", raising=False)
    counter = {"calls": 0}
    result = run_choice_experiment(
        request(),
        decision_fn=fake_decision(counter),
        admission_factory=FakeAdmission,
    )
    assert result["reason"] == "experimental_paid_jev_shadow_disabled"
    assert counter["calls"] == 0
