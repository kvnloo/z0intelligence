from datetime import datetime, timezone
from pathlib import Path

import pytest

from z0int.outcome_coverage import ingest_outcome_expectation
from z0int.outcome_observation import ingest_outcome_observation
from z0int.outcome_randomized_evidence import (
    ingest_outcome_assignment,
    outcome_assignments_path,
    summarize_randomized_outcome_evidence,
    validate_outcome_assignment,
)


NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
COVERAGE_POLICY = {
    "min_eligible_per_window": 4,
    "min_coverage": 1.0,
    "min_complete_coverage": 1.0,
}
RANDOMIZED_POLICY = {
    "min_total_live_units": 4,
    "min_complete_units_per_arm": 2,
}


def subject(index: int) -> str:
    return "agentweb-subject:" + f"{index:024x}"[-24:]


def trace(index: int) -> str:
    return f"{index:064x}"


def expectation(index: int):
    return {
        "schema": "agentweb.outcome_expectation.v1",
        "expectation_id": "agentweb-outcome-expectation-" + f"{index:064x}",
        "decision_trace_id": trace(index),
        "subject_ref": subject(index),
        "decision_time": "2026-09-01T12:00:00Z",
        "execution_time": "2026-09-01T12:00:05Z",
        "required_windows": ["1h", "24h", "7d"],
        "metric": {
            "source": "agentweb.analytics",
            "name": "conversion_rate",
            "unit": "ratio",
            "direction": "higher_better",
        },
        "quality_authoritative": False,
    }


def assignment(index: int, arm: str, *, method="randomized", policy="production"):
    treatment = "a" * 16 if arm == "candidate" else "b" * 16
    return {
        "schema": "agentweb.outcome_assignment.v1",
        "assignment_id": "agentweb-outcome-assignment-" + f"{index:064x}",
        "decision_trace_id": trace(index),
        "unit_ref": subject(index),
        "experiment_id": "agentweb-live-outcome-v1",
        "pair_id": f"pair-{index}",
        "arm_id": arm,
        "treatment_hash": treatment,
        "selection_policy": policy,
        "assignment_method": method,
        "assignment_probability": 0.5,
        "assigned_at": "2026-09-01T11:59:59Z",
        "quality_authoritative": False,
    }


def observation(index: int, window: str, value: float):
    observed = {
        "1h": "2026-09-01T13:00:00Z",
        "24h": "2026-09-02T12:00:00Z",
        "7d": "2026-09-08T12:00:00Z",
    }[window]
    return {
        "schema": "agentweb.outcome_observation.v1",
        "observation_id": "agentweb-outcome-" + f"{index * 10 + {'1h':1,'24h':2,'7d':3}[window]:064x}",
        "decision_trace_id": trace(index),
        "subject_ref": subject(index),
        "window": window,
        "decision_time": "2026-09-01T12:00:00Z",
        "execution_time": "2026-09-01T12:00:05Z",
        "observation_time": observed,
        "measurement_state": "complete",
        "metric": {
            "source": "agentweb.analytics",
            "name": "conversion_rate",
            "value": value,
            "unit": "ratio",
            "direction": "higher_better",
        },
        "quality_authoritative": False,
    }


def populate_complete_fixture(tmp_path: Path):
    values = {
        1: 0.20,
        2: 0.24,
        3: 0.10,
        4: 0.12,
    }
    for index in range(1, 5):
        arm = "candidate" if index <= 2 else "reference"
        ingest_outcome_expectation(expectation(index), root=tmp_path)
        ingest_outcome_assignment(assignment(index, arm), root=tmp_path)
        for window in ("1h", "24h", "7d"):
            ingest_outcome_observation(
                observation(index, window, values[index]),
                root=tmp_path,
            )


def test_assignment_is_append_only_and_conflicts_on_arm_change(tmp_path: Path):
    first = ingest_outcome_assignment(assignment(1, "candidate"), root=tmp_path)
    second = ingest_outcome_assignment(assignment(1, "candidate"), root=tmp_path)
    assert first["replayed"] is False
    assert second["replayed"] is True

    changed = assignment(1, "reference")
    with pytest.raises(ValueError, match="reused for changed payload"):
        ingest_outcome_assignment(changed, root=tmp_path)

    assert len(outcome_assignments_path(tmp_path).read_text().splitlines()) == 1


def test_assignment_requires_randomization_probability_shape():
    value = assignment(1, "candidate")
    value["assignment_probability"] = 1.0
    with pytest.raises(ValueError, match="strictly between 0 and 1"):
        validate_outcome_assignment(value)


def test_randomized_complete_fixture_is_review_ready_but_never_auto_promotes(tmp_path: Path):
    populate_complete_fixture(tmp_path)

    summary = summarize_randomized_outcome_evidence(
        root=tmp_path,
        now=NOW,
        coverage_policy=COVERAGE_POLICY,
        randomized_policy=RANDOMIZED_POLICY,
    )
    experiment = summary["experiments"]["agentweb-live-outcome-v1"]

    assert summary["coverage"]["promotion_gate"]["eligible"] is True
    assert experiment["review_ready"] is True
    assert experiment["human_review_required"] is True
    assert experiment["auto_promotion_allowed"] is False
    assert experiment["causal_quality_proven"] is False
    assert experiment["arms"]["candidate"]["units"] == 2
    assert experiment["arms"]["reference"]["units"] == 2

    one_hour = experiment["metric_windows"]["agentweb.analytics:conversion_rate"]["1h"]
    assert one_hour["candidate_n"] == 2
    assert one_hour["reference_n"] == 2
    assert one_hour["candidate_mean"] == pytest.approx(0.22)
    assert one_hour["reference_mean"] == pytest.approx(0.11)
    assert one_hour["difference_in_means"] == pytest.approx(0.11)
    assert one_hour["standard_error"] is not None
    assert len(one_hour["ci95"]) == 2


def test_nonrandomized_or_audit_assignment_blocks_review(tmp_path: Path):
    for index in range(1, 5):
        arm = "candidate" if index <= 2 else "reference"
        ingest_outcome_expectation(expectation(index), root=tmp_path)
        ingest_outcome_assignment(
            assignment(
                index,
                arm,
                method="deterministic" if index == 1 else "randomized",
                policy="audit" if index == 2 else "production",
            ),
            root=tmp_path,
        )
        for window in ("1h", "24h", "7d"):
            ingest_outcome_observation(
                observation(index, window, 0.1 + index / 100),
                root=tmp_path,
            )

    summary = summarize_randomized_outcome_evidence(
        root=tmp_path,
        now=NOW,
        coverage_policy=COVERAGE_POLICY,
        randomized_policy=RANDOMIZED_POLICY,
    )
    experiment = summary["experiments"]["agentweb-live-outcome-v1"]
    assert experiment["review_ready"] is False
    assert "invalid_or_noncausal_assignment_present" in experiment["reasons"]
    assert experiment["auto_promotion_allowed"] is False


def test_coverage_gate_must_pass_before_randomized_review(tmp_path: Path):
    populate_complete_fixture(tmp_path)
    strict = {
        "min_eligible_per_window": 5,
        "min_coverage": 1.0,
        "min_complete_coverage": 1.0,
    }
    summary = summarize_randomized_outcome_evidence(
        root=tmp_path,
        now=NOW,
        coverage_policy=strict,
        randomized_policy=RANDOMIZED_POLICY,
    )
    experiment = summary["experiments"]["agentweb-live-outcome-v1"]
    assert summary["coverage"]["promotion_gate"]["eligible"] is False
    assert experiment["review_ready"] is False
    assert "outcome_coverage_gate_closed" in experiment["reasons"]
