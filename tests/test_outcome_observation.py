from pathlib import Path

import pytest

from z0int.outcome_observation import (
    STORED_SCHEMA_V1,
    find_outcome_observation,
    ingest_outcome_observation,
    outcome_observations_path,
    validate_outcome_observation,
)


def row(window="1h", *, value=0.12, observation_id=None):
    return {
        "schema": "agentweb.outcome_observation.v1",
        "observation_id": observation_id or ("agentweb-outcome-" + "a" * 64),
        "decision_trace_id": "b" * 64,
        "subject_ref": "agentweb-subject:" + "c" * 24,
        "window": window,
        "decision_time": "2026-10-01T12:00:00Z",
        "execution_time": "2026-10-01T12:00:05Z",
        "observation_time": {
            "1h": "2026-10-01T13:00:00Z",
            "24h": "2026-10-02T12:00:00Z",
            "7d": "2026-10-08T12:00:00Z",
        }[window],
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


@pytest.mark.parametrize("window", ["1h", "24h", "7d"])
def test_accepts_all_supported_measurement_windows(window):
    validate_outcome_observation(row(window))


def test_rejects_observation_before_decision_or_execution():
    value = row()
    value["observation_time"] = "2026-10-01T11:59:59Z"
    with pytest.raises(ValueError, match="predate decision_time"):
        validate_outcome_observation(value)

    value = row()
    value["execution_time"] = "2026-10-01T11:59:59Z"
    with pytest.raises(ValueError, match="execution_time cannot predate"):
        validate_outcome_observation(value)


def test_rejects_observation_before_declared_window():
    value = row("24h")
    value["observation_time"] = "2026-10-01T13:00:00Z"
    with pytest.raises(ValueError, match="earlier than the declared measurement window"):
        validate_outcome_observation(value)


def test_rejects_raw_subject_identity_and_quality_authority():
    value = row()
    value["subject_ref"] = "raw-customer-123"
    with pytest.raises(ValueError, match="pseudonymous"):
        validate_outcome_observation(value)

    value = row()
    value["quality_authoritative"] = True
    with pytest.raises(ValueError, match="must not be quality-authoritative"):
        validate_outcome_observation(value)


def test_ingest_is_append_only_and_idempotent(tmp_path: Path):
    first = ingest_outcome_observation(row(), root=tmp_path)
    second = ingest_outcome_observation(row(), root=tmp_path)

    assert first["replayed"] is False
    assert second["replayed"] is True
    assert first["observation_id"] == second["observation_id"]

    stored = find_outcome_observation(first["observation_id"], root=tmp_path)
    assert stored is not None
    assert stored["schema"] == STORED_SCHEMA_V1
    assert stored["source_schema"] == "agentweb.outcome_observation.v1"
    assert stored["observational"] is True
    assert stored["quality_authoritative"] is False
    assert stored["window_seconds"] == 3600
    assert stored["age_seconds"] == 3600

    lines = outcome_observations_path(tmp_path).read_text().splitlines()
    assert len(lines) == 1


def test_changed_payload_under_same_observation_identity_conflicts(tmp_path: Path):
    ingest_outcome_observation(row(value=0.12), root=tmp_path)
    with pytest.raises(ValueError, match="reused for changed payload"):
        ingest_outcome_observation(row(value=0.13), root=tmp_path)


def test_separate_windows_are_separate_immutable_observations(tmp_path: Path):
    observations = [
        row("1h", observation_id="agentweb-outcome-" + "1" * 64),
        row("24h", observation_id="agentweb-outcome-" + "2" * 64),
        row("7d", observation_id="agentweb-outcome-" + "3" * 64),
    ]
    for value in observations:
        assert ingest_outcome_observation(value, root=tmp_path)["replayed"] is False

    assert len(outcome_observations_path(tmp_path).read_text().splitlines()) == 3
