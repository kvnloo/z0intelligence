"""Append-only AgentWeb real-world outcome observations.

These rows are observational ETL only. They are deliberately stored separately
from canonical quality joins so they cannot mint gold or affect routing by mere
existence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any

from . import paths

SCHEMA_V1 = "agentweb.outcome_observation.v1"
STORED_SCHEMA_V1 = "z0int.agentweb_outcome_observation.v1"
WINDOW_SECONDS = {"1h": 3600, "24h": 86400, "7d": 604800}
MEASUREMENT_STATES = {"complete", "partial"}
DIRECTIONS = {"higher_better", "lower_better", "neutral"}
OBSERVATION_RE = re.compile(r"^agentweb-outcome-[0-9a-f]{64}$")
DECISION_RE = re.compile(r"^[0-9a-f]{64}$")
SUBJECT_RE = re.compile(r"^agentweb-subject:[0-9a-f]{24}$")
METRIC_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")


def outcome_observations_path(root: Path | None = None) -> Path:
    layout = paths.ensure_layout(root)
    return layout["receipts"] / "agentweb_outcome_observations.jsonl"


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _parse_time(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError(f"Invalid {name}")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"Invalid {name}") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def validate_outcome_observation(row: dict[str, Any]) -> None:
    allowed = {
        "schema",
        "observation_id",
        "decision_trace_id",
        "subject_ref",
        "window",
        "decision_time",
        "execution_time",
        "observation_time",
        "measurement_state",
        "metric",
        "quality_authoritative",
    }
    if not isinstance(row, dict) or set(row) - allowed:
        raise ValueError("Unknown AgentWeb outcome observation fields")
    if row.get("schema") != SCHEMA_V1:
        raise ValueError("Invalid outcome observation schema")

    observation_id = row.get("observation_id")
    if not isinstance(observation_id, str) or not OBSERVATION_RE.fullmatch(observation_id):
        raise ValueError("Invalid outcome observation identity")

    decision_trace_id = row.get("decision_trace_id")
    if not isinstance(decision_trace_id, str) or not DECISION_RE.fullmatch(decision_trace_id):
        raise ValueError("Invalid decision trace identity")

    subject_ref = row.get("subject_ref")
    if subject_ref is not None and (
        not isinstance(subject_ref, str) or not SUBJECT_RE.fullmatch(subject_ref)
    ):
        raise ValueError("Outcome subject_ref must already be pseudonymous")

    window = row.get("window")
    if window not in WINDOW_SECONDS:
        raise ValueError("Invalid outcome measurement window")

    decision_time = _parse_time(row.get("decision_time"), "decision_time")
    execution_raw = row.get("execution_time")
    execution_time = (
        _parse_time(execution_raw, "execution_time")
        if execution_raw is not None
        else None
    )
    observation_time = _parse_time(row.get("observation_time"), "observation_time")
    if execution_time is not None and execution_time < decision_time:
        raise ValueError("execution_time cannot predate decision_time")
    if observation_time < decision_time:
        raise ValueError("observation_time cannot predate decision_time")
    if execution_time is not None and observation_time < execution_time:
        raise ValueError("observation_time cannot predate execution_time")

    if row.get("measurement_state") not in MEASUREMENT_STATES:
        raise ValueError("Invalid outcome measurement_state")

    if row.get("quality_authoritative") is not False:
        raise ValueError("Outcome observations must not be quality-authoritative")

    metric = row.get("metric")
    if not isinstance(metric, dict):
        raise ValueError("Outcome metric must be an object")
    if set(metric) - {"source", "name", "value", "unit", "direction"}:
        raise ValueError("Outcome metric contains unsupported fields")

    source = metric.get("source")
    name = metric.get("name")
    if not isinstance(source, str) or not METRIC_ID_RE.fullmatch(source):
        raise ValueError("Invalid outcome metric source")
    if not isinstance(name, str) or not METRIC_ID_RE.fullmatch(name):
        raise ValueError("Invalid outcome metric name")

    value = metric.get("value")
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise ValueError("Outcome metric value must be finite numeric")
    if abs(float(value)) > 1e15:
        raise ValueError("Outcome metric value is out of range")

    unit = metric.get("unit")
    if unit is not None and (
        not isinstance(unit, str)
        or not unit.strip()
        or len(unit) > 32
        or any(ord(ch) < 32 for ch in unit)
    ):
        raise ValueError("Invalid outcome metric unit")

    direction = metric.get("direction")
    if direction is not None and direction not in DIRECTIONS:
        raise ValueError("Invalid outcome metric direction")


def _iter_rows(path: Path):
    if not path.is_file():
        return
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if line:
                yield json.loads(line)


def find_outcome_observation(
    observation_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any] | None:
    found = None
    for row in _iter_rows(outcome_observations_path(root)) or []:
        if row.get("observation_id") == observation_id:
            found = row
    return found


def ingest_outcome_observation(
    row: dict[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    validate_outcome_observation(row)
    observation_id = row["observation_id"]
    fingerprint = _digest(row)

    path = outcome_observations_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        previous = None
        for line in stream:
            line = line.strip()
            if not line:
                continue
            candidate = json.loads(line)
            if candidate.get("observation_id") == observation_id:
                previous = candidate

        if previous is not None:
            if previous.get("observation_sha256") != fingerprint:
                raise ValueError(
                    "outcome observation identity reused for changed payload"
                )
            return {
                "ok": True,
                "observation_id": observation_id,
                "replayed": True,
                "age_seconds": previous["age_seconds"],
                "window_seconds": previous["window_seconds"],
            }

        decision_time = _parse_time(row["decision_time"], "decision_time")
        observation_time = _parse_time(
            row["observation_time"],
            "observation_time",
        )
        stored = dict(row)
        stored.update(
            schema=STORED_SCHEMA_V1,
            source_schema=SCHEMA_V1,
            observation_sha256=fingerprint,
            observational=True,
            quality_authoritative=False,
            age_seconds=max(
                0,
                int((observation_time - decision_time).total_seconds()),
            ),
            window_seconds=WINDOW_SECONDS[row["window"]],
        )
        stream.seek(0, os.SEEK_END)
        stream.write(json.dumps(stored, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream, fcntl.LOCK_UN)

    return {
        "ok": True,
        "observation_id": observation_id,
        "replayed": False,
        "age_seconds": stored["age_seconds"],
        "window_seconds": stored["window_seconds"],
    }
