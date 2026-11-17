"""circadiand: the owner's human posture as a small local service (CIRCADIAN-SPEC WP1).

Every tick (default 60 s) it reads the adapters, evaluates the state, computes
the policy, writes ``posture.json`` (0600) and releases held notices when the
owner is awake again. It serves ``GET /posture`` on loopback only.

Notice routing does not need the daemon: ``route_notice`` reads the last
``posture.json`` (or the clock-only state when it is missing or stale) and is
called in-process by ``circadian route`` from the notify scripts. A dead
daemon therefore never drops a notice: the scripts fail open and deliver.

Enforcement (C12, R2b, R11): only the conservative rule is enforced, and only
when ``enforce = "quiet_only"``: in a quiet phase (WIND_DOWN, PRE_SLEEP, SLEEP,
DISENGAGED) reports, decisions and digests are held for one morning digest.
Day-phase batching, capture mode and readiness rows run in shadow (logged only).
Owner days listed in ``shadowDates`` (the 2026-11-01 DST night) enforce nothing.
"""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import threading
import time as _time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator, Mapping
from zoneinfo import ZoneInfo

from .. import human_posture as hp
from . import adapters

POSTURE_SCHEMA = "z0.circadian_posture/v1"
STALE_AFTER = timedelta(minutes=15)
DEFAULTS: dict[str, Any] = {
    **hp.DEFAULT_CONFIG,
    "shadowDates": ["2026-10-31", "2026-11-01", "2026-11-02"],
    "tickSeconds": 60,
    "phone": {"everyMinutes": 15},
    "deliverCmd": [str(Path.home() / ".local/bin/dash-report")],
    "http": {"port": None},
}


def config_path() -> Path:
    return Path(os.environ.get("CIRCADIAN_CONFIG", Path.home() / ".config/circadian/config.json"))


def state_dir() -> Path:
    d = Path(os.environ.get("CIRCADIAN_STATE_DIR", Path.home() / ".local/state/circadian"))
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(d, 0o700)
    return d


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULTS))
    p = path or config_path()
    if p.exists():
        raw = json.loads(p.read_text())
        for k, v in raw.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k] = {**cfg[k], **v}
            else:
                cfg[k] = v
    return cfg


def policy_config(cfg: Mapping[str, Any]) -> dict[str, Any]:
    return {k: cfg[k] for k in hp.DEFAULT_CONFIG}


# ---------------------------------------------------------------- private files

def _write_private(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def _append_private(path: Path, row: Mapping[str, Any]) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(ln) for ln in path.read_text().splitlines() if ln.strip()]
    except OSError:
        return []


@contextmanager
def _lock(d: Path) -> Iterator[None]:
    fd = os.open(d / ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ---------------------------------------------------------------- facts

def collect_facts(cfg: Mapping[str, Any], now: datetime, d: Path, *, phone: bool | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Adapters -> (facts for evaluate, source health for posture.json)."""
    tz = ZoneInfo(cfg["tz"])
    aw = adapters.activitywatch(cfg, now)
    cache_path = d / "phone-screen.json"
    cache = _read_json(cache_path, {"events": [], "snapshotAt": None, "lastOk": None})
    every = timedelta(minutes=int((cfg.get("phone") or {}).get("everyMinutes", 15)))
    last = hp.parse_ts(cache["snapshotAt"]) if cache.get("snapshotAt") else None
    phone_health: dict[str, Any] = {"available": False, "reason": "not polled yet"}
    want = phone if phone is not None else (last is None or now - last >= every)
    if want:
        snap = adapters.phone_snapshot(cfg, tz)
        cache["snapshotAt"] = hp.fmt(now)
        if snap.get("available"):
            keep = now - timedelta(hours=48)
            merged = {tuple(e) for e in cache.get("events", [])} | {tuple(e) for e in snap["events"]}
            cache["events"] = sorted([list(e) for e in merged if hp.parse_ts(e[0]) >= keep])
            cache["lastOk"] = hp.fmt(now)
        phone_health = {"available": bool(snap.get("available")), "reason": snap.get("reason")}
        _write_private(cache_path, json.dumps(cache))
    elif cache.get("lastOk"):
        phone_health = {"available": True, "reason": None}
    phone_ok = bool(cache.get("lastOk")) and now - hp.parse_ts(cache["lastOk"]) <= timedelta(hours=30)
    intervals = list(aw.get("intervals") or [])
    if phone_ok:
        intervals += adapters.phone_intervals([tuple(e) for e in cache.get("events", [])], now)
    wear = adapters.noop_last_session(cfg)
    done = _read_json(d / "done.json", {})
    counters = _read_json(d / "counters.json", {})
    facts = {
        "activity": {"available": bool(aw.get("available")) or phone_ok, "intervals": intervals},
        "wearable": wear,
        "doneAt": done.get("doneAt"),
        "counters": counters,
    }
    health = {
        "activitywatch": {"available": bool(aw.get("available")), "reason": aw.get("reason")},
        "phone": {**phone_health, "lastOk": cache.get("lastOk")},
        "wearable": {"available": bool(wear.get("available")), "reason": wear.get("reason"),
                     "lastSessionEnd": wear.get("lastSessionEnd")},
    }
    return facts, health


# ---------------------------------------------------------------- posture

def owner_date(state: Mapping[str, Any], tz: ZoneInfo) -> str:
    wake = hp.parse_ts(state["anchors"]["wakeAt"]).astimezone(tz)
    d = wake.date() if wake.hour >= hp.DAY_PIVOT_HOUR else wake.date() - timedelta(days=1)
    return d.isoformat()


def enforcing(cfg: Mapping[str, Any], state: Mapping[str, Any], decision: Mapping[str, Any]) -> bool:
    if cfg.get("enforce") != "quiet_only" or decision["phase"] not in hp.QUIET:
        return False
    return owner_date(state, ZoneInfo(cfg["tz"])) not in set(cfg.get("shadowDates") or [])


def _local(ts: str | None, tz: ZoneInfo) -> str | None:
    return hp.parse_ts(ts).astimezone(tz).isoformat() if ts else None


def build_posture(cfg: Mapping[str, Any], facts: Mapping[str, Any], health: Mapping[str, Any], now: datetime) -> dict[str, Any]:
    tz = ZoneInfo(cfg["tz"])
    state = hp.evaluate_human_state(facts, hp.fmt(now), policy_config(cfg))
    decision = hp.policy(state, hp.fmt(now))
    a = state["anchors"]
    return {
        "schema": POSTURE_SCHEMA,
        "generatedAt": hp.fmt(now),
        "state": state,
        "decision": decision,
        "public": hp.public_view(decision),
        "local": {
            "phase": decision["phase"], "now": _local(hp.fmt(now), tz),
            "wakeAt": _local(a["wakeAt"], tz), "freezeAt": _local(a["freezeAt"], tz),
            "bedTarget": _local(a["bedTarget"], tz), "nextTransitionAt": _local(decision["nextTransitionAt"], tz),
            "nextPeakAt": _local(decision["nextPeakAt"], tz), "lastActivityAt": _local(state["lastActivityAt"], tz),
        },
        "sources": health,
        "enforcement": {"mode": cfg.get("enforce"), "ownerDate": owner_date(state, tz),
                        "enforcingNow": enforcing(cfg, state, decision)},
    }


def current_posture(cfg: Mapping[str, Any], now: datetime, d: Path) -> dict[str, Any]:
    """The last posture.json if fresh, else the clock-only posture (quality default)."""
    p = _read_json(d / "posture.json", None)
    if p and p.get("schema") == POSTURE_SCHEMA and now - hp.parse_ts(p["generatedAt"]) <= STALE_AFTER:
        state = p["state"]
        decision = hp.policy(state, hp.fmt(now))
        return {**p, "decision": decision, "public": hp.public_view(decision)}
    facts = {"doneAt": _read_json(d / "done.json", {}).get("doneAt"), "counters": _read_json(d / "counters.json", {})}
    return build_posture(cfg, facts, {"note": {"available": False, "reason": "posture.json missing or stale"}}, now)


# ---------------------------------------------------------------- routing

def route_notice(cfg: Mapping[str, Any], notice: Mapping[str, Any], now: datetime, d: Path) -> dict[str, Any]:
    """Decide one notice and record it. Returns {"deliver": bool, ...}."""
    with _lock(d):
        post = current_posture(cfg, now, d)
        state, decision = post["state"], post["decision"]
        r = hp.route(decision, notice, hp.fmt(now))
        enforce = enforcing(cfg, state, decision)
        held = r["action"] != "deliver" and enforce
        row = {"at": hp.fmt(now), "phase": decision["phase"], "route": r, "enforced": held,
               "source": notice.get("source"), "tag": notice.get("tag"), "title": notice.get("title")}
        if held:
            _append_private(d / "deferred.jsonl", {**row, "body": notice.get("body"), "id": f"{hp.fmt(now)}-{os.getpid()}"})
        else:
            c = _read_json(d / "counters.json", {})
            hour_key = now.strftime("%Y-%m-%dT%H")
            if c.get("hourKey") != hour_key:
                c = {**c, "hourKey": hour_key, "delivered": 0}
            c["delivered"] = int(c.get("delivered", 0)) + 1
            if decision["phase"] == "PEAK":
                if c.get("blockStart") != decision["peakStartAt"]:
                    c["blockStart"], c["interrupts"] = decision["peakStartAt"], 0
                c["interrupts"] = int(c.get("interrupts", 0)) + 1
            _write_private(d / "counters.json", json.dumps(c))
        _append_private(d / "routes.jsonl", {k: v for k, v in row.items() if k != "title"})
        return {"deliver": not held, "enforced": held, "shadowAction": r["action"], **r,
                "phase": decision["phase"]}


def due(items: list[dict[str, Any]], state: Mapping[str, Any], decision: Mapping[str, Any], now: datetime, tz: ZoneInfo) -> bool:
    """R2 release rule. Pure. Held items go out only when the owner is awake again:
    a day phase, at least wake + 30 min, never before 08:00 local, and with a
    measured/proxy wake (or after 12:00 local when no source sees the owner)."""
    if not items or decision["phase"] not in hp.DAY:
        return False
    wake = hp.parse_ts(state["anchors"]["wakeAt"])
    local = now.astimezone(tz)
    if now < wake + timedelta(minutes=30) or local.hour < 8:
        return False
    if state.get("quality") == "default" and local.hour < 12:
        return False
    return True


def digest_text(items: list[dict[str, Any]]) -> tuple[str, str]:
    n = len(items)
    title = f"Held overnight: {n} update{'s' if n != 1 else ''}"
    lines = []
    for it in items:
        t = (it.get("title") or it.get("tag") or "update").strip().replace("\n", " ")
        lines.append(f"- {t[:90]}")
    body = "\n".join(lines)
    if len(body) > 580:
        body = body[:560].rsplit("\n", 1)[0] + f"\n... and more ({n} total)"
    return title, body


def release(cfg: Mapping[str, Any], now: datetime, d: Path, *, dry_run: bool = False, force: bool = False) -> dict[str, Any]:
    tz = ZoneInfo(cfg["tz"])
    with _lock(d):
        items = _read_jsonl(d / "deferred.jsonl")
        if not items:
            return {"released": 0}
        post = current_posture(cfg, now, d)
        if not force and not due(items, post["state"], post["decision"], now, tz):
            return {"released": 0, "held": len(items)}
        title, body = digest_text(items)
        if dry_run:
            return {"released": 0, "wouldRelease": len(items), "title": title, "body": body}
        env = {**os.environ, "CIRCADIAN_BYPASS": "1"}
        cmd = [os.path.expanduser(c) for c in cfg["deliverCmd"]] + [title, body]
        ok = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60).returncode == 0
        if not ok:
            return {"released": 0, "held": len(items), "error": "deliver command failed"}
        _append_private(d / "released.jsonl", {"at": hp.fmt(now), "count": len(items), "title": title})
        os.replace(d / "deferred.jsonl", d / f"deferred.released.{now.strftime('%Y%m%dT%H%M%S')}.jsonl")
        return {"released": len(items), "title": title}


# ---------------------------------------------------------------- daemon

def tick(cfg: Mapping[str, Any], now: datetime, d: Path, *, phone: bool | None = None) -> dict[str, Any]:
    facts, health = collect_facts(cfg, now, d, phone=phone)
    post = build_posture(cfg, facts, health, now)
    prev = _read_json(d / "posture.json", {})
    _write_private(d / "posture.json", json.dumps(post, sort_keys=True, indent=1))
    prev_phase = ((prev.get("decision") or {}).get("phase"))
    if prev_phase != post["decision"]["phase"] or now.minute == 0:
        _append_private(d / "history.jsonl", {"at": hp.fmt(now), "phase": post["decision"]["phase"],
                                              "quality": post["state"]["quality"],
                                              "enforcingNow": post["enforcement"]["enforcingNow"]})
    post["release"] = release(cfg, now, d)
    return post


class _Handler(BaseHTTPRequestHandler):
    server_version = "circadiand/1"
    cfg: Mapping[str, Any] = {}
    sdir: Path = Path(".")

    def log_message(self, *args: Any) -> None:  # no request logs: they would hold times
        return

    def _json(self, code: int, obj: Any) -> None:
        body = json.dumps(obj, sort_keys=True).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if self.path in ("/posture", "/posture/"):
            p = current_posture(self.cfg, now, self.sdir)
            self._json(200, {k: p[k] for k in ("schema", "generatedAt", "state", "decision", "public", "local", "enforcement")})
        elif self.path == "/healthz":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        now = datetime.now(timezone.utc).replace(microsecond=0)
        n = min(int(self.headers.get("Content-Length") or 0), 8192)
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json(400, {"error": "invalid JSON"})
        if self.path == "/route":
            return self._json(200, route_notice(self.cfg, body, now, self.sdir))
        if self.path == "/done":
            return self._json(200, set_done(self.sdir, now))
        if self.path == "/undone":
            return self._json(200, clear_done(self.sdir, now))
        self._json(404, {"error": "not found"})


def set_done(d: Path, now: datetime) -> dict[str, Any]:
    _write_private(d / "done.json", json.dumps({"doneAt": hp.fmt(now)}))
    _append_private(d / "history.jsonl", {"at": hp.fmt(now), "event": "done"})
    return {"doneAt": hp.fmt(now)}


def clear_done(d: Path, now: datetime) -> dict[str, Any]:
    try:
        (d / "done.json").unlink()
    except FileNotFoundError:
        pass
    _append_private(d / "history.jsonl", {"at": hp.fmt(now), "event": "undone"})
    return {"doneAt": None}


def serve(cfg: Mapping[str, Any], d: Path) -> None:
    port = (cfg.get("http") or {}).get("port")
    if port:
        _Handler.cfg, _Handler.sdir = cfg, d
        httpd = ThreadingHTTPServer(("127.0.0.1", int(port)), _Handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
    every = max(15, int(cfg.get("tickSeconds", 60)))
    while True:
        try:
            tick(cfg, datetime.now(timezone.utc).replace(microsecond=0), d)
        except Exception as exc:  # noqa: BLE001 - keep serving; the next tick retries
            _append_private(d / "errors.jsonl", {"at": hp.fmt(datetime.now(timezone.utc)), "error": repr(exc)[:300]})
        _time.sleep(every)
