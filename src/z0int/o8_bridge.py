"""Versioned o8 -> z0intelligence bridge.

This module is transport-agnostic. It projects bounded o8 lane state into the
existing z0intelligence routing/receipt spine without taking ownership of o8
execution, approvals, credentials, or task state.
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

PROTOCOL_VERSION = "o8.z0.bridge.v1"
STATE_PACKET_SCHEMA = "z0int.o8_state_packet.v1"

MODES = {"shadow", "advisory", "active"}
RISK_CLASSES = {"read", "artifact", "mutation", "external_side_effect"}
APPROVAL_STATES = {"not_required", "required", "granted", "denied", "unknown"}
PRIVACY_CLASSES = {"PUBLIC", "LOCAL", "PRIVATE", "EPHEMERAL"}
TERMINAL_STATES = {"completed", "failed", "archived"}
O8_OUTCOMES = {
    "no_changes",
    "merged",
    "discarded",
    "closed_unmerged",
    "pr_opened",
    "asked",
    "archived_recoverable",
}
AODL_EXECUTOR_IDS = {"hermes", "omp", "grok", "codex", "claude", "pi", "fx"}

MAX_REQUEST_BYTES = 48_000
MAX_OBSERVATION_BYTES = 8_000
MAX_EVIDENCE_ITEMS = 12
MAX_EVIDENCE_CONTENT = 2_048

_FORBIDDEN_KEYS = {
    "user_id",
    "userid",
    "session_id",
    "sessionid",
    "session_key",
    "sessionkey",
    "api_key",
    "apikey",
    "access_token",
    "accesstoken",
    "oauth_token",
    "oauthtoken",
    "authorization",
    "credential",
    "credentials",
    "password",
    "secret",
}
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_INSTANCE = re.compile(r"^o8-instance:[0-9a-f]{16,64}$")


class BridgeValidationError(ValueError):
    """Request violates the o8 <-> z0 bridge contract."""


@dataclass(frozen=True)
class FailureSemantics:
    execution_state: str
    reconcile_required: bool
    incumbent_fallback_allowed: bool


def derive_trace_id(integration_instance: str, operation_id: str) -> str:
    """Stable execution identity for one logical o8 operation."""
    return hashlib.sha256(
        f"{integration_instance}\0{operation_id}".encode("utf-8")
    ).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_request_fingerprint(request: Mapping[str, Any]) -> str:
    """Digest semantic request content while ignoring later outcome correlation."""
    semantic = dict(request)
    correlation = semantic.get("correlation")
    if isinstance(correlation, Mapping):
        correlation = dict(correlation)
        correlation.pop("outcome_id", None)
        semantic["correlation"] = correlation
    return hashlib.sha256(_canonical_json(semantic).encode("utf-8")).hexdigest()


def failure_semantics(mode: str) -> FailureSemantics:
    if mode == "shadow":
        return FailureSemantics(
            execution_state="not_attempted",
            reconcile_required=False,
            incumbent_fallback_allowed=True,
        )
    if mode == "advisory":
        return FailureSemantics(
            execution_state="attempted_unknown",
            reconcile_required=True,
            incumbent_fallback_allowed=True,
        )
    if mode == "active":
        return FailureSemantics(
            execution_state="attempted_unknown",
            reconcile_required=True,
            incumbent_fallback_allowed=False,
        )
    raise BridgeValidationError("unsupported mode")


def _walk_forbidden_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z0-9_]", "", str(key).lower())
            if normalized in _FORBIDDEN_KEYS:
                raise BridgeValidationError(
                    f"forbidden secret/raw-identity field at {path}.{key}"
                )
            _walk_forbidden_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_forbidden_keys(child, f"{path}[{index}]")


def _require_str(
    obj: Mapping[str, Any],
    key: str,
    *,
    maximum: int = 200,
) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise BridgeValidationError(
            f"{key} must be a non-empty string <= {maximum} chars"
        )
    return value


def _parse_iso8601(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BridgeValidationError(f"{field} must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise BridgeValidationError(f"{field} must include a timezone")
    return parsed


def _validate_json_size(value: Any, maximum: int, label: str) -> None:
    try:
        encoded = _canonical_json(value).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BridgeValidationError(f"{label} must be JSON-serializable") from exc
    if len(encoded) > maximum:
        raise BridgeValidationError(f"{label} too large")


def validate_bridge_request(
    request: Mapping[str, Any],
    *,
    now_unix_ms: int | None = None,
) -> None:
    if not isinstance(request, Mapping):
        raise BridgeValidationError("request must be an object")
    _validate_json_size(request, MAX_REQUEST_BYTES, "request")

    allowed_top = {
        "protocol_version",
        "mode",
        "operation_id",
        "trace_id",
        "integration_instance",
        "deadline_unix_ms",
        "lane",
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
    integration_instance = _require_str(request, "integration_instance")

    if not _HEX64.fullmatch(trace_id):
        raise BridgeValidationError("trace_id must be a lowercase sha256 hex digest")
    if not _INSTANCE.fullmatch(integration_instance):
        raise BridgeValidationError(
            "integration_instance must be pseudonymous o8-instance:<hex>"
        )
    if trace_id != derive_trace_id(integration_instance, operation_id):
        raise BridgeValidationError(
            "trace_id does not match integration_instance + operation_id"
        )

    deadline = request.get("deadline_unix_ms")
    if not isinstance(deadline, int) or isinstance(deadline, bool) or deadline <= 0:
        raise BridgeValidationError("deadline_unix_ms must be a positive integer")
    if now_unix_ms is None:
        now_unix_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    if deadline <= now_unix_ms:
        raise BridgeValidationError("deadline_expired")

    lane = request.get("lane")
    if not isinstance(lane, Mapping):
        raise BridgeValidationError("lane must be an object")
    allowed_lane = {"id", "packet_id", "project_id", "runtime", "revision"}
    extra = set(lane) - allowed_lane
    if extra:
        raise BridgeValidationError(f"unknown lane fields: {sorted(extra)}")
    _require_str(lane, "id")
    revision = lane.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise BridgeValidationError("lane.revision must be a non-negative integer")
    for key in ("packet_id", "project_id"):
        if lane.get(key) is not None:
            if not isinstance(lane[key], str) or not lane[key] or len(lane[key]) > 200:
                raise BridgeValidationError(f"lane.{key} must be null or bounded string")
    if lane.get("runtime") is not None:
        if (
            not isinstance(lane["runtime"], str)
            or not lane["runtime"]
            or len(lane["runtime"]) > 64
        ):
            raise BridgeValidationError("lane.runtime must be null or bounded string")

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
    _require_str(capability, "task", maximum=16_000)
    if "context" in capability:
        if (
            not isinstance(capability["context"], str)
            or len(capability["context"]) > 16_000
        ):
            raise BridgeValidationError("capability.context must be a bounded string")
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
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 1 <= value <= 2048
        ):
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
    if (
        mode == "active"
        and risk_class in {"mutation", "external_side_effect"}
        and approval_state != "granted"
    ):
        raise BridgeValidationError(
            "active mutation/external side effect requires approval_state=granted"
        )

    # o8 is currently an AODL control room, not an admitted executor. An active
    # call must therefore be anchored to a real admitted worker runtime.
    runtime = lane.get("runtime")
    if mode == "active" and runtime not in AODL_EXECUTOR_IDS:
        raise BridgeValidationError(
            "active mode requires an AODL-admitted lane.runtime; o8 is a control room"
        )

    evidence = request.get("evidence", [])
    if not isinstance(evidence, list) or len(evidence) > MAX_EVIDENCE_ITEMS:
        raise BridgeValidationError(
            f"evidence must be a list of <= {MAX_EVIDENCE_ITEMS} items"
        )
    for item in evidence:
        if not isinstance(item, Mapping):
            raise BridgeValidationError("evidence items must be objects")
        if set(item) - {
            "source_id",
            "retrieved_at",
            "sha256",
            "content",
            "privacy",
        }:
            raise BridgeValidationError("unknown evidence field")
        _require_str(item, "source_id")
        retrieved_at = _require_str(item, "retrieved_at", maximum=64)
        _parse_iso8601(retrieved_at, "evidence.retrieved_at")
        digest = _require_str(item, "sha256", maximum=64)
        if not _HEX64.fullmatch(digest):
            raise BridgeValidationError(
                "evidence.sha256 must be lowercase sha256 hex"
            )
        content = _require_str(
            item,
            "content",
            maximum=MAX_EVIDENCE_CONTENT,
        )
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != digest:
            raise BridgeValidationError("evidence.sha256 does not match content")
        privacy = item.get("privacy", "LOCAL")
        if privacy not in PRIVACY_CLASSES:
            raise BridgeValidationError("unsupported evidence privacy class")
        if privacy in {"PRIVATE", "LOCAL"} and policy["allow_remote_context"]:
            # A local/private item may still be used locally; it must not be
            # silently marked remote-safe by a coarse request flag.
            raise BridgeValidationError(
                "LOCAL/PRIVATE evidence cannot be marked remote-context safe"
            )

    correlation = request.get("correlation", {})
    if not isinstance(correlation, Mapping):
        raise BridgeValidationError("correlation must be an object")
    if set(correlation) - {
        "judgment_receipt_id",
        "approval_id",
        "outcome_id",
    }:
        raise BridgeValidationError("unknown correlation field")
    for key, value in correlation.items():
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise BridgeValidationError(
                f"correlation.{key} must be a non-empty bounded string"
            )

    _walk_forbidden_keys(capability)
    _walk_forbidden_keys(evidence)
    _walk_forbidden_keys(correlation)


def _parent_agent(request: Mapping[str, Any]) -> str:
    lane = request["lane"]
    raw = f"{request['integration_instance']}\0{lane['id']}"
    return "o8:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def project_state_packet(request: Mapping[str, Any]) -> dict[str, Any]:
    """Project bounded o8 facts into evidence-only z0 state.

    This packet is not authorization. It intentionally carries no raw o8
    session key, credentials, full lane history, or model-authored approval
    authority.
    """
    validate_bridge_request(request)
    lane = request["lane"]
    policy = request["policy"]

    claims: list[dict[str, Any]] = []
    for key, value in (
        ("o8.lane.id", lane["id"]),
        ("o8.lane.packet_id", lane.get("packet_id")),
        ("o8.lane.project_id", lane.get("project_id")),
        ("o8.lane.runtime", lane.get("runtime")),
        ("o8.lane.revision", lane["revision"]),
        ("o8.policy.risk_class", policy["risk_class"]),
        ("o8.policy.approval_state", policy["approval_state"]),
    ):
        if value is not None:
            claims.append(
                {
                    "key": key,
                    "value": value,
                    "status": "observed",
                    "source": "o8_bridge",
                }
            )

    evidence = []
    for item in request.get("evidence", []):
        evidence.append(
            {
                "source_id": item["source_id"],
                "retrieved_at": item["retrieved_at"],
                "sha256": item["sha256"],
                "privacy": item.get("privacy", "LOCAL"),
                "content": item["content"],
            }
        )

    packet = {
        "schema": STATE_PACKET_SCHEMA,
        "trace_id": request["trace_id"],
        "operation_id": request["operation_id"],
        "source_revision": {
            "lane_id": lane["id"],
            "revision": lane["revision"],
            "packet_id": lane.get("packet_id"),
        },
        "current_claims": claims,
        "evidence": evidence,
        "blocking_unknowns": [],
        "authorization": {
            "can_mutate": False,
            "reason": "o8 bridge projection is evidence, not authority",
        },
    }
    packet["sha256"] = hashlib.sha256(
        _canonical_json(packet).encode("utf-8")
    ).hexdigest()
    return packet


def project_intelligence_args(request: Mapping[str, Any]) -> dict[str, Any]:
    """Project onto the existing z0int.intelligence contract."""
    validate_bridge_request(request)
    capability = request["capability"]
    policy = request["policy"]
    lane = request["lane"]
    packet = project_state_packet(request)

    runtime = lane.get("runtime")
    harness = runtime if runtime in AODL_EXECUTOR_IDS else "o8-control-room"

    projected: dict[str, Any] = {
        "harness": harness,
        "trace_id": request["trace_id"],
        "parent_agent": _parent_agent(request),
        "function": capability["function"],
        "task": capability["task"],
        "allow_remote": policy["allow_remote_context"],
        "experimental": capability.get("experimental", False),
        "automatic": capability.get("automatic", False),
        "integration_instance": request["integration_instance"],
        "free_only": policy["free_only"],
        "caller_request_sha256": canonical_request_fingerprint(request),
    }

    if "context" in capability:
        projected["context"] = capability["context"]
    else:
        projected["context"] = _canonical_json(packet)

    for key in (
        "state",
        "expected_parent_tokens",
        "expected_parent_ms",
        "max_tokens",
    ):
        if key in capability:
            projected[key] = capability[key]
    return projected


def _append_shadow_receipt(
    request: Mapping[str, Any],
    projected: Mapping[str, Any],
    selected: Mapping[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    from . import receipt

    lane = request["lane"]
    correlation = request.get("correlation")
    if not isinstance(correlation, Mapping):
        correlation = {}
    packet = project_state_packet(request)

    row = receipt.build_receipt(
        trace_id=str(request["trace_id"]),
        session_id=_parent_agent(request),
        capability_id=str(projected["function"]),
        prediction=str(selected.get("kind") or "unknown"),
        action_taken="not_applied",
        route="shadow",
        execution="shadow",
        measurement_state="unknown",
        state_reason="o8_shadow_decision_no_outcome",
        extra={
            "bridge_protocol_version": PROTOCOL_VERSION,
            "caller_request_sha256": projected["caller_request_sha256"],
            "integration_instance": request["integration_instance"],
            "o8_lane_id": lane["id"],
            "o8_packet_id": lane.get("packet_id"),
            "o8_lane_revision": lane["revision"],
            "o8_runtime": lane.get("runtime"),
            "o8_judgment_receipt_id": correlation.get("judgment_receipt_id"),
            "o8_approval_id": correlation.get("approval_id"),
            "o8_outcome_id": correlation.get("outcome_id"),
            "o8_state_packet_sha256": packet["sha256"],
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
    from . import receipt
    from .intelligence import route, routing_snapshot

    lock_path = receipt.receipts_path(root).parent / "o8-shadow.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = str(projected["caller_request_sha256"])

    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            prior = receipt.find_receipt(str(request["trace_id"]), root=root)
            if prior is not None:
                extra = (
                    prior.get("extra")
                    if isinstance(prior.get("extra"), Mapping)
                    else {}
                )
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
                    raise BridgeValidationError(
                        "shadow replay receipt missing route"
                    )
                return dict(saved), True

            selected = route(dict(projected), routing_snapshot(dict(projected)))
            if not isinstance(selected, Mapping):
                raise BridgeValidationError("shadow route must be an object")
            _append_shadow_receipt(
                request,
                projected,
                selected,
                root=root,
            )
            return dict(selected), False
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def handle_bridge_request(
    request: Mapping[str, Any],
    *,
    receipt_root: Path | None = None,
) -> dict[str, Any]:
    """Handle one bridge request without creating a second o8 authority path."""
    validate_bridge_request(request)
    projected = project_intelligence_args(request)
    fingerprint = projected["caller_request_sha256"]
    mode = str(request["mode"])

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
            "state_packet_sha256": project_state_packet(request)["sha256"],
            "executed": False,
            "reconcile_required": False,
            "replayed": replayed,
            "route": selected,
        }

    # Advisory/active execute only z0-owned cognition through the existing
    # receipt-backed dispatch authority. The result still cannot authorize an
    # o8 mutation; o8 must apply its native gate/approval path.
    from .intelligence import dispatch

    result = dispatch(projected)
    uncertain = result.get("execution_status") == "uncertain"
    return {
        **result,
        "protocol_version": PROTOCOL_VERSION,
        "mode": mode,
        "trace_id": request["trace_id"],
        "request_sha256": fingerprint,
        "state_packet_sha256": project_state_packet(request)["sha256"],
        "o8_authority_unchanged": True,
        "reconcile_required": uncertain,
    }


def canonical_observation_fingerprint(observation: Mapping[str, Any]) -> str:
    semantic = dict(observation)
    semantic.pop("observed_at", None)
    return hashlib.sha256(
        _canonical_json(semantic).encode("utf-8")
    ).hexdigest()


def validate_o8_observation(observation: Mapping[str, Any]) -> None:
    if not isinstance(observation, Mapping):
        raise BridgeValidationError("observation must be an object")
    _validate_json_size(
        observation,
        MAX_OBSERVATION_BYTES,
        "observation",
    )
    allowed = {
        "protocol_version",
        "trace_id",
        "observation_id",
        "lane_id",
        "lane_revision",
        "state",
        "outcome",
        "observed_at",
        "verified_success",
        "verification_source",
    }
    extra = set(observation) - allowed
    if extra:
        raise BridgeValidationError(
            f"unknown observation fields: {sorted(extra)}"
        )
    if observation.get("protocol_version") != PROTOCOL_VERSION:
        raise BridgeValidationError("unsupported protocol_version")
    trace_id = _require_str(observation, "trace_id", maximum=64)
    if not _HEX64.fullmatch(trace_id):
        raise BridgeValidationError("trace_id must be lowercase sha256 hex")
    _require_str(observation, "observation_id")
    _require_str(observation, "lane_id")
    revision = observation.get("lane_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise BridgeValidationError(
            "lane_revision must be a non-negative integer"
        )
    state = _require_str(observation, "state", maximum=32)
    if state not in TERMINAL_STATES:
        raise BridgeValidationError("observation state must be terminal")
    outcome = observation.get("outcome")
    if outcome is not None and outcome not in O8_OUTCOMES:
        raise BridgeValidationError("unsupported o8 outcome")
    observed_at = _require_str(observation, "observed_at", maximum=64)
    _parse_iso8601(observed_at, "observed_at")

    if "verified_success" in observation:
        if type(observation["verified_success"]) is not bool:
            raise BridgeValidationError("verified_success must be boolean")
        verification_source = observation.get("verification_source")
        if (
            not isinstance(verification_source, str)
            or not verification_source.strip()
            or len(verification_source) > 200
        ):
            raise BridgeValidationError(
                "verified_success requires verification_source"
            )
    elif "verification_source" in observation:
        raise BridgeValidationError(
            "verification_source requires verified_success"
        )


def join_o8_observation(
    observation: Mapping[str, Any],
    *,
    receipt_root: Path | None = None,
) -> dict[str, Any]:
    """Join terminal o8 facts without confusing completion with verification."""
    validate_o8_observation(observation)
    from . import receipt

    trace_id = str(observation["trace_id"])
    fingerprint = canonical_observation_fingerprint(observation)
    lock_path = receipt.receipts_path(receipt_root).parent / "o8-observation.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            base = receipt.find_receipt(trace_id, root=receipt_root)
            if base is None:
                return {
                    "ok": False,
                    "protocol_version": PROTOCOL_VERSION,
                    "trace_id": trace_id,
                    "reason": "bridge_receipt_not_found",
                }

            extra = (
                dict(base.get("extra"))
                if isinstance(base.get("extra"), Mapping)
                else {}
            )
            if extra.get("bridge_protocol_version") != PROTOCOL_VERSION:
                raise BridgeValidationError(
                    "trace does not belong to o8 bridge v1"
                )
            if extra.get("o8_lane_id") != observation["lane_id"]:
                raise BridgeValidationError("observation lane mismatch")
            if int(extra.get("o8_lane_revision", -1)) > int(
                observation["lane_revision"]
            ):
                raise BridgeValidationError("stale o8 observation revision")

            prior_fingerprint = extra.get("o8_observation_sha256")
            if prior_fingerprint is not None:
                if prior_fingerprint != fingerprint:
                    raise BridgeValidationError(
                        "observation replay conflict"
                    )
                return {
                    "ok": True,
                    "protocol_version": PROTOCOL_VERSION,
                    "trace_id": trace_id,
                    "replayed": True,
                    "outcome_tier": base.get("outcome_tier", "execution"),
                    "quality_verified": (
                        base.get("outcome_tier") == "gold"
                    ),
                }

            extra.update(
                o8_observation_sha256=fingerprint,
                o8_observation_id=observation["observation_id"],
                o8_observed_at=observation["observed_at"],
                o8_lane_state=observation["state"],
                o8_lane_outcome=observation.get("outcome"),
                o8_observed_revision=observation["lane_revision"],
            )

            marked = dict(base)
            marked["extra"] = extra
            marked["measurement_state"] = "unknown"
            marked["state_reason"] = "o8_terminal_execution_observed"
            receipt.append_receipt(marked, root=receipt_root)

            kwargs: dict[str, Any] = {
                "execution_completed": True,
                "source": f"o8_lane_{observation['state']}",
                "note": (
                    "o8 terminal lane observation; completion/outcome label alone "
                    "does not prove authored-intent success"
                ),
            }
            if "verified_success" in observation:
                kwargs["verified_success"] = observation["verified_success"]
                kwargs["verification_source"] = observation[
                    "verification_source"
                ]

            joined = receipt.join_outcome(
                trace_id,
                receipt.Outcome(**kwargs),
                root=receipt_root,
            )
            tier = (
                joined.get("outcome_tier")
                if isinstance(joined, Mapping)
                else "execution"
            )
            return {
                "ok": True,
                "protocol_version": PROTOCOL_VERSION,
                "trace_id": trace_id,
                "replayed": False,
                "outcome_tier": tier,
                "quality_verified": tier == "gold",
            }
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
