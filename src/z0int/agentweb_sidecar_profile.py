"""Static, credential-free AgentWeb local sidecar deployment profile."""
from __future__ import annotations

from typing import Any

from .agentweb_bridge_capabilities import MAX_ACTIVE, SOCKET_BACKLOG

PROFILE_SCHEMA_V1 = "agentweb.z0.sidecar_profile.v1"
DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 11501


def agentweb_sidecar_profile() -> dict[str, Any]:
    return {
        "schema": PROFILE_SCHEMA_V1,
        "deployment_mode": "local_sidecar",
        "transport": "http",
        "default_bind": DEFAULT_BIND,
        "default_port": DEFAULT_PORT,
        "loopback_hosts": ["127.0.0.1", "localhost", "::1"],
        "public_wildcard_supported": False,
        "tls_mode": "none_loopback",
        "mtls_gateway_supported": False,
        "graceful_drain_supported": True,
        "paths": {
            "health": "/healthz",
            "ready": "/readyz",
            "capabilities": "/v1/bridge/capabilities",
            "deployment_profile": "/v1/bridge/deployment-profile",
        },
        "process": {
            "entrypoint": "python -m z0int.intelligence_service",
            "default_args": ["--bind", DEFAULT_BIND, "--port", str(DEFAULT_PORT)],
        },
        "limits": {
            "max_active": MAX_ACTIVE,
            "socket_backlog": SOCKET_BACKLOG,
            "overload_status": 503,
            "overload_execution": "not_started",
            "overload_execution_header": "X-Z0-Execution",
        },
        "credentials_required": False,
    }
