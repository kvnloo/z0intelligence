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
    assert request["parent_agent"] == "agentweb-session-secret"
    assert request["trace_id"] == hashlib.sha256(
        b"agentweb-session-secret\\0operation-42"
    ).hexdigest()
    assert request["function"] == "summarization"
    assert request["automatic"] is True
    assert request["allow_remote"] is False
