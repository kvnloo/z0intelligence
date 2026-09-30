import hashlib

from z0int import automatic


def test_agentweb_is_a_supported_automatic_harness():
    assert "agentweb" in automatic.HARNESSES


def test_agentweb_event_normalizes_to_stable_pseudonymous_dispatch_identity():
    event = {
        "harness": "agentweb",
        "session_id": "agentweb-session-secret",
        "turn_id": "operation-42",
        "instance_id": "agentweb-lab-v0",
        "text": "summarize this bounded fixture",
        "allow_remote": False,
    }

    request = automatic.normalize(event)

    assert request["harness"] == "agentweb"
    expected_parent = "agentweb:" + hashlib.sha256(
        b"agentweb-session-secret"
    ).hexdigest()[:24]
    assert request["parent_agent"] == expected_parent
    assert "agentweb-session-secret" not in request["parent_agent"]
    assert request["trace_id"] == hashlib.sha256(
        (expected_parent + "\\0operation-42").encode()
    ).hexdigest()
    assert request["function"] == "summarization"
    assert request["automatic"] is True
    assert request["allow_remote"] is False


def test_shadow_plan_does_not_enter_dispatch(monkeypatch):
    from z0int import intelligence_service

    monkeypatch.setattr(
        intelligence_service,
        "routing_snapshot",
        lambda args: {"fixture": True},
    )
    monkeypatch.setattr(
        intelligence_service,
        "route",
        lambda args, snapshot: {"kind": "PARENT_ONLY", "executed": False},
    )

    def forbidden_dispatch(_args):
        raise AssertionError("shadow planning must never enter dispatch")

    monkeypatch.setattr(intelligence_service, "dispatch", forbidden_dispatch)
    result = intelligence_service.plan_intelligence({
        "harness": "agentweb",
        "trace_id": "trace",
        "parent_agent": "agentweb:fixture",
        "function": "summarization",
        "task": "summarize fixture",
    })

    assert result == {
        "ok": True,
        "mode": "shadow",
        "executed": False,
        "route": {"kind": "PARENT_ONLY", "executed": False},
    }
