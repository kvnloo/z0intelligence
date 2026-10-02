"""Randomized AgentWeb outcome assignment registry and human-review barrier."""
from __future__ import annotations

from collections import defaultdict
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
from .outcome_coverage import (
    WINDOW_ORDER,
    _expectation_key,
    _observation_key,
    gated_outcome_observations,
    outcome_expectations_path,
    summarize_outcome_coverage,
)
from .outcome_observation import SUBJECT_RE, _iter_rows, _parse_time

ASSIGNMENT_SCHEMA_V1 = "agentweb.outcome_assignment.v1"
STORED_ASSIGNMENT_SCHEMA_V1 = "z0int.agentweb_outcome_assignment.v1"
ASSIGNMENT_RE = re.compile(r"^agentweb-outcome-assignment-[0-9a-f]{64}$")
DECISION_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
TREATMENT_RE = re.compile(r"^[a-f0-9]{16,64}$")

ARM_IDS = {"candidate", "reference"}
ASSIGNMENT_METHODS = {"randomized", "deterministic", "unknown"}
SELECTION_POLICIES = {"active", "audit", "historical_replay", "production"}
CAUSAL_SELECTION_POLICIES = {"active", "production"}

DEFAULT_MIN_TOTAL = 1000
DEFAULT_MIN_PER_ARM = 200


def outcome_assignments_path(root: Path | None = None) -> Path:
    layout = paths.ensure_layout(root)
    return layout["receipts"] / "agentweb_outcome_assignments.jsonl"


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


def validate_outcome_assignment(row: dict[str, Any]) -> None:
    allowed = {
        "schema",
        "assignment_id",
        "decision_trace_id",
        "unit_ref",
        "experiment_id",
        "pair_id",
        "arm_id",
        "treatment_hash",
        "selection_policy",
        "assignment_method",
        "assignment_probability",
        "assigned_at",
        "quality_authoritative",
    }
    if not isinstance(row, dict) or set(row) - allowed:
        raise ValueError("Unknown AgentWeb outcome assignment fields")
    if row.get("schema") != ASSIGNMENT_SCHEMA_V1:
        raise ValueError("Invalid outcome assignment schema")

    assignment_id = row.get("assignment_id")
    if (
        not isinstance(assignment_id, str)
        or not ASSIGNMENT_RE.fullmatch(assignment_id)
    ):
        raise ValueError("Invalid outcome assignment identity")

    decision_trace_id = row.get("decision_trace_id")
    if (
        not isinstance(decision_trace_id, str)
        or not DECISION_RE.fullmatch(decision_trace_id)
    ):
        raise ValueError("Invalid assignment decision trace identity")

    unit_ref = row.get("unit_ref")
    if not isinstance(unit_ref, str) or not SUBJECT_RE.fullmatch(unit_ref):
        raise ValueError("Assignment unit_ref must already be pseudonymous")

    experiment_id = row.get("experiment_id")
    if not isinstance(experiment_id, str) or not ID_RE.fullmatch(experiment_id):
        raise ValueError("Invalid experiment_id")

    pair_id = row.get("pair_id")
    if pair_id is not None and (
        not isinstance(pair_id, str) or not ID_RE.fullmatch(pair_id)
    ):
        raise ValueError("Invalid pair_id")

    if row.get("arm_id") not in ARM_IDS:
        raise ValueError("Invalid arm_id")

    treatment_hash = row.get("treatment_hash")
    if (
        not isinstance(treatment_hash, str)
        or not TREATMENT_RE.fullmatch(treatment_hash)
    ):
        raise ValueError("Invalid treatment_hash")

    if row.get("selection_policy") not in SELECTION_POLICIES:
        raise ValueError("Invalid selection_policy")

    if row.get("assignment_method") not in ASSIGNMENT_METHODS:
        raise ValueError("Invalid assignment_method")

    probability = row.get("assignment_probability")
    if (
        type(probability) not in (int, float)
        or not math.isfinite(float(probability))
        or not 0.0 < float(probability) < 1.0
    ):
        raise ValueError(
            "assignment_probability must be a finite probability strictly between 0 and 1"
        )

    _parse_time(row.get("assigned_at"), "assigned_at")

    if row.get("quality_authoritative") is not False:
        raise ValueError("Outcome assignments must not be quality-authoritative")


def ingest_outcome_assignment(
    row: dict[str, Any],
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    validate_outcome_assignment(row)
    assignment_id = row["assignment_id"]
    fingerprint = _digest(row)

    path = outcome_assignments_path(root)
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
            if candidate.get("assignment_id") == assignment_id:
                previous = candidate

        if previous is not None:
            if previous.get("assignment_sha256") != fingerprint:
                raise ValueError(
                    "outcome assignment identity reused for changed payload"
                )
            return {
                "ok": True,
                "assignment_id": assignment_id,
                "replayed": True,
            }

        stored = dict(row)
        stored.update(
            schema=STORED_ASSIGNMENT_SCHEMA_V1,
            source_schema=ASSIGNMENT_SCHEMA_V1,
            assignment_sha256=fingerprint,
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
        "assignment_id": assignment_id,
        "replayed": False,
    }


def _env_int(env: dict[str, str], key: str, default: int) -> int:
    try:
        value = int(env.get(key, ""))
    except (TypeError, ValueError):
        return default
    return value if 1 <= value <= 1_000_000 else default


def randomized_evidence_policy(
    env: dict[str, str] | None = None,
) -> dict[str, int]:
    source = dict(os.environ if env is None else env)
    return {
        "min_total_live_units": _env_int(
            source,
            "Z0INT_AGENTWEB_RANDOMIZED_MIN_TOTAL",
            DEFAULT_MIN_TOTAL,
        ),
        "min_complete_units_per_arm": _env_int(
            source,
            "Z0INT_AGENTWEB_RANDOMIZED_MIN_PER_ARM",
            DEFAULT_MIN_PER_ARM,
        ),
    }


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _sample_variance(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return sum((value - mean) ** 2 for value in values) / (len(values) - 1)


def _effect(candidate: list[float], reference: list[float]) -> dict[str, Any]:
    candidate_mean = _mean(candidate)
    reference_mean = _mean(reference)
    if candidate_mean is None or reference_mean is None:
        return {
            "candidate_n": len(candidate),
            "reference_n": len(reference),
            "candidate_mean": candidate_mean,
            "reference_mean": reference_mean,
            "difference_in_means": None,
            "standard_error": None,
            "ci95": None,
        }

    difference = candidate_mean - reference_mean
    candidate_var = _sample_variance(candidate)
    reference_var = _sample_variance(reference)
    standard_error = None
    ci95 = None
    if candidate_var is not None and reference_var is not None:
        standard_error = math.sqrt(
            candidate_var / len(candidate)
            + reference_var / len(reference)
        )
        ci95 = [
            difference - 1.96 * standard_error,
            difference + 1.96 * standard_error,
        ]

    return {
        "candidate_n": len(candidate),
        "reference_n": len(reference),
        "candidate_mean": candidate_mean,
        "reference_mean": reference_mean,
        "difference_in_means": difference,
        "standard_error": standard_error,
        "ci95": ci95,
    }


def summarize_randomized_outcome_evidence(
    *,
    root: Path | None = None,
    now: datetime | None = None,
    coverage_policy: dict[str, Any] | None = None,
    randomized_policy: dict[str, int] | None = None,
) -> dict[str, Any]:
    resolved_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    coverage = summarize_outcome_coverage(
        root=root,
        now=resolved_now,
        policy=coverage_policy,
    )
    policy = dict(randomized_policy or randomized_evidence_policy())

    assignments = list(_iter_rows(outcome_assignments_path(root)) or [])
    expectations = list(_iter_rows(outcome_expectations_path(root)) or [])
    gated = gated_outcome_observations(
        root=root,
        now=resolved_now,
        policy=coverage_policy,
    )

    expectation_index = {
        (
            row["decision_trace_id"],
            row.get("subject_ref"),
        ): row
        for row in expectations
        if isinstance(row, dict)
    }

    all_by_experiment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for assignment in assignments:
        all_by_experiment[str(assignment.get("experiment_id"))].append(assignment)

    valid_by_experiment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    invalid_assignment_reasons: dict[str, list[str]] = defaultdict(list)
    for experiment_id, rows in all_by_experiment.items():
        for row in rows:
            reasons: list[str] = []
            if row.get("assignment_method") != "randomized":
                reasons.append("assignment_not_randomized")
            if row.get("selection_policy") not in CAUSAL_SELECTION_POLICIES:
                reasons.append("selection_policy_not_live")
            if (
                row.get("decision_trace_id"),
                row.get("unit_ref"),
            ) not in expectation_index:
                reasons.append("assignment_missing_outcome_expectation")
            if reasons:
                invalid_assignment_reasons[experiment_id].extend(reasons)
            else:
                valid_by_experiment[experiment_id].append(row)

    observation_rows = gated["rows"] if gated.get("eligible") else []
    observations_by_assignment_key: dict[
        tuple[str, str | None],
        list[dict[str, Any]],
    ] = defaultdict(list)
    for row in observation_rows:
        observations_by_assignment_key[
            (row["decision_trace_id"], row.get("subject_ref"))
        ].append(row)

    experiments: dict[str, Any] = {}
    any_review_ready = False

    for experiment_id in sorted(all_by_experiment):
        all_rows = all_by_experiment[experiment_id]
        valid_rows = valid_by_experiment.get(experiment_id, [])
        reasons: list[str] = []

        if not coverage["promotion_gate"]["eligible"]:
            reasons.append("outcome_coverage_gate_closed")

        if invalid_assignment_reasons.get(experiment_id):
            reasons.append("invalid_or_noncausal_assignment_present")

        probabilities = {
            round(float(row["assignment_probability"]), 12)
            for row in valid_rows
        }
        if len(probabilities) != 1:
            reasons.append("varying_assignment_probability_not_supported")

        treatment_by_arm: dict[str, set[str]] = {
            "candidate": set(),
            "reference": set(),
        }
        units_by_arm: dict[str, set[str]] = {
            "candidate": set(),
            "reference": set(),
        }
        duplicate_units: set[str] = set()
        seen_unit_arm: dict[str, str] = {}

        for row in valid_rows:
            arm = row["arm_id"]
            unit = row["unit_ref"]
            treatment_by_arm[arm].add(row["treatment_hash"])
            units_by_arm[arm].add(unit)
            previous_arm = seen_unit_arm.get(unit)
            if previous_arm is not None and previous_arm != arm:
                duplicate_units.add(unit)
            seen_unit_arm[unit] = arm

        if duplicate_units:
            reasons.append("unit_assigned_to_multiple_arms")
        for arm in ("candidate", "reference"):
            if len(treatment_by_arm[arm]) != 1:
                reasons.append(f"{arm}_treatment_not_stable")

        total_live_units = len(set().union(*units_by_arm.values()))
        if total_live_units < int(policy["min_total_live_units"]):
            reasons.append("minimum_live_sample_not_met")
        for arm in ("candidate", "reference"):
            if len(units_by_arm[arm]) < int(policy["min_complete_units_per_arm"]):
                reasons.append(f"{arm}_minimum_sample_not_met")

        metric_windows: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        required_metric_windows: set[tuple[str, str]] = set()

        for assignment in valid_rows:
            expectation = expectation_index.get(
                (
                    assignment["decision_trace_id"],
                    assignment["unit_ref"],
                )
            )
            if expectation is None:
                continue
            metric = expectation["metric"]
            metric_key = f"{metric['source']}:{metric['name']}"
            for window in expectation["required_windows"]:
                required_metric_windows.add((metric_key, window))

        values: dict[
            tuple[str, str, str],
            list[float],
        ] = defaultdict(list)

        assignment_index = {
            (row["decision_trace_id"], row["unit_ref"]): row
            for row in valid_rows
        }
        for observation in observation_rows:
            assignment = assignment_index.get(
                (
                    observation["decision_trace_id"],
                    observation.get("subject_ref"),
                )
            )
            if assignment is None:
                continue
            expectation = expectation_index.get(
                (
                    observation["decision_trace_id"],
                    observation.get("subject_ref"),
                )
            )
            if expectation is None:
                continue
            metric = observation["metric"]
            metric_key = f"{metric['source']}:{metric['name']}"
            values[
                (
                    metric_key,
                    observation["window"],
                    assignment["arm_id"],
                )
            ].append(float(metric["value"]))

        for metric_key, window in sorted(required_metric_windows):
            candidate_values = values.get(
                (metric_key, window, "candidate"),
                [],
            )
            reference_values = values.get(
                (metric_key, window, "reference"),
                [],
            )
            effect = _effect(candidate_values, reference_values)
            metric_windows[metric_key][window] = effect
            if (
                effect["candidate_n"] < int(policy["min_complete_units_per_arm"])
                or effect["reference_n"] < int(policy["min_complete_units_per_arm"])
            ):
                reasons.append(
                    f"{metric_key}:{window}:complete_arm_sample_not_met"
                )

        review_ready = not reasons
        any_review_ready = any_review_ready or review_ready
        experiments[experiment_id] = {
            "registered_assignments": len(all_rows),
            "valid_randomized_assignments": len(valid_rows),
            "assignment_probability": (
                next(iter(probabilities)) if len(probabilities) == 1 else None
            ),
            "arms": {
                "candidate": {
                    "units": len(units_by_arm["candidate"]),
                    "treatment_hashes": sorted(treatment_by_arm["candidate"]),
                },
                "reference": {
                    "units": len(units_by_arm["reference"]),
                    "treatment_hashes": sorted(treatment_by_arm["reference"]),
                },
            },
            "metric_windows": dict(metric_windows),
            "review_ready": review_ready,
            "human_review_required": True,
            "auto_promotion_allowed": False,
            "causal_quality_proven": False,
            "reasons": sorted(set(reasons)),
        }

    return {
        "schema": "z0int.agentweb_randomized_outcome_evidence.v1",
        "as_of": resolved_now.isoformat(),
        "coverage": coverage,
        "policy": policy,
        "experiments": experiments,
        "review_ready_any": any_review_ready,
        "human_review_required": True,
        "auto_promotion_allowed": False,
        "causal_quality_proven": False,
    }
