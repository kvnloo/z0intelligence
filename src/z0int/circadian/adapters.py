"""Read-only adapters that turn the owner's devices into ``human_posture`` facts.

Each adapter returns ``{"available": bool, ...}`` and never raises: a missing
source is missing, never zero (spec §1.2). Nothing here writes to a device.

* ActivityWatch (desk): ``aw-watcher-afk_<host>`` not-afk events over the local REST API.
* Phone screen (S25): ``adb shell dumpsys usagestats`` read-only, SCREEN_INTERACTIVE /
  SCREEN_NON_INTERACTIVE from the "Last 24 hour events" section (R3). No input events,
  so the screen never wakes (R2). Extracted events are kept in private state, the raw
  dump is discarded.
* NOOP wearable DB copy: the latest ``sleepSession`` start/end only (epoch seconds),
  read with ``mode=ro``. Stalled since 2026-10-22, so ``evaluate`` treats it as stale.

Host specifics (adb path, device model, AW port, DB path) come from the private
config ``~/.config/circadian/config.json``, never from this file.
"""

from __future__ import annotations

import json
import re
import socket
import sqlite3
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from ..human_posture import fmt

EVENT_RE = re.compile(r'^\s*time="(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})" type=(SCREEN_INTERACTIVE|SCREEN_NON_INTERACTIVE)\b')


def activitywatch(cfg: Mapping[str, Any], now: datetime, hours: int = 48) -> dict[str, Any]:
    aw = cfg.get("activitywatch") or {}
    base = aw.get("url")
    if not base:
        return {"available": False, "reason": "not configured"}
    try:
        with urllib.request.urlopen(f"{base}/api/0/buckets/", timeout=3) as r:
            buckets = json.load(r)
        host = aw.get("host") or socket.gethostname()
        name = f"aw-watcher-afk_{host}"
        if name not in buckets:
            cands = [b for b in buckets if b.startswith("aw-watcher-afk_")]
            if not cands:
                return {"available": False, "reason": "no afk bucket"}
            name = cands[0]
        q = urllib.parse.urlencode({"start": fmt(now - timedelta(hours=hours)), "end": fmt(now), "limit": 5000})
        with urllib.request.urlopen(f"{base}/api/0/buckets/{name}/events?{q}", timeout=5) as r:
            events = json.load(r)
    except Exception as exc:  # noqa: BLE001 - a dead source is "unavailable", not an error
        return {"available": False, "reason": type(exc).__name__}
    out = []
    for ev in events:
        if (ev.get("data") or {}).get("status") != "not-afk":
            continue
        raw = re.sub(r"\.\d+", "", ev["timestamp"]).replace("Z", "+00:00")
        start = datetime.fromisoformat(raw).astimezone(timezone.utc)
        end = start + timedelta(seconds=int(float(ev.get("duration") or 0)))
        if end <= start:
            end = start + timedelta(minutes=1)
        out.append({"start": fmt(start), "end": fmt(min(end, now)), "source": "desk"})
    return {"available": True, "intervals": out}


def parse_usagestats(text: str, tz: ZoneInfo) -> list[tuple[str, str]]:
    """``dumpsys usagestats`` -> [(utc_iso, "on"|"off")] from the first (last-24-hour) section."""
    events: list[tuple[str, str]] = []
    started = False
    for line in text.splitlines():
        if "Last 24 hour events" in line:
            started = True
            continue
        if started and line and not line.startswith(" "):
            break
        if not started:
            continue
        m = EVENT_RE.match(line)
        if m:
            local = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
            events.append((fmt(local), "on" if m.group(2) == "SCREEN_INTERACTIVE" else "off"))
    return events


def phone_snapshot(cfg: Mapping[str, Any], tz: ZoneInfo) -> dict[str, Any]:
    ph = cfg.get("phone") or {}
    adb, model = ph.get("adb"), ph.get("model")
    if not adb or not model:
        return {"available": False, "reason": "not configured"}
    try:
        devs = subprocess.run([adb, "devices", "-l"], capture_output=True, text=True, timeout=10).stdout
        serials = [ln.split()[0] for ln in devs.splitlines() if model in ln and " device " in ln + " "]
        tls = [s for s in serials if "_adb-tls-connect" in s]
        serial = (tls or serials or [None])[0]
        if not serial:
            return {"available": False, "reason": "phone not connected"}
        out = subprocess.run([adb, "-s", serial, "shell", "dumpsys", "usagestats"], capture_output=True,
                             text=True, timeout=30, stdin=subprocess.DEVNULL).stdout
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": type(exc).__name__}
    events = parse_usagestats(out, tz)
    if not events:
        return {"available": False, "reason": "no screen events"}
    return {"available": True, "events": events}


def phone_intervals(events: list[tuple[str, str]], now: datetime) -> list[dict[str, str]]:
    """Screen on/off events -> intervals. An unmatched final "on" runs until now."""
    out: list[dict[str, str]] = []
    on: str | None = None
    for ts, kind in sorted(set(events)):
        if kind == "on":
            on = on or ts
        elif on is not None:
            out.append({"start": on, "end": ts, "source": "phone"})
            on = None
    if on is not None:
        out.append({"start": on, "end": fmt(now), "source": "phone"})
    return out


def noop_last_session(cfg: Mapping[str, Any]) -> dict[str, Any]:
    db = (cfg.get("noop") or {}).get("db")
    if not db or not Path(db).exists():
        return {"available": False, "reason": "no db copy"}
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            row = con.execute("select startTs, endTs from sleepSession order by endTs desc limit 1").fetchone()
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": type(exc).__name__}
    if not row:
        return {"available": False, "reason": "no sessions"}
    s, e = (int(v) // 1000 if int(v) > 10**11 else int(v) for v in row)
    return {"available": True,
            "lastSessionStart": fmt(datetime.fromtimestamp(s, timezone.utc)),
            "lastSessionEnd": fmt(datetime.fromtimestamp(e, timezone.utc))}
