"""Idempotent ingest for privacy-minimized AgentWeb reliability observations."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .dispatch_authority import locked
from .receipt import SCHEMA, append_receipt, find_receipt

TRACE_RE = re.compile(r"^agentweb-observe-[0-9a-f]{64}$")
SESSION_RE = re.compile(r"^agentweb:[0-9a-f]{24}$")
OUTCOMES = {
    "success", "error", "blocked", "skipped", "timeout",
    "pending_confirmation", "user_refused",
}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
    ).hexdigest()


def validate_observation(row: dict[str, Any]) -> None:
    allowed = {
        "schema", "trace_id", "session_id", "capability_id", "provider", "route",
        "execution", "action_taken", "latency_ms", "measurement_state",
        "state_reason", "outcome", "extra",
    }
    if not isinstance(row, dict) or set(row) - allowed:
        raise ValueError("Unknown AgentWeb observation fields")
    if row.get("schema") != SCHEMA:
        raise ValueError("Invalid observation schema")
    trace_id = row.get("trace_id")
    if not isinstance(trace_id, str) or not TRACE_RE.fullmatch(trace_id):
        raise ValueError("Invalid observation trace id")
    session_id = row.get("session_id")
    if session_id is not None and (not isinstance(session_id, str) or not SESSION_RE.fullmatch(session_id)):
        raise ValueError("AgentWeb session identity must already be pseudonymous")

    capability_id = row.get("capability_id")
    if not isinstance(capability_id, str) or not capability_id.startswith("agentweb.tool.") or len(capability_id) > 240:
        raise ValueError("Invalid AgentWeb capability id")
    if row.get("provider") != "agentweb" or row.get("route") != "shadow" or row.get("execution") != "log_only":
        raise ValueError("AgentWeb observations must be shadow/log_only")
    if row.get("measurement_state") != "partial":
        raise ValueError("AgentWeb reliability observations are partial measurements")
    if row.get("state_reason") != "observational_agentweb_tool_outcome_not_quality_verification":
        raise ValueError("Invalid observation state reason")
    action = row.get("action_taken")
    if action not in OUTCOMES:
        raise ValueError("Invalid reliability outcome")
    latency = row.get("latency_ms")
    if not isinstance(latency, (int, float)) or not 0 <= float(latency) <= 30 * 60 * 1000:
        raise ValueError("Invalid observation latency")

    outcome = row.get("outcome")
    if not isinstance(outcome, dict):
        raise ValueError("Observation outcome must be an object")
    if set(outcome) - {"source", "execution_completed", "tool_ok", "success", "user_refused"}:
        raise ValueError("Observation outcome contains unsupported quality fields")
    if outcome.get("source") != "agentweb_reliability_event":
        raise ValueError("Invalid observation source")
    for key in ("execution_completed", "tool_ok", "success", "user_refused"):
        if key in outcome and type(outcome[key]) is not bool:
            raise ValueError("Observation outcome booleans must be bool")

    extra = row.get("extra")
    if not isinstance(extra, dict):
        raise ValueError("Observation extra must be an object")
    if set(extra) - {"observational", "tool", "reliability_outcome", "platform", "user_edit"}:
        raise ValueError("Observation extra contains unsupported fields")
    if extra.get("observational") is not True:
        raise ValueError("Observation must be marked observational")
    if extra.get("reliability_outcome") != action:
        raise ValueError("Observation outcome mismatch")
    tool = extra.get("tool")
    if not isinstance(tool, str) or not tool.strip() or len(tool) > 120:
        raise ValueError("Invalid observed tool")
    platform = extra.get("platform")
    if platform is not None and (not isinstance(platform, str) or len(platform) > 64):
        raise ValueError("Invalid observation platform")
    if "user_edit" in extra and type(extra["user_edit"]) is not bool:
        raise ValueError("Invalid observation user_edit")


def ingest_observation(row: dict[str, Any]) -> dict[str, Any]:
    validate_observation(row)
    trace_id = row["trace_id"]
    fingerprint = _digest(row)
    lock_id = hashlib.sha256(trace_id.encode()).hexdigest()
    with locked("observe-" + lock_id):
        previous = find_receipt(trace_id)
        if previous is not None:
            previous_digest = (previous.get("extra") or {}).get("observation_sha256")
            if previous_digest != fingerprint:
                raise ValueError("observation trace reused for changed payload")
            return {"ok": True, "receipt_id": trace_id, "replayed": True}

        stored = dict(row)
        stored_extra = dict(stored["extra"])
        stored_extra.update(
            observation_sha256=fingerprint,
            status="observed",
            source="agentweb_reliability_projection",
            quality_authoritative=False,
        )
        stored["extra"] = stored_extra
        stored["schema"] = SCHEMA
        append_receipt(stored)
        return {"ok": True, "receipt_id": trace_id, "replayed": False}
