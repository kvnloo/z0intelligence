import json
import pytest
from z0int.fsm import live


def test_append_only_batch_dedup_and_decision_resolution(tmp_path):
    from z0int.fsm import supervise as S
    t = S.Task("deleg_a/task-0", "widget", 1, "interrupted", 2, "/evidence.log")
    def report(decisions):
        worker = S.worker_traces([t], {}, {"_spawned": {}, "_reaped": {}}, decisions, 1000, 10)[0]
        assert worker.final is not None
        return {"generated_at": "2026-10-21T12:00:00Z", "workers": [{"phase": worker.final.phase.value}],
            "needs_kevin": {"items": [] if worker.final.phase.value == "Resumed" else [{"worker": t.id,
            "goal": t.goal, "why": "overdue", "recommended": "resume", "log": t.log, "phase": "Overdue"}]}}
    batch = live.publish(report({}), tmp_path)
    ledger = tmp_path / "needs-kevin.jsonl"
    prefix = ledger.read_bytes()
    assert batch["size"] == 1
    assert live.publish(report({}), tmp_path)["items"][0]["id"] == batch["items"][0]["id"]
    assert ledger.read_bytes() == prefix
    assert live.publish(report({t.id: {"action": "resume"}}), tmp_path)["size"] == 0
    assert ledger.read_bytes().startswith(prefix)
    assert len(ledger.read_text().splitlines()) == 2
    assert json.loads((tmp_path / "state.json").read_text())["workers"][0]["phase"] == "Resumed"


def test_live_exit_does_not_mask_runtime_errors(tmp_path, monkeypatch):
    from z0int.fsm import supervise as S
    report = {"generated_at": "2026-10-21T12:00:00Z", "needs_kevin": {"items": []},
              "summary": {"L2": {"violation": 1}}}
    monkeypatch.setattr(S, "build", lambda args: report)
    monkeypatch.setattr(S, "render", lambda report: "findings")
    assert S.main(["--live-dir", str(tmp_path)]) == 0
    assert S.main([]) == 1
    def fail(args):
        raise RuntimeError("source read failed")
    monkeypatch.setattr(S, "build", fail)
    before = (tmp_path / "state.json").read_bytes()
    with pytest.raises(RuntimeError):
        S.main(["--live-dir", str(tmp_path)])
    assert (tmp_path / "state.json").read_bytes() == before
