"""AgentWeb outcome expectation registry and conservative coverage gate."""
from __future__ import annotations

from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

from . import paths
from .outcome_observation import (
    DIRECTIONS,
    METRIC_ID_RE,
    SUBJECT_RE,
    WINDOW_SECONDS,
    _iter_rows,
    _parse_time,
    outcome_observations_path,
)

EXPECTATION_SCHEMA_V1 = "agentweb.outcome_expectation.v1"
STORED_EXPECTATION_SCHEMA_V1 = "z0int.agentweb_outcome_expectation.v1"
EXPECTATION_RE = re.compile(r"^agentweb-outcome-expectation-[0-9a-f]{64}$")
DECISION_RE = re.compile(r"^[0-9a-f]{64}$")
WINDOW_ORDER = ("1h", "24h", "7d")

DEFAULT_MIN_ELIGIBLE_PER_WINDOW = 20
DEFAULT_MIN_COVERAGE = 0.90
DEFAULT_MIN_COMPLETE_COVERAGE = 0.85


def outcome_expectations_path(root: Path | None = None) -> Path:
    layout = paths.ensure_layout(root)
    return layout["receipts"] / "agentweb_outcome_expectations.jsonl"


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


def validate_outcome_expectation(row: dict[str, Any]) -> None:
    allowed = {
        "schema",
        "expectation_id",
        "decision_trace_id",
        "subject_ref",
        "decision_time",
        "execution_time",
        "required_windows",
        "metric",
        "quality_authoritative",
    }
    if not isinstance(row, dict) or set(row) - allowed:
        raise ValueError("Unknown AgentWeb outcome expectation fields")
    if row.get("schema") != EXPECTATION_SCHEMA_V1:
        raise ValueError("Invalid outcome expectation schema")

    expectation_id = row.get("expectation_id")
    if (
        not isinstance(expectation_id, str)
        or not EXPECTATION_RE.fullmatch(expectation_id)
    ):
        raise ValueError("Invalid outcome expectation identity")

    decision_trace_id = row.get("decision_trace_id")
    if (
        not isinstance(decision_trace_id, str)
        or not DECISION_RE.fullmatch(decision_trace_id)
    ):
        raise ValueError("Invalid decision trace identity")

    subject_ref = row.get("subject_ref")
    if subject_ref is not None and (
        not isinstance(subject_ref, str)
        or not SUBJECT_RE.fullmatch(subject_ref)
    ):
        raise ValueError("Outcome expectation subject_ref must already be pseudonymous")

    decision_time = _parse_time(row.get("decision_time"), "decision_time")
    execution_raw = row.get("execution_time")
    if execution_raw is not None:
        execution_time = _parse_time(execution_raw, "execution_time")
        if execution_time < decision_time:
            raise ValueError("execution_time cannot predate decision_time")

    windows = row.get("required_windows")
    if (
        not isinstance(windows, list)
        or not windows
        or len(windows) > len(WINDOW_ORDER)
        or any(window not in WINDOW_SECONDS for window in windows)
        or len(set(windows)) != len(windows)
        or windows != [window for window in WINDOW_ORDER if window in windows]
    ):
        raise ValueError(
            "required_windows must be a unique canonical subset of 1h,24h,7d"
        )

    metric = row.get("metric")
    if not isinstance(metric, dict):
        raise ValueError("Outcome expectation metric must be an object")
    if set(metric) - {"source", "name", "unit", "direction"}:
        raise ValueError("Outcome expectation metric contains unsupported fields")
    if (
        not isinstance(metric.get("source"), str)
        or not METRIC_ID_RE.fullmatch(metric["source"])
    ):
        raise ValueError("Invalid outcome expectation metric source")
    if (
        not isinstance(metric.get("name"), str)
        or not METRIC_ID_RE.fullmatch(metric["name"])
    ):
        raise ValueError("Invalid outcome expectation metric name")

    unit = metric.get("unit")
    if unit is not None and (
        not isinstance(unit, str)
        or not unit.strip()
        or len(unit) > 32
        or any(ord(ch) < 32 for ch in unit)
    ):
        raise ValueError("Invalid outcome expectation metric unit")

    direction = metric.get("direction")
    if direction is not None and direction not in DIRECTIONS:
        raise ValueError("Invalid outcome expectation metric direction")

    if row.get("quality_authoritative") is not False:
        raise ValueError("Outcome expectations must not be quality-authoritative")


def ingest_outcome_expectation(
    row: dict[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    validate_outcome_expectation(row)
    expectation_id = row["expectation_id"]
    fingerprint = _digest(row)

    path = outcome_expectations_path(root)
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
            if candidate.get("expectation_id") == expectation_id:
                previous = candidate

        if previous is not None:
            if previous.get("expectation_sha256") != fingerprint:
                raise ValueError(
                    "outcome expectation identity reused for changed payload"
                )
            return {
                "ok": True,
                "expectation_id": expectation_id,
                "replayed": True,
            }

        stored = dict(row)
        stored.update(
            schema=STORED_EXPECTATION_SCHEMA_V1,
            source_schema=EXPECTATION_SCHEMA_V1,
            expectation_sha256=fingerprint,
            observational=True,
            quality_authoritative=False,
        )
        stream.seek(0, os.SEEK_END)
        stream.write(json.dumps(stored, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
        fcntl.flock(stream, fcntl.LOCK_UN)

    return {
        "ok": True,
        "expectation_id": expectation_id,
        "replayed": False,
    }


def _env_int(env: dict[str, str], key: str, default: int) -> int:
    try:
        value = int(env.get(key, ""))
    except (TypeError, ValueError):
        return default
    return value if 1 <= value <= 1_000_000 else default


def _env_ratio(env: dict[str, str], key: str, default: float) -> float:
    try:
        value = float(env.get(key, ""))
    except (TypeError, ValueError):
        return default
    return value if 0.0 <= value <= 1.0 else default


def outcome_coverage_policy(
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    source = dict(os.environ if env is None else env)
    return {
        "min_eligible_per_window": _env_int(
            source,
            "Z0INT_AGENTWEB_OUTCOME_MIN_ELIGIBLE_PER_WINDOW",
            DEFAULT_MIN_ELIGIBLE_PER_WINDOW,
        ),
        "min_coverage": _env_ratio(
            source,
            "Z0INT_AGENTWEB_OUTCOME_MIN_COVERAGE",
            DEFAULT_MIN_COVERAGE,
        ),
        "min_complete_coverage": _env_ratio(
            source,
            "Z0INT_AGENTWEB_OUTCOME_MIN_COMPLETE_COVERAGE",
            DEFAULT_MIN_COMPLETE_COVERAGE,
        ),
    }


def _expectation_key(row: dict[str, Any]) -> tuple[str, str | None, str, str]:
    metric = row["metric"]
    return (
        row["decision_trace_id"],
        row.get("subject_ref"),
        metric["source"],
        metric["name"],
    )


def _observation_key(
    row: dict[str, Any],
) -> tuple[str, str | None, str, str, str]:
    metric = row["metric"]
    return (
        row["decision_trace_id"],
        row.get("subject_ref"),
        metric["source"],
        metric["name"],
        row["window"],
    )


def summarize_outcome_coverage(
    *,
    root: Path | None = None,
    now: datetime | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    resolved_policy = dict(policy or outcome_coverage_policy())

    expectations = list(_iter_rows(outcome_expectations_path(root)) or [])
    observations = list(_iter_rows(outcome_observations_path(root)) or [])

    expectation_keys = {
        _expectation_key(row)
        for row in expectations
        if isinstance(row, dict) and isinstance(row.get("metric"), dict)
    }
    observation_index = {
        _observation_key(row): row
        for row in observations
        if isinstance(row, dict)
        and isinstance(row.get("metric"), dict)
        and row.get("window") in WINDOW_SECONDS
    }

    by_window: dict[str, dict[str, Any]] = {
        window: {
            "registered": 0,
            "eligible": 0,
            "observed": 0,
            "complete": 0,
            "partial": 0,
            "missing": 0,
            "early_or_invalid": 0,
            "coverage": None,
            "complete_coverage": None,
            "passes": False,
            "reasons": [],
        }
        for window in WINDOW_ORDER
    }

    required_windows: set[str] = set()
    eligible_keys: set[tuple[str, str | None, str, str, str]] = set()

    for expectation in expectations:
        base_key = _expectation_key(expectation)
        decision_time = _parse_time(
            expectation["decision_time"],
            "decision_time",
        )
        for window in expectation["required_windows"]:
            required_windows.add(window)
            stats = by_window[window]
            stats["registered"] += 1

            due_at = decision_time.timestamp() + WINDOW_SECONDS[window]
            if resolved_now.timestamp() < due_at:
                continue

            stats["eligible"] += 1
            key = (*base_key, window)
            eligible_keys.add(key)
            observation = observation_index.get(key)
            if observation is None:
                stats["missing"] += 1
                continue

            if int(observation.get("age_seconds", -1)) < WINDOW_SECONDS[window]:
                stats["early_or_invalid"] += 1
                stats["missing"] += 1
                continue

            stats["observed"] += 1
            if observation.get("measurement_state") == "complete":
                stats["complete"] += 1
            else:
                stats["partial"] += 1

    min_eligible = int(resolved_policy["min_eligible_per_window"])
    min_coverage = float(resolved_policy["min_coverage"])
    min_complete = float(resolved_policy["min_complete_coverage"])

    gate_reasons: list[str] = []
    for window in WINDOW_ORDER:
        stats = by_window[window]
        eligible = stats["eligible"]
        if eligible > 0:
            stats["coverage"] = stats["observed"] / eligible
            stats["complete_coverage"] = stats["complete"] / eligible

        if window not in required_windows:
            stats["reasons"] = ["not_required"]
            continue

        reasons: list[str] = []
        if eligible < min_eligible:
            reasons.append("insufficient_mature_sample")
        if stats["coverage"] is None or stats["coverage"] < min_coverage:
            reasons.append("joined_coverage_below_threshold")
        if (
            stats["complete_coverage"] is None
            or stats["complete_coverage"] < min_complete
        ):
            reasons.append("complete_coverage_below_threshold")
        if stats["early_or_invalid"] > 0:
            reasons.append("early_or_invalid_observation_present")

        stats["passes"] = not reasons
        stats["reasons"] = reasons
        gate_reasons.extend(f"{window}:{reason}" for reason in reasons)

    orphan_observations = 0
    for key in observation_index:
        base = key[:4]
        if base not in expectation_keys:
            orphan_observations += 1

    promotion_eligible = bool(required_windows) and all(
        by_window[window]["passes"]
        for window in required_windows
    )

    return {
        "schema": "z0int.agentweb_outcome_coverage.v1",
        "as_of": resolved_now.isoformat(),
        "policy": resolved_policy,
        "expectations": len(expectations),
        "observations": len(observations),
        "required_windows": [
            window for window in WINDOW_ORDER if window in required_windows
        ],
        "by_window": by_window,
        "orphan_observations": orphan_observations,
        "promotion_gate": {
            "eligible": promotion_eligible,
            "necessary_not_sufficient": True,
            "causal_quality_proven": False,
            "reasons": gate_reasons if gate_reasons else (
                [] if promotion_eligible else ["no_required_expectations"]
            ),
        },
    }


def gated_outcome_observations(
    *,
    root: Path | None = None,
    now: datetime | None = None,
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = summarize_outcome_coverage(
        root=root,
        now=now,
        policy=policy,
    )
    if not summary["promotion_gate"]["eligible"]:
        return {
            "schema": "z0int.agentweb_outcome_gated_rows.v1",
            "eligible": False,
            "rows": [],
            "coverage": summary,
        }

    expectations = list(_iter_rows(outcome_expectations_path(root)) or [])
    expected = {
        (*_expectation_key(row), window)
        for row in expectations
        for window in row["required_windows"]
    }
    rows = [
        row
        for row in (_iter_rows(outcome_observations_path(root)) or [])
        if _observation_key(row) in expected
        and row.get("measurement_state") == "complete"
        and int(row.get("age_seconds", -1)) >= WINDOW_SECONDS[row["window"]]
    ]
    return {
        "schema": "z0int.agentweb_outcome_gated_rows.v1",
        "eligible": True,
        "rows": rows,
        "coverage": summary,
    }
