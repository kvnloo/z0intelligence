"""Versioned AgentWeb -> z0intelligence bridge contract.

This module is intentionally transport-agnostic. It validates the wire envelope,
pins execution identity, and projects accepted requests onto the existing
z0int.intelligence request surface. It does not execute AgentWeb tools and it
does not accept AgentWeb credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

PROTOCOL_VERSION = "agentweb.z0.bridge.v1"
MODES = {"shadow", "advisory", "active"}
RISK_CLASSES = {"read", "artifact", "mutation", "external_side_effect"}
APPROVAL_STATES = {"not_required", "required", "granted", "denied", "unknown"}
MAX_REQUEST_BYTES = 48_000
MAX_EVIDENCE_ITEMS = 16
MAX_EVIDENCE_CONTENT = 8_000

_FORBIDDEN_KEYS = {
    "user_id",
    "userid",
    "userid",
    "session_id",
    "sessionid",
    "api_key",
    "apikey",
    "access_token",
    "accesstoken",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_PARENT = re.compile(r"^agentweb:[0-9a-f]{24,64}$")
_INSTANCE = re.compile(r"^agentweb-instance:[0-9a-f]{16,64}$")


class BridgeValidationError(ValueError):
    """Request violates the AgentWeb <-> z0 bridge contract."""


@dataclass(frozen=True)
class FailureSemantics:
    execution_state: str
    reconcile_required: bool
    incumbent_fallback_allowed: bool


def derive_trace_id(parent_agent: str, operation_id: str) -> str:
    return hashlib.sha256(f"{parent_agent}\0{operation_id}".encode("utf-8")).hexdigest()


def canonical_request_fingerprint(request: Mapping[str, Any]) -> str:
    """Stable digest of semantic request content.

    Correlation-only observation IDs are excluded so the same logical operation
    can be joined to later observations without changing dispatch identity.
    """
    semantic = dict(request)
    correlation = semantic.get("correlation")
    if isinstance(correlation, Mapping):
        correlation = dict(correlation)
        correlation.pop("observation_id", None)
        semantic["correlation"] = correlation
    raw = json.dumps(semantic, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def failure_semantics(mode: str) -> FailureSemantics:
    if mode == "shadow":
        return FailureSemantics(
            execution_state="not_attempted",
            reconcile_required=False,
            incumbent_fallback_allowed=True,
        )
    if mode in {"advisory", "active"}:
        return FailureSemantics(
            execution_state="attempted_unknown",
            reconcile_required=True,
            incumbent_fallback_allowed=(mode == "advisory"),
        )
    raise BridgeValidationError("unsupported mode")


def _walk_forbidden_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9_]", "", str(key).lower())
            if normalized in _FORBIDDEN_KEYS:
                raise BridgeValidationError(f"forbidden secret/raw-identity field at {path}.{key}")
            _walk_forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_forbidden_keys(child, f"{path}[{index}]")


def _require_str(obj: Mapping[str, Any], key: str, *, maximum: int = 200) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise BridgeValidationError(f"{key} must be a non-empty string <= {maximum} chars")
    return value


def _parse_iso8601(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BridgeValidationError("retrieved_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise BridgeValidationError("retrieved_at must include a timezone")
    return parsed


def validate_bridge_request(
    request: Mapping[str, Any],
    *,
    now_unix_ms: int | None = None,
) -> None:
    if not isinstance(request, Mapping):
        raise BridgeValidationError("request must be an object")
    try:
        encoded = json.dumps(request, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BridgeValidationError("request must be JSON-serializable") from exc
    if len(encoded) > MAX_REQUEST_BYTES:
        raise BridgeValidationError("request too large")

    allowed_top = {
        "protocol_version",
        "mode",
        "operation_id",
        "trace_id",
        "integration_instance",
        "parent_agent",
        "deadline_unix_ms",
        "capability",
        "policy",
        "evidence",
        "correlation",
    }
    extra = set(request) - allowed_top
    if extra:
        raise BridgeValidationError(f"unknown top-level fields: {sorted(extra)}")

    if request.get("protocol_version") != PROTOCOL_VERSION:
        raise BridgeValidationError("unsupported protocol_version")

    mode = _require_str(request, "mode", maximum=16)
    if mode not in MODES:
        raise BridgeValidationError("unsupported mode")

    operation_id = _require_str(request, "operation_id")
    trace_id = _require_str(request, "trace_id", maximum=64)
    parent_agent = _require_str(request, "parent_agent")
    integration_instance = _require_str(request, "integration_instance")

    if not _HEX64.fullmatch(trace_id):
        raise BridgeValidationError("trace_id must be a lowercase sha256 hex digest")
    if not _PARENT.fullmatch(parent_agent):
        raise BridgeValidationError("parent_agent must be pseudonymous agentweb:<hex>")
    if not _INSTANCE.fullmatch(integration_instance):
        raise BridgeValidationError("integration_instance must be pseudonymous agentweb-instance:<hex>")
    if trace_id != derive_trace_id(parent_agent, operation_id):
        raise BridgeValidationError("trace_id does not match parent_agent + operation_id")

    deadline = request.get("deadline_unix_ms")
    if not isinstance(deadline, int) or isinstance(deadline, bool) or deadline <= 0:
        raise BridgeValidationError("deadline_unix_ms must be a positive integer")
    if now_unix_ms is None:
        now_unix_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    if deadline <= now_unix_ms:
        raise BridgeValidationError("deadline_expired")

    capability = request.get("capability")
    if not isinstance(capability, Mapping):
        raise BridgeValidationError("capability must be an object")
    allowed_capability = {
        "function",
        "task",
        "context",
        "state",
        "expected_parent_tokens",
        "expected_parent_ms",
        "experimental",
        "automatic",
        "max_tokens",
    }
    extra = set(capability) - allowed_capability
    if extra:
        raise BridgeValidationError(f"unknown capability fields: {sorted(extra)}")
    _require_str(capability, "function")
    _require_str(capability, "task", maximum=24_000)
    if "context" in capability and not isinstance(capability["context"], str):
        raise BridgeValidationError("capability.context must be a string")
    for key in ("experimental", "automatic"):
        if key in capability and type(capability[key]) is not bool:
            raise BridgeValidationError(f"capability.{key} must be boolean")
    for key in ("expected_parent_tokens", "expected_parent_ms"):
        if key in capability:
            value = capability[key]
            if type(value) not in (int, float) or value < 0:
                raise BridgeValidationError(f"capability.{key} must be non-negative")
    if "max_tokens" in capability:
        value = capability["max_tokens"]
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 2048:
            raise BridgeValidationError("capability.max_tokens must be 1..2048")

    policy = request.get("policy")
    if not isinstance(policy, Mapping):
        raise BridgeValidationError("policy must be an object")
    allowed_policy = {
        "allow_remote_context",
        "free_only",
        "risk_class",
        "approval_state",
    }
    extra = set(policy) - allowed_policy
    if extra:
        raise BridgeValidationError(f"unknown policy fields: {sorted(extra)}")
    for key in ("allow_remote_context", "free_only"):
        if type(policy.get(key)) is not bool:
            raise BridgeValidationError(f"policy.{key} must be boolean")
    risk_class = _require_str(policy, "risk_class", maximum=32)
    approval_state = _require_str(policy, "approval_state", maximum=32)
    if risk_class not in RISK_CLASSES:
        raise BridgeValidationError("unsupported risk_class")
    if approval_state not in APPROVAL_STATES:
        raise BridgeValidationError("unsupported approval_state")
    if mode == "active" and risk_class in {"mutation", "external_side_effect"} and approval_state != "granted":
        raise BridgeValidationError("active mutation/external side effect requires approval_state=granted")

    evidence = request.get("evidence", [])
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE_ITEMS:
        raise BridgeValidationError(f"evidence must be a list of <= {MAX_EVIDENCE_ITEMS} items")
    for item in evidence:
        if not isinstance(item, Mapping):
            raise BridgeValidationError("evidence items must be objects")
        if set(item) - {"source_id", "retrieved_at", "sha256", "content"}:
            raise BridgeValidationError("unknown evidence field")
        _require_str(item, "source_id")
        retrieved_at = _require_str(item, "retrieved_at", maximum=64)
        _parse_iso8601(retrieved_at)
        digest = _require_str(item, "sha256", maximum=64)
        if not _HEX64.fullmatch(digest):
            raise BridgeValidationError("evidence.sha256 must be lowercase sha256 hex")
        content = _require_str(item, "content", maximum=MAX_EVIDENCE_CONTENT)
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != digest:
            raise BridgeValidationError("evidence.sha256 does not match content")

    correlation = request.get("correlation", {})
    if not isinstance(correlation, Mapping):
        raise BridgeValidationError("correlation must be an object")
    if set(correlation) - {"reliability_event_id", "observation_id"}:
        raise BridgeValidationError("unknown correlation field")
    for key, value in correlation.items():
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise BridgeValidationError(f"correlation.{key} must be a non-empty bounded string")

    _walk_forbidden_keys(capability)
    _walk_forbidden_keys(evidence)
    _walk_forbidden_keys(correlation)


def _append_shadow_receipt(
    request: Mapping[str, Any],
    projected: Mapping[str, Any],
    selected: Mapping[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Atomically persist one non-executing shadow decision.

    The caller must invoke this while holding the same shadow lock used for the
    replay check. This helper deliberately writes no outcome or verification
    signal.
    """
    from . import receipt

    correlation = request.get("correlation")
    if not isinstance(correlation, Mapping):
        correlation = {}
    row = receipt.build_receipt(
        trace_id=str(request["trace_id"]),
        session_id=str(request["parent_agent"]),
        capability_id=str(projected["function"]),
        prediction=str(selected.get("kind") or "unknown"),
        action_taken="not_applied",
        route="shadow",
        execution="shadow",
        measurement_state="unknown",
        state_reason="agentweb_shadow_decision_no_outcome",
        extra={
            "bridge_protocol_version": PROTOCOL_VERSION,
            "caller_request_sha256": projected["caller_request_sha256"],
            "integration_instance": request["integration_instance"],
            "observation_id": correlation.get("observation_id"),
            "reliability_event_id": correlation.get("reliability_event_id"),
            "bridge_route": dict(selected),
            "route_reason": str(selected.get("reason") or "")[:500],
            "physical_call_attempted": False,
            "status": "observed",
        },
    )
    return receipt.append_receipt(row, root=root)


def _route_shadow_once(
    request: Mapping[str, Any],
    projected: Mapping[str, Any],
    *,
    root: Path | None = None,
) -> tuple[dict[str, Any], bool]:
    """Atomically replay or create exactly one shadow route receipt."""
    from . import receipt
    from .intelligence import route, routing_snapshot

    lock_path = receipt.receipts_path(root).parent / "agentweb-shadow.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = str(projected["caller_request_sha256"])
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            prior = receipt.find_receipt(str(request["trace_id"]), root=root)
            if prior is not None:
                extra = prior.get("extra") if isinstance(prior.get("extra"), Mapping) else {}
                if (
                    prior.get("execution") != "shadow"
                    or extra.get("bridge_protocol_version") != PROTOCOL_VERSION
                ):
                    raise BridgeValidationError(
                        "shadow trace already claimed by incompatible receipt"
                    )
                if extra.get("caller_request_sha256") != fingerprint:
                    raise BridgeValidationError("shadow trace conflict")
                saved = extra.get("bridge_route")
                if not isinstance(saved, Mapping):
                    raise BridgeValidationError("shadow replay receipt missing route")
                return dict(saved), True

            selected = route(dict(projected), routing_snapshot(dict(projected)))
            if not isinstance(selected, Mapping):
                raise BridgeValidationError("shadow route must be an object")
            _append_shadow_receipt(request, projected, selected, root=root)
            return dict(selected), False
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def handle_bridge_request(
    request: Mapping[str, Any],
    *,
    receipt_root: Path | None = None,
) -> dict[str, Any]:
    """Execute one bridge request without creating a second authority path.

    Shadow mode is route-only: it may inspect current capability/provider
    availability, but it never enters dispatch authority or performs a model/tool
    call. Advisory/active reuse the existing receipt-backed dispatch path.
    """
    validate_bridge_request(request)
    projected = project_intelligence_args(request)
    fingerprint = projected["caller_request_sha256"]
    mode = request["mode"]

    if mode == "shadow":
        selected, replayed = _route_shadow_once(
            request,
            projected,
            root=receipt_root,
        )
        return {
            "ok": True,
            "protocol_version": PROTOCOL_VERSION,
            "mode": "shadow",
            "trace_id": request["trace_id"],
            "request_sha256": fingerprint,
            "executed": False,
            "reconcile_required": False,
            "replayed": replayed,
            "route": selected,
        }

    from .intelligence import dispatch

    result = dispatch(projected)
    uncertain = result.get("execution_status") == "uncertain"
    return {
        **result,
        "protocol_version": PROTOCOL_VERSION,
        "mode": mode,
        "trace_id": request["trace_id"],
        "request_sha256": fingerprint,
        "reconcile_required": uncertain,
    }


def project_intelligence_args(request: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and project onto the existing z0int.intelligence contract."""
    validate_bridge_request(request)
    capability = request["capability"]
    policy = request["policy"]

    projected: dict[str, Any] = {
        "harness": "agentweb",
        "trace_id": request["trace_id"],
        "parent_agent": request["parent_agent"],
        "function": capability["function"],
        "task": capability["task"],
        "allow_remote": policy["allow_remote_context"],
        "experimental": capability.get("experimental", False),
        "automatic": capability.get("automatic", False),
        "integration_instance": request["integration_instance"],
        "free_only": policy["free_only"],
        # Bind the complete bridge envelope to z0's existing durable dispatch
        # fingerprint. This covers provenance/policy fields that are intentionally
        # not otherwise projected into model-facing task/context/state.
        "caller_request_sha256": canonical_request_fingerprint(request),
    }
    for key in ("context", "state", "expected_parent_tokens", "expected_parent_ms", "max_tokens"):
        if key in capability:
            projected[key] = capability[key]
    return projected
