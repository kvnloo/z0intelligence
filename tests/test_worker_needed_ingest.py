"""Direct, hermetic tests of WorkerNeededReplayV1 Tokenomics ingestion."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from z0int.replay.worker_needed import (
    SCHEMA,
    cmd_ingest,
    ingest_worker_needed_results,
    results_path,
)


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    return tmp_path


def _write_results(*lines: str) -> Path:
    path = results_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _events(home: Path) -> list[dict]:
    path = home / "tokenomics" / "events.jsonl"
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_results_path_under_replay_layout(home: Path) -> None:
    expected = home.resolve() / "replay" / "rlm-worker-needed" / "results.jsonl"
    assert results_path().resolve() == expected.resolve()


def test_missing_file_returns_note_and_emits_nothing(home: Path) -> None:
    report = ingest_worker_needed_results()
    assert report["ok"] is True
    assert report["ingested"] == 0
    assert report["note"] == "no results yet"
    assert not (home / "tokenomics" / "events.jsonl").exists()


def test_row_fields_projected(home: Path) -> None:
    _write_results(
        json.dumps(
            {
                "gold": "A",
                "pair_id": "p1",
                "snapshot_id": "s1",
                "replay_snapshot_hash": "h",
                "tokenomics_joined": 1,
                "native_aggregate": {"pass": True, "wallMs": 5, "tokens": 9},
                "worker_aggregate": {"pass": False},
            }
        )
    )
    report = ingest_worker_needed_results()
    assert report["ingested"] == 1
    event, = _events(home)
    assert event["schema"] == SCHEMA
    assert isinstance(event["ts"], float)
    assert event["capability_id"] == "rlm.worker_needed"
    assert event["pair_id"] == "p1"
    assert event["gold"] == "A"
    assert event["replay_snapshot_id"] == "s1"
    assert event["replay_snapshot_hash"] == "h"
    assert event["tokenomics_joined"] is True
    assert event["native_pass"] is True
    assert event["native_wall_ms"] == 5
    assert event["native_tokens"] == 9
    assert event["worker_pass"] is False
    assert event["worker_wall_ms"] is None
    assert event["worker_tokens"] is None
    assert event["trace_id"] is None


def test_savings_fields_never_invented(home: Path) -> None:
    _write_results(
        json.dumps(
            {
                "pair_id": "p0",
                "cost_usd": 10.5,
                "estimated_tokens_avoided": 100,
                "measured_tokens_avoided": 50,
            }
        ),
        json.dumps(
            {
                "pair_id": "p1",
                "cost_usd": 0,
                "estimated_tokens_avoided": -1,
                "measured_tokens_avoided": 999,
            }
        ),
    )
    assert ingest_worker_needed_results()["ingested"] == 2
    events = _events(home)
    assert len(events) == 2
    for event in events:
        assert event["cost_usd"] is None
        assert event["estimated_tokens_avoided"] is None
        assert event["measured_tokens_avoided"] is None


def test_gold_defaults_and_by_gold_counts(home: Path) -> None:
    _write_results(
        json.dumps({"gold": "A", "pair_id": "p0"}),
        json.dumps({"gold": "A", "experiment_id": "custom-exp"}),
        json.dumps({"pair_id": "p2"}),
        json.dumps({"gold": None, "pair_id": "p3"}),
    )
    report = ingest_worker_needed_results()
    assert report["ingested"] == 4
    assert report["by_gold"] == {"A": 2, "UNKNOWN": 2}
    events = _events(home)
    assert [event["gold"] for event in events] == [
        "A", "A", "UNKNOWN", "UNKNOWN"
    ]
    assert events[0]["experiment_id"] == "rlm-worker-needed-replay-v1"
    assert events[1]["experiment_id"] == "custom-exp"
    assert events[2]["experiment_id"] == "rlm-worker-needed-replay-v1"


def test_blank_and_malformed_lines_skipped(home: Path) -> None:
    _write_results(
        json.dumps({"pair_id": "p0"}), "{bad", "", json.dumps({"pair_id": "p1"})
    )
    report = ingest_worker_needed_results()
    assert report["ingested"] == 2
    assert [event["pair_id"] for event in _events(home)] == ["p0", "p1"]


def test_limit_takes_last_lines(home: Path) -> None:
    _write_results(
        *(json.dumps({"pair_id": f"p{i}"}) for i in range(5))
    )
    report = ingest_worker_needed_results(limit=2)
    assert report["ingested"] == 2
    assert [event["pair_id"] for event in _events(home)] == ["p3", "p4"]


def test_limit_counts_lines_before_parsing(home: Path) -> None:
    _write_results(
        json.dumps({"pair_id": "p0"}), "{bad", json.dumps({"pair_id": "p1"})
    )
    report = ingest_worker_needed_results(limit=2)
    assert report["ingested"] == 1
    assert [event["pair_id"] for event in _events(home)] == ["p1"]


def test_report_paths(home: Path) -> None:
    path = _write_results(json.dumps({"pair_id": "p0"}))
    report = ingest_worker_needed_results()
    assert report["path"] == str(path)
    assert report["events_path"] == str(home / "tokenomics" / "events.jsonl")
    assert report["events_path"].endswith("tokenomics/events.jsonl")


def test_cmd_ingest_prints_json_and_returns_zero(
    home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_results(
        json.dumps({"pair_id": "p0"}),
        json.dumps({"pair_id": "p1"}),
    )
    assert cmd_ingest(limit=1) == 0
    printed = capsys.readouterr().out
    report = json.loads(printed)
    assert report["ok"] is True
    assert report["ingested"] == 1

    assert cmd_ingest(as_json=False, limit=1) == 0
    compact = capsys.readouterr().out
    assert len(compact.splitlines()) == 1
    assert json.loads(compact)["ok"] is True


def test_limit_zero_ingests_nothing(home: Path) -> None:
    _write_results(json.dumps({"pair_id": "p0"}), json.dumps({"pair_id": "p1"}))
    report = ingest_worker_needed_results(limit=0)
    assert report["ok"] is True
    assert report["ingested"] == 0
    assert report["by_gold"] == {}
    assert not (home / "tokenomics" / "events.jsonl").exists()


def test_non_object_json_line_skipped(home: Path) -> None:
    _write_results(
        json.dumps({"pair_id": "p0"}),
        "[1]",
        '"x"',
        "null",
        json.dumps({"pair_id": "p1"}),
    )
    report = ingest_worker_needed_results()
    assert report["ingested"] == 2
    assert [event["pair_id"] for event in _events(home)] == ["p0", "p1"]
