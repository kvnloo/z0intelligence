import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from z0int import posture as P

NOW = datetime(2026, 9, 30, 18, 0, tzinfo=timezone.utc)


def iso(hours):
    return (NOW + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def pool(**kw):
    base = dict(id="claude:weekly", kind="frontier", group="claude", unit="percent", capacity=100.0,
                remaining=80.0, resets_at=iso(9), window_hours=168.0, burn_rate_per_hour=0.5,
                rate_observed_hours=159.0, observed_at=iso(0), source="test")
    base.update(kw)
    return P.Pool(**base)


def ev(p, **th):
    return P.evaluate_pool(p, NOW, P.Thresholds(**th))


# --- per-pool arithmetic ---------------------------------------------------------------


def test_expiring_tonight_with_surplus_is_burn():
    r = ev(pool(remaining=80.0, resets_at=iso(9), burn_rate_per_hour=0.5))
    assert r["posture"] == "BURN" and r["reason"] == "surplus_perishes_at_reset"
    assert r["projected_use_until_reset"] == 4.5 and r["projected_surplus_at_reset"] == 75.5
    assert "perishes at reset" in r["arithmetic"] and "4.5%" in r["arithmetic"]


def test_week_left_but_runs_out_in_two_days_is_offload():
    # 7 days to reset, 60% left, burning 1.25%/h -> 48h of runway
    r = ev(pool(remaining=60.0, resets_at=iso(168), burn_rate_per_hour=1.25))
    assert r["posture"] == "OFFLOAD" and r["reason"] == "runs_out_before_reset"
    assert r["runway_hours"] == 48.0 and r["projected_use_until_reset"] == 210.0 and r["ratio"] == 3.5
    assert "runs out in 2.0d" in r["arithmetic"] and "5.0d before reset" in r["arithmetic"]


def test_tight_is_reserve_and_on_pace_is_balanced():
    assert ev(pool(remaining=50.0, resets_at=iso(100), burn_rate_per_hour=0.45))["posture"] == "RESERVE"  # ratio .9
    assert ev(pool(remaining=50.0, resets_at=iso(100), burn_rate_per_hour=0.30))["posture"] == "BALANCED"  # ratio .6


def test_surplus_far_from_reset_is_not_burn():
    r = ev(pool(remaining=90.0, resets_at=iso(100), burn_rate_per_hour=0.1))
    assert r["posture"] == "BALANCED" and r["reason"] == "on_pace"


def test_small_surplus_near_reset_is_not_burn():
    r = ev(pool(remaining=10.0, resets_at=iso(5), burn_rate_per_hour=0.5))  # 7.5% surplus < 20%
    assert r["posture"] == "BALANCED"


def test_exhausted_is_offload():
    r = ev(pool(remaining=0.0, resets_at=iso(200)))
    assert r["posture"] == "OFFLOAD" and r["reason"] == "exhausted"


def test_low_confidence_and_stale_rates_do_not_assert_burn_or_offload():
    low = ev(pool(remaining=90.0, resets_at=iso(4), burn_rate_per_hour=30.0, rate_observed_hours=0.1))
    assert low["posture"] == "BALANCED" and low["unconfirmed_posture"] == "OFFLOAD" and low["confidence"] == "low"
    stale = ev(pool(observed_at=iso(-10)))
    assert stale["posture"] == "BALANCED" and stale["unconfirmed_posture"] == "BURN" and stale["confidence"] == "stale"


def test_observation_predating_reset_is_unknown_not_verdict():
    r = ev(pool(remaining=0.0, resets_at=iso(-1)))
    assert r["posture"] == "BALANCED" and r["reason"] == "observation_predates_reset"


def test_nonperishable_short_runway_is_reserve():
    r = ev(pool(resets_at=None, remaining=20.0, burn_rate_per_hour=1.0))
    assert r["posture"] == "RESERVE" and r["runway_hours"] == 20.0
    assert ev(pool(resets_at=None, remaining=20.0, burn_rate_per_hour=0.1))["posture"] == "BALANCED"


def test_unmetered_and_unrated():
    assert ev(pool(remaining=None))["reason"] == "unmetered_capacity"
    assert ev(pool(burn_rate_per_hour=None))["reason"] == "no_rate_observation"


def test_thresholds_are_configurable():
    p = pool(remaining=80.0, resets_at=iso(30), burn_rate_per_hour=0.5)
    assert ev(p)["posture"] == "BALANCED"
    assert ev(p, burn_horizon_hours=36)["posture"] == "BURN"
    assert P.Thresholds.from_dict({"burn_horizon_hours": 36, "bogus": 1, "reserve_ratio": "x"}).burn_horizon_hours == 36


# --- groups + factory ------------------------------------------------------------------


def test_group_binding_window_precedence():
    five = pool(id="claude:5h", window_hours=5.0, remaining=5.0, resets_at=iso(3), burn_rate_per_hour=4.0,
                rate_observed_hours=2.0)
    week = pool()
    out = P.evaluate([five, week], NOW)
    assert out["groups"]["claude"]["posture"] == "OFFLOAD" and out["groups"]["claude"]["binding_pool"] == "claude:5h"
    # both BURN -> the longer (plan) window binds
    five_ok = pool(id="claude:5h", window_hours=5.0, remaining=95.0, resets_at=iso(3), burn_rate_per_hour=1.0,
                   rate_observed_hours=2.0)
    out = P.evaluate([five_ok, week], NOW)
    assert out["groups"]["claude"]["binding_pool"] == "claude:weekly"


def target(pid, kind):
    return P.Pool(id=pid, kind=kind, group=pid, unit="tokens", remaining=None)


def test_factory_burn_prefers_burning_and_avoids_offload():
    cursor = pool(id="cursor:30d", group="cursor", remaining=0.0, resets_at=iso(200))
    out = P.evaluate([pool(), cursor, target("route:local", "local")], NOW)
    f = out["factory"]
    assert f["posture"] == "BURN" and f["prefer"] == ["claude"] and f["avoid"] == ["cursor"]
    assert f["earliest_reset"] == iso(9) and "perishes" in f["action"]


def test_factory_offload_when_no_frontier_on_pace():
    hot = pool(remaining=60.0, resets_at=iso(168), burn_rate_per_hour=1.25)
    out = P.evaluate([hot, target("kerdoios:google", "free"), target("route:groq", "fast"),
                      target("route:local", "local")], NOW)
    f = out["factory"]
    assert f["posture"] == "OFFLOAD" and f["avoid"] == ["claude"]
    assert f["offload_targets"] == ["route:groq", "route:local", "kerdoios:google"]
    out = P.evaluate([hot], NOW)
    assert out["factory"]["posture"] == "RESERVE" and out["factory"]["offload_targets"] == []


def test_factory_balanced_prefers_on_pace_group():
    hot = pool(remaining=60.0, resets_at=iso(168), burn_rate_per_hour=1.25)
    ok = pool(id="codex:weekly", group="codex", remaining=50.0, resets_at=iso(100), burn_rate_per_hour=0.3)
    f = P.evaluate([hot, ok], NOW)["factory"]
    assert f["posture"] == "BALANCED" and f["prefer"] == ["codex"] and f["avoid"] == ["claude"]


def test_no_frontier_pools():
    f = P.evaluate([target("route:local", "local")], NOW)["factory"]
    assert f["posture"] == "BALANCED" and f["rule"] == "no_frontier_pools"


def test_evaluate_is_deterministic_and_revision_tracks_verdicts_only():
    pools = [pool(), pool(id="codex:weekly", group="codex", remaining=50.0, resets_at=iso(100), burn_rate_per_hour=0.3)]
    a, b = P.evaluate(pools, NOW), P.evaluate(pools, NOW)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    later = P.evaluate(pools, NOW + timedelta(minutes=20))
    assert later["pools"] != a["pools"] and later["revision"] == a["revision"]
    burnt = P.evaluate([pool(remaining=0.0)] + pools[1:], NOW)
    assert burnt["revision"] != a["revision"]


def test_rejects_unknown_kind():
    with pytest.raises(ValueError):
        P.Pool.from_dict({"id": "x", "kind": "gold"})


# --- sources ----------------------------------------------------------------------------

CODEXBAR = [
    {"provider": "claude", "usage": {
        "identity": {"loginMethod": "Claude Max 20x", "accountEmail": "someone@example.com"},
        "accountEmail": "someone@example.com", "updatedAt": iso(0),
        "primary": {"resetsAt": iso(4), "windowMinutes": 300, "usedPercent": 6},
        "secondary": {"resetsAt": iso(9), "windowMinutes": 10080, "usedPercent": 18},
        "extraRateWindows": [
            {"id": "claude-routines", "window": {"windowMinutes": 10080, "usedPercent": 0}},
            {"id": "claude-weekly-scoped-fable", "window": {"resetsAt": iso(9), "windowMinutes": 10080, "usedPercent": 0}}]}},
    {"provider": "cursor", "usage": {"updatedAt": iso(0),
        "primary": {"usedPercent": 100, "resetsAt": iso(200), "windowMinutes": 43200},
        "secondary": {"usedPercent": 100, "resetsAt": iso(200), "windowMinutes": 43200},
        "tertiary": {"usedPercent": 100, "resetsAt": iso(200), "windowMinutes": 43200}}},
    {"provider": "openrouter", "usage": {"updatedAt": iso(0), "primary": {"windowMinutes": 10080, "usedPercent": 44.2,
                                                                          "resetDescription": "$27.92 credits left"}}},
    {"provider": "vercel", "usage": {"primary": {"quota": False}}},
    "garbage",
]


def test_codexbar_mapping():
    pools = {p.id: p for p in P.pools_from_codexbar(CODEXBAR, NOW)}
    assert set(pools) == {"claude:5h", "claude:weekly", "claude:weekly-scoped-fable", "cursor:30d", "openrouter:credits"}
    w = pools["claude:weekly"]
    assert w.remaining == 82.0 and w.window_hours == 168.0 and w.rate_observed_hours == 159.0
    assert w.burn_rate_per_hour == pytest.approx(18 / 159)
    assert pools["openrouter:credits"].kind == "paid" and pools["openrouter:credits"].resets_at is None
    blob = json.dumps([vars(p) for p in pools.values()])
    assert "example.com" not in blob and "Max 20x" not in blob
    out = P.evaluate(list(pools.values()), NOW)
    assert out["factory"]["posture"] == "BURN" and out["groups"]["cursor"]["posture"] == "OFFLOAD"


def test_overrides_alias_group_and_manual_pool():
    pools = P.pools_from_codexbar(CODEXBAR, NOW)
    pools, applied = P.apply_overrides(pools, {
        "claude-max": {"resets_at": "2026-10-01T03:00:00-05:00"},
        "pools": {"cerebras-trial": {"kind": "fast", "unit": "usd", "remaining": 3.0, "capacity": 10.0}},
        "bad": {"kind": "gold"},
        "posture_enforce": False,
    })
    weekly = next(p for p in pools if p.id == "claude:weekly")
    assert weekly.resets_at == "2026-10-01T03:00:00-05:00" and weekly.source == "codexbar+manual"
    assert any(p.id == "cerebras-trial" and p.source == "manual" for p in pools)
    assert any(a.startswith("bad: rejected") for a in applied)
    assert not any(p.id == "posture_enforce" for p in pools)


def test_kerdoios_offers_become_unmetered_targets():
    econ = SimpleNamespace(remaining_free_quota=50_000.0, seconds_until_quota_reset=3600)
    offers = [SimpleNamespace(provider="cerebras", local=False, economics=econ),
              SimpleNamespace(provider="local", local=True, economics=None),
              SimpleNamespace(provider="google", local=False, economics=None),
              SimpleNamespace(provider="google", local=False, economics=None)]
    pools = {p.id: p for p in P.pools_from_kerdoios(offers)}
    assert pools["kerdoios:cerebras"].kind == "fast" and pools["kerdoios:local"].kind == "local"
    assert pools["kerdoios:google"].kind == "free" and "2 free model" in pools["kerdoios:google"].note
    assert all(p.remaining is None for p in pools.values())


def test_current_posture_fail_open_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    out = P.current_posture(NOW, config={}, codexbar=tmp_path / "missing.json", kerdoios=False)
    assert out["sources"][0]["status"].startswith("unavailable")
    assert out["factory"]["posture"] in P.POSTURES and out["enforce"] is False


def test_shadow_annotation(monkeypatch):
    monkeypatch.setattr(P, "current_posture", lambda *a, **k: P.evaluate([pool()], NOW, enforce=False))
    ann = P.shadow_annotation("offload")
    assert ann["factory_posture"] == "BURN" and ann["agrees"] is False and ann["enforce"] is False
    assert P.shadow_annotation("frontier")["agrees"] is True

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(P, "current_posture", boom)
    ann = P.shadow_annotation()
    assert ann["available"] is False and ann["enforce"] is False


def test_cli_json_and_human(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    cb = tmp_path / "last.json"
    cb.write_text(json.dumps(CODEXBAR))
    cfg = tmp_path / "posture.json"
    cfg.write_text(json.dumps({"claude-max": {"resets_at": iso(9)}}))
    assert P.main(["--json", "--now", iso(0), "--codexbar", str(cb), "--config", str(cfg), "--no-kerdoios"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == P.SCHEMA and out["factory"]["posture"] == "BURN"
    assert P.main(["--now", iso(0), "--codexbar", str(cb), "--config", str(cfg), "--no-kerdoios"]) == 0
    text = capsys.readouterr().out
    assert "FACTORY: BURN" in text and "claude:weekly" in text and "override claude-max -> claude:weekly" in text


def test_z0int_cli_dispatch(tmp_path, monkeypatch, capsys):
    from z0int.cli import main as z0int_main

    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    cb = tmp_path / "last.json"
    cb.write_text(json.dumps(CODEXBAR))
    assert z0int_main(["posture", "--json", "--now", iso(0), "--codexbar", str(cb), "--no-kerdoios"]) == 0
    assert json.loads(capsys.readouterr().out)["factory"]["posture"] == "BURN"


def test_log_snapshot_is_compact_and_identity_free(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    cb = tmp_path / "last.json"
    cb.write_text(json.dumps(CODEXBAR))
    assert P.main(["--json", "--log", "--now", iso(0), "--codexbar", str(cb), "--no-kerdoios", "--config",
                   str(tmp_path / "none.json")]) == 0
    capsys.readouterr()
    rows = [json.loads(x) for x in P.history_path().read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["factory"] == "BURN"
    assert {r["id"] for r in rows[0]["pools"]} >= {"claude:weekly", "cursor:30d"}
    assert "example.com" not in P.history_path().read_text()


def test_group_tie_prefers_more_constrained_pool():
    idle = pool(id="codex:a-reserve", group="codex", remaining=100.0, resets_at=iso(160), burn_rate_per_hour=0.0)
    busy = pool(id="codex:weekly", group="codex", remaining=50.0, resets_at=iso(74), burn_rate_per_hour=0.5)
    out = P.evaluate([idle, busy], NOW)
    assert out["groups"]["codex"]["binding_pool"] == "codex:weekly"


_FREE_ROUTE = {'provider': 'groq', 'model': 'm-probe', 'validated': True, 'price_usd': 0, 'evidence_sha256': 'a' * 64}
_FREE_ROUTE_CASES = [({}, True), ({'price_usd': 0.0}, True)] + [(change, False) for change in (
    {'price_usd': False}, {'price_usd': True}, {'price_usd': -1}, {'price_usd': None}, {'price_usd': ''},
    {'price_usd': '0'}, {'validated': 1}, {'evidence_sha256': ''}, {'provider': 'vercel'})]


def _policy_with_one_route(monkeypatch, tmp_path, change):
    from z0int import worker_routing as wr
    monkeypatch.setenv('Z0INT_HOME', str(tmp_path))
    policy, providers = wr.configuration()
    entry = {**_FREE_ROUTE, **change}
    policy['validated_free_routes'] = [entry]
    monkeypatch.setattr(wr, 'configuration', lambda: (policy, providers))
    return entry, wr.free_route(policy, entry['provider'], entry['model']) is not None


@pytest.mark.parametrize('change,free', _FREE_ROUTE_CASES)
def test_worker_route_pools_are_exactly_the_routes_free_route_accepts(monkeypatch, tmp_path, change, free):
    from z0int import posture
    entry, routed = _policy_with_one_route(monkeypatch, tmp_path, change)
    assert routed is free
    assert [p.id for p in posture.pools_from_worker_routes()] == ([f"route:{entry['provider']}"] if free else [])
