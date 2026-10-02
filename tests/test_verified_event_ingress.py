from pathlib import Path

import pytest

from z0int.outcome_observation import outcome_observations_path
from z0int.verified_event_ingress import (
    ingest_verified_event_batch,
    validate_verified_event_batch,
    verified_events_path,
)


def outcome(index: int, window: str):
    observed = {
        "1h": "2026-09-01T13:00:00Z",
        "24h": "2026-09-02T12:00:00Z",
    }[window]
    return {
        "schema": "agentweb.outcome_observation.v1",
        "observation_id": "agentweb-outcome-" + f"{index:064x}",
        "decision_trace_id": "a" * 64,
        "subject_ref": "agentweb-subject:" + "b" * 24,
        "window": window,
        "decision_time": "2026-09-01T12:00:00Z",
        "execution_time": "2026-09-01T12:00:05Z",
        "observation_time": observed,
        "measurement_state": "complete",
        "metric": {
            "source": "agentweb.analytics",
            "name": "conversion_rate",
            "value": 0.12 if window == "1h" else 0.16,
            "unit": "ratio",
            "direction": "higher_better",
        },
        "quality_authoritative": False,
    }


def batch(*, payload_sha256="c" * 64):
    return {
        "schema": "agentweb.verified_event_batch.v1",
        "event_id": "agentweb-event-" + "d" * 64,
        "provider": "example-provider",
        "event_type": "analytics.updated",
        "event_time": "2026-09-02T12:00:00Z",
        "received_at": "2026-09-02T12:00:01Z",
        "subject_ref": "agentweb-subject:" + "b" * 24,
        "verification": {
            "status": "verified",
            "scheme": "hmac-sha256",
            "verified_at": "2026-09-02T12:00:01Z",
        },
        "payload_sha256": payload_sha256,
        "projections": [
            {"kind": "outcome", "value": outcome(1, "1h")},
            {"kind": "outcome", "value": outcome(2, "24h")},
        ],
        "quality_authoritative": False,
    }


def test_batch_requires_prior_verification():
    value = batch()
    value["verification"]["status"] = "unverified"
    with pytest.raises(ValueError, match="already be signature-verified"):
        validate_verified_event_batch(value)


def test_exact_retry_deduplicates_event_and_child_outcomes(tmp_path: Path):
    first = ingest_verified_event_batch(batch(), root=tmp_path)
    second = ingest_verified_event_batch(batch(), root=tmp_path)

    assert first["replayed"] is False
    assert second["replayed"] is True
    assert first["fanout_count"] == 2
    assert second["fanout_count"] == 2

    event_lines = verified_events_path(tmp_path).read_text().splitlines()
    outcome_lines = outcome_observations_path(tmp_path).read_text().splitlines()
    assert len(event_lines) == 1
    assert len(outcome_lines) == 2


def test_changed_batch_under_same_event_identity_conflicts(tmp_path: Path):
    ingest_verified_event_batch(batch(), root=tmp_path)
    with pytest.raises(ValueError, match="reused for changed batch"):
        ingest_verified_event_batch(
            batch(payload_sha256="e" * 64),
            root=tmp_path,
        )

    assert len(verified_events_path(tmp_path).read_text().splitlines()) == 1
    assert len(outcome_observations_path(tmp_path).read_text().splitlines()) == 2


def test_invalid_child_is_rejected_before_any_fanout(tmp_path: Path):
    value = batch()
    value["projections"][1]["value"]["observation_time"] = "2026-09-01T13:00:00Z"

    with pytest.raises(ValueError, match="earlier than the declared measurement window"):
        ingest_verified_event_batch(value, root=tmp_path)

    assert not verified_events_path(tmp_path).exists()
    assert not outcome_observations_path(tmp_path).exists()


def test_stored_event_does_not_duplicate_normalized_child_payloads(tmp_path: Path):
    ingest_verified_event_batch(batch(), root=tmp_path)
    stored = verified_events_path(tmp_path).read_text()
    assert '"projections"' not in stored
    assert '"projection_results"' in stored
    assert '"fanout_count": 2' in stored
