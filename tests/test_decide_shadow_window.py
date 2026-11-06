import json
import os
import subprocess
import sys

import pytest

from z0int import decide_shadow as S


def row(ts, sid="x"):
    return {"source": "fx", "id": sid, "ts": ts,
            "req": {"privacy": "public"},
            "actual": {"provider": "openai-codex", "model": "example"},
            "decided": {"kind": "ROUTE", "provider": "openai-codex", "model": "example"},
            "tokens": {}, "agree": True}


def test_report_bounds_exclude_future_and_old_events_before_deduplication():
    rows = [row(-1, "old"), row(3600), row(86401), row(7200, "y")]
    rep = S.report(rows, lambda *a: 0.0, start=0, end=86400)
    assert rep["n"] == 2
    assert rep["window"] == {
        "start_ts": 0, "end_ts": 86400, "requested_hours": 24.0,
        "first_event_ts": 3600, "last_event_ts": 7200, "observed_span_hours": 1.0,
        "note": "Event span does not prove continuous collector uptime or source completeness.",
    }
    assert rep["agreement_basis"] == "provider and model only; harness, effort and account are not compared"
    assert "API-equivalent" in rep["cost_basis"]


def test_empty_window_has_no_observed_span():
    rep = S.report([], lambda *a: None, start=0, end=86400)
    assert rep["n"] == 0 and rep["agree_pct"] is None
    assert rep["window"]["first_event_ts"] is None
    assert rep["window"]["observed_span_hours"] == 0.0


def test_coverage_keeps_zero_boundary_and_counts_window_edges():
    rows = [{"source": "vllm-sr", "ts": t} for t in (3600, 4200)]
    c = S.coverage(rows, start=0, end=86400)
    assert c["pct"] == 0.7
    assert [g["minutes"] for g in c["gaps"]] == [60, 1370]
    assert "not collector uptime" in c["note"]


def test_report_cli_uses_frozen_input_and_end_without_live_taps(tmp_path, monkeypatch, capsys):
    path = tmp_path / "rows.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in (row(3600), row(86401, "future"))))
    monkeypatch.setattr(S, "hermes_price", lambda *a: 0.0)
    monkeypatch.setattr(S, "tap", lambda: pytest.fail("report must never tap"))
    S.main(["report", "--rows", str(path), "--end", "86400", "--hours", "24"])
    rep = json.loads(capsys.readouterr().out)
    assert rep["n"] == 1 and rep["window"]["end_ts"] == 86400


def test_report_module_cli_end_to_end_is_read_only(tmp_path):
    path = tmp_path / "rows.jsonl"
    original = json.dumps(row(3600)) + "\n"
    path.write_text(original)
    home = tmp_path / "empty-home"
    home.mkdir()
    proc = subprocess.run(
        [sys.executable, "-m", "z0int.decide_shadow", "report", "--rows", str(path),
         "--end", "86400", "--hours", "24"],
        env={**os.environ, "HOME": str(home)}, capture_output=True, text=True, check=True,
    )
    rep = json.loads(proc.stdout)
    assert rep["n"] == 1 and rep["agree_pct"] == 100.0
    assert rep["window"]["requested_hours"] == 24.0
    assert rep["window"]["observed_span_hours"] == 0.0
    assert path.read_text() == original
    assert list(home.iterdir()) == []


@pytest.mark.parametrize("hours", ["0", "-1", "nan", "inf"])
def test_report_cli_rejects_invalid_hours(hours, capsys):
    with pytest.raises(SystemExit) as exc:
        S.main(["report", "--hours", hours])
    assert exc.value.code == 2
    assert "positive finite" in capsys.readouterr().err
