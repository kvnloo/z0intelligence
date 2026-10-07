"""Hermes transport adapter for z0intelligence.

Scope: identity + observation shape only. No model calls, no promotion.
"""

from __future__ import annotations

import os
import time
from typing import Any

from z0int.harness_id import HarnessIdentity, identity_from_bridge


def normalize_envelope(raw: dict[str, Any], *, build_id: str | None = None) -> dict[str, Any]:
    """Map a Hermes tool/turn envelope into a stable z0int event dict."""
    session_id = (
        raw.get("session_id")
        or raw.get("sessionId")
        or os.environ.get("HERMES_SESSION_ID")
        or raw.get("conversation_id")
    )
    turn_id = raw.get("turn_id") or raw.get("turnId") or raw.get("message_id")
    trace_id = raw.get("trace_id") or raw.get("traceId") or session_id
    process_id = raw.get("process_id") or raw.get("pid") or os.getpid()
    try:
        process_id = int(process_id) if process_id is not None else None
    except (TypeError, ValueError):
        process_id = None

    identity = identity_from_bridge(
        session_id=str(session_id) if session_id else None,
        process_id=process_id,
        trace_id=str(trace_id) if trace_id else None,
        turn_id=str(turn_id) if turn_id else None,
        bridge_generation=int(raw["bridge_generation"]) if raw.get("bridge_generation") is not None else None,
        build_id=build_id or raw.get("build_id") or os.environ.get("Z0INT_BUILD_ID"),
        harness_id="hermes",
    )
    text = raw.get("text") or raw.get("content") or raw.get("message") or ""
    if isinstance(text, list):
        # Hermes sometimes sends content blocks
        parts = []
        for block in text:
            if isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
            elif isinstance(block, str):
                parts.append(block)
        text = "\n".join(parts)

    return {
        "schema": "z0int.harness_envelope.v1",
        "identity": identity.to_dict(),
        "text": text,
        "capability_id": raw.get("capability_id"),
        "meta": {
            k: raw[k]
            for k in ("tools", "model", "profile", "channel")
            if k in raw
        },
        "received_at": time.time(),
    }


def close_observation(
    envelope: dict[str, Any],
    *,
    execution_completed: bool,
    verified_success: bool | None,
    baseline_tokens: int | None = None,
    actual_tokens: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    cost: float = 0.0,
) -> dict[str, Any]:
    """Build z0int.allocation_observation.v1 from a closed turn.

    Ambient close → execution_completed may be True while verified_success is null.
    """
    identity = envelope.get("identity") or {}
    return {
        "schema": "z0int.allocation_observation.v1",
        "harness_id": identity.get("harness_id") or "hermes",
        "session_id": identity.get("session_id"),
        "process_id": identity.get("process_id"),
        "trace_id": identity.get("trace_id"),
        "turn_id": identity.get("turn_id"),
        "bridge_generation": identity.get("bridge_generation"),
        "build_id": identity.get("build_id"),
        "capability_id": envelope.get("capability_id"),
        "provider": provider or "hermes",
        "model": model or "unknown",
        "execution_completed": bool(execution_completed),
        "verified_success": verified_success,
        # do not set completed=True as verified proxy
        "completed": bool(execution_completed),
        "actual_cost": float(cost),
        "baseline_tokens": baseline_tokens,
        "actual_tokens": actual_tokens,
        "closed_at": time.time(),
    }


def join_outcome(
    observation: dict[str, Any],
    *,
    gold_verified: bool | None = None,
    judge_verified: bool | None = None,
) -> dict[str, Any]:
    """Join an external outcome onto a close observation without inventing success.

    Prefer explicit gold/judge. Never upgrade ambient execution_completed into verified.
    """
    out = dict(observation)
    verified = out.get("verified_success")
    if gold_verified is not None:
        verified = bool(gold_verified)
    elif judge_verified is not None and verified is None:
        verified = bool(judge_verified)
    out["verified_success"] = verified
    out["outcome_join"] = {
        "gold_verified": gold_verified,
        "judge_verified": judge_verified,
        "joined_at": time.time(),
    }
    return out



def resolve_repository_context(
    envelope: dict[str, Any],
    *,
    project_root: str | os.PathLike[str],
    query: str | None = None,
    needs: list[Any] | None = None,
    allow_qmd: bool = True,
    event_log: Any | None = None,
) -> dict[str, Any]:
    """Resolve repository evidence through z0's shared FFF/QMD ContextPacket path.

    Hermes remains a transport adapter: this records what was retrieved but
    does not authorize tools, claim completion, or promote retrieved text.
    """
    from z0int.context_resolve import append_context_packet_event, resolve_context

    identity = envelope.get("identity") or {}
    text = str(query if query is not None else envelope.get("text") or "").strip()
    packet = resolve_context(
        needs=needs,
        query=text if not needs else None,
        task_id=str(identity.get("trace_id") or identity.get("turn_id") or "") or None,
        project_root=project_root,
        allow_fff=True,
        allow_qmd=allow_qmd,
        allow_memory=False,
        use_cache=False,
    )
    event = append_context_packet_event(
        packet,
        source="harness:hermes",
        project=str(project_root),
        session_id=str(identity.get("session_id") or "") or None,
        event_log=event_log,
    )
    return {
        "schema": "z0int.harness_context.v1",
        "harness_id": "hermes",
        "event_id": event.event_id,
        "packet": packet.to_dict(),
    }
