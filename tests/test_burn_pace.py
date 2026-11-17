"""burn-pace: recent-rate weighting and the Nous cap, with the original output keys intact."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "burn-pace"
ORIGINAL_LANE_KEYS = {"weekly_used_pct", "hours_to_reset", "sustainable_pct_per_h", "observed_pct_per_h",
                      "pace_ratio", "pct_budget_next_hour", "band", "max_workflow_agents", "effort", "offload"}
NOW = datetime(2026, 10, 20, 11, 0, tzinfo=timezone.utc)


@pytest.fixture()
def bp():
    loader = importlib.machinery.SourceFileLoader("burn_pace", str(SCRIPT))
    spec = importlib.util.spec_from_loader("burn_pace", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def hist(points):
    return [((NOW - timedelta(hours=h)).timestamp(), {"claude": pct}) for h, pct in points]


def test_window_average_alone_keeps_the_old_band(bp):
    # 52% used after 120 h of a 168 h window: 0.43 %/h vs 1.0 %/h sustainable -> under-burn.
    row = bp.lane_row(52.0, 48.0, 120.0, None)
    assert ORIGINAL_LANE_KEYS <= set(row)
    assert (row["band"], row["band_basis"], row["recent_pct_per_h"]) == ("under-burn", "window-average", None)


def test_a_recent_burst_moves_the_band_before_the_window_average_does(bp):
    # The 2026-10-20 case: average ~0.43 %/h, last 3 h at 1.66 %/h.
    recent = bp.recent_rate(hist([(3, 47.0), (2, 48.7), (1, 50.3)]), "claude", NOW, 52.0)
    assert recent == (pytest.approx(5 / 3), pytest.approx(3.0))
    row = bp.lane_row(52.0, 48.0, 120.0, recent)
    assert row["pace_ratio"] == 0.43  # window-average meaning is unchanged
    assert row["weighted_pace_ratio"] == pytest.approx(1.05, abs=0.01)
    assert (row["band"], row["band_basis"]) == ("on-pace", "weighted")
    assert row["max_workflow_agents"] == 6


def test_short_or_reset_history_is_not_trusted(bp):
    assert bp.recent_rate(hist([(0.5, 51.0)]), "claude", NOW, 52.0) is None
    # Used% fell (window reset 1.5 h ago): only samples after the reset count.
    r = bp.recent_rate(hist([(3, 97.0), (1.5, 1.0), (0.2, 2.0)]), "claude", NOW, 2.5)
    assert r == (pytest.approx(1.0), pytest.approx(1.5))


def test_nous_cap_from_kerdoios_state_or_newest_notice(bp, tmp_path):
    state = tmp_path / "capacity_state.json"
    old = (NOW - timedelta(hours=5)).timestamp()
    state.write_text(json.dumps({"nous_cap": {"used_usd": 150.0, "cap_usd": 220.0, "observed_at": old,
                                              "source": "executor notice via luna"}}))
    n = bp.nous_cap(NOW, state_path=state, globs=[])
    assert (n["remaining_usd"], n["used_pct"], n["age_h"]) == (70.0, 68.2, 5.0)
    notice = tmp_path / "run.txt"
    notice.write_text("answer\n  • You've used $161.40 of your $220.00 cap\n")
    os.utime(notice, (NOW.timestamp() - 600, NOW.timestamp() - 600))
    n = bp.nous_cap(NOW, state_path=state, globs=[str(notice)])
    assert (n["used_usd"], n["source"], n["age_h"]) == (161.4, str(notice), 0.2)
    assert bp.nous_cap(NOW, state_path=tmp_path / "missing.json", globs=[]) is None


def test_json_output_keeps_every_original_key(bp, monkeypatch, tmp_path, capsys):
    rows = [{"provider": "claude", "usage": {"secondary": {"usedPercent": 52, "windowMinutes": 10080,
                                                            "resetsAt": (NOW + timedelta(hours=48)).isoformat()}}}]
    monkeypatch.setattr(bp, "fetch", lambda p: rows if p == "claude" else [])
    monkeypatch.setattr(bp, "STATE", tmp_path)
    monkeypatch.setattr(bp, "FRESH", tmp_path / "fresh.json")
    monkeypatch.setattr(bp, "SAMPLES", tmp_path / "burn-pace.jsonl")
    monkeypatch.setattr(bp, "KERDOIOS_STATE", tmp_path / "none.json")
    monkeypatch.setattr(bp, "DEFAULT_NOTICE_GLOBS", [])
    monkeypatch.delenv("BURN_PACE_NOTICE_GLOBS", raising=False)
    monkeypatch.delenv("KERDOIOS_NOTICE_GLOBS", raising=False)
    import types
    import sys
    fake = types.ModuleType("z0int.posture")
    fake.evaluate = lambda pools, now: {"factory": {"posture": "BURN", "prefer": ["claude"]}}
    fake.pools_from_codexbar = lambda rows, now: []
    monkeypatch.setitem(sys.modules, "z0int.posture", fake)
    monkeypatch.setattr(sys, "argv", ["burn-pace", "--json"])
    bp.main()
    out = json.loads(capsys.readouterr().out)
    assert {"at", "z0_factory", "lanes", "free_lane"} <= set(out)
    assert ORIGINAL_LANE_KEYS <= set(out["lanes"]["claude"])
    assert out["nous_cap"] is None
    assert len((tmp_path / "burn-pace.jsonl").read_text().splitlines()) == 1
