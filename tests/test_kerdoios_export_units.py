"""Hermetic unit tests for Kerdoios receipt projection and export."""

import json
from pathlib import Path

import pytest

from z0int import kerdoios_export as ke


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def test_missing_receipt_dir_yields_empty_export(tmp_path: Path) -> None:
    assert not (tmp_path / "receipts").exists()

    out = ke.export_observations()

    assert out["n"] == 0
    assert out["observations"] == []
    assert out["schema"] == "z0int.kerdoios_export.v1"
    assert out["since"] is None
    assert "path" not in out


def test_receipt_without_trace_id_dropped() -> None:
    assert ke.receipt_to_observation({}) is None
    assert ke.receipt_to_observation({"trace_id": ""}) is None


def test_field_fallback_chains() -> None:
    obs = ke.receipt_to_observation(
        {
            "trace_id": "t",
            "kerdoios_plan": {"provider": "P", "model": "M"},
            "completed": 1,
            "verified": True,
            "measured_input_tokens": 5,
            "cached_input_tokens": None,
        }
    )

    assert obs is not None
    assert obs["schema"] == ke.OBS_SCHEMA
    assert obs["allocation_id"] == "alloc:t"
    assert obs["provider"] == "P"
    assert obs["model"] == "M"
    assert obs["execution_completed"] is True
    assert obs["verified_success"] is True
    assert obs["input_tokens"] == 5
    assert obs["output_tokens"] is None
    assert obs["cached_input_tokens"] == 0

    explicit = ke.receipt_to_observation(
        {
            "trace_id": "t2",
            "allocation_id": "explicit",
            "kerdoios_allocation_id": "alias",
            "provider": "direct-provider",
            "model": "direct-model",
            "kerdoios_plan": {"provider": "plan-provider", "model": "plan-model"},
            "verification_source": "direct-verifier",
            "verifier_id": "backup-verifier",
        }
    )
    assert explicit is not None
    assert explicit["allocation_id"] == "explicit"
    assert explicit["provider"] == "direct-provider"
    assert explicit["model"] == "direct-model"
    assert explicit["verification_source"] == "direct-verifier"

    alias = ke.receipt_to_observation(
        {"trace_id": "t3", "kerdoios_allocation_id": "alias", "verifier_id": "backup-verifier"}
    )
    assert alias is not None
    assert alias["allocation_id"] == "alias"
    assert alias["verification_source"] == "backup-verifier"


def test_verified_only_literal_bools() -> None:
    for value in (0, "yes"):
        obs = ke.receipt_to_observation({"trace_id": "t", "verified_success": value})
        assert obs is not None
        assert obs["verified_success"] is None

    absent = ke.receipt_to_observation({"trace_id": "absent"})
    assert absent is not None
    assert absent["verified_success"] is None

    explicit_null = ke.receipt_to_observation(
        {"trace_id": "null", "verified_success": None, "verified": True}
    )
    assert explicit_null is not None
    assert explicit_null["verified_success"] is None

    explicit_false = ke.receipt_to_observation({"trace_id": "false", "verified_success": False})
    assert explicit_false is not None
    assert explicit_false["verified_success"] is False


def test_jsonl_receipts_exported_and_blank_lines_skipped(tmp_path: Path) -> None:
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    first = {"trace_id": "r1"}
    second = {"trace_id": "r2"}
    (receipts / "b.jsonl").write_text(
        "\n".join((json.dumps(first), "", json.dumps(second))) + "\n",
        encoding="utf-8",
    )

    out = ke.export_observations()

    assert out["n"] == 2
    assert [obs["trace_id"] for obs in out["observations"]] == ["r1", "r2"]


def test_since_filters_by_ts_or_closed_at(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "receipts" / "clock.jsonl",
        [
            {"trace_id": "after", "ts": 10},
            {"trace_id": "before", "ts": 5},
            {"trace_id": "closed", "closed_at": 8},
            {"trace_id": "no-timestamp"},
        ],
    )

    out = ke.export_observations(since=7)

    assert out["since"] == 7
    assert out["n"] == 2
    assert [obs["trace_id"] for obs in out["observations"]] == ["after", "closed"]


def test_stream_rows_merged_and_deduplicated(tmp_path: Path) -> None:
    _write_jsonl(
        tmp_path / "receipts" / "r.jsonl",
        [{"trace_id": "same", "allocation_id": "a1", "provider": "receipt", "ts": 11}],
    )
    _write_jsonl(
        tmp_path / "stream" / "bridge_heart.jsonl",
        [
            {"trace_id": "same", "allocation_id": "a1", "provider": "stream-duplicate", "ts": 12},
            {"trace_id": "other", "allocation_id": "a2", "provider": "stream", "ts": 12},
            {"trace_id": "old", "allocation_id": "a3", "provider": "stream", "ts": 2},
        ],
    )

    out = ke.export_observations(since=10)

    assert out["n"] == 2
    assert [obs["trace_id"] for obs in out["observations"]] == ["same", "other"]
    assert [obs["allocation_id"] for obs in out["observations"]] == ["a1", "a2"]
    assert [obs["provider"] for obs in out["observations"]] == ["receipt", "stream"]


def test_output_file_written(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "receipts" / "r.jsonl", [{"trace_id": "saved"}])
    output = tmp_path / "x" / "o.json"

    out = ke.export_observations(output=output)

    assert output.is_file()
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["n"] == out["n"] == 1
    assert written["observations"] == out["observations"]
    assert written["schema"] == out["schema"]
    assert out["path"] == str(output)
    assert "path" not in written


def test_malformed_line_aborts_rest_of_file(tmp_path: Path) -> None:
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "bad.jsonl").write_text(
        "\n".join((json.dumps({"trace_id": "c1"}), "{bad", json.dumps({"trace_id": "c2"}))) + "\n",
        encoding="utf-8",
    )

    out = ke.export_observations()

    # Current behavior: the file-level try/except discards rows after a bad line.
    assert out["n"] == 1
    assert [obs["trace_id"] for obs in out["observations"]] == ["c1"]


def test_json_receipt_files_are_exported(tmp_path: Path) -> None:
    receipts = tmp_path / "receipts"
    receipts.mkdir()
    (receipts / "a.json").write_text(json.dumps({"trace_id": "j1"}), encoding="utf-8")

    out = ke.export_observations()

    assert out["n"] == 1
    assert [obs["trace_id"] for obs in out["observations"]] == ["j1"]
