"""Resource posture v1: recent (EWMA) burn rate, band gating, replay simulator, Claude Code hint."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from z0int import claude_code
from z0int import posture as P
from z0int import posture_history as H
from z0int import posture_sim as S

UTC = timezone.utc


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def row(now, pools, groups=None):
    """A v0-schema log row, as the live timer writes it."""
    return {"schema": "z0int.resource_posture.v0.log", "now": iso(now), "factory": "BALANCED", "revision": "x",
            "groups": groups or {p["id"].split(":")[0]: "BALANCED" for p in pools},
            "pools": [{"burn_rate_per_hour": None, "confidence": "ok", "kind": "frontier", "posture": "BALANCED",
                       "projected_surplus_at_reset": None, **p} for p in pools]}


# --- the 2026-09-30 day, as logged (claude + codex windows) -------------------------------------------

CLAUDE_RESET = "2026-10-01T03:00:00-05:00"  # = 08:00Z, weekly
CODEX_RESET = "2026-10-03T21:05:36Z"
DAY_0930 = [  # (time, claude:weekly remaining, codex:weekly remaining)
    ("2026-09-30T18:38:40Z", 81.0, 50.0), ("2026-09-30T18:50:43Z", 81.0, 50.0), ("2026-09-30T19:00:15Z", 80.0, 50.0),
    ("2026-09-30T20:00:25Z", 78.0, 50.0), ("2026-09-30T21:00:16Z", 76.0, 50.0), ("2026-09-30T22:00:02Z", 71.0, 47.0),
    ("2026-09-30T23:00:00Z", 70.0, 47.0)]


def day_0930_rows():
    return [row(P.parse_time(t), [
        {"id": "claude:weekly", "remaining": c, "resets_at": CLAUDE_RESET, "window_hours": 168.0},
        {"id": "codex:weekly", "remaining": x, "resets_at": CODEX_RESET, "window_hours": 168.0}],
        groups={"claude": "BURN", "codex": "BALANCED", "route:groot": "BALANCED", "route:local": "BALANCED"})
        for t, c, x in DAY_0930]


def test_0930_recent_rate_sees_the_burst_the_window_average_misses():
    rows = day_0930_rows()
    now = P.parse_time(DAY_0930[-1][0])
    est = H.recent_rate(H.points_for(rows, "claude:weekly"), now)
    assert est["ok"] and 2.0 <= est["rate"] <= 3.2  # ~11pp in ~4.4h
    window_avg = (100 - 70.0) / (168 - (P.parse_time(CLAUDE_RESET) - now).total_seconds() / 3600)
    assert window_avg < 0.2 and est["rate"] > 10 * window_avg
    assert est["lo"] < est["rate"] < est["hi"]


def test_0930_v1_still_burns_claude_with_the_band_confirming_it():
    rows = day_0930_rows()
    last = S.replay(rows, "v1")[-1]
    cw = last["pools"]["claude:weekly"]
    assert cw["posture"] == "BURN" and cw["rate_source"].startswith("ewma_recent")
    assert cw["projected_surplus_band"][0] >= 20.0  # BURN holds even at the band's high rate
    assert last["factory"] == "BURN" and last["prefer"] == ["claude"]
    # v0 replay agrees on the factory verdict every hour of that day
    assert {d["factory"] for d in S.replay(rows, "v0")} == {"BURN"}


def test_replay_has_no_lookahead():
    rows = day_0930_rows()
    full = S.replay(rows, "v1")
    for k in range(1, len(rows) + 1):
        assert S.replay(rows[:k], "v1") == full[:k]


def test_logged_policy_reads_the_v0_log_verbatim():
    rows = day_0930_rows()
    d = S.replay(rows, "v0-logged")
    assert d[0]["groups"]["claude"] == "BURN" and d[0]["binding"]["claude"] == "claude:weekly"


# --- rate estimation -------------------------------------------------------------------------------------

T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def pts(values, reset, step_h=1.0, start=T0):
    return [(start + timedelta(hours=i * step_h), float(v), reset) for i, v in enumerate(values)]


def test_ewma_weighs_recent_intervals_more():
    reset = T0 + timedelta(hours=40)
    series = pts([90, 89, 88, 87, 86, 80, 74], reset)  # 1/h, then 6/h
    now = series[-1][0]
    fast = H.recent_rate(series, now, H.RateConfig(horizon_hours=1.0))["rate"]
    slow = H.recent_rate(series, now, H.RateConfig(horizon_hours=100.0))["rate"]
    assert fast > slow and fast > 4.5 and 2.5 < slow < 3.0


def test_reset_starts_a_new_window_and_old_usage_does_not_leak():
    r1, r2 = T0 + timedelta(hours=3), T0 + timedelta(hours=8)
    series = pts([40, 30, 20], r1) + [(T0 + timedelta(hours=3 + i), float(v), r2) for i, v in enumerate([100, 99, 98])]
    est = H.recent_rate(series, series[-1][0])
    assert est["ok"] and est["points"] == 3 and abs(est["rate"] - 1.0) < 1e-9


def test_remaining_jump_up_is_a_reset_even_with_the_same_reset_time():
    reset = T0 + timedelta(hours=30)
    series = pts([50, 45, 100, 99, 98], reset)
    assert H.recent_rate(series, series[-1][0])["points"] == 3


def test_short_intervals_are_thinned_so_quantization_does_not_explode_the_band():
    reset = T0 + timedelta(hours=30)
    series = pts([80, 79, 79, 78, 78, 77, 77], reset, step_h=0.25)
    est = H.recent_rate(series, series[-1][0])
    assert est["intervals"] <= 2  # 15-min points thinned to >= 0.75h apart


def test_too_little_history_falls_back_to_the_window_average():
    pool = P.Pool(id="claude:weekly", kind="frontier", group="claude", remaining=60.0, resets_at=iso(T0 + timedelta(hours=10)),
                  window_hours=168.0, burn_rate_per_hour=0.25, rate_observed_hours=158.0,
                  rate_source="window_average_since_window_start", observed_at=iso(T0))
    notes = H.apply_recent_rates([pool], [], T0)
    assert pool.burn_rate_per_hour == 0.25 and "fallback" in pool.rate_source
    assert notes[0]["rate"] == "window_average"
    assert pool.rate_lo == pytest.approx(0.25 - 1 / 158) and pool.rate_hi == pytest.approx(0.25 + 1 / 158)


def test_window_average_method_ignores_history():
    reset = iso(T0 + timedelta(hours=10))
    rows = [row(T0 - timedelta(hours=h), [{"id": "claude:weekly", "remaining": 60.0 + 5 * h, "resets_at": reset,
                                           "window_hours": 168.0}]) for h in (3, 2, 1)]
    pool = P.Pool(id="claude:weekly", kind="frontier", remaining=60.0, resets_at=reset, window_hours=168.0,
                  burn_rate_per_hour=0.25, rate_observed_hours=158.0, observed_at=iso(T0))
    H.apply_recent_rates([pool], rows, T0, H.RateConfig(method="window_average"))
    assert pool.burn_rate_per_hour == 0.25


def test_rate_config_from_dict_validates():
    cfg = H.RateConfig.from_dict({"method": "ewma", "horizon_hours": 3, "min_intervals": 4, "bogus": 1,
                                  "quantum": -1, "z": True})
    assert cfg.horizon_hours == 3.0 and cfg.min_intervals == 4 and cfg.quantum == 1.0 and cfg.z == 1.96
    assert H.RateConfig.from_dict({"method": "magic"}).method == "ewma"
    assert H.RateConfig.from_dict(None) == H.RateConfig()


def test_load_history_tolerates_garbage_and_sorts(tmp_path):
    path = tmp_path / "h.jsonl"
    rows = day_0930_rows()
    path.write_text("\n".join([json.dumps(rows[3]), "not json", json.dumps({"now": "x"}), json.dumps(rows[0])]) + "\n")
    got = H.load_history(path)
    assert [r["now"] for r in got] == [rows[0]["now"], rows[3]["now"]]
    assert H.load_history(tmp_path / "missing.jsonl") == []


# --- band gating in evaluate_pool ---------------------------------------------------------------------


def banded(rate, lo, hi, remaining=60.0, hours=10.0):
    return P.Pool(id="claude:weekly", kind="frontier", group="claude", remaining=remaining,
                  resets_at=iso(T0 + timedelta(hours=hours)), window_hours=168.0, burn_rate_per_hour=rate,
                  rate_lo=lo, rate_hi=hi, rate_observed_hours=10.0, observed_at=iso(T0))


def test_burn_needs_to_hold_at_the_band_high_rate():
    ok = P.evaluate_pool(banded(1.0, 0.5, 2.0), T0)  # surplus 50 .. 40
    assert ok["posture"] == "BURN" and ok["projected_surplus_band"] == [40.0, 55.0]
    shaky = P.evaluate_pool(banded(1.0, 0.5, 5.0), T0)  # at 5/h surplus is 10 < 20
    assert shaky["posture"] == "BALANCED" and shaky["unconfirmed_posture"] == "BURN"
    assert shaky["confidence"] == "band" and shaky["reason"].endswith("_band_unconfirmed")


def test_offload_needs_to_hold_at_the_band_low_rate_else_reserve():
    sure = P.evaluate_pool(banded(8.0, 7.0, 9.0), T0)  # 70..90 used of 60
    assert sure["posture"] == "OFFLOAD"
    maybe = P.evaluate_pool(banded(8.0, 2.0, 12.0), T0)  # at 2/h only 20 used
    assert maybe["posture"] == "RESERVE" and maybe["unconfirmed_posture"] == "OFFLOAD"


def test_burst_turns_a_v0_burn_into_a_v1_offload():
    """40% left, 20h to reset: the window average (0.4/h) says BURN; a steady 3pp/h recent burn empties it."""
    reset = iso(T0 + timedelta(hours=20))
    hist = [row(T0 - timedelta(hours=h), [{"id": "claude:weekly", "remaining": 40.0 + 3 * h, "resets_at": reset,
                                           "window_hours": 168.0}]) for h in (6, 5, 4, 3, 2, 1)]
    cur = hist + [row(T0, [{"id": "claude:weekly", "remaining": 40.0, "resets_at": reset, "window_hours": 168.0}])]
    v0 = S.replay(cur, "v0")[-1]["pools"]["claude:weekly"]
    v1 = S.replay(cur, "v1")[-1]["pools"]["claude:weekly"]
    assert v0["posture"] == "BURN" and v1["posture"] == "OFFLOAD"
    assert v1["burn_rate_per_hour"] == pytest.approx(3.0)


def test_current_posture_uses_history_rows(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    rows = day_0930_rows()
    now = P.parse_time(DAY_0930[-1][0])
    cb = [{"provider": "claude", "usage": {"updatedAt": iso(now), "secondary": {
        "usedPercent": 30, "resetsAt": "2026-10-01T08:00:00Z", "windowMinutes": 10080}}}]
    path = tmp_path / "cb.json"
    path.write_text(json.dumps(cb))
    out = P.current_posture(now, config={}, codexbar=path, kerdoios=False, history=rows[:-1])
    cw = next(r for r in out["pools"] if r["id"] == "claude:weekly")
    assert cw["rate_source"].startswith("ewma_recent") and cw["rate_window_avg"] < 0.2
    assert out["rate"]["method"] == "ewma" and out["schema"] == "z0int.resource_posture.v1"
    off = P.current_posture(now, config={"rate": {"method": "window_average"}}, codexbar=path, kerdoios=False,
                            history=rows[:-1])
    assert next(r for r in off["pools"] if r["id"] == "claude:weekly")["rate_source"] == "window_average_since_window_start"


def test_log_rows_round_trip_into_the_simulator(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    now = P.parse_time(DAY_0930[-1][0])
    p = P.evaluate([banded(1.0, 0.5, 2.0)], now)
    P.log_snapshot(p)
    got = H.load_history()
    assert got[0]["schema"] == "z0int.resource_posture.v1.log" and got[0]["pools"][0]["rate_lo"] == 0.5
    assert S.replay(got, "v1")[0]["pools"]["claude:weekly"]["remaining"] == 60.0


# --- simulator: a synthetic 8-day history with the pre-registered pipeline --------------------------------

START = datetime(2026, 9, 24, 9, 0, tzinfo=UTC)
C_RESET, C_RESET2 = datetime(2026, 10, 1, 8, 0, tzinfo=UTC), datetime(2026, 10, 8, 8, 0, tzinfo=UTC)
X_RESET = datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
K_NONCACHE, K_CACHE = 1e-5, 1e-6


def synthetic_week(claude_rate=0.35, codex_rate=0.7, hours=24 * 8):
    """Hourly rows: claude weekly on pace to leave ~33% at reset (perishes); codex runs dry before its reset.
    Claude usage is generated from a token stream so calibration has a ground truth."""
    rows, turns = [], []
    used_c = claude_rate * (START - (C_RESET - timedelta(hours=168))).total_seconds() / 3600
    for h in range(hours):
        now = START + timedelta(hours=h)
        noncache = claude_rate / K_NONCACHE * (0.5 if h % 3 == 0 else 1.25)
        cache = 100000.0 if h % 2 else 0.0  # +0.05 pp/h on average
        if h:
            turns.append(S.Turn(ts=now - timedelta(minutes=30), group="claude", noncache=noncache, cache_read=cache,
                                output=500, tool_calls=0))
            used_c += K_NONCACHE * noncache + K_CACHE * cache
        c_reset = C_RESET if now < C_RESET else C_RESET2
        if now >= C_RESET and not any(t >= C_RESET for t in [START + timedelta(hours=x) for x in range(h)]):
            used_c = 0.0
        x_reset = X_RESET if now < X_RESET else X_RESET + timedelta(hours=168)
        used_x = codex_rate * (now - (X_RESET - timedelta(hours=168))).total_seconds() / 3600 if now < X_RESET else 0.0
        rows.append(row(now, [
            {"id": "claude:weekly", "remaining": float(max(0, round(100 - used_c))), "resets_at": iso(c_reset),
             "window_hours": 168.0},
            {"id": "codex:weekly", "remaining": float(max(0, round(100 - used_x))), "resets_at": iso(x_reset),
             "window_hours": 168.0}], groups={"claude": "BALANCED", "codex": "BALANCED", "route:groot": "BALANCED"}))
    return rows, turns


def burn_receipts(rows, tokens=60000.0):
    """A route_worker offload from a Claude Code parent every hour of the last day before the claude reset."""
    return [{"ts": C_RESET - timedelta(hours=h, minutes=10), "tokens": tokens, "group": "claude",
             "logged_posture": None} for h in range(1, 23)]


def test_outcomes_read_the_end_of_each_window():
    rows, _ = synthetic_week()
    outs = S.outcomes(rows)
    claude = S._outcome_for(outs, "claude:weekly", iso(C_RESET))
    codex = S._outcome_for(outs, "codex:weekly", iso(X_RESET))
    assert claude["complete"] and 28 <= claude["final_remaining"] <= 38 and not claude["exhausted"]
    assert codex["complete"] and codex["exhausted"] and codex["ran_dry"]
    assert S._outcome_for(outs, "claude:weekly", iso(C_RESET2))["complete"] is False


def test_calibration_recovers_tokens_to_percent():
    rows, turns = synthetic_week()
    cal = S.calibrate(rows, turns, "claude:weekly", until=START + timedelta(days=3))
    assert cal["ok"] and cal["method"] == "two_coefficient"
    assert cal["a_noncache"] == pytest.approx(K_NONCACHE, rel=0.25)


@pytest.mark.parametrize("policy", ["v0", "v1"])
def test_preregistered_evaluation_runs_end_to_end(policy):
    rows, turns = synthetic_week()
    rep = S.evaluate_preregistered(rows, policy, turns=turns, receipts=burn_receipts(rows), limits=[])
    assert rep["status"] == "pass", json.dumps(rep["criteria"], default=str)
    c = rep["criteria"]
    assert c["1_waste"]["value"]["W_blind"] - c["1_waste"]["value"]["W_aware"] >= 10
    assert c["2_limits"]["value"] == {"L_blind": 0.0, "L_aware": 0.0}
    assert c["3_projection"]["value"]["burn_precision"] == 1.0 and c["3_projection"]["value"]["offload_precision"] == 1.0
    assert rep["counterfactual"]["moves"]["burn_absorb"] == 22


def test_without_token_streams_the_evaluation_is_insufficient_not_passed():
    rows, _ = synthetic_week()
    rep = S.evaluate_preregistered(rows, "v1")
    assert rep["status"] == "insufficient_data" and rep["criteria"]["1_waste"]["pass"] is None
    assert rep["criteria"]["3_projection"]["pass"] is True  # history-only criteria still score


def test_short_history_is_insufficient():
    rep = S.evaluate_preregistered(day_0930_rows(), "v1", turns=[], receipts=[], limits=[])
    assert rep["status"] == "insufficient_data" and any("history covers" in m for m in rep["missing"])


def test_nothing_perishes_is_reported_as_falsified():
    rows, turns = synthetic_week(claude_rate=0.55)  # claude ends the week ~0-2% unused
    rep = S.evaluate_preregistered(rows, "v1", turns=turns, receipts=[], limits=[])
    assert any(f.startswith("nothing_perishes") for f in rep["falsified"])


def test_offload_moves_can_avoid_a_limit_and_burn_moves_can_cause_one():
    reset = datetime(2026, 10, 2, 0, 0, tzinfo=UTC)
    base = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    rem = [30, 20, 10, 0, 0]
    rows = [row(base + timedelta(hours=h), [{"id": "claude:weekly", "remaining": float(r), "resets_at": iso(reset),
                                             "window_hours": 168.0}]) for h, r in enumerate(rem)]
    turns = [S.Turn(ts=base + timedelta(hours=h, minutes=30), group="claude", noncache=1e6, cache_read=0, output=100,
                    tool_calls=0) for h in range(4)]
    cal = {"claude:weekly": {"ok": True, "a_noncache": 5e-6, "b_cache_read": 0.0}}
    limit = [{"ts": base + timedelta(hours=3, minutes=5), "reset": reset, "group": "claude"}]
    offload = [{"now": iso(base), "factory": "OFFLOAD", "prefer": [], "groups": {}, "binding": {}, "pools": {}}]
    cf = S.counterfactual(rows, offload, turns=turns, receipts=[], limits=limit, calibration=cal,
                          period=(base, reset))
    assert cf["L_blind"] > 0 and cf["L_aware"] == 0.0 and cf["limits"][0]["avoided"] is True
    burn = [{"now": iso(base), "factory": "BURN", "prefer": ["claude"], "groups": {}, "binding": {}, "pools": {}}]
    receipts = [{"ts": base + timedelta(minutes=20), "tokens": 5e6, "group": "claude"}]  # +25pp
    rows2 = [row(base + timedelta(hours=h), [{"id": "claude:weekly", "remaining": float(r), "resets_at": iso(reset),
                                              "window_hours": 168.0}]) for h, r in enumerate([30, 25, 20, 15])]
    cf2 = S.counterfactual(rows2, burn, turns=[], receipts=receipts, limits=[], calibration=cal, period=(base, reset))
    assert cf2["L_blind"] == 0.0 and cf2["L_aware"] > 0  # BURN would have run the pool dry -> criterion 2 fails


def test_transcript_and_receipt_loaders(tmp_path):
    proj = tmp_path / "projects" / "p"
    proj.mkdir(parents=True)
    rows = [
        {"type": "assistant", "timestamp": "2026-10-01T10:00:00Z", "message": {"id": "m1", "model": "claude-x",
         "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 100}, "content": []}},
        {"type": "assistant", "timestamp": "2026-10-01T10:00:01Z", "message": {"id": "m1", "model": "claude-x",
         "usage": {"input_tokens": 10, "output_tokens": 50, "cache_read_input_tokens": 100},
         "content": [{"type": "tool_use", "name": "Bash"}]}},
        {"type": "assistant", "timestamp": "2026-10-01T11:00:00Z", "isApiErrorMessage": True,
         "message": {"model": "<synthetic>", "content": [{"type": "text", "text": "Claude AI usage limit reached|1790856000"}]}},
        {"type": "assistant", "timestamp": "2026-10-01T11:00:00Z", "isApiErrorMessage": True, "error": "authentication_failed",
         "message": {"model": "<synthetic>", "content": [{"type": "text", "text": "Not logged in"}]}},
    ]
    (proj / "s.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    turns, limits = S.load_transcripts(tmp_path / "projects")
    assert len(turns) == 1 and turns[0].noncache == 60 and turns[0].cache_read == 100 and turns[0].tool_calls == 1
    assert len(limits) == 1 and limits[0]["reset"] == datetime.fromtimestamp(1790856000, tz=UTC)
    rec = tmp_path / "decisions.jsonl"
    rec.write_text("\n".join(json.dumps(r) for r in [
        {"trace_id": "a", "capability_id": "codex.delegated_text", "ts": 1790850000.0, "estimated_frontier_tokens_avoided": 59,
         "extra": {"status": "started", "harness": "claude-code"}},
        {"trace_id": "a", "capability_id": "codex.delegated_text", "ts": 1790850001.0, "estimated_frontier_tokens_avoided": 59,
         "extra": {"status": "completed", "harness": "claude-code", "resource_posture": {"factory_posture": "BURN"}}},
        {"trace_id": "b", "capability_id": "coding.next_action", "ts": 1790850002.0}]) + "\n")
    got = S.load_route_receipts(rec)
    assert got == [{"ts": datetime.fromtimestamp(1790850001.0, tz=UTC), "tokens": 59.0, "group": "claude",
                    "logged_posture": "BURN"}]


def test_replay_cli(tmp_path, capsys):
    path = tmp_path / "h.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in day_0930_rows()) + "\n")
    assert S.main(["--history", str(path)]) == 0
    out = capsys.readouterr().out
    assert "POSTURE REPLAY [v0-logged]: INSUFFICIENT_DATA" in out and "POSTURE REPLAY [v1]" in out
    assert S.main(["--history", str(path), "--policy", "v1", "--decisions"]) == 0
    assert json.loads(capsys.readouterr().out)["v1"][-1]["factory"] == "BURN"


def test_z0int_cli_dispatches_posture_replay(tmp_path, capsys):
    from z0int.cli import main as z0int_main

    path = tmp_path / "h.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in day_0930_rows()) + "\n")
    assert z0int_main(["posture-replay", "--history", str(path), "--policy", "v1", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["v1"]["status"] == "insufficient_data"


# --- Claude Code hint: one line, only on change, shadow by default ------------------------------------------


def fake_posture(factory, groups=None, prefer=None):
    groups = groups or {"claude": factory}
    rows = [{"id": f"{g}:weekly", "posture": p, "remaining": 60.0, "projected_surplus_at_reset": 45.0,
             "hours_until_reset": 9.0} for g, p in groups.items()]
    return {"factory": {"posture": factory, "prefer": prefer if prefer is not None else
                        [g for g, p in groups.items() if p == "BURN"], "avoid": [g for g, p in groups.items() if p == "OFFLOAD"],
                        "offload_targets": ["route:groot", "route:local"], "action": "x"},
            "groups": {g: {"posture": p, "kind": "frontier", "binding_pool": f"{g}:weekly"} for g, p in groups.items()},
            "pools": rows, "revision": factory}


@pytest.fixture
def hint_env(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.delenv("Z0INT_CLAUDE_CODE_POSTURE_HINT", raising=False)
    return tmp_path


def hints_log(root):
    path = root / "state" / "claude-code" / "posture-hints.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines()] if path.exists() else []


def test_hint_defaults_to_shadow_records_but_injects_nothing(hint_env):
    out = claude_code.posture_hint({"session_id": "s1"}, "SessionStart", posture_fn=lambda: fake_posture("BURN"))
    assert out is None
    log = hints_log(hint_env)
    assert len(log) == 1 and log[0]["mode"] == "shadow" and log[0]["delivered"] is False
    assert log[0]["line"].startswith("[z0 resource posture] BURN: claude:weekly ~45% projected unused")


def test_hint_only_on_change_not_every_prompt(hint_env, monkeypatch):
    monkeypatch.setenv("Z0INT_CLAUDE_CODE_POSTURE_HINT", "on")
    burn, balanced = (lambda: fake_posture("BURN")), (lambda: fake_posture("BALANCED"))
    hook = {"session_id": "s1"}
    assert claude_code.posture_hint(hook, "SessionStart", posture_fn=burn).startswith("[z0 resource posture] BURN")
    assert claude_code.posture_hint(hook, "UserPromptSubmit", posture_fn=burn) is None
    assert claude_code.posture_hint(hook, "UserPromptSubmit", posture_fn=burn) is None
    back = claude_code.posture_hint(hook, "UserPromptSubmit", posture_fn=balanced)
    assert back == "[z0 resource posture] BALANCED: frontier on pace (was BURN)."
    assert claude_code.posture_hint(hook, "UserPromptSubmit", posture_fn=balanced) is None
    # another session gets its own first hint
    assert claude_code.posture_hint({"session_id": "s2"}, "UserPromptSubmit", posture_fn=burn) is not None
    assert [r["delivered"] for r in hints_log(hint_env)] == [True, True, True]


def test_balanced_session_start_is_silent(hint_env, monkeypatch):
    monkeypatch.setenv("Z0INT_CLAUDE_CODE_POSTURE_HINT", "on")
    assert claude_code.posture_hint({"session_id": "s"}, "SessionStart", posture_fn=lambda: fake_posture("BALANCED")) is None
    assert hints_log(hint_env) == []


def test_hint_off_does_nothing(hint_env, monkeypatch):
    monkeypatch.setenv("Z0INT_CLAUDE_CODE_POSTURE_HINT", "off")
    assert claude_code.posture_hint({"session_id": "s"}, "SessionStart", posture_fn=lambda: 1 / 0) is None
    assert not (hint_env / "state" / "claude-code" / "posture-hint.json").exists()


def test_hint_mode_from_host_config(hint_env):
    (hint_env / "config").mkdir(parents=True, exist_ok=True)
    (hint_env / "config" / "claude-code.json").write_text(json.dumps({"posture_hint": "on"}))
    assert claude_code.posture_hint_mode() == "on"
    (hint_env / "config" / "claude-code.json").write_text(json.dumps({"posture_hint": "loud"}))
    assert claude_code.posture_hint_mode() == "shadow"


def test_offload_hint_names_zero_cost_routes():
    line = P.hint_line(fake_posture("OFFLOAD", {"claude": "OFFLOAD"}))
    assert "OFFLOAD" in line and "route_worker" in line and "groot, local" in line and len(line) <= 320


def test_hooks_merge_the_hint_and_fail_open(hint_env, monkeypatch):
    monkeypatch.setenv("Z0INT_CLAUDE_CODE_POSTURE_HINT", "on")
    monkeypatch.setenv("Z0INT_CLAUDE_CODE_PACKET", "0")
    monkeypatch.setattr(P, "current_posture", lambda *a, **k: fake_posture("BURN"))
    out = claude_code.on_session_start(json.dumps({"session_id": "h1"}))
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert out["hookSpecificOutput"]["additionalContext"].startswith("[z0 resource posture] BURN")
    # prompt path: shadow automatic routing returns None, hint alone is delivered once, then silent
    monkeypatch.setattr(claude_code, "_on_prompt", lambda hook, text: None)
    assert claude_code.on_prompt({"session_id": "h1", "prompt": "hi"}) is None
    monkeypatch.setattr(claude_code, "_on_prompt", lambda hook, text: {"hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit", "additionalContext": "ctx"}})
    monkeypatch.setattr(P, "current_posture", lambda *a, **k: fake_posture("OFFLOAD", {"claude": "OFFLOAD"}))
    merged = claude_code.on_prompt({"session_id": "h1", "prompt": "hi"})
    assert merged["hookSpecificOutput"]["additionalContext"].startswith("ctx\n[z0 resource posture] OFFLOAD")
    # harness hand-backs never trigger a hint; a posture failure never breaks the turn
    assert claude_code.on_prompt({"session_id": "h1", "prompt": "<task-notification>x"})["hookSpecificOutput"][
        "additionalContext"] == "ctx"

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(P, "current_posture", boom)
    assert claude_code.on_prompt({"session_id": "h2", "prompt": "hi"})["hookSpecificOutput"]["additionalContext"] == "ctx"
    assert claude_code.on_session_start("not json") is None


def test_v1_preregistration_comparison_runs_mechanically():
    rows, turns = synthetic_week()
    kw = dict(turns=turns, receipts=burn_receipts(rows), limits=[])
    rep = S.v1_preregistered(S.evaluate_preregistered(rows, "v0", **kw), S.evaluate_preregistered(rows, "v1", **kw))
    c = rep["criteria"]
    assert c["A_v1_passes_v0_criteria"]["pass"] is True and c["B4_withheld_rate"]["value"] is not None
    assert rep["status"] == "v1_adopted"  # steady synthetic: v1 within the 1pp non-inferiority margin
    short = S.v1_preregistered(S.evaluate_preregistered(day_0930_rows(), "v0"),
                               S.evaluate_preregistered(day_0930_rows(), "v1"))
    assert short["status"] == "insufficient_data"
