"""Static, credential-free AgentWeb bridge capability negotiation."""
from __future__ import annotations

from typing import Any

from .agentweb_bridge_protocol import SCHEMA_V1

CAPABILITIES_SCHEMA_V1 = "agentweb.z0.bridge.capabilities.v1"
MAX_REQUEST_BYTES = 40000
MAX_ACTIVE = 4
SOCKET_BACKLOG = 8


def agentweb_bridge_capabilities() -> dict[str, Any]:
    """Describe supported wire behavior without probing providers or mutating state."""
    return {
        "schema": CAPABILITIES_SCHEMA_V1,
        "bridge_protocol_versions": [SCHEMA_V1],
        "transport": {
            "max_request_bytes": MAX_REQUEST_BYTES,
            "max_active": MAX_ACTIVE,
            "socket_backlog": SOCKET_BACKLOG,
            "overload_status": 503,
            "retry_after_seconds": 1,
        },
        "active_dispatch_supported": True,
        "receipt_schemas": ["z0int.decision_receipt.v1"],
        "endpoints": [
            {
                "path": "/v1/plan",
                "kind": "plan",
                "modes": ["shadow"],
                "capability_source": "payload.function",
                "physical_execution": False,
                "free_only": "caller_explicit",
            },
            {
                "path": "/v1/intelligence",
                "kind": "dispatch",
                "modes": ["active"],
                "capability_source": "payload.function",
                "physical_execution": True,
                "free_only": "caller_explicit",
            },
            {
                "path": "/v1/experimental/choice",
                "kind": "decide",
                "modes": ["shadow"],
                "capabilities": ["experiment.report_type_v1"],
                "physical_execution": True,
                "free_only": False,
                "experimental": True,
            },
            {
                "path": "/v1/experimental/noul",
                "kind": "decide",
                "modes": ["shadow"],
                "capabilities": ["experiment.stop_request_v1"],
                "physical_execution": True,
                "free_only": False,
                "experimental": True,
            },
        ],
    }
