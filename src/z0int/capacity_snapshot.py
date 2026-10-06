"""Read-only federated capacity snapshot projection.

This module does not schedule work. It joins bounded, sanitized observations
from local hardware, Tern exports, Kerdoios quota projections, and placement
leases into one inspectable snapshot for CompanyOS/Tern-style HUDs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Mapping

from . import paths

SCHEMA = "z0.capacity.snapshot.v1"
DEFAULT_FILENAME = "capacity_snapshot.json"

_SAFE_HOST_RESOURCE_KEYS = {
    "cpu_logical",
    "ram_total_bytes",
    "ram_available_bytes",
    "storage_total_bytes",
    "storage_free_bytes",
    "gpu_count",
    "gpus",
}
_SAFE_GPU_KEYS = {
    "id",
    "name",
    "memory_total_bytes",
    "memory_free_bytes",
    "utilization_pct",
}
_SAFE_QUOTA_DIMENSION_KEYS = {
    "limit",
    "remaining",
    "reset_at",
    "source",
}
_SAFE_LEASE_KEYS = {
    "placement_id",
    "demand_id",
    "offer_id",
    "status",
    "lease_expires_at",
    "selected_offer_id",
    "selection_reason",
    "policy_revision",
}


def _source(status: str, *, observed_at: float | None = None, reason: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"status": status}
    if observed_at is not None:
        out["observed_at"] = observed_at
    if reason:
        out["reason"] = reason
    return out


def _read_json(path: Path | None) -> Any:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
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


def _nvidia_gpus() -> list[dict[str, Any]]:
    if not shutil.which("nvidia-smi"):
        return []
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
        return []
    if proc.returncode != 0:
        return []
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
        except ValueError:
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
            "gpu_count": len(gpus),
            "gpus": gpus,
        },
        "observed_at": now,
    }


def _safe_resources(value: Any) -> dict[str, Any]:
    src = value if isinstance(value, Mapping) else {}
    out = {key: src.get(key) for key in _SAFE_HOST_RESOURCE_KEYS if key in src}
    gpus = []
    for gpu in src.get("gpus") or []:
        if isinstance(gpu, Mapping):
            gpus.append({key: gpu.get(key) for key in _SAFE_GPU_KEYS if key in gpu})
    if "gpus" in out or gpus:
        out["gpus"] = gpus
        out["gpu_count"] = src.get("gpu_count", len(gpus))
    return out


def _normalize_host(host: Mapping[str, Any], *, now: float) -> dict[str, Any] | None:
    raw_id = host.get("host_id") or host.get("id") or host.get("name") or host.get("label") or host.get("slot")
    if raw_id is None:
        return None
    explicit_host_id = host.get("host_id")
    normalized_host_id = str(explicit_host_id) if explicit_host_id is not None else f"tern:{raw_id}"
    return {
        "host_id": normalized_host_id,
        "label": host.get("label") or host.get("name"),
        "mode": host.get("mode") if host.get("mode") in {"direct", "kubernetes", "remote"} else "remote",
        "status": host.get("status") or host.get("state") or "unknown",
        "rtt_ms": host.get("rtt_ms"),
        "resources": _safe_resources(host.get("resources")),
        "observed_at": host.get("observed_at") or now,
    }


def _normalize_session(session: Mapping[str, Any], *, now: float) -> dict[str, Any] | None:
    raw_id = session.get("session_id") or session.get("id")
    if raw_id is None:
        return None
    host_id = session.get("host_id") or session.get("host")
    if host_id is not None:
        host_id = str(host_id)
        if not host_id.startswith("tern:"):
            host_id = f"tern:{host_id}"
    status = session.get("status") or session.get("state")
    if status is None:
        if session.get("current") is True and session.get("locked") is True:
            status = "current_locked"
        elif session.get("current") is True:
            status = "current"
        elif session.get("locked") is True:
            status = "locked"
        else:
            status = "available"
    return {
        "session_id": str(raw_id),
        "name": session.get("name"),
        "host_id": host_id,
        "runtime": session.get("runtime") or session.get("program"),
        "status": status,
        "current": session.get("current"),
        "locked": session.get("locked"),
        "tab_count": session.get("tab_count") or session.get("tabs"),
        "rtt_ms": session.get("rtt_ms"),
        "pane_id": session.get("pane_id"),
        "session_sticky": True,
        "migration_allowed": False,
        "observed_at": session.get("observed_at") or now,
    }


def _normalize_tern(raw: Any, *, now: float) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return [], [], _source("missing", reason="tern_export_missing")
    hosts = []
    for host in raw.get("hosts") or []:
        if isinstance(host, Mapping) and (normalized := _normalize_host(host, now=now)):
            hosts.append(normalized)
    sessions = []
    for session in raw.get("sessions") or []:
        if isinstance(session, Mapping) and (normalized := _normalize_session(session, now=now)):
            sessions.append(normalized)
    observed_at = raw.get("observed_at") or raw.get("generated_at") or now
    status = "ok" if hosts or sessions else "degraded"
    return hosts, sessions, _source(status, observed_at=observed_at, reason=None if status == "ok" else "empty_tern_export")


def _safe_quota(quota: Any) -> dict[str, Any]:
    src = quota if isinstance(quota, Mapping) else {}
    out: dict[str, Any] = {}
    dimensions: dict[str, Any] = {}
    raw_dimensions = src.get("dimensions")
    if isinstance(raw_dimensions, Mapping):
        for name, dimension in raw_dimensions.items():
            if not isinstance(name, str) or not isinstance(dimension, Mapping):
                continue
            dimensions[name] = {
                key: dimension.get(key)
                for key in _SAFE_QUOTA_DIMENSION_KEYS
                if key in dimension
            }
    if dimensions:
        out["dimensions"] = dimensions
    for key in (
        "remaining_free_quota",
        "remaining_tokens",
        "remaining",
        "day_tokens_remaining",
        "limit",
        "daily_limit",
        "day_tokens_limit",
        "reset_at",
    ):
        if key in src:
            out[key] = src.get(key)
    return out


def _normalize_kerdoios(raw: Any, *, now: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(raw, Mapping):
        return [], _source("missing", reason="kerdoios_export_missing")
    entries = raw.get("entries") or []
    if isinstance(entries, Mapping):
        entries = list(entries.values())
    offers: list[dict[str, Any]] = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, Mapping):
            continue
        provider = entry.get("provider")
        model = entry.get("model")
        if not provider:
            continue
        offers.append(
            {
                "offer_id": str(entry.get("offer_id") or _stable_id("offer", "provider", provider, model)),
                "origin": "provider",
                "provider": provider,
                "model": model,
                "actor_id": model,
                "health": entry.get("health") or entry.get("status") or "unknown",
                "quota": _safe_quota(entry.get("quota")),
                "price": entry.get("price"),
                "predicted_cost": entry.get("predicted_cost"),
                "burn_rate": entry.get("burn_rate"),
                "time_to_exhaustion": entry.get("time_to_exhaustion"),
                "time_to_reset": entry.get("time_to_reset"),
                "reset_at": entry.get("reset_at"),
                "observed_at": entry.get("updated_at") or entry.get("observed_at") or now,
            }
        )
    observed_at = raw.get("saved_at") or raw.get("observed_at") or now
    return offers, _source(
        "ok" if offers else "degraded",
        observed_at=observed_at,
        reason=None if offers else "empty_kerdoios_export",
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
    rows = raw.get("leases") if isinstance(raw, Mapping) else raw
    if not isinstance(rows, list):
        return [], _source("degraded", reason="invalid_lease_projection")
    out = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        clean = {key: row.get(key) for key in _SAFE_LEASE_KEYS if key in row}
        if clean.get("placement_id"):
            out.append(clean)
    return out, _source("ok", observed_at=now)


def build_snapshot(
    *,
    tern: Any = None,
    kerdoios: Any = None,
    leases: Any = None,
    include_local: bool = True,
    now: float | None = None,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    hosts: list[dict[str, Any]] = []
    offers: list[dict[str, Any]] = []
    sources: dict[str, Any] = {}

    if include_local:
        local = probe_local_host(now=now)
        hosts.append(local)
        offers.append(_local_offer(local))
        sources["local"] = _source("ok", observed_at=now)

    tern_hosts, sessions, tern_source = _normalize_tern(tern, now=now)
    by_id = {host["host_id"]: host for host in hosts}
    for host in tern_hosts:
        by_id[host["host_id"]] = {**by_id.get(host["host_id"], {}), **host}
    hosts = list(by_id.values())
    sources["tern"] = tern_source

    provider_offers, kerdoios_source = _normalize_kerdoios(kerdoios, now=now)
    offers.extend(provider_offers)
    sources["kerdoios"] = kerdoios_source

    normalized_leases, lease_source = _normalize_leases(leases, now=now)
    sources["leases"] = lease_source

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
    tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
    payload = json.dumps(snapshot, indent=2, sort_keys=True, allow_nan=False) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, dest)
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
    )
    dest = write_snapshot(snapshot, Path(args.output).expanduser() if args.output else None)
    if args.json:
        print(json.dumps({**snapshot, "path": str(dest)}, indent=2, sort_keys=True))
    else:
        print(str(dest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
