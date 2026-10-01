"""Validator for the AgentWeb <-> z0intelligence bridge protocol v1.

This module defines protocol semantics only. It does not change dispatch behavior.
"""
from __future__ import annotations

from typing import Any

SCHEMA_V1 = "agentweb.z0.bridge.v1"

MODES = {"shadow", "active"}
KINDS = {"plan", "decide", "dispatch", "observe"}
RISKS = {"read", "generate", "mutate", "external_side_effect"}
APPROVALS = {"not_required", "required", "confirmed"}
STATUSES = {"completed", "not_executed", "rejected", "failed", "unknown"}
RETRIES = {"none", "caller_may_fallback", "same_identity_only"}

FORBIDDEN_TOP_LEVEL_FIELDS = {
    "user_id", "userId", "account_id", "accountId", "session_id", "sessionId",
    "api_key", "apiKey", "oauth_token", "oauthToken", "access_token", "accessToken",
    "authorization", "cookie", "password",
}

FORBIDDEN_PAYLOAD_KEYS = {
    "userid",
    "accountid",
    "sessionid",
    "apikey",
    "oauthtoken",
    "authtoken",
    "accesstoken",
    "refreshtoken",
    "bearertoken",
    "authorization",
    "cookie",
    "password",
    "clientsecret",
    "privatekey",
    "secretkey",
    "credential",
    "credentials",
}

MAX_PAYLOAD_DEPTH = 12
MAX_PAYLOAD_NODES = 4096


def _normalize_payload_key(key: str) -> str:
    return "".join(ch for ch in key.lower() if ch.isalnum())


def validate_payload_isolation_v1(payload: Any) -> list[str]:
    errors: list[str] = []
    seen: set[int] = set()
    nodes = 0
    stopped = False

    def visit(value: Any, path: str, depth: int) -> None:
        nonlocal nodes, stopped
        if stopped:
            return

        nodes += 1
        if nodes > MAX_PAYLOAD_NODES:
            errors.append(
                f"payload structure exceeds max node count {MAX_PAYLOAD_NODES}"
            )
            stopped = True
            return

        if depth > MAX_PAYLOAD_DEPTH:
            errors.append(
                f"payload structure exceeds max depth {MAX_PAYLOAD_DEPTH}"
            )
            stopped = True
            return

        if not isinstance(value, (dict, list)):
            return

        identity = id(value)
        if identity in seen:
            errors.append("payload must be acyclic")
            stopped = True
            return
        seen.add(identity)

        if isinstance(value, list):
            for index, entry in enumerate(value):
                visit(entry, f"{path}[{index}]", depth + 1)
                if stopped:
                    return
            return

        for key, entry in value.items():
            key_text = str(key)
            if _normalize_payload_key(key_text) in FORBIDDEN_PAYLOAD_KEYS:
                errors.append(
                    f"forbidden payload field at {path}.{key_text}: {key_text}"
                )
            visit(entry, f"{path}.{key_text}", depth + 1)
            if stopped:
                return

    visit(payload, "payload", 0)
    return errors

KNOWN_REJECTIONS = {"validation", "policy", "trace_conflict", "http_4xx"}
AMBIGUOUS_FAILURES = {"timeout", "transport", "http_5xx", "invalid_response"}


def _bounded_string(value: Any, maximum: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _is_hex(value: Any, length: int) -> bool:
    if not isinstance(value, str) or len(value) != length:
        return False
    return all(ch in "0123456789abcdef" for ch in value)


def validate_request_v1(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["request must be an object"]

    errors: list[str] = []

    for field in FORBIDDEN_TOP_LEVEL_FIELDS:
        if field in value:
            errors.append(f"forbidden top-level field: {field}")

    if value.get("schema") != SCHEMA_V1:
        errors.append(f"schema must be {SCHEMA_V1}")
    if not _bounded_string(value.get("operation_id"), 200):
        errors.append("operation_id must be a non-empty string <= 200 characters")
    if not _is_hex(value.get("trace_id"), 64):
        errors.append("trace_id must be a lowercase 64-character sha256 hex string")

    parent_agent = value.get("parent_agent")
    if (
        not isinstance(parent_agent, str)
        or not parent_agent.startswith("agentweb:")
        or not _is_hex(parent_agent.removeprefix("agentweb:"), 24)
    ):
        errors.append("parent_agent must be a pseudonymous agentweb:<24 hex> identifier")

    if value.get("mode") not in MODES:
        errors.append("mode must be shadow or active")
    if value.get("kind") not in KINDS:
        errors.append("kind must be plan, decide, dispatch, or observe")
    if not _bounded_string(value.get("capability"), 200):
        errors.append("capability must be a non-empty string <= 200 characters")
    if "payload" not in value:
        errors.append("payload is required")
    else:
        errors.extend(validate_payload_isolation_v1(value["payload"]))

    policy = value.get("policy")
    if not isinstance(policy, dict):
        errors.append("policy must be an object")
        return errors

    for field in ("allow_remote", "free_only", "experimental"):
        if not isinstance(policy.get(field), bool):
            errors.append(f"policy.{field} must be boolean")
    if policy.get("risk_class") not in RISKS:
        errors.append("policy.risk_class is invalid")
    if policy.get("approval_state") not in APPROVALS:
        errors.append("policy.approval_state is invalid")

    if (
        value.get("mode") == "active"
        and value.get("kind") == "dispatch"
        and policy.get("risk_class") == "external_side_effect"
        and policy.get("approval_state") != "confirmed"
    ):
        errors.append(
            "active external_side_effect dispatch requires confirmed AgentWeb approval"
        )

    return errors


def validate_response_v1(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return ["response must be an object"]

    errors: list[str] = []

    if value.get("schema") != SCHEMA_V1:
        errors.append(f"schema must be {SCHEMA_V1}")
    if not _bounded_string(value.get("operation_id"), 200):
        errors.append("operation_id must be a non-empty string <= 200 characters")
    if not _is_hex(value.get("trace_id"), 64):
        errors.append("trace_id must be a lowercase 64-character sha256 hex string")
    if value.get("mode") not in MODES:
        errors.append("mode must be shadow or active")
    if value.get("kind") not in KINDS:
        errors.append("kind must be plan, decide, dispatch, or observe")
    if value.get("status") not in STATUSES:
        errors.append("status is invalid")

    for field in ("applied", "replayed", "reconcile_required"):
        if not isinstance(value.get(field), bool):
            errors.append(f"{field} must be boolean")
    if value.get("retry") not in RETRIES:
        errors.append("retry is invalid")

    if value.get("mode") == "shadow" and value.get("applied") is True:
        errors.append("shadow response cannot be applied")

    if (
        value.get("mode") == "shadow"
        and value.get("kind") != "decide"
        and value.get("status") == "unknown"
    ):
        errors.append(
            "side-effect-free shadow response cannot have unknown execution status"
        )

    if (
        value.get("mode") == "shadow"
        and value.get("kind") != "decide"
        and value.get("reconcile_required") is True
    ):
        errors.append(
            "side-effect-free shadow response cannot require reconciliation"
        )

    if value.get("status") == "unknown":
        if value.get("reconcile_required") is not True:
            errors.append("unknown status requires reconciliation")
        if value.get("retry") != "same_identity_only":
            errors.append("unknown status requires same_identity_only retry")

    return errors


def failure_disposition(
    mode: str,
    failure: str,
    kind: str | None = None,
) -> dict[str, Any]:
    if mode not in MODES:
        raise ValueError(f"unsupported bridge mode: {mode}")
    resolved_kind = kind or ("plan" if mode == "shadow" else "dispatch")
    if resolved_kind not in KINDS:
        raise ValueError(f"unsupported bridge kind: {resolved_kind}")

    if failure in KNOWN_REJECTIONS:
        return {
            "status": "rejected",
            "reconcile_required": False,
            "retry": "none",
        }

    if failure not in AMBIGUOUS_FAILURES:
        raise ValueError(f"unsupported bridge failure class: {failure}")

    if mode == "shadow" and resolved_kind != "decide":
        return {
            "status": "not_executed",
            "reconcile_required": False,
            "retry": "caller_may_fallback",
        }

    return {
        "status": "unknown",
        "reconcile_required": True,
        "retry": "same_identity_only",
    }
