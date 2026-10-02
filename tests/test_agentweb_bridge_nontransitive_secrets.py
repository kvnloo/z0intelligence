from z0int.agentweb_bridge_protocol import (
    validate_payload_isolation_v1,
    validate_request_v1,
)


def request(payload):
    return {
        "schema": "agentweb.z0.bridge.v1",
        "operation_id": "security-op-1",
        "trace_id": "a" * 64,
        "parent_agent": "agentweb:" + "b" * 24,
        "mode": "shadow",
        "kind": "plan",
        "capability": "summarization",
        "policy": {
            "allow_remote": False,
            "free_only": True,
            "experimental": False,
            "risk_class": "read",
            "approval_state": "not_required",
        },
        "payload": payload,
    }


def test_rejects_nested_credential_and_raw_identity_keys():
    for key in (
        "apiKey",
        "access_token",
        "refresh-token",
        "Authorization",
        "clientSecret",
        "private_key",
        "userId",
        "account_id",
        "sessionId",
    ):
        errors = validate_request_v1(
            request(
                {
                    "function": "summarization",
                    "task": "fixture",
                    "state": {"nested": {key: "secret"}},
                }
            )
        )
        assert any(f".{key}:" in error for error in errors), (key, errors)


def test_rejects_sensitive_keys_inside_arrays():
    errors = validate_payload_isolation_v1(
        {
            "records": [
                {"safe": True},
                {"metadata": {"oauthToken": "secret"}},
            ]
        }
    )
    assert any("oauthToken" in error for error in errors)


def test_allows_prose_that_mentions_credentials():
    assert validate_payload_isolation_v1(
        {
            "excerpt": "Example docs mention an Authorization header and API key.",
            "note": "Never paste passwords into production prompts.",
        }
    ) == []


def test_fails_closed_on_cycles():
    payload = {}
    payload["self"] = payload
    assert "payload must be acyclic" in validate_payload_isolation_v1(payload)


def test_fails_closed_on_excess_depth():
    payload = {"leaf": True}
    for _ in range(14):
        payload = {"next": payload}
    assert any(
        "max depth" in error
        for error in validate_payload_isolation_v1(payload)
    )


def test_fails_closed_on_excess_node_count():
    errors = validate_payload_isolation_v1(
        {"values": list(range(4100))}
    )
    assert any("max node count" in error for error in errors)
