import json

from z0int.agentweb_bridge_capabilities import (
    CAPABILITIES_SCHEMA_V1,
    MAX_ACTIVE,
    MAX_REQUEST_BYTES,
    SOCKET_BACKLOG,
    agentweb_bridge_capabilities,
)


def test_bridge_capabilities_are_bounded_static_and_versioned():
    value = agentweb_bridge_capabilities()

    assert value["schema"] == CAPABILITIES_SCHEMA_V1
    assert value["bridge_protocol_versions"] == ["agentweb.z0.bridge.v1"]
    assert value["transport"] == {
        "max_request_bytes": MAX_REQUEST_BYTES,
        "max_active": MAX_ACTIVE,
        "socket_backlog": SOCKET_BACKLOG,
        "overload_status": 503,
        "retry_after_seconds": 1,
        "queue_policy": "reject_excess",
        "overload_execution": "not_started",
        "overload_execution_header": "X-Z0-Execution",
    }
    assert value["active_dispatch_supported"] is True
    assert value["receipt_schemas"] == ["z0int.decision_receipt.v1"]


def test_capabilities_advertise_only_current_bridge_wire_lanes():
    endpoints = {
        row["path"]: row
        for row in agentweb_bridge_capabilities()["endpoints"]
    }

    assert endpoints["/v1/plan"]["kind"] == "plan"
    assert endpoints["/v1/plan"]["physical_execution"] is False
    assert endpoints["/v1/intelligence"]["kind"] == "dispatch"
    assert endpoints["/v1/intelligence"]["physical_execution"] is True

    choice = endpoints["/v1/experimental/choice"]
    assert choice["capabilities"] == ["experiment.report_type_v1"]
    assert choice["free_only"] is False

    noul = endpoints["/v1/experimental/noul"]
    assert noul["capabilities"] == ["experiment.stop_request_v1"]
    assert noul["free_only"] is False


def test_capability_document_contains_no_secret_or_identity_fields():
    encoded = json.dumps(agentweb_bridge_capabilities()).lower()

    for forbidden in (
        "api_key",
        "apikey",
        "oauth",
        "access_token",
        "authorization",
        "cookie",
        "password",
        "user_id",
        "session_id",
        "provider_key",
    ):
        assert forbidden not in encoded
