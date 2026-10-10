"""Read-only federated capacity snapshot projection.

This module does not schedule work. It joins bounded, sanitized observations
from local hardware, Tern exports, Kerdoios quota projections, and placement
leases into one inspectable snapshot for CompanyOS/Tern-style HUDs.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from . import paths
from .posture import OBSERVATION_PREDATES_RESET, observation_predates_reset, parse_time
from .state_packet import redact

SCHEMA = "z0.capacity.snapshot.v1"
DEFAULT_FILENAME = "capacity_snapshot.json"

_RESOURCE_INT_KEYS = (
    "cpu_logical",
    "ram_total_bytes",
    "ram_available_bytes",
    "storage_total_bytes",
    "storage_free_bytes",
)
_QUOTA_REMAINING_KEYS = ("remaining_free_quota", "remaining_tokens", "remaining", "day_tokens_remaining")
_QUOTA_LIMIT_KEYS = ("limit", "daily_limit", "day_tokens_limit")
_LEASE_TEXT_KEYS = (
    "placement_id",
    "demand_id",
    "offer_id",
    "selected_offer_id",
    "selection_reason",
    "policy_revision",
)

# Every emitted value is one of: a finite bounded number, a bool, a member of a
# fixed set, or a short single-line label. Anything else becomes null/unknown.
_HOST_MODES = frozenset({"direct", "kubernetes", "remote"})
_HOST_STATUSES = frozenset(
    {"online", "offline", "connected", "disconnected", "connecting", "degraded", "error", "unknown"}
)
_SESSION_STATUSES = frozenset(
    {"running", "idle", "current", "current_locked", "locked", "available", "detached", "attached",
     "exited", "stopped", "unknown"}
)
_HEALTH = frozenset(
    {"ok", "healthy", "available", "online", "degraded", "rate_limited", "cooldown", "exhausted",
     "unhealthy", "unavailable", "offline", "down", "error", "unknown"}
)
_QUOTA_SOURCES = frozenset(
    {"provider", "headers", "header", "api", "observed", "estimated", "configured", "config",
     "static", "default", "local", "cache", "unknown"}
)
_LEASE_STATUSES = frozenset(
    {"proposed", "pending", "selected", "granted", "leased", "active", "released", "completed",
     "expired", "revoked", "rejected", "cancelled", "failed", "unknown"}
)
_MAX_TEXT = 128
_MAX_DIMENSION_NAME = 48
_MAX_DIMENSIONS = 32
_MAX_GPUS = 64
_MAX_NUMBER = 2**63
_MAX_EPOCH = 32_503_680_000.0  # year 3000; keeps datetime conversion in range
_FUTURE_SKEW_S = 5.0


class _Unreadable:
    """A source file that exists but could not be read as JSON."""

    def __init__(self, reason: str) -> None:
        self.reason = reason


def _num(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or abs(value) >= _MAX_NUMBER:  # NaN, +-Infinity, absurd magnitudes
        return None
    return value


def _count(value: Any) -> int | None:
    number = _num(value)
    if number is None or number < 0 or number != int(number):
        return None
    return int(number)


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _enum(value: Any, allowed: frozenset[str], default: str = "unknown") -> str:
    return value if isinstance(value, str) and value in allowed else default


def _text(value: Any, limit: int = _MAX_TEXT) -> str | None:
    """A short plain label, or None. Never a container, a path, or anything the repo's redactor would scrub."""
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) < _MAX_NUMBER:
        return str(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > limit or not text.isprintable():
        return None
    if text.startswith(("/", "~")) or "/home/" in text or "/Users/" in text:
        return None
    if redact(text, limit=len(text) + 1) != " ".join(text.split()):
        return None
    return text


def _first(src: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if src.get(key) is not None:
            return src.get(key)
    return None


def _epoch(value: Any) -> float | None:
    """Epoch seconds from a number or ISO-8601 string; None when absent or not a real instant."""
    if isinstance(value, str):
        parsed = parse_time(value) if len(value) <= 40 else None
        value = parsed.timestamp() if parsed else None
    number = _num(value)
    if number is None or not 0 < number < _MAX_EPOCH:
        return None
    return float(number)


def _observed(value: Any, *, now: float) -> tuple[float | None, str | None]:
    """(observation time, None), or (None, why it is unknown). A missing time is never replaced by now."""
    if value is None:
        return None, "missing_observed_at"
    observed_at = _epoch(value)
    if observed_at is None or observed_at > now + _FUTURE_SKEW_S:
        return None, "invalid_observed_at"
    return observed_at, None


def _reset_passed(reset_at: float | None, now: float) -> bool:
    if reset_at is None or not 0 < now < _MAX_EPOCH:
        return False
    return observation_predates_reset(
        datetime.fromtimestamp(reset_at, timezone.utc), datetime.fromtimestamp(now, timezone.utc)
    )


def _source(status: str, *, observed_at: float | None = None, reason: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"status": status}
    if observed_at is not None:
        out["observed_at"] = observed_at
    if reason:
        out["reason"] = reason
    return out


def _observed_source(value: Any, *, now: float, reason: str | None = None) -> dict[str, Any]:
    """Source record with an honest age: unknown time means unknown age and a non-ok status."""
    observed_at, time_reason = _observed(value, now=now)
    reason = time_reason or reason
    out: dict[str, Any] = {
        "status": "degraded" if reason else "ok",
        "observed_at": observed_at,
        "age_s": None if observed_at is None else max(0.0, now - observed_at),
    }
    if reason:
        out["reason"] = reason
    return out


def _non_finite(name: str) -> Any:
    raise ValueError(f"non-finite JSON number: {name}")


def _read_json(path: Path | None) -> Any:
    """Parsed JSON, None when the file is absent, or _Unreadable when it exists but is malformed."""
    if path is None:
        return None
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        return _Unreadable("unreadable_export")
    try:
        return json.loads(data.decode("utf-8"), parse_constant=_non_finite)
    except (ValueError, RecursionError):
        return _Unreadable("invalid_json")


def _mtime(path: Path | None) -> float | None:
    try:
        return None if path is None else path.stat().st_mtime
    except OSError:
        return None


def _stable_id(kind: str, *parts: object) -> str:
    raw = "\0".join("" if part is None else str(part) for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    return f"{kind}-{digest}"


def _machine_seed() -> str:
    for candidate in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        try:
            value = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return socket.gethostname() or "unknown-host"


def _meminfo() -> tuple[int | None, int | None]:
    try:
        rows = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, value = line.split(":", 1)
            rows[key] = int(value.strip().split()[0]) * 1024
        return rows.get("MemTotal"), rows.get("MemAvailable")
    except (OSError, ValueError, IndexError):
        return None, None


def _disk() -> tuple[int | None, int | None]:
    try:
        usage = shutil.disk_usage(Path.home())
        return usage.total, usage.free
    except OSError:
        return None, None


def _nvidia_gpus() -> list[dict[str, Any]] | None:
    """GPU rows, or None when they could not be observed (unknown is not zero)."""
    if not shutil.which("nvidia-smi"):
        return None
    argv = [
        "nvidia-smi",
        "--query-gpu=uuid,name,memory.total,memory.free,utilization.gpu",
        "--format=csv,noheader,nounits",
    ]
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    out: list[dict[str, Any]] = []
    for line in proc.stdout.splitlines():
        parts = [part.strip() for part in line.split(",", 4)]
        if len(parts) != 5:
            continue
        uuid, name, total_mb, free_mb, util = parts
        try:
            total_bytes = int(float(total_mb) * 1024 * 1024)
            free_bytes = int(float(free_mb) * 1024 * 1024)
            utilization = float(util)
        except (ValueError, OverflowError):
            total_bytes = free_bytes = None
            utilization = None
        out.append(
            {
                "id": uuid or None,
                "name": name or None,
                "memory_total_bytes": total_bytes,
                "memory_free_bytes": free_bytes,
                "utilization_pct": utilization,
            }
        )
    return out


def probe_local_host(*, now: float | None = None) -> dict[str, Any]:
    """Return a bounded direct-host observation; no credentials or process data."""
    now = time.time() if now is None else now
    ram_total, ram_available = _meminfo()
    storage_total, storage_free = _disk()
    gpus = _nvidia_gpus()
    return {
        "host_id": _stable_id("host", _machine_seed()),
        "label": socket.gethostname() or "local",
        "mode": "direct",
        "status": "online",
        "rtt_ms": 0.0,
        "resources": {
            "cpu_logical": os.cpu_count(),
            "ram_total_bytes": ram_total,
            "ram_available_bytes": ram_available,
            "storage_total_bytes": storage_total,
            "storage_free_bytes": storage_free,
            "gpu_count": None if gpus is None else len(gpus),
            "gpus": gpus,
        },
        "observed_at": now,
    }


def _safe_resources(value: Any) -> dict[str, Any]:
    src = value if isinstance(value, Mapping) else {}
    out: dict[str, Any] = {key: _count(src.get(key)) for key in _RESOURCE_INT_KEYS if key in src}
    if "gpus" in src or "gpu_count" in src:
        raw_gpus = src.get("gpus")
        gpus = None
        if isinstance(raw_gpus, list):
            gpus = [
                {
                    "id": _text(gpu.get("id")),
                    "name": _text(gpu.get("name")),
                    "memory_total_bytes": _count(gpu.get("memory_total_bytes")),
                    "memory_free_bytes": _count(gpu.get("memory_free_bytes")),
                    "utilization_pct": _num(gpu.get("utilization_pct")),
                }
                for gpu in raw_gpus[:_MAX_GPUS]
                if isinstance(gpu, Mapping)
            ]
        count = _count(src.get("gpu_count"))
        out["gpus"] = gpus
        out["gpu_count"] = count if count is not None else (None if gpus is None else len(gpus))
    return out


def _normalize_host(host: Mapping[str, Any], *, now: float) -> dict[str, Any] | None:
    explicit_host_id = _text(host.get("host_id"))
    raw_id = explicit_host_id
    for key in ("id", "name", "label", "slot"):
        raw_id = raw_id or _text(host.get(key))
    if raw_id is None:
        return None
    return {
        "host_id": explicit_host_id if explicit_host_id is not None else f"tern:{raw_id}",
        "label": _text(host.get("label")) or _text(host.get("name")),
        "mode": _enum(host.get("mode"), _HOST_MODES, "remote"),
        "status": _enum(_first(host, "status", "state"), _HOST_STATUSES),
        "rtt_ms": _num(host.get("rtt_ms")),
        "resources": _safe_resources(host.get("resources")),
        "observed_at": _observed(host.get("observed_at"), now=now)[0],
    }


def _normalize_session(session: Mapping[str, Any], *, now: float) -> dict[str, Any] | None:
    raw_id = _text(session.get("session_id")) or _text(session.get("id"))
    if raw_id is None:
        return None
    host_id = _text(session.get("host_id")) or _text(session.get("host"))
    if host_id is not None and not host_id.startswith("tern:"):
        host_id = f"tern:{host_id}"
    current = _bool(session.get("current"))
    locked = _bool(session.get("locked"))
    raw_status = _first(session, "status", "state")
    if raw_status is not None:
        status = _enum(raw_status, _SESSION_STATUSES)
    elif current is True and locked is True:
        status = "current_locked"
    elif current is True:
        status = "current"
    elif locked is True:
        status = "locked"
    else:
        status = "available"
    return {
        "session_id": raw_id,
        "name": _text(session.get("name")),
        "host_id": host_id,
        "runtime": _text(session.get("runtime")) or _text(session.get("program")),
        "status": status,
        "current": current,
        "locked": locked,
        "tab_count": _count(_first(session, "tab_count", "tabs")),
        "rtt_ms": _num(session.get("rtt_ms")),
        "pane_id": _text(session.get("pane_id")),
        "session_sticky": True,
        "migration_allowed": False,
        "observed_at": _observed(session.get("observed_at"), now=now)[0],
    }


def _normalize_tern(raw: Any, *, now: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if raw is None:
        return [], [], _source("missing", reason="tern_export_missing")
    if isinstance(raw, _Unreadable):
        return [], [], _source("error", reason=raw.reason)
    if not isinstance(raw, Mapping):
        return [], [], _source("error", reason="invalid_tern_export")
    raw_hosts = raw.get("hosts") if raw.get("hosts") is not None else []
    raw_sessions = raw.get("sessions") if raw.get("sessions") is not None else []
    if not isinstance(raw_hosts, list) or not isinstance(raw_sessions, list):
        return [], [], _source("error", reason="invalid_tern_export")
    hosts = []
    for host in raw_hosts:
        if isinstance(host, Mapping) and (normalized := _normalize_host(host, now=now)):
            hosts.append(normalized)
    sessions = []
    for session in raw_sessions:
        if isinstance(session, Mapping) and (normalized := _normalize_session(session, now=now)):
            sessions.append(normalized)
    return hosts, sessions, _observed_source(
        _first(raw, "observed_at", "generated_at"),
        now=now,
        reason=None if hosts or sessions else "empty_tern_export",
    )


def _safe_quota(quota: Any, *, now: float, entry_reset_at: float | None) -> tuple[dict[str, Any], bool]:
    """Typed quota facts, and whether any of them was observed before its reset (see posture.py)."""
    src = quota if isinstance(quota, Mapping) else {}
    out: dict[str, Any] = {}
    reset_at = _epoch(src.get("reset_at"))
    window_passed = _reset_passed(entry_reset_at, now) or _reset_passed(reset_at, now)
    stale = window_passed
    dimensions: dict[str, Any] = {}
    raw_dimensions = src.get("dimensions")
    if isinstance(raw_dimensions, Mapping):
        for raw_name, dimension in list(raw_dimensions.items())[:_MAX_DIMENSIONS]:
            name = _text(raw_name, _MAX_DIMENSION_NAME) if isinstance(raw_name, str) else None
            if name is None or not isinstance(dimension, Mapping):
                continue
            clean: dict[str, Any] = {}
            dimension_reset_at = _epoch(dimension.get("reset_at"))
            passed = _reset_passed(dimension_reset_at, now) if dimension_reset_at is not None else window_passed
            if "limit" in dimension:
                clean["limit"] = _num(dimension.get("limit"))
            if "remaining" in dimension:
                clean["remaining"] = None if passed else _num(dimension.get("remaining"))
            if "reset_at" in dimension:
                clean["reset_at"] = dimension_reset_at
            if "source" in dimension:
                clean["source"] = _enum(dimension.get("source"), _QUOTA_SOURCES)
            if passed:
                clean["stale_reason"] = OBSERVATION_PREDATES_RESET
                stale = True
            dimensions[name] = clean
    if dimensions:
        out["dimensions"] = dimensions
    for key in _QUOTA_REMAINING_KEYS:
        if key in src:
            out[key] = None if window_passed else _num(src.get(key))
    for key in _QUOTA_LIMIT_KEYS:
        if key in src:
            out[key] = _num(src.get(key))
    if "reset_at" in src:
        out["reset_at"] = reset_at
    return out, stale


def _normalize_kerdoios(raw: Any, *, now: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if raw is None:
        return [], _source("missing", reason="kerdoios_export_missing")
    if isinstance(raw, _Unreadable):
        return [], _source("error", reason=raw.reason)
    if not isinstance(raw, Mapping):
        return [], _source("error", reason="invalid_kerdoios_export")
    entries = raw.get("entries") if raw.get("entries") is not None else []
    if isinstance(entries, Mapping):
        entries = list(entries.values())
    if not isinstance(entries, list):
        return [], _source("error", reason="invalid_kerdoios_export")
    offers: list[dict[str, Any]] = []
    any_stale = False
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        provider = _text(entry.get("provider"))
        model = _text(entry.get("model"))
        if not provider:
            continue
        reset_at = _epoch(entry.get("reset_at"))
        quota, stale = _safe_quota(entry.get("quota"), now=now, entry_reset_at=reset_at)
        offer = {
            "offer_id": _text(entry.get("offer_id")) or _stable_id("offer", "provider", provider, model),
            "origin": "provider",
            "provider": provider,
            "model": model,
            "actor_id": model,
            "health": _enum(_first(entry, "health", "status"), _HEALTH),
            "quota": quota,
            "price": _num(entry.get("price")),
            "predicted_cost": _num(entry.get("predicted_cost")),
            # Derived from the pre-reset observation, so unknown once that observation is stale.
            "burn_rate": None if stale else _num(entry.get("burn_rate")),
            "time_to_exhaustion": None if stale else _num(entry.get("time_to_exhaustion")),
            "time_to_reset": None if stale else _num(entry.get("time_to_reset")),
            "reset_at": reset_at,
            "observed_at": _observed(_first(entry, "updated_at", "observed_at"), now=now)[0],
        }
        if stale:
            offer["stale_reason"] = OBSERVATION_PREDATES_RESET
            any_stale = True
        offers.append(offer)
    reason = None if offers else "empty_kerdoios_export"
    return offers, _observed_source(
        _first(raw, "generated_at", "saved_at", "observed_at"),
        now=now,
        reason=OBSERVATION_PREDATES_RESET if any_stale else reason,
    )


def _local_offer(host: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "offer_id": _stable_id("offer", "host", host.get("host_id"), "local.compute"),
        "origin": "host",
        "host_id": host.get("host_id"),
        "actor_id": "local.compute",
        "health": host.get("status") or "unknown",
        "resources": _safe_resources(host.get("resources")),
        "quota": {},
        "price": 0.0,
        "predicted_cost": 0.0,
        "burn_rate": None,
        "time_to_exhaustion": None,
        "time_to_reset": None,
        "observed_at": host.get("observed_at"),
    }


def _normalize_leases(raw: Any, *, now: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if raw is None:
        return [], _source("missing", reason="lease_projection_missing")
    if isinstance(raw, _Unreadable):
        return [], _source("error", reason=raw.reason)
    rows = raw.get("leases") if isinstance(raw, Mapping) else raw
    if not isinstance(rows, list):
        return [], _source("error", reason="invalid_lease_projection")
    out = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        clean: dict[str, Any] = {key: _text(row.get(key)) for key in _LEASE_TEXT_KEYS if key in row}
        if "status" in row:
            clean["status"] = _enum(row.get("status"), _LEASE_STATUSES)
        if "lease_expires_at" in row:
            clean["lease_expires_at"] = _epoch(row.get("lease_expires_at"))
        if clean.get("placement_id"):
            out.append(clean)
    observed_at = _first(raw, "observed_at", "generated_at") if isinstance(raw, Mapping) else None
    return out, _observed_source(observed_at, now=now)


def build_snapshot(
    *,
    tern: Any = None,
    kerdoios: Any = None,
    leases: Any = None,
    include_local: bool = True,
    now: float | None = None,
    file_mtimes: Mapping[str, float | None] | None = None,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    hosts: list[dict[str, Any]] = []
    offers: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}

    # Each source fails soft: one bad input marks that source "error" and the rest is still emitted.
    if include_local:
        try:
            local = probe_local_host(now=now)
            local_offer = _local_offer(local)
        except Exception:
            sources["local"] = _source("error", reason="local_probe_failed")
        else:
            hosts.append(local)
            offers.append(local_offer)
            sources["local"] = {"status": "ok", "observed_at": now, "age_s": 0.0}

    try:
        tern_hosts, sessions, tern_source = _normalize_tern(tern, now=now)
    except Exception:
        tern_hosts, sessions, tern_source = [], [], _source("error", reason="tern_normalization_failed")
    by_id = {host["host_id"]: host for host in hosts}
    for host in tern_hosts:
        by_id[host["host_id"]] = {**by_id.get(host["host_id"], {}), **host}
    hosts = list(by_id.values())
    sources["tern"] = tern_source

    try:
        provider_offers, kerdoios_source = _normalize_kerdoios(kerdoios, now=now)
    except Exception:
        provider_offers, kerdoios_source = [], _source("error", reason="kerdoios_normalization_failed")
    offers.extend(provider_offers)
    sources["kerdoios"] = kerdoios_source

    try:
        normalized_leases, lease_source = _normalize_leases(leases, now=now)
    except Exception:
        normalized_leases, lease_source = [], _source("error", reason="lease_normalization_failed")
    sources["leases"] = lease_source

    # A file's mtime says when the file was written, not when its contents were observed: label it as such.
    for name, mtime in (file_mtimes or {}).items():
        mtime = _epoch(mtime)
        if name in sources and mtime is not None:
            sources[name]["file_mtime"] = mtime
            sources[name]["file_age_s"] = max(0.0, now - mtime)

    required = ("local",) if include_local else ()
    overall = "ok"
    if any(sources[name]["status"] != "ok" for name in required):
        overall = "degraded"
    elif any(value["status"] not in {"ok", "missing"} for value in sources.values()):
        overall = "degraded"

    return {
        "schema": SCHEMA,
        "generated_at": now,
        "status": overall,
        "sources": sources,
        "hosts": sorted(hosts, key=lambda row: str(row.get("host_id"))),
        "sessions": sorted(sessions, key=lambda row: str(row.get("session_id"))),
        "offers": sorted(offers, key=lambda row: str(row.get("offer_id"))),
        "leases": sorted(normalized_leases, key=lambda row: str(row.get("placement_id"))),
        "summary": {
            "hosts": len(hosts),
            "sessions": len(sessions),
            "offers": len(offers),
            "leases": len(normalized_leases),
        },
    }


def default_snapshot_path() -> Path:
    return paths.home() / "state" / DEFAULT_FILENAME


def write_snapshot(snapshot: Mapping[str, Any], path: Path | None = None) -> Path:
    if snapshot.get("schema") != SCHEMA:
        raise ValueError(f"expected schema {SCHEMA}")
    dest = path or default_snapshot_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot, indent=2, sort_keys=True, allow_nan=False) + "\n"
    # mkstemp: unpredictable name, O_EXCL | O_NOFOLLOW, mode 0600, in the destination directory.
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return dest


def _path_arg(value: str | None, env_name: str) -> Path | None:
    raw = value or os.environ.get(env_name)
    return Path(raw).expanduser() if raw else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tern", help="normalized Tern host/session JSON export")
    parser.add_argument("--kerdoios", help="Kerdoios capacity/quota JSON export")
    parser.add_argument("--leases", help="optional placement-lease JSON projection")
    parser.add_argument("--output", help="snapshot destination; defaults under Z0INT_HOME/state")
    parser.add_argument("--no-local", action="store_true", help="omit local direct-host probe")
    parser.add_argument("--json", action="store_true", help="print the emitted snapshot")
    args = parser.parse_args(argv)

    tern_path = _path_arg(args.tern, "Z0INT_TERN_CAPACITY_SNAPSHOT")
    if tern_path is None:
        tern_path = paths.home() / "state" / "tern_capacity.json"
    kerdoios_path = _path_arg(args.kerdoios, "Z0INT_KERDOIOS_CAPACITY_SNAPSHOT")
    if kerdoios_path is None:
        kerdoios_path = Path.home() / ".cache" / "kerdoios" / "capacity.json"
    leases_path = _path_arg(args.leases, "Z0INT_PLACEMENT_LEASES_SNAPSHOT")

    snapshot = build_snapshot(
        tern=_read_json(tern_path),
        kerdoios=_read_json(kerdoios_path),
        leases=_read_json(leases_path),
        include_local=not args.no_local,
        file_mtimes={"tern": _mtime(tern_path), "kerdoios": _mtime(kerdoios_path), "leases": _mtime(leases_path)},
    )
    dest = write_snapshot(snapshot, Path(args.output).expanduser() if args.output else None)
    if args.json:
        print(json.dumps({**snapshot, "path": str(dest)}, indent=2, sort_keys=True))
    else:
        print(str(dest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
