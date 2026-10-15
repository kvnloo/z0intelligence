from datetime import datetime, timezone
from pathlib import Path

import pytest

from z0int.outcome_coverage import (
    gated_outcome_observations,
    ingest_outcome_expectation,
    outcome_expectations_path,
    summarize_outcome_coverage,
    validate_outcome_expectation,
)
from z0int.outcome_observation import ingest_outcome_observation


NOW = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
POLICY = {
    "min_eligible_per_window": 2,
    "min_coverage": 1.0,
    "min_complete_coverage": 1.0,
}


def expectation(index: int):
    hex_id = f"{index:064x}"
    subject = f"{index:024x}"[-24:]
    return {
        "schema": "agentweb.outcome_expectation.v1",
        "expectation_id": "agentweb-outcome-expectation-" + hex_id,
        "decision_trace_id": hex_id,
        "subject_ref": "agentweb-subject:" + subject,
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


def observation(index: int, window: str, *, state="complete", value=0.1):
    seconds = {"1h": 3600, "24h": 86400, "7d": 604800}[window]
    observed = datetime.fromtimestamp(
        datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc).timestamp() + seconds,
        tz=timezone.utc,
    ).isoformat().replace("+00:00", "Z")
    return {
        "schema": "agentweb.outcome_observation.v1",
        "observation_id": "agentweb-outcome-" + (
            f"{index * 10 + {'1h': 1, '24h': 2, '7d': 3}[window]:064x}"
        ),
        "decision_trace_id": f"{index:064x}",
        "subject_ref": "agentweb-subject:" + f"{index:024x}"[-24:],
        "window": window,
        "decision_time": "2026-09-01T12:00:00Z",
        "execution_time": "2026-09-01T12:00:05Z",
        "observation_time": observed,
        "measurement_state": state,
        "metric": {
            "source": "agentweb.analytics",
            "name": "conversion_rate",
            "value": value,
            "unit": "ratio",
            "direction": "higher_better",
        },
        "quality_authoritative": False,
    }


def test_expectation_is_append_only_and_conflicts_on_changed_windows(tmp_path: Path):
    first = ingest_outcome_expectation(expectation(1), root=tmp_path)
    second = ingest_outcome_expectation(expectation(1), root=tmp_path)
    assert first["replayed"] is False
    assert second["replayed"] is True

    changed = expectation(1)
    changed["required_windows"] = ["1h", "24h"]
    with pytest.raises(ValueError, match="reused for changed payload"):
        ingest_outcome_expectation(changed, root=tmp_path)

    assert len(outcome_expectations_path(tmp_path).read_text().splitlines()) == 1


def test_expectation_requires_canonical_windows_and_non_authoritative_status():
    value = expectation(1)
    value["required_windows"] = ["24h", "1h"]
    with pytest.raises(ValueError, match="canonical subset"):
        validate_outcome_expectation(value)

    value = expectation(1)
    value["quality_authoritative"] = True
    with pytest.raises(ValueError, match="must not be quality-authoritative"):
        validate_outcome_expectation(value)


def test_gate_stays_closed_until_every_mature_window_meets_threshold(tmp_path: Path):
    for index in (1, 2):
        ingest_outcome_expectation(expectation(index), root=tmp_path)

    for window in ("1h", "24h", "7d"):
        ingest_outcome_observation(observation(1, window), root=tmp_path)
    for window in ("1h", "24h"):
        ingest_outcome_observation(observation(2, window), root=tmp_path)

    blocked = summarize_outcome_coverage(
        root=tmp_path,
        now=NOW,
        policy=POLICY,
    )
    assert blocked["promotion_gate"]["eligible"] is False
    assert blocked["by_window"]["1h"]["passes"] is True
    assert blocked["by_window"]["24h"]["passes"] is True
    assert blocked["by_window"]["7d"]["coverage"] == 0.5
    assert blocked["by_window"]["7d"]["passes"] is False
    assert gated_outcome_observations(
        root=tmp_path,
        now=NOW,
        policy=POLICY,
    )["rows"] == []

    ingest_outcome_observation(observation(2, "7d"), root=tmp_path)
    passed = summarize_outcome_coverage(
        root=tmp_path,
        now=NOW,
        policy=POLICY,
    )
    assert passed["promotion_gate"]["eligible"] is True
    assert passed["promotion_gate"]["necessary_not_sufficient"] is True
    assert passed["promotion_gate"]["causal_quality_proven"] is False

    gated = gated_outcome_observations(
        root=tmp_path,
        now=NOW,
        policy=POLICY,
    )
    assert gated["eligible"] is True
    assert len(gated["rows"]) == 6


def test_partial_rows_count_for_joined_coverage_but_not_complete_coverage(tmp_path: Path):
    for index in (1, 2):
        ingest_outcome_expectation(expectation(index), root=tmp_path)
        for window in ("1h", "24h", "7d"):
            ingest_outcome_observation(
                observation(index, window, state="partial" if index == 2 else "complete"),
                root=tmp_path,
            )

    summary = summarize_outcome_coverage(
        root=tmp_path,
        now=NOW,
        policy={
            "min_eligible_per_window": 2,
            "min_coverage": 1.0,
            "min_complete_coverage": 0.75,
        },
    )
    assert summary["by_window"]["1h"]["coverage"] == 1.0
    assert summary["by_window"]["1h"]["complete_coverage"] == 0.5
    assert summary["promotion_gate"]["eligible"] is False


def test_not_yet_due_windows_do_not_hurt_coverage(tmp_path: Path):
    value = expectation(1)
    value["decision_time"] = "2026-10-01T11:30:00Z"
    value["execution_time"] = "2026-10-01T11:30:05Z"
    ingest_outcome_expectation(value, root=tmp_path)

    summary = summarize_outcome_coverage(
        root=tmp_path,
        now=NOW,
        policy={
            "min_eligible_per_window": 1,
            "min_coverage": 1.0,
            "min_complete_coverage": 1.0,
        },
    )
    assert summary["by_window"]["1h"]["eligible"] == 0
    assert summary["by_window"]["24h"]["eligible"] == 0
    assert summary["by_window"]["7d"]["eligible"] == 0
    assert summary["promotion_gate"]["eligible"] is False
