"""Durable AODL admission records for the dispatch authority.

The canonical decision ledger remains the only source of truth. AODL admission
is recorded as a normal z0int.decision_receipt.v1 row before a dispatch-start
row can exist.
"""

from __future__ import annotations

import fcntl
import json
from typing import Any

from .aodl_admission import AdmissionDecision, SpawnRequest, decide_spawn
from .receipt import append_receipt, receipts_path

ADMISSION_PREFIX = "aodl-admission-"
ADMISSION_CAPABILITY = "aodl.structural_admission"


def trace_id(key: str) -> str:
    return ADMISSION_PREFIX + key


def _payload_for_missing() -> dict[str, Any]:
    return {
        "schema": "z0int.aodl_admission.v1",
        "allowed": False,
        "decision": "DENY",
        "codes": ["aodl-required"],
        "numeric_codes": [],
        "source": "fail-closed",
        "aodl_canon_version": "aodl-canon-1",
        "aodl_semantic_fingerprint": None,
        "aodl_intent_source_hash": None,
        "contract_revision": None,
        "request_revision": None,
        "parent_node_id": None,
        "latency_us": 0.0,
    }


def _envelope(request: dict[str, Any], *, required: bool) -> tuple[object, SpawnRequest] | None:
    raw = request.get("aodl")
    if raw is None:
        if required:
            return None
        return None
    if not isinstance(raw, dict) or set(raw) != {"document", "spawn"}:
        raise ValueError("aodl must contain exactly document and spawn")
    spawn = raw.get("spawn")
    if not isinstance(spawn, dict):
        raise ValueError("aodl.spawn must be an object")
    allowed = {
        "request_revision",
        "parent_node_id",
        "live_children",
        "parent_depth",
        "observed",
        "proposed",
        "requested",
    }
    if set(spawn) - allowed:
        raise ValueError("unknown aodl.spawn fields")
    try:
        proposal = SpawnRequest(**spawn)
    except TypeError as exc:
        raise ValueError("invalid aodl.spawn shape") from exc
    return raw.get("document"), proposal


def evaluate(request: dict[str, Any], *, required: bool) -> dict[str, Any] | None:
    """Return a serializable admission payload, or None for legacy ungoverned v2."""

    try:
        env = _envelope(request, required=required)
    except (TypeError, ValueError):
        return {
            **_payload_for_missing(),
            "codes": ["aodl-envelope-invalid"],
        }
    if env is None:
        return _payload_for_missing() if required else None
    document, proposal = env
    decision: AdmissionDecision = decide_spawn(document, proposal)
    return decision.receipt()


def previous(request_sha256: str, key: str) -> dict[str, Any] | None:
    """Return the last durable admission row; corruption fails closed."""

    path = receipts_path()
    if not path.exists():
        return None
    row = None
    with path.open() as stream:
        fcntl.flock(stream, fcntl.LOCK_SH)
        for line in stream:
            candidate = json.loads(line)
            if candidate.get("trace_id") == trace_id(key):
                row = candidate
    if row is not None:
        extra = row.get("extra")
        if not isinstance(extra, dict) or extra.get("request_sha256") != request_sha256:
            raise ValueError("trace_id reused for different AODL admission request")
    return row


def _emit_tokenomics(row: dict[str, Any]) -> None:
    extra = row.get("extra")
    payload = extra.get("aodl_admission") if isinstance(extra, dict) else None
    if not isinstance(payload, dict):
        return
    try:
        from .tokenomics_emit import emit_aodl_admission_once

        emit_aodl_admission_once(
            receipt_id=str(row.get("trace_id")),
            caller_trace_id=extra.get("caller_trace_id"),
            session_id=row.get("session_id"),
            harness=extra.get("harness"),
            admission=payload,
        )
    except Exception:
        # Measurement is non-authoritative. The durable admission receipt remains
        # the source of truth and a replay can retry the one-time projection.
        return


def ensure(
    request: dict[str, Any],
    key: str,
    request_sha256: str,
    *,
    required: bool,
) -> dict[str, Any] | None:
    """Evaluate once and fsync before dispatch can be claimed."""

    prior = previous(request_sha256, key)
    if prior is not None:
        _emit_tokenomics(prior)
        return prior

    payload = evaluate(request, required=required)
    if payload is None:
        return None

    allowed = payload.get("allowed") is True
    row = {
        "trace_id": trace_id(key),
        "session_id": request.get("parent_agent"),
        "capability_id": ADMISSION_CAPABILITY,
        "prediction": "ALLOW" if allowed else "DENY",
        "action_taken": "dispatch_admit" if allowed else "dispatch_deny",
        "route": "structural_gate",
        "execution": "log_only",
        "latency_ms": float(payload.get("latency_us") or 0.0) / 1000.0,
        "extra": {
            "status": "allowed" if allowed else "denied",
            "request_sha256": request_sha256,
            "harness": request.get("harness"),
            "caller_trace_id": request.get("trace_id"),
            "aodl_admission": payload,
        },
    }
    saved = append_receipt(row)
    _emit_tokenomics(saved)
    return saved


def is_allowed(row: dict[str, Any] | None) -> bool:
    if row is None:
        return True
    extra = row.get("extra")
    payload = extra.get("aodl_admission") if isinstance(extra, dict) else None
    return isinstance(payload, dict) and payload.get("allowed") is True


def denied_result(row: dict[str, Any]) -> dict[str, Any]:
    extra = row.get("extra")
    payload = extra.get("aodl_admission") if isinstance(extra, dict) else {}
    codes = payload.get("codes") if isinstance(payload, dict) else []
    reason = "AODL structural admission denied"
    if codes:
        reason += ": " + ", ".join(str(code) for code in codes)
    return {
        "ok": False,
        "executed": False,
        "requires_parent": True,
        "execution_status": "denied",
        "reason": reason,
        "aodl_admission_receipt_id": row["trace_id"],
        "aodl_admission": payload,
    }
