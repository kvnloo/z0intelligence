"""Compatibility shim for carrying AgentWeb bridge v1 over existing z0 endpoints."""
from __future__ import annotations

from typing import Any

from .agentweb_bridge_protocol import SCHEMA_V1, validate_request_v1


BRIDGE_PATH_KINDS = {
    "/v1/plan": "plan",
    "/v1/intelligence": "dispatch",
    "/v1/experimental/choice": "decide",
    "/v1/experimental/noul": "decide",
}


def unwrap_agentweb_bridge_request(
    path: str,
    value: Any,
) -> tuple[dict[str, Any] | None, Any]:
    """Return (envelope, legacy_args); pass legacy bodies through unchanged."""
    if not isinstance(value, dict) or value.get("schema") != SCHEMA_V1:
        return None, value

    expected_kind = BRIDGE_PATH_KINDS.get(path)
    if expected_kind is None:
        raise ValueError("bridge v1 is not supported on this endpoint")

    errors = validate_request_v1(value)
    if errors:
        raise ValueError("invalid bridge request: " + "; ".join(errors))

    if value["kind"] != expected_kind:
        raise ValueError(
            f"bridge kind {value['kind']!r} is invalid for {path}; "
            f"expected {expected_kind!r}"
        )

    payload = value.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("bridge payload must be an object")

    legacy = dict(payload)
    policy = value["policy"]
    legacy.update(
        harness="agentweb",
        trace_id=value["trace_id"],
        parent_agent=value["parent_agent"],
        allow_remote=policy["allow_remote"],
        experimental=policy["experimental"],
    )

    if path in {"/v1/plan", "/v1/intelligence"}:
        legacy["free_only"] = policy["free_only"]
        if value["capability"] != legacy.get("function"):
            raise ValueError("bridge capability must match payload.function")
    else:
        decision_id = legacy.get("decision_id")
        if value["capability"] != f"experiment.{decision_id}":
            raise ValueError("bridge capability must match experiment decision_id")
        if policy["free_only"] is True:
            raise ValueError(
                "free_only=true cannot authorize the paid experimental decision lane"
            )

    return value, legacy


def wrap_agentweb_bridge_response(
    envelope: dict[str, Any] | None,
    result: Any,
) -> Any:
    """Wrap an unchanged legacy result for a v1 caller."""
    if envelope is None:
        return result
    if not isinstance(result, dict):
        raise ValueError("bridge result must be an object")

    ok = result.get("ok") is True
    reason = result.get("reason")
    rejected_reasons = {
        "experimental_opt_in_required",
        "remote_context_not_authorized",
        "experimental_paid_jev_shadow_disabled",
    }
    status = (
        "completed"
        if ok
        else "rejected"
        if reason in rejected_reasons
        else "failed"
    )

    receipt_id = result.get("dispatch_receipt_id")
    response: dict[str, Any] = {
        "schema": SCHEMA_V1,
        "operation_id": envelope["operation_id"],
        "trace_id": envelope["trace_id"],
        "mode": envelope["mode"],
        "kind": envelope["kind"],
        "status": status,
        "applied": bool(result.get("applied", False)),
        "replayed": bool(result.get("replayed", False)),
        "reconcile_required": False,
        "retry": "none",
        "output": result,
    }
    if envelope["mode"] == "shadow":
        response["applied"] = False
    if isinstance(receipt_id, str) and receipt_id:
        response["evidence"] = {"receipt_id": receipt_id}
    return response
