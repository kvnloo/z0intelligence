import json

from z0int.agentweb_bridge_capabilities import MAX_ACTIVE, SOCKET_BACKLOG
from z0int.agentweb_sidecar_profile import (
    DEFAULT_BIND,
    DEFAULT_PORT,
    PROFILE_SCHEMA_V1,
    agentweb_sidecar_profile,
)


def test_sidecar_profile_is_static_loopback_and_credential_free():
    value = agentweb_sidecar_profile()
    assert value["schema"] == PROFILE_SCHEMA_V1
    assert value["deployment_mode"] == "local_sidecar"
    assert value["transport"] == "http"
    assert value["default_bind"] == DEFAULT_BIND == "127.0.0.1"
    assert value["default_port"] == DEFAULT_PORT == 11501
    assert value["public_wildcard_supported"] is False
    assert value["tls_mode"] == "none_loopback"
    assert value["mtls_gateway_supported"] is False
    assert value["graceful_drain_supported"] is True
    assert value["credentials_required"] is False
    assert value["limits"]["max_active"] == MAX_ACTIVE
    assert value["limits"]["socket_backlog"] == SOCKET_BACKLOG
    assert value["limits"]["overload_status"] == 503
    assert value["limits"]["overload_execution"] == "not_started"
    assert value["limits"]["overload_execution_header"] == "X-Z0-Execution"


def test_sidecar_profile_contains_no_secret_or_identity_fields():
    encoded = json.dumps(agentweb_sidecar_profile()).lower()
    for forbidden in (
        "api_key",
        "oauth",
        "access_token",
        "authorization",
        "cookie",
        "password",
        "session_id",
        "user_id",
        "account_id",
    ):
        assert forbidden not in encoded
