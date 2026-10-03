"""Thin bridge from z0int harness seams to neutral Tokenomics events.

Append-only projection to ``~/.z0int/tokenomics/events.jsonl``.
Does not replace z0int decision receipts or Hermes/OMP counters.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import paths


def events_path(root: Path | None = None) -> Path:
    layout = paths.ensure_layout(root)
    return layout["tokenomics"] / "events.jsonl"


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, separators=(",", ":"), sort_keys=True))
        fh.write("\n")


def emit_raw(row: dict[str, Any], *, root: Path | None = None, path: Path | None = None) -> Path:
    """Append a pre-shaped provider/session row (OMP/Hermes adapters consume this).

    ``path``: an explicit events file instead of the layout's (the loop tick writes only under its out root)."""
    p = path or events_path(root)
    _append_jsonl(p, row)
    return p


def emit_provider_usage(
    *,
    harness: str,
    trace_id: str | None,
    session_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    usage: dict[str, Any],
    role: str = "root",
    attribution: str = "incremental",
    cost_usd: float | None = None,
    latency_ms: float | None = None,
    measurement_state: str | None = None,
    state_reason: str | None = None,
    observer_id: str | None = None,
    logical_source_id: str | None = None,
    physical_source_id: str | None = None,
    identity_basis: str | None = None,
    extra: dict[str, Any] | None = None,
    root: Path | None = None,
) -> Path | None:
    """Best-effort emit; returns path or None when tokenomics is unavailable."""
    schema = f"{harness}.provider_usage.v0" if attribution == "incremental" else f"{harness}.session_usage.v0"
    raw: dict[str, Any] = {
        "schema": schema,
        "harness": harness,
        "trace_id": trace_id,
        "session_id": session_id,
        "provider": provider,
        "model": model,
        "role": role,
        "attribution": attribution,
        "usage": usage,
    }
    if cost_usd is not None:
        raw["cost_usd"] = cost_usd
    if latency_ms is not None:
        raw["latency_ms"] = latency_ms
    for key, value in (
        ("measurement_state", measurement_state),
        ("state_reason", state_reason),
        ("observer_id", observer_id),
        ("logical_source_id", logical_source_id),
        ("physical_source_id", physical_source_id),
        ("identity_basis", identity_basis),
    ):
        if value is not None:
            raw[key] = value
    if extra:
        raw.update(extra)
    try:
        from tokenomics.emit import append_raw

        return append_raw(raw, path=events_path(root))
    except Exception:
        return emit_raw(raw, root=root)


def measurement_gaps(*, range_spec: str = "7d", limit: int = 24) -> list[dict[str, Any]]:
    """Ranked measurement gaps for autoresearchd replay planning."""
    try:
        from tokenomics.coverage import coverage_report_from_sources

        report = coverage_report_from_sources(range_spec=range_spec)
        gaps = list(report.get("measurement_gaps") or [])
        return gaps[:limit]
    except Exception:
        return []



def emit_aodl_admission_once(
    *,
    receipt_id: str,
    caller_trace_id: str | None,
    session_id: str | None,
    harness: str | None,
    admission: dict[str, Any],
    root: Path | None = None,
) -> bool:
    """Record structural-gate latency once; never represents task success."""

    p = events_path(root)
    if p.exists():
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("schema") == "z0int.aodl_gate_latency.v1" and row.get("trace_id") == receipt_id:
                    return False
    row = {
        "schema": "z0int.aodl_gate_latency.v1",
        "event_kind": "structural_admission",
        "trace_id": receipt_id,
        "caller_trace_id": caller_trace_id,
        "session_id": session_id,
        "harness": harness,
        "allowed": admission.get("allowed") is True,
        "codes": list(admission.get("codes") or []),
        "latency_ms": float(admission.get("latency_us") or 0.0) / 1000.0,
        "aodl_canon_version": admission.get("aodl_canon_version"),
        "aodl_semantic_fingerprint": admission.get("aodl_semantic_fingerprint"),
        "aodl_intent_source_hash": admission.get("aodl_intent_source_hash"),
        "measurement_state": "complete",
        "measurement_scope": "gate_latency",
        "task_success": None,
        "verified_success": None,
    }
    emit_raw(row, root=root)
    return True
