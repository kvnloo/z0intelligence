"""Unit tests for z0int.kerdoios_export: receipt scan, since filter, dedup, fallbacks, output file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from z0int import kerdoios_export as ke


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    return tmp_path


def _write_jsonl(path: Path, rows: list[dict], *, extra_lines: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r) for r in rows] + list(extra_lines or [])
    path.write_text("\n".join(lines), encoding="utf-8")


def _traces(out: dict) -> list[str]:
    return [o["trace_id"] for o in out["observations"]]


def test_missing_receipt_dir_yields_empty_export(home: Path) -> None:
    assert not (home / "receipts").exists()
    out = ke.export_observations()
    assert out["n"] == 0
    assert out["observations"] == []
    assert out["schema"] == "z0int.kerdoios_export.v1"
    assert out["since"] is None
    assert "path" not in out


def test_receipt_without_trace_id_dropped() -> None:
    assert ke.receipt_to_observation({}) is None
    assert ke.receipt_to_observation({"trace_id": ""}) is None
    assert ke.receipt_to_observation({"trace_id": None, "provider": "P"}) is None


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
    assert obs["trace_id"] == "t"
    assert obs["provider"] == "P"
    assert obs["model"] == "M"
    assert obs["execution_completed"] is True
    assert obs["verified_success"] is True
    assert obs["input_tokens"] == 5
    assert obs["output_tokens"] is None
    assert obs["cached_input_tokens"] == 0
    assert obs["verification_source"] is None

    explicit = ke.receipt_to_observation(
        {"trace_id": "t", "allocation_id": "A1", "kerdoios_allocation_id": "K1", "verifier_id": "v-1"}
    )
    assert explicit is not None
    assert explicit["allocation_id"] == "A1"
    assert explicit["verification_source"] == "v-1"
    assert explicit["execution_completed"] is False

    kerd = ke.receipt_to_observation({"trace_id": "t", "kerdoios_allocation_id": "K1"})
    assert kerd is not None
    assert kerd["allocation_id"] == "K1"

    direct = ke.receipt_to_observation(
        {
            "trace_id": "t",
            "provider": "direct-p",
            "kerdoios_plan": {"provider": "P"},
            "verification_source": "src",
            "verifier_id": "v-1",
            "measured_output_tokens": 7,
        }
    )
    assert direct is not None
    assert direct["provider"] == "direct-p"
    assert direct["verification_source"] == "src"
    assert direct["output_tokens"] == 7


def test_verified_only_literal_bools() -> None:
    for value in (0, "yes"):
        obs = ke.receipt_to_observation({"trace_id": "t", "verified_success": value})
        assert obs is not None
        assert obs["verified_success"] is None, value
    absent = ke.receipt_to_observation({"trace_id": "t"})
    assert absent is not None
    assert absent["verified_success"] is None

    false = ke.receipt_to_observation({"trace_id": "t", "verified_success": False})
    assert false is not None
    assert false["verified_success"] is False

    # Key present as None: the `verified` fallback must not be consulted.
    explicit_none = ke.receipt_to_observation({"trace_id": "t", "verified_success": None, "verified": True})
    assert explicit_none is not None
    assert explicit_none["verified_success"] is None


def test_jsonl_receipts_exported_and_blank_lines_skipped(home: Path) -> None:
    _write_jsonl(
        home / "receipts" / "b.jsonl",
        [{"trace_id": "b1", "provider": "p1"}, {"trace_id": "b2", "provider": "p2"}],
        extra_lines=["", "   "],
    )
    out = ke.export_observations()
    assert out["n"] == 2
    assert _traces(out) == ["b1", "b2"]
    assert [o["provider"] for o in out["observations"]] == ["p1", "p2"]


def test_since_filters_by_ts_or_closed_at(home: Path) -> None:
    _write_jsonl(
        home / "receipts" / "r.jsonl",
        [
            {"trace_id": "ts10", "ts": 10},
            {"trace_id": "ts5", "ts": 5},
            {"trace_id": "closed8", "closed_at": 8},
            {"trace_id": "neither"},
        ],
    )
    out = ke.export_observations(since=7)
    assert out["since"] == 7
    assert _traces(out) == ["ts10", "closed8"]

    # Without `since`, nothing is filtered.
    assert _traces(ke.export_observations()) == ["ts10", "ts5", "closed8", "neither"]


def test_stream_rows_merged_and_deduplicated(home: Path) -> None:
    _write_jsonl(
        home / "receipts" / "r.jsonl",
        [{"trace_id": "shared", "allocation_id": "a1", "provider": "receipt", "ts": 20}],
    )
    _write_jsonl(
        home / "stream" / "bridge_heart.jsonl",
        [
            {"trace_id": "shared", "allocation_id": "a1", "provider": "stream", "ts": 20},
            {"trace_id": "stream-only", "ts": 20},
            {"trace_id": "stream-old", "ts": 1},
        ],
    )
    out = ke.export_observations()
    assert _traces(out) == ["shared", "stream-only", "stream-old"]
    assert out["observations"][0]["provider"] == "receipt"
    assert out["n"] == 3

    filtered = ke.export_observations(since=10)
    assert _traces(filtered) == ["shared", "stream-only"]
    assert filtered["observations"][0]["provider"] == "receipt"


def test_same_trace_different_allocation_not_deduplicated(home: Path) -> None:
    _write_jsonl(
        home / "receipts" / "r.jsonl",
        [{"trace_id": "t", "allocation_id": "a1"}, {"trace_id": "t", "allocation_id": "a2"}],
    )
    out = ke.export_observations()
    assert [o["allocation_id"] for o in out["observations"]] == ["a1", "a2"]


def test_output_file_written(home: Path) -> None:
    _write_jsonl(home / "receipts" / "r.jsonl", [{"trace_id": "o1"}, {"trace_id": "o2"}])
    output = home / "x" / "o.json"
    assert not output.parent.exists()
    out = ke.export_observations(output=output)
    assert out["path"] == str(output)
    assert output.is_file()
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["n"] == out["n"] == 2
    assert written["observations"] == out["observations"]
    assert written["schema"] == "z0int.kerdoios_export.v1"
    assert "path" not in written


def test_malformed_line_aborts_rest_of_file(home: Path) -> None:
    # Current behaviour: the try wraps the whole file, so a bad line drops every later line in that file.
    receipt = home / "receipts" / "c.jsonl"
    receipt.parent.mkdir(parents=True)
    receipt.write_text('{"trace_id":"c1"}\n{bad\n{"trace_id":"c2"}\n', encoding="utf-8")
    # Other files are unaffected.
    _write_jsonl(home / "receipts" / "d.jsonl", [{"trace_id": "d1"}])
    assert _traces(ke.export_observations()) == ["c1", "d1"]


def test_json_receipt_files_are_exported(home: Path) -> None:
    receipts = home / "receipts"
    receipts.mkdir()
    (receipts / "a.json").write_text(json.dumps({"trace_id": "j1"}), encoding="utf-8")
    (receipts / "list.json").write_text(json.dumps([{"trace_id": "not-a-dict"}]), encoding="utf-8")
    (receipts / "broken.json").write_text("{nope", encoding="utf-8")
    (receipts / "notes.txt").write_text(json.dumps({"trace_id": "txt"}), encoding="utf-8")
    _write_jsonl(receipts / "nested" / "b.jsonl", [{"trace_id": "b1"}])
    assert _traces(ke.export_observations()) == ["j1", "b1"]
