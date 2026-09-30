from z0int.ao_bridge import (
    OUTCOME_SCHEMA,
    SPAWN_SCHEMA,
    join_ao_outcome,
    spawn_decision,
)


def request(trace="ao-spawn-s1"):
    return {
        "schema": SPAWN_SCHEMA,
        "trace_id": trace,
        "session_id": "proj-1",
        "project_id": "proj",
        "kind": "worker",
        "task": "fix the retry race",
        "current": {
            "harness": "codex",
            "model": "gpt-5",
            "mode": "chat",
            "permission": "default",
        },
        "constraints": {
            "explicit_harness": False,
            "explicit_model": False,
            "explicit_mode": False,
        },
    }


def test_spawn_shadow_is_durable_and_idempotent(tmp_path):
    first = spawn_decision(request(), root=tmp_path)
    second = spawn_decision(request(), root=tmp_path)
    assert first["action"] == "abstain"
    assert first["recommendation"]["harness"] == "codex"
    assert first["replayed"] is False
    assert second["decision_id"] == first["decision_id"]
    assert second["replayed"] is True


def test_spawn_trace_conflict_fails_closed(tmp_path):
    spawn_decision(request(), root=tmp_path)
    changed = request()
    changed["task"] = "different task"
    try:
        spawn_decision(changed, root=tmp_path)
    except ValueError as exc:
        assert "reused" in str(exc)
    else:
        raise AssertionError("expected trace conflict")


def test_outcome_join_is_session_bound_and_idempotent(tmp_path):
    spawn_decision(request(), root=tmp_path)
    event = {
        "schema": OUTCOME_SCHEMA,
        "trace_id": "ao-spawn-s1",
        "session_id": "proj-1",
        "outcome": {
            "execution_completed": True,
            "test_pass": True,
            "verification_source": "ao-ci",
        },
    }
    first = join_ao_outcome(event, root=tmp_path)
    second = join_ao_outcome(event, root=tmp_path)
    assert first["outcome_tier"] == "gold"
    assert first["replayed"] is False
    assert second["outcome_tier"] == "gold"
    assert second["replayed"] is True


def test_outcome_cannot_cross_session_boundary(tmp_path):
    spawn_decision(request(), root=tmp_path)
    event = {
        "schema": OUTCOME_SCHEMA,
        "trace_id": "ao-spawn-s1",
        "session_id": "other",
        "outcome": {"execution_completed": True},
    }
    try:
        join_ao_outcome(event, root=tmp_path)
    except ValueError as exc:
        assert "session" in str(exc)
    else:
        raise AssertionError("expected session mismatch")
