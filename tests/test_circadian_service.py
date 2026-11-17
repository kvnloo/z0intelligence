"""circadiand: adapters parsing, routing with enforcement, the R2 release rule. SYNTHETIC data only."""
from __future__ import annotations

import json
import stat
import sys
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from z0int import human_posture as hp  # noqa: E402
from z0int.circadian import adapters, service  # noqa: E402

TZ = ZoneInfo("America/New_York")
WAKE = hp.parse_ts("2026-10-27T11:30:00Z")  # SYNTHETIC default wake 07:30 EDT


@pytest.fixture
def env(tmp_path, monkeypatch):
    sent = tmp_path / "sent.jsonl"
    deliver = tmp_path / "deliver.sh"
    deliver.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$CIRCADIAN_BYPASS|$1\" >> {sent}\nprintf '%s' \"$2\" > {sent}.body\n")
    deliver.chmod(0o755)
    monkeypatch.setenv("CIRCADIAN_STATE_DIR", str(tmp_path / "state"))
    cfg = service.load_config(tmp_path / "missing.json")
    cfg["deliverCmd"] = [str(deliver)]
    return cfg, service.state_dir(), sent


def test_usagestats_parser_reads_only_the_24h_section():
    text = """Last 24 hour events (timeRange="SYNTHETIC")
    time="2026-10-26 23:10:00" type=SCREEN_INTERACTIVE package=android flags=0x0
    time="2026-10-26 23:40:00" type=NOTIFICATION_INTERRUPTION package=x flags=0x0
    time="2026-10-27 02:05:00" type=SCREEN_NON_INTERACTIVE package=android flags=0x0
In-memory daily stats
    time="2026-10-20 01:00:00" type=SCREEN_INTERACTIVE package=android flags=0x0
"""
    ev = adapters.parse_usagestats(text, TZ)
    assert ev == [("2026-10-27T03:10:00Z", "on"), ("2026-10-27T06:05:00Z", "off")]


def test_phone_intervals_pairs_and_open_tail():
    now = hp.parse_ts("2026-10-27T10:00:00Z")
    ev = [("2026-10-27T04:10:00Z", "on"), ("2026-10-27T07:05:00Z", "off"), ("2026-10-27T09:50:00Z", "on")]
    assert adapters.phone_intervals(ev, now) == [
        {"start": "2026-10-27T04:10:00Z", "end": "2026-10-27T07:05:00Z", "source": "phone"},
        {"start": "2026-10-27T09:50:00Z", "end": "2026-10-27T10:00:00Z", "source": "phone"}]


def test_unconfigured_adapters_are_unavailable_not_zero():
    assert adapters.activitywatch({}, WAKE)["available"] is False
    assert adapters.phone_snapshot({}, TZ)["available"] is False
    assert adapters.noop_last_session({})["available"] is False


def _route(cfg, d, minutes, **notice):
    return service.route_notice(cfg, {"title": "t", "body": "b", **notice}, WAKE + timedelta(minutes=minutes), d)


def test_quiet_phase_reports_are_held_and_private(env):
    cfg, d, _ = env
    r = _route(cfg, d, 870, cls="report", source="z0-phone-notify")   # after the freeze
    assert r["deliver"] is False and r["phase"] == "WIND_DOWN"
    held = (d / "deferred.jsonl").read_text().splitlines()
    assert len(held) == 1 and json.loads(held[0])["title"] == "t"
    assert stat.S_IMODE((d / "deferred.jsonl").stat().st_mode) == 0o600
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_day_batching_is_shadow_only(env):
    cfg, d, _ = env
    r = _route(cfg, d, 400, cls="report")   # DIP: the policy says defer
    assert r["shadowAction"] == "defer" and r["deliver"] is True and r["enforced"] is False


def test_enforce_off_and_dst_shadow_dates_deliver(env):
    cfg, d, _ = env
    assert _route({**cfg, "enforce": "none"}, d, 870, cls="report")["deliver"] is True
    dst = service.route_notice(cfg, {"cls": "report", "title": "x"}, hp.parse_ts("2026-11-01T06:00:00Z"), d)
    assert dst["shadowAction"] == "digest" and dst["deliver"] is True


def test_release_waits_for_the_owner_then_sends_one_digest(env):
    cfg, d, sent = env
    for i in range(3):
        _route(cfg, d, 900 + i, cls="report")
    # next morning, clock-only (quality default): nothing before 12:00 local (R2, C15)
    assert service.release(cfg, WAKE + timedelta(minutes=1440 + 40), d)["released"] == 0
    assert not sent.exists()
    out = service.release(cfg, WAKE + timedelta(minutes=1440 + 280), d)   # 12:10 local
    assert out["released"] == 3
    lines = sent.read_text().splitlines()
    assert lines == ["1|Held overnight: 3 updates"]
    assert Path(f"{sent}.body").read_text() == "- t\n- t\n- t"
    assert not (d / "deferred.jsonl").exists()


def test_due_rule_needs_day_phase_and_not_before_eight(env):
    cfg, d, _ = env
    st = hp.evaluate_human_state({}, hp.fmt(WAKE), hp.DEFAULT_CONFIG)
    st = {**st, "quality": "proxy", "anchors": {**st["anchors"], "wakeAt": "2026-10-27T10:30:00Z"}}  # 06:30 EDT
    items = [{"title": "x"}]
    early = hp.parse_ts("2026-10-27T11:10:00Z")   # 07:10 EDT, INERTIA, wake+40
    dec = hp.policy(st, hp.fmt(early))
    assert dec["phase"] == "INERTIA"
    assert service.due(items, st, dec, early, TZ) is False
    later = hp.parse_ts("2026-10-27T12:05:00Z")   # 08:05 EDT
    assert service.due(items, st, hp.policy(st, hp.fmt(later)), later, TZ) is True
    assert service.due([], st, hp.policy(st, hp.fmt(later)), later, TZ) is False


def test_done_and_undone(env):
    cfg, d, _ = env
    service.set_done(d, WAKE + timedelta(minutes=500))
    assert _route(cfg, d, 510, cls="report")["phase"] == "DISENGAGED"
    service.clear_done(d, WAKE + timedelta(minutes=511))
    assert _route(cfg, d, 512, cls="report")["phase"] == "SECOND"


def test_digest_text_is_bounded():
    title, body = service.digest_text([{"title": "x" * 200}] * 40)
    assert title == "Held overnight: 40 updates" and len(body) <= 600
