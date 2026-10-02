"""Agent Orchestrator experiment pairing without AO schema pollution.

AO remains the execution/session authority. Experiment identity lives in a
private z0intelligence sidecar registry keyed by receipt trace ids. Historical
counterfactual snapshots can be materialized into deterministic reference
receipts so AO candidates can be compared against frozen prior runs.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from . import paths
from .ao_bridge import CAPABILITY
from .counterfactual import SNAPSHOTS_NAME
from .receipt import DecisionReceipt, append_receipt, find_receipt, receipts_path

PAIR_SCHEMA = "ao.z0int.experiment_pair.v1"
REFERENCE_CAPABILITY = "counterfactual.reference.v1"


def pair_registry_path(root: Path | None = None) -> Path:
    return receipts_path(root).with_name("ao-experiment-pairs.jsonl")


def _text(value: Any, name: str, limit: int = 240) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    value = value.strip()
    if not value or len(value) > limit:
        raise ValueError(f"invalid {name}")
    return value


def _digest(value: Any) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        allow_nan=False,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _iter_jsonl(path: Path):
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def list_pairs(*, root: Path | None = None) -> list[dict[str, Any]]:
    """Return latest pair row per (experiment_id, pair_id)."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _iter_jsonl(pair_registry_path(root)) or []:
        if row.get("schema") != PAIR_SCHEMA:
            continue
        experiment_id = row.get("experiment_id")
        pair_id = row.get("pair_id")
        if isinstance(experiment_id, str) and isinstance(pair_id, str):
            latest[(experiment_id, pair_id)] = row
    return [
        latest[key]
        for key in sorted(latest)
    ]


def _receipt_task_snapshot(row: dict[str, Any]) -> str | None:
    value = row.get("task_snapshot_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
    value = extra.get("task_snapshot_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _snapshot_path(root: Path | None = None) -> Path:
    return paths.ensure_layout(root)["replay"] / SNAPSHOTS_NAME


def find_reference_snapshot(task_snapshot_id: str, *, root: Path | None = None) -> dict[str, Any] | None:
    task_snapshot_id = _text(task_snapshot_id, "task_snapshot_id")
    found = None
    for row in _iter_jsonl(_snapshot_path(root)) or []:
        if row.get("task_snapshot_id") == task_snapshot_id:
            found = row
    return found


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def materialize_reference_snapshot(
    task_snapshot_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Create or replay one deterministic reference receipt from a frozen snapshot."""
    snapshot = find_reference_snapshot(task_snapshot_id, root=root)
    if snapshot is None:
        raise ValueError("unknown task_snapshot_id")

    task_snapshot_id = _text(task_snapshot_id, "task_snapshot_id")
    trace_id = "ao-reference-" + task_snapshot_id
    previous = find_receipt(trace_id, root=root)
    if previous is not None:
        if (
            previous.get("capability_id") != REFERENCE_CAPABILITY
            or _receipt_task_snapshot(previous) != task_snapshot_id
        ):
            raise ValueError("reference trace id conflicts with another receipt")
        return {"trace_id": trace_id, "receipt": previous, "replayed": True}

    usage = snapshot.get("usage") if isinstance(snapshot.get("usage"), dict) else {}
    input_tokens = _optional_int(usage.get("input"))
    output_tokens = _optional_int(usage.get("output"))
    cached_tokens = _optional_int(usage.get("cacheRead"))
    measured_tokens = _optional_int(usage.get("totalTokens"))
    if measured_tokens is None and input_tokens is not None and output_tokens is not None:
        measured_tokens = input_tokens + output_tokens
    cost_usd = _optional_float(usage.get("cost_total"))

    measurement_state = "partial"
    state_reason = (
        "Historical reference snapshot preserves available usage/cost; "
        "decision latency and executable verification may be absent"
    )
    measurement = {
        "scope": "reference_turn",
        "usage_state": "historical_snapshot",
        "provenance": "counterfactual_task_snapshot",
    }
    if input_tokens is not None:
        measurement["input_tokens"] = input_tokens
    if output_tokens is not None:
        measurement["output_tokens"] = output_tokens
    if cached_tokens is not None:
        measurement["cached_input_tokens"] = cached_tokens
    if measured_tokens is not None:
        measurement["measured_frontier_tokens"] = measured_tokens
    if cost_usd is not None:
        measurement["cost_usd"] = cost_usd

    receipt = DecisionReceipt(
        trace_id=trace_id,
        session_id=str(snapshot.get("session_id") or "") or None,
        capability_id=REFERENCE_CAPABILITY,
        provider=str(snapshot.get("provider") or "") or None,
        model=str(snapshot.get("model") or "") or None,
        prediction="reference",
        action_taken="historical_replay",
        route="reference",
        execution="historical_replay",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_tokens,
        measured_frontier_tokens=measured_tokens,
        measurement_state=measurement_state,
        state_reason=state_reason,
        extra={
            "source": "counterfactual_snapshot",
            "task_snapshot_id": task_snapshot_id,
            "arm_id": "reference",
            "selection_policy": snapshot.get("selection_policy") or "historical_replay",
            "replay_grade": snapshot.get("replay_grade"),
            "treatment_hash": snapshot.get("treatment_hash"),
            "prompt_hash": snapshot.get("prompt_hash"),
            "output_hash": snapshot.get("output_hash"),
            "reference_requested": snapshot.get("reference_requested", True),
            "reason_for_reference": snapshot.get("reason_for_reference"),
            "decision_measurement": measurement,
        },
    )
    stored = append_receipt(receipt, root=root)
    return {"trace_id": trace_id, "receipt": stored, "replayed": False}


def register_pair(
    *,
    experiment_id: str,
    pair_id: str,
    task_snapshot_id: str,
    candidate_trace_id: str,
    reference_trace_id: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Append an idempotent explicit candidate/reference pair declaration."""
    experiment_id = _text(experiment_id, "experiment_id")
    pair_id = _text(pair_id, "pair_id")
    task_snapshot_id = _text(task_snapshot_id, "task_snapshot_id")
    candidate_trace_id = _text(candidate_trace_id, "candidate_trace_id")

    candidate = find_receipt(candidate_trace_id, root=root)
    if candidate is None:
        raise ValueError("unknown candidate_trace_id")
    if candidate.get("capability_id") != CAPABILITY:
        raise ValueError("candidate_trace_id is not an AO decision receipt")

    if reference_trace_id is None:
        materialized = materialize_reference_snapshot(task_snapshot_id, root=root)
        reference_trace_id = str(materialized["trace_id"])
    reference_trace_id = _text(reference_trace_id, "reference_trace_id")
    if reference_trace_id == candidate_trace_id:
        raise ValueError("candidate and reference traces must differ")

    reference = find_receipt(reference_trace_id, root=root)
    if reference is None:
        raise ValueError("unknown reference_trace_id")
    reference_snapshot = _receipt_task_snapshot(reference)
    if reference_snapshot != task_snapshot_id:
        raise ValueError("reference receipt task_snapshot_id does not match pair")
    reference_source = (
        "counterfactual_snapshot"
        if reference.get("capability_id") == REFERENCE_CAPABILITY
        else "receipt"
    )

    core = {
        "schema": PAIR_SCHEMA,
        "experiment_id": experiment_id,
        "pair_id": pair_id,
        "task_snapshot_id": task_snapshot_id,
        "candidate_trace_id": candidate_trace_id,
        "reference_trace_id": reference_trace_id,
        "reference_source": reference_source,
    }
    pair_sha256 = _digest(core)

    path = pair_registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        fh.seek(0)
        for line in fh:
            try:
                previous = json.loads(line)
            except json.JSONDecodeError:
                continue
            if (
                previous.get("schema") == PAIR_SCHEMA
                and previous.get("experiment_id") == experiment_id
                and previous.get("pair_id") == pair_id
            ):
                if previous.get("pair_sha256") != pair_sha256:
                    raise ValueError("experiment pair id reused with different traces or snapshot")
                replay = dict(previous)
                replay["replayed"] = True
                fcntl.flock(fh, fcntl.LOCK_UN)
                return replay

        row = dict(core)
        row["pair_sha256"] = pair_sha256
        row["ts"] = time.time()
        fh.seek(0, 2)
        fh.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
        fcntl.flock(fh, fcntl.LOCK_UN)

    result = dict(row)
    result["replayed"] = False
    return result
