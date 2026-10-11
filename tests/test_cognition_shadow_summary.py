"""The shadow scorecard, on synthetic rows only."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from z0int.cognition.shadow import summarize_shadow_receipts


def _model(label: str, **over: object) -> dict:
    row = {"label": label, "backend": "llama_http", "model": label, "selected_action": None,
           "abstained": True, "invalid_call": False, "latency_ms": 5.0}
    row.update(over)
    return row


def _down(label: str, error: str = "URLError: refused", **over: object) -> dict:
    return _model(label, backend=None, error=error, parse_error=error, **over)


ROWS = [
    # Current format: tool_name is on the receipt.
    {"ts": 100.0, "tool_name": "read", "legal_ids": ["read", "grep"],
     "shadow": [_model("a", selected_action="read", abstained=False), _down("b")]},
    {"ts": 200.0, "tool_name": "bash", "legal_ids": ["read", "grep"],
     "shadow": [_model("a", selected_action="grep", abstained=False),
                _down("b", "shadow_timeout", timed_out=True)]},
    # Older format: no tool_name, the observed tool is the first state line.
    {"ts": 300.0, "state": "OMP tool_call: grep\ninput: {}", "legal_ids": ["read", "grep"],
     "shadow": [_model("a", selected_action="grep", abstained=False), _model("b")]},
    # An illegal answer is invalid, not a selection; a stored verdict wins over recomputing.
    {"ts": 400.0, "tool_name": "read", "legal_ids": ["read"],
     "shadow": [_model("a", invalid_call=True, attempted_action="rm"),
                _model("b", selected_action="read", abstained=False, agrees_with_observed=False)]},
    # A backend whose server is down or slow answers with a transport error of its own.
    {"ts": 450.0, "tool_name": "read", "legal_ids": ["read"],
     "shadow": [_model("a", parse_error="transport_error: <urlopen error [Errno 111] Connection refused>"),
                _model("b", parse_error="transport_error: timed out")]},
    # Not an OMP call: no observed tool, and no model rows.
    {"ts": 500.0, "state": "eval case", "legal_ids": ["x"], "shadow": []},
]


def _write(tmp_path: Path, rows: list, extra: str = "") -> Path:
    path = tmp_path / "shadow.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows) + extra, encoding="utf-8")
    return path


def test_counts(tmp_path: Path) -> None:
    out = summarize_shadow_receipts(_write(tmp_path, ROWS, extra="not json\n\n[1]\n"))
    assert out["receipts"] == 6
    assert out["unreadable_lines"] == 2
    assert out["observed_tool_known"] == 5
    assert out["observed_in_legal"] == 4
    assert out["observed_in_legal_share"] == 0.8
    assert out["selections"] == 4
    assert out["agreements"] == 2
    assert out["models"]["a"] == {
        "rows": 5, "answered": 4, "unreachable": 1, "timed_out": 0,
        "abstained": 0, "invalid": 1, "selections": 3, "agreements": 2,
    }
    assert out["models"]["b"] == {
        "rows": 5, "answered": 2, "unreachable": 1, "timed_out": 2,
        "abstained": 1, "invalid": 0, "selections": 1, "agreements": 0,
    }


def test_a_row_without_a_backend_or_with_an_error_did_not_answer(tmp_path: Path) -> None:
    shadow = [
        # No backend and no failure text: nothing ran.
        {"label": "a", "backend": None, "selected_action": "read"},
        # A backend is named, but the row carries an error.
        _model("b", selected_action="read", abstained=False, error="shadow_timeout"),
        _model("c", selected_action="read", abstained=False, error="URLError: refused"),
    ]
    out = summarize_shadow_receipts(
        _write(tmp_path, [{"ts": 1.0, "tool_name": "read", "legal_ids": ["read"], "shadow": shadow}])
    )
    assert out["selections"] == 0
    assert out["agreements"] == 0
    assert [out["models"][m]["answered"] for m in "abc"] == [0, 0, 0]
    assert [out["models"][m]["unreachable"] for m in "abc"] == [1, 0, 1]
    assert [out["models"][m]["timed_out"] for m in "abc"] == [0, 1, 0]


def test_since_ts_and_empty(tmp_path: Path) -> None:
    out = summarize_shadow_receipts(_write(tmp_path, ROWS), since_ts=400.0)
    assert out["receipts"] == 3
    assert out["selections"] == 1
    empty = summarize_shadow_receipts(_write(tmp_path, []))
    assert empty["receipts"] == 0
    assert empty["observed_in_legal_share"] is None
    assert empty["models"] == {}


def test_output_is_counts_only_and_file_is_untouched(tmp_path: Path) -> None:
    rows = [dict(ROWS[0], state="PRIVATE-TEXT", session_id="PRIVATE-SESSION")]
    path = _write(tmp_path, rows)
    before = path.read_bytes()
    done = subprocess.run(
        [sys.executable, "-m", "z0int.cognition.shadow", str(path)],
        capture_output=True, text=True, check=True,
    )
    assert json.loads(done.stdout)["receipts"] == 1
    assert "PRIVATE" not in done.stdout
    assert path.read_bytes() == before
