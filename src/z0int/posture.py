"""Resource posture — is a budget perishing, on pace, or running dry?

v1 (this module + ``z0int.posture_history``): the burn rate is a recent EWMA
over the hourly snapshot log with the v0 window average as fallback; every
rate carries an uncertainty band and a BURN/OFFLOAD verdict is asserted only
when it holds across the band (see ``docs/resource-posture-v1.md``). The v0
pre-registration in ``docs/resource-posture.md`` is unchanged.

The factory is contextually blind about budgets without this: a frontier plan
whose weekly window resets at 03:00 tonight with 80% unused should be *burned*
on the highest-value work, while a pool that the observed burn rate will empty
two days before its reset should *offload* bounded work to fast/local/free
pools.

``evaluate`` is a pure, deterministic function of (pools, now, thresholds):

* per pool   ``projected_use_until_reset = burn_rate_per_hour * hours_until_reset``
             compared against ``remaining`` -> BURN / BALANCED / OFFLOAD / RESERVE
             with the arithmetic shown;
* per group  (one provider may expose several windows, e.g. Claude 5h + weekly)
             the binding window wins: OFFLOAD > RESERVE > BURN > BALANCED;
* factory    one recommendation (posture, prefer, avoid, offload targets).

Sources (``collect_pools``), all read-only and local, none authoritative over
the provider:

* ``codexbar`` last-good cache (``~/.cache/codexbar-waybar/last.json``) written
  by the usage-island Waybar module — the existing Claude/Codex/Cursor plan
  usage + reset tracker on this host (we do not add a second tracker);
* Kerdoios free inventory cache (``~/.cache/kerdoios/inventory.json``) when the
  ``kerdoios`` package is importable — offload targets, never network;
* host config ``~/.z0int/config/posture.local.json`` — manual pools/overrides
  and ``posture_enforce`` (default false: shadow only).
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "z0int.resource_posture.v1"
LOG_SCHEMAS = ("z0int.resource_posture.v0.log", "z0int.resource_posture.v1.log")
POSTURES = ("BURN", "BALANCED", "OFFLOAD", "RESERVE")
KINDS = ("frontier", "fast", "local", "free", "paid")
# Binding-constraint precedence when one group exposes several windows.
_GROUP_PRECEDENCE = {"OFFLOAD": 3, "RESERVE": 2, "BURN": 1, "BALANCED": 0}
# Offload-target preference: fast hosted first, then local, then free catalog.
_TARGET_ORDER = {"fast": 0, "local": 1, "free": 2}
# Config keys that are settings, not pool overrides.
_SETTINGS = {"posture_enforce", "thresholds", "pools", "sources", "aliases", "rate"}
# Friendly names a host config may use for a codexbar provider group.
DEFAULT_ALIASES = {"claude-max": "claude", "claude-pro": "claude", "chatgpt-pro": "codex", "cursor-ultra": "cursor"}
# codexbar providers and the pool kind they represent.
CODEXBAR_KINDS = {"claude": "frontier", "codex": "frontier", "cursor": "frontier", "grok": "frontier",
                  "gemini": "frontier", "copilot": "frontier", "antigravity": "frontier", "openrouter": "paid"}
FAST_PROVIDERS = {"cerebras", "groq"}


@dataclass(frozen=True)
class Thresholds:
    # projected/remaining at or above this -> the pool runs out before reset -> OFFLOAD
    offload_ratio: float = 1.0
    # projected/remaining at or above this (but < offload_ratio) -> tight -> RESERVE
    reserve_ratio: float = 0.85
    # BURN only when the reset is this close (surplus is about to perish) ...
    burn_horizon_hours: float = 24.0
    # ... and at least this fraction of capacity is projected to be left unused
    burn_min_surplus_frac: float = 0.20
    # rates observed over less than this are low-confidence (no BURN/OFFLOAD verdict)
    min_observation_hours: float = 0.5
    # observations older than this are stale (no BURN/OFFLOAD verdict)
    max_observation_age_hours: float = 6.0
    # non-perishable pools with less runway than this -> RESERVE
    min_runway_hours: float = 72.0

    @classmethod
    def from_dict(cls, raw: Any) -> "Thresholds":
        if not isinstance(raw, dict):
            return cls()
        known = {k: float(v) for k, v in raw.items() if k in cls.__dataclass_fields__ and isinstance(v, (int, float))}
        return cls(**known)


@dataclass
class Pool:
    id: str
    kind: str
    group: str | None = None
    unit: str = "percent"
    capacity: float | None = None
    remaining: float | None = None
    resets_at: str | None = None  # ISO-8601; None = non-perishable
    window_hours: float | None = None
    burn_rate_per_hour: float | None = None  # unit/hour
    rate_observed_hours: float | None = None  # how long the rate was observed over
    rate_source: str | None = None
    observed_at: str | None = None
    source: str = "manual"
    note: str | None = None
    rate_lo: float | None = None  # uncertainty band on burn_rate_per_hour (v1)
    rate_hi: float | None = None
    rate_window_avg: float | None = None  # the v0 single-snapshot rate, kept for comparison when EWMA wins
    prev_posture: str | None = None  # this window's asserted posture at the previous snapshot (hysteresis)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Pool":
        fields = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
        pool = cls(**fields)
        if pool.kind not in KINDS:
            raise ValueError(f"pool {pool.id}: kind must be one of {KINDS}")
        return pool


# ---------------------------------------------------------------------------
# time helpers
# ---------------------------------------------------------------------------


def parse_time(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _r(x: float | None, n: int = 2) -> float | None:
    return None if x is None else round(x, n)


def _hours(h: float | None) -> str:
    if h is None:
        return "?"
    return f"{h / 24:.1f}d" if h >= 48 else f"{h:.1f}h"


def _amt(x: float | None, unit: str) -> str:
    if x is None:
        return "?"
    return f"{x:.1f}%" if unit == "percent" else f"{x:.2f} {unit}"


# ---------------------------------------------------------------------------
# pure evaluation
# ---------------------------------------------------------------------------


def _classify(remaining: float, rate: float, hours_left: float, cap: float | None,
              th: Thresholds) -> tuple[str, str, float, float, float]:
    """Perishable-pool verdict at one rate: (posture, reason, projected, surplus, ratio)."""
    projected = rate * hours_left
    surplus = remaining - projected
    ratio = projected / remaining
    surplus_frac = surplus / cap if cap else None
    if ratio >= th.offload_ratio:
        return "OFFLOAD", "runs_out_before_reset", projected, surplus, ratio
    if ratio >= th.reserve_ratio:
        return "RESERVE", "tight_until_reset", projected, surplus, ratio
    if hours_left <= th.burn_horizon_hours and surplus_frac is not None and surplus_frac >= th.burn_min_surplus_frac:
        return "BURN", "surplus_perishes_at_reset", projected, surplus, ratio
    return "BALANCED", "on_pace", projected, surplus, ratio


def evaluate_pool(pool: Pool, now: datetime, th: Thresholds = Thresholds()) -> dict[str, Any]:
    """Posture for one pool, with every number that produced it.

    v1 band gating: when the pool carries a rate band (``rate_lo``/``rate_hi``), BURN is asserted only if it
    also holds at ``rate_hi`` (surplus survives the fastest plausible burn) and OFFLOAD only if it also holds
    at ``rate_lo`` (runs out even at the slowest plausible burn). A BURN the band cannot confirm degrades to
    BALANCED; an OFFLOAD it cannot confirm degrades to RESERVE (possibly tight). Without a band, v0 rules.

    Hysteresis: entering BURN/OFFLOAD needs the band; *staying* in it (``prev_posture`` equal to the central
    verdict for the same window) needs only the central estimate, so a rate jittering at a threshold edge
    does not flip the posture every hour.
    """
    reset = parse_time(pool.resets_at)
    hours_left = (reset - now).total_seconds() / 3600 if reset else None
    observed = parse_time(pool.observed_at)
    age_h = (now - observed).total_seconds() / 3600 if observed else None
    rate = pool.burn_rate_per_hour
    out: dict[str, Any] = {
        "id": pool.id, "kind": pool.kind, "group": pool.group or pool.id, "unit": pool.unit, "source": pool.source,
        "capacity": pool.capacity, "remaining": _r(pool.remaining), "resets_at": pool.resets_at,
        "window_hours": pool.window_hours, "hours_until_reset": _r(hours_left), "burn_rate_per_hour": _r(rate, 4),
        "rate_lo": _r(pool.rate_lo, 4), "rate_hi": _r(pool.rate_hi, 4), "rate_window_avg": _r(pool.rate_window_avg, 4),
        "rate_observed_hours": _r(pool.rate_observed_hours), "rate_source": pool.rate_source,
        "observation_age_hours": _r(age_h), "projected_use_until_reset": None, "projected_surplus_at_reset": None,
        "projected_surplus_band": None, "ratio": None, "runway_hours": None, "confidence": "ok",
    }
    if pool.note:
        out["note"] = pool.note

    def done(posture: str, arithmetic: str, reason: str) -> dict[str, Any]:
        out.update(posture=posture, arithmetic=arithmetic, reason=reason)
        return out

    unit = pool.unit
    if pool.remaining is None:
        return done("BALANCED", "unmetered: no perishable quota observed", "unmetered_capacity")
    if reset is not None and hours_left is not None and hours_left <= 0:
        out["confidence"] = "stale"
        return done("BALANCED", f"observation predates reset at {pool.resets_at}; current remaining unknown",
                    "observation_predates_reset")
    if pool.remaining <= 0:
        return done("OFFLOAD", f"remaining {_amt(pool.remaining, unit)} = exhausted"
                    + (f" for {_hours(hours_left)} until reset" if hours_left is not None else ""), "exhausted")
    if rate is not None and rate > 0:
        out["runway_hours"] = _r(pool.remaining / rate)
    if rate is None:
        return done("BALANCED", f"remaining {_amt(pool.remaining, unit)}; no burn-rate observation", "no_rate_observation")

    low_conf = pool.rate_observed_hours is not None and pool.rate_observed_hours < th.min_observation_hours
    stale = age_h is not None and age_h > th.max_observation_age_hours
    if low_conf:
        out["confidence"] = "low"
    if stale:
        out["confidence"] = "stale"

    band_why = None
    if reset is None:  # non-perishable (credits): runway, not expiry, is what matters
        runway = out["runway_hours"]
        arithmetic = (f"non-perishable: remaining {_amt(pool.remaining, unit)} / rate {rate:.3g}/h = runway "
                      f"{_hours(runway)}" if runway is not None else f"non-perishable: remaining {_amt(pool.remaining, unit)}, rate 0")
        if runway is not None and runway < th.min_runway_hours:
            posture, reason = "RESERVE", "short_runway_nonperishable"
        else:
            posture, reason = "BALANCED", "nonperishable"
    else:
        cap = pool.capacity or (100.0 if unit == "percent" else None)
        posture, reason, projected, surplus, ratio = _classify(pool.remaining, rate, hours_left, cap, th)
        out.update(projected_use_until_reset=_r(projected), projected_surplus_at_reset=_r(surplus), ratio=_r(ratio, 3))
        arithmetic = (f"projected {rate:.3g}/h x {_hours(hours_left)} = {_amt(projected, unit)} vs remaining "
                      f"{_amt(pool.remaining, unit)} (ratio {ratio:.2f})")
        if pool.rate_lo is not None and pool.rate_hi is not None:
            s_hi = pool.remaining - pool.rate_lo * hours_left  # slow burn -> most surplus
            s_lo = pool.remaining - pool.rate_hi * hours_left
            out["projected_surplus_band"] = [_r(s_lo), _r(s_hi)]
            arithmetic += f" [rate band {pool.rate_lo:.3g}-{pool.rate_hi:.3g}/h -> surplus {_amt(s_lo, unit)}..{_amt(s_hi, unit)}]"
            if posture == "BURN" and _classify(pool.remaining, pool.rate_hi, hours_left, cap, th)[0] != "BURN":
                band_why, degraded = "BURN fails at the band's high rate", "BALANCED"
            elif posture == "OFFLOAD" and _classify(pool.remaining, pool.rate_lo, hours_left, cap, th)[0] != "OFFLOAD":
                band_why, degraded = "OFFLOAD fails at the band's low rate", "RESERVE"
        if posture == "OFFLOAD":
            arithmetic += f"; runs out in {_hours(out['runway_hours'])}, {_hours(hours_left - out['runway_hours'])} before reset"
        elif posture == "BURN":
            arithmetic += f"; {_amt(surplus, unit)} perishes at reset in {_hours(hours_left)}"
    if (low_conf or stale) and posture in ("BURN", "OFFLOAD"):
        out["unconfirmed_posture"] = posture
        why = "rate observed < %.1fh" % th.min_observation_hours if low_conf else "observation older than %.0fh" % th.max_observation_age_hours
        return done("BALANCED", arithmetic + f"; {posture} not asserted ({why})", reason + "_unconfirmed")
    if band_why and pool.prev_posture == posture:
        out["confidence"] = "band_held"
        return done(posture, arithmetic + f"; held from previous snapshot ({band_why})", reason + "_held")
    if band_why:
        out["unconfirmed_posture"] = posture
        out["confidence"] = "band"
        return done(degraded, arithmetic + f"; {posture} not asserted ({band_why})", reason + "_band_unconfirmed")
    return done(posture, arithmetic, reason)


def _group_postures(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        # Binding window: highest-precedence posture; ties -> longest window (the plan budget, e.g. weekly
        # over 5h), then the more constrained pool (higher ratio; unobserved last), then id (rows arrive id-sorted).
        ratio = row.get("ratio")
        return (_GROUP_PRECEDENCE[row["posture"]], row.get("window_hours") or 0, -1.0 if ratio is None else ratio)

    groups: dict[str, dict[str, Any]] = {}
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        g = groups.setdefault(row["group"], {"group": row["group"], "kind": row["kind"], "pools": []})
        g["pools"].append(row["id"])
        if row["kind"] == "frontier":
            g["kind"] = "frontier"
        if row["group"] not in best or key(row) > key(best[row["group"]]):
            best[row["group"]] = row
    for name, g in groups.items():
        g["posture"], g["binding_pool"] = best[name]["posture"], best[name]["id"]
    return groups


def recommend(groups: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_id = {r["id"]: r for r in rows}
    frontier = sorted(g for g, v in groups.items() if v["kind"] == "frontier")
    of = lambda p: [g for g in frontier if groups[g]["posture"] == p]  # noqa: E731
    burn, balanced, offload, reserve = of("BURN"), of("BALANCED"), of("OFFLOAD"), of("RESERVE")
    targets = sorted((g for g, v in groups.items() if v["kind"] in _TARGET_ORDER and v["posture"] != "OFFLOAD"),
                     key=lambda g: (_TARGET_ORDER[groups[g]["kind"]], not g.startswith("route:"), g))
    if not frontier:
        return {"posture": "BALANCED", "prefer": [], "avoid": [], "offload_targets": targets,
                "action": "no frontier budget observed; route by task policy", "rule": "no_frontier_pools"}
    if burn:
        resets = sorted((by_id[groups[g]["binding_pool"]]["resets_at"] or "", g) for g in burn)
        surplus = [f"{g} {_amt(by_id[groups[g]['binding_pool']]['projected_surplus_at_reset'], by_id[groups[g]['binding_pool']]['unit'])}"
                   f" by {by_id[groups[g]['binding_pool']]['resets_at']}" for g in burn]
        return {"posture": "BURN", "prefer": burn, "avoid": offload, "offload_targets": targets,
                "earliest_reset": resets[0][0] or None,
                "action": ("spend frontier on the highest-value work now; unused " + ", ".join(surplus)
                           + " perishes. Do not offload work these pools can absorb."
                           + (f" Avoid {', '.join(offload)} (exhausted/over pace)." if offload else "")),
                "rule": "any_frontier_BURN"}
    if not balanced:
        if targets:
            return {"posture": "OFFLOAD", "prefer": targets, "avoid": offload + reserve, "offload_targets": targets,
                    "action": "every frontier pool is over pace or tight; route bounded/cheap work to "
                              + ", ".join(targets) + " and keep frontier for work only it can do",
                    "rule": "no_frontier_BALANCED_with_targets"}
        return {"posture": "RESERVE", "prefer": [], "avoid": offload, "offload_targets": [],
                "action": "every frontier pool is over pace or tight and no offload target is observed; ration frontier",
                "rule": "no_frontier_BALANCED_no_targets"}
    return {"posture": "BALANCED", "prefer": balanced, "avoid": offload + reserve, "offload_targets": targets,
            "action": "frontier on pace" + (f"; prefer {', '.join(balanced)} over {', '.join(offload + reserve)}"
                                            if offload or reserve else ""),
            "rule": "frontier_on_pace"}


def evaluate(pools: list[Pool | dict[str, Any]], now: datetime, thresholds: Thresholds | None = None,
             *, enforce: bool = False, sources: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Pure: pools + clock -> per-pool posture, per-group posture, factory recommendation."""
    th = thresholds or Thresholds()
    objs = [p if isinstance(p, Pool) else Pool.from_dict(p) for p in pools]
    rows = [evaluate_pool(p, now, th) for p in sorted(objs, key=lambda p: p.id)]
    groups = _group_postures(rows)
    factory = recommend(groups, rows)
    return {
        "schema": SCHEMA, "now": _iso(now), "factory": factory,
        "groups": {g: groups[g] for g in sorted(groups)}, "pools": rows,
        "thresholds": asdict(th), "enforce": bool(enforce), "sources": sources or [],
        "revision": revision_of(factory, groups),
    }


def revision_of(factory: dict[str, Any], groups: dict[str, dict[str, Any]]) -> str:
    """Stable while postures are unchanged (numbers drift every minute; verdicts do not)."""
    key = {"factory": factory["posture"], "groups": {g: v["posture"] for g, v in groups.items()}}
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# sources (read-only, fail-open)
# ---------------------------------------------------------------------------


def codexbar_path() -> Path:
    return Path(os.environ.get("Z0INT_POSTURE_CODEXBAR") or Path.home() / ".cache" / "codexbar-waybar" / "last.json")


def config_path() -> Path:
    from . import paths
    return paths.home() / "config" / "posture.local.json"


def load_config(path: Path | None = None) -> dict[str, Any]:
    try:
        raw = json.loads((path or config_path()).read_text())
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _window_label(minutes: Any, fallback: str) -> str:
    if not isinstance(minutes, (int, float)) or minutes <= 0:
        return fallback
    if minutes % 10080 == 0:
        return "weekly" if minutes == 10080 else f"{int(minutes // 10080)}w"
    if minutes % 1440 == 0:
        return f"{int(minutes // 1440)}d"
    return f"{int(minutes // 60)}h" if minutes % 60 == 0 else f"{int(minutes)}m"


def pools_from_codexbar(rows: Any, now: datetime) -> list[Pool]:
    """Map codexbar provider snapshots to pools. Only rate windows with a reset (or credit balances) become pools;
    identity fields (account e-mail, org ids) are never copied."""
    pools: list[Pool] = []
    if not isinstance(rows, list):
        return pools
    for entry in rows:
        if not isinstance(entry, dict) or not isinstance(entry.get("usage"), dict):
            continue
        provider = str(entry.get("provider") or "")
        kind = CODEXBAR_KINDS.get(provider)
        if not kind:
            continue
        usage = entry["usage"]
        updated = usage.get("updatedAt")
        windows: list[tuple[str, dict[str, Any]]] = []
        for slot in ("primary", "secondary", "tertiary"):
            w = usage.get(slot)
            if isinstance(w, dict):
                windows.append((slot, w))
        for extra in usage.get("extraRateWindows") or []:
            if isinstance(extra, dict) and isinstance(extra.get("window"), dict):
                windows.append((str(extra.get("id") or "extra").removeprefix(provider + "-"), extra["window"]))
        seen: set[tuple[Any, Any, Any]] = set()
        ids: set[str] = set()
        for slot, w in windows:
            used = w.get("usedPercent")
            if not isinstance(used, (int, float)) or isinstance(used, bool):
                continue
            reset, minutes = w.get("resetsAt"), w.get("windowMinutes")
            if kind != "paid" and not reset:
                continue  # a rate window without a reset time is not a perishable pool we can reason about
            standard = slot in ("primary", "secondary", "tertiary")
            if standard:
                if (reset, minutes, used) in seen:
                    continue  # cursor reports identical primary/secondary/tertiary windows
                seen.add((reset, minutes, used))
            label = _window_label(minutes, slot) if standard else slot
            if f"{provider}:{label}" in ids:
                label = f"{label}-{slot}"
            ids.add(f"{provider}:{label}")
            rate = observed_h = None
            source_rate = None
            reset_dt = parse_time(reset)
            if kind == "paid" and not reset:
                pools.append(Pool(id=f"{provider}:credits", kind=kind, group=provider, unit="percent", capacity=100.0,
                                  remaining=max(0.0, 100.0 - float(used)), resets_at=None, observed_at=updated,
                                  source="codexbar", note=str(w.get("resetDescription") or "")[:60] or None))
                continue
            if reset_dt and isinstance(minutes, (int, float)) and minutes > 0:
                observed_at = parse_time(updated) or now
                observed_h = minutes / 60 - (reset_dt - observed_at).total_seconds() / 3600
                if observed_h > 0:
                    rate = float(used) / observed_h
                    source_rate = "window_average_since_window_start"
            pools.append(Pool(id=f"{provider}:{label}", kind=kind, group=provider, unit="percent", capacity=100.0,
                              remaining=max(0.0, 100.0 - float(used)), resets_at=reset, window_hours=_r(minutes / 60)
                              if isinstance(minutes, (int, float)) else None, burn_rate_per_hour=rate,
                              rate_observed_hours=_r(observed_h), rate_source=source_rate, observed_at=updated,
                              source="codexbar"))
    return pools


def _import_kerdoios_cache():
    try:
        from kerdoios.cache import load_inventory_cache  # type: ignore
        return load_inventory_cache
    except ImportError:
        pass
    for root in (os.environ.get("Z0INT_KERDOIOS_ROOT"), str(Path.home() / ".hermes" / "plugins" / "kerdoios")):
        if root and (Path(root) / "kerdoios" / "cache.py").is_file():
            sys.path.insert(0, root)
            try:
                from kerdoios.cache import load_inventory_cache  # type: ignore
                return load_inventory_cache
            except ImportError:
                return None
            finally:
                sys.path.remove(root)
    return None


def pools_from_kerdoios(offers: Any) -> list[Pool]:
    """Kerdoios free inventory -> unmetered offload-target pools, one per provider (never network)."""
    by_provider: dict[str, dict[str, Any]] = {}
    for o in offers or []:
        provider = getattr(o, "provider", None)
        if not provider:
            continue
        econ = getattr(o, "economics", None)
        kind = "local" if getattr(o, "local", False) else "fast" if provider in FAST_PROVIDERS else "free"
        row = by_provider.setdefault(provider, {"kind": kind, "models": 0, "quota": 0.0, "reset_s": None})
        row["models"] += 1
        if econ is not None and getattr(econ, "remaining_free_quota", 0):
            row["quota"] += float(econ.remaining_free_quota)
            row["reset_s"] = getattr(econ, "seconds_until_quota_reset", None) or row["reset_s"]
    pools = []
    for provider, row in sorted(by_provider.items()):
        # Kerdoios quota figures are provider claims/placeholders, not observed usage: record them, do not meter.
        pools.append(Pool(id=f"kerdoios:{provider}", kind=row["kind"], group=f"kerdoios:{provider}", unit="tokens",
                          remaining=None, source="kerdoios.inventory_cache",
                          note=f"{row['models']} free model(s)" + (f", claimed quota {int(row['quota'])}" if row["quota"] else "")))
    return pools


def pools_from_worker_routes() -> list[Pool]:
    """Offload targets the z0int router can actually use: validated $0 routes (incl. host-local ones)."""
    from .worker_routing import configuration

    policy, providers = configuration()
    pools = []
    for route in policy.get("validated_free_routes") or []:
        provider, model = route.get("provider"), route.get("model")
        if not provider or route.get("validated") is not True or route.get("price_usd") != 0:
            continue
        cohort = (providers.get(provider) or {}).get("cohort")
        kind = "local" if cohort == "local" else "fast" if provider in FAST_PROVIDERS else "free"
        pid = f"route:{provider}"
        if any(p.id == pid for p in pools):
            continue
        pools.append(Pool(id=pid, kind=kind, group=pid, unit="tokens", remaining=None, source="worker_routing",
                          note=f"validated $0 route {model}"))
    return pools


def apply_overrides(pools: list[Pool], config: dict[str, Any]) -> tuple[list[Pool], list[str]]:
    """Manual host overrides. Key = pool id, else group (or alias) -> that group's longest window."""
    aliases = {**DEFAULT_ALIASES, **(config.get("aliases") if isinstance(config.get("aliases"), dict) else {})}
    entries: dict[str, Any] = dict(config.get("pools") or {}) if isinstance(config.get("pools"), dict) else {}
    entries.update({k: v for k, v in config.items() if k not in _SETTINGS and isinstance(v, dict)})
    applied: list[str] = []
    by_id = {p.id: p for p in pools}
    for key, override in entries.items():
        target = by_id.get(key)
        if target is None:
            group = aliases.get(key, key)
            members = [p for p in pools if (p.group or p.id) == group]
            if members:
                target = min(members, key=lambda p: (-(p.window_hours or 0), p.id))  # longest window, plain id
        fields = {k: v for k, v in override.items() if k in Pool.__dataclass_fields__ and k not in ("id", "source")}
        if target is None:
            fields.setdefault("kind", "frontier")
            try:
                pools.append(Pool.from_dict({**fields, "id": key, "source": "manual"}))
                applied.append(f"{key}: manual pool")
            except (TypeError, ValueError) as exc:
                applied.append(f"{key}: rejected ({exc})")
            continue
        changed = []
        for k, v in fields.items():
            if getattr(target, k) != v:
                setattr(target, k, v)
                changed.append(k)
        if changed:
            target.source = f"{target.source}+manual"
        applied.append(f"{key} -> {target.id}: " + (", ".join(changed) if changed else "no change"))
    return pools, applied


def collect_pools(now: datetime, *, config: dict[str, Any] | None = None, codexbar: Path | None = None,
                  kerdoios: bool = True) -> tuple[list[Pool], list[dict[str, Any]]]:
    cfg = load_config() if config is None else config
    pools: list[Pool] = []
    sources: list[dict[str, Any]] = []
    path = codexbar or codexbar_path()
    try:
        rows = json.loads(path.read_text())
        found = pools_from_codexbar(rows, now)
        pools += found
        sources.append({"source": "codexbar", "path": str(path), "status": "ok", "pools": len(found)})
    except (OSError, ValueError) as exc:
        sources.append({"source": "codexbar", "path": str(path), "status": f"unavailable: {type(exc).__name__}"})
    if (cfg.get("sources") or {}).get("worker_routing", True) is not False:
        try:
            found = pools_from_worker_routes()
            pools += found
            sources.append({"source": "worker_routing", "status": "ok", "pools": len(found)})
        except Exception as exc:  # fail-open
            sources.append({"source": "worker_routing", "status": f"error: {type(exc).__name__}"})
    if kerdoios and (cfg.get("sources") or {}).get("kerdoios", True) is not False:
        loader = _import_kerdoios_cache()
        if loader is None:
            sources.append({"source": "kerdoios", "status": "not_importable"})
        else:
            try:
                found = pools_from_kerdoios(loader(ignore_ttl=True) or [])
                pools += found
                sources.append({"source": "kerdoios", "status": "ok" if found else "empty_cache", "pools": len(found)})
            except Exception as exc:  # fail-open: a foreign cache must never break posture
                sources.append({"source": "kerdoios", "status": f"error: {type(exc).__name__}"})
    pools, applied = apply_overrides(pools, cfg)
    sources.append({"source": "host_config", "path": str(config_path()), "status": "ok" if cfg else "absent",
                    "applied": applied})
    return pools, sources


def current_posture(now: datetime | None = None, *, config: dict[str, Any] | None = None,
                    codexbar: Path | None = None, kerdoios: bool = True,
                    history: list[dict[str, Any]] | Path | None = None) -> dict[str, Any]:
    """Live posture. ``history``: snapshot rows, a history.jsonl path, or None for the default log."""
    from .posture_history import RateConfig, apply_recent_rates, load_history, previous_postures

    now = now or datetime.now(timezone.utc)
    cfg = load_config() if config is None else config
    pools, sources = collect_pools(now, config=cfg, codexbar=codexbar, kerdoios=kerdoios)
    rate_cfg = RateConfig.from_dict(cfg.get("rate"))
    rows: list[dict[str, Any]] = []
    if rate_cfg.method == "ewma":
        from datetime import timedelta
        since = now - timedelta(hours=rate_cfg.lookback_hours)
        if isinstance(history, list):
            rows = [r for r in history if (t := parse_time(r.get("now"))) is not None and since <= t <= now]
        else:
            rows = load_history(history, since=since, until=now)
    notes = apply_recent_rates(pools, rows, now, rate_cfg)
    if rate_cfg.band and rows:
        previous_postures(pools, rows[-1], rate_cfg)
    sources.append({"source": "posture_history", "status": f"{rate_cfg.method}: {len(rows)} snapshot(s)",
                    "rates": notes})
    out = evaluate(pools, now, Thresholds.from_dict(cfg.get("thresholds")),
                   enforce=cfg.get("posture_enforce") is True, sources=sources)
    out["rate"] = rate_cfg.as_dict()
    return out


def shadow_annotation(route_kind: str = "offload") -> dict[str, Any]:
    """Fail-open annotation for a routing decision. ``route_kind`` = what the router is about to do:
    ``offload`` (cheap/local worker) or ``frontier``. Never raises."""
    try:
        p = current_posture()
    except Exception as exc:
        return {"schema": SCHEMA, "available": False, "error": type(exc).__name__, "enforce": False}
    fac = p["factory"]
    agrees = not ((fac["posture"] == "BURN" and route_kind == "offload")
                  or (fac["posture"] == "OFFLOAD" and route_kind == "frontier"))
    return {"schema": SCHEMA, "available": True, "factory_posture": fac["posture"], "prefer": fac["prefer"],
            "avoid": fac["avoid"], "revision": p["revision"], "route_kind": route_kind, "agrees": agrees,
            "enforce": p["enforce"], "action": fac["action"][:240]}


# ---------------------------------------------------------------------------
# Claude Code hint (one line, emitted by the hook adapter only when the key changes)
# ---------------------------------------------------------------------------


def hint_key(p: dict[str, Any]) -> str:
    """What a hint is about: factory verdict + frontier group verdicts. Numbers never change it."""
    groups = ",".join(f"{g}={v['posture']}" for g, v in p["groups"].items() if v["kind"] == "frontier")
    return f"{p['factory']['posture']}|{groups}"


def hint_line(p: dict[str, Any], previous_key: str | None = None) -> str:
    """One line for Claude Code's context. Bounded (~300 chars), no identities, advisory wording."""
    fac = p["factory"]
    rows = {r["id"]: r for r in p["pools"]}

    def pool_bit(group: str) -> str:
        g = p["groups"].get(group) or {}
        r = rows.get(g.get("binding_pool")) or {}
        label = r.get("id", group)
        if r.get("posture") == "OFFLOAD" and (r.get("remaining") or 0) <= 0:
            return f"{label} exhausted"
        if r.get("projected_surplus_at_reset") is not None and r.get("hours_until_reset") is not None:
            return (f"{label} ~{max(0.0, r['projected_surplus_at_reset']):.0f}% projected unused at reset in "
                    f"{_hours(r['hours_until_reset'])}")
        return label

    posture = fac["posture"]
    if posture == "BURN":
        body = ("BURN: " + "; ".join(pool_bit(g) for g in fac["prefer"][:2])
                + ". Frontier quota perishes at reset: do bounded work directly rather than offloading it.")
    elif posture == "OFFLOAD":
        targets = ", ".join(t.removeprefix("route:") for t in fac.get("offload_targets", [])[:3])
        body = ("OFFLOAD: " + "; ".join(pool_bit(g) for g in fac.get("avoid", [])[:2])
                + ". Frontier over pace: send bounded/cheap subtasks to route_worker"
                + (f" ($0 routes: {targets})" if targets else "") + ".")
    elif posture == "RESERVE":
        body = "RESERVE: frontier tight and no offload target observed; keep turns lean."
    else:
        body = "BALANCED: frontier on pace" + (f" (was {previous_key.split('|', 1)[0]})" if previous_key else "") + "."
    return f"[z0 resource posture] {body}"[:320]


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render_human(p: dict[str, Any]) -> str:
    fac = p["factory"]
    lines = [f"RESOURCE POSTURE @ {p['now']}  (enforce={'on' if p['enforce'] else 'off: shadow only'})",
             f"FACTORY: {fac['posture']} — {fac['action']}"]
    if fac.get("offload_targets"):
        lines.append("  offload targets: " + ", ".join(fac["offload_targets"]))
    lines.append("")
    lines.append(f"{'group':<22}{'posture':<10}binding pool")
    metered = {r["group"] for r in p["pools"] if r["remaining"] is not None}
    for g, v in p["groups"].items():
        if g not in metered:
            continue
        lines.append(f"{g:<22}{v['posture']:<10}{v['binding_pool']}")
    lines.append("")
    for r in p["pools"]:
        if r["remaining"] is None:
            continue
        lines.append(f"- {r['id']:<30} {r['posture']:<9} {r['arithmetic']}")
    unmetered = [r["id"] for r in p["pools"] if r["remaining"] is None]
    if unmetered:
        lines.append(f"- unmetered: {', '.join(unmetered)}")
    lines.append("")
    lines.append("sources: " + "; ".join(f"{s['source']}={s['status']}" for s in p["sources"]))
    for s in p["sources"]:
        for a in s.get("applied") or []:
            lines.append(f"  override {a}")
    return "\n".join(lines)


def render_line(p: dict[str, Any]) -> str:
    """One line for the State Packet NOW block."""
    fac = p["factory"]
    groups = ", ".join(f"{g}={v['posture']}" for g, v in p["groups"].items() if v["kind"] in ("frontier", "paid"))
    return f"{fac['posture']} ({groups}) — {fac['action']}"


def history_path() -> Path:
    from . import paths
    return paths.home() / "state" / "posture" / "history.jsonl"


def log_snapshot(p: dict[str, Any], path: Path | None = None) -> Path:
    """Append a compact snapshot (no identities) — the evidence stream the pre-registered evaluation replays."""
    path = path or history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"schema": SCHEMA + ".log", "now": p["now"], "revision": p["revision"], "factory": p["factory"]["posture"],
           "groups": {g: v["posture"] for g, v in p["groups"].items()},
           "pools": [{k: r.get(k) for k in ("id", "kind", "group", "posture", "remaining", "resets_at", "window_hours",
                                            "burn_rate_per_hour", "rate_lo", "rate_hi", "rate_window_avg",
                                            "rate_source", "projected_surplus_at_reset", "confidence",
                                            "unconfirmed_posture")}
                     for r in p["pools"] if r["remaining"] is not None]}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="z0int posture", description="Resource posture: BURN / BALANCED / OFFLOAD / RESERVE")
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--now", default=None, help="ISO time to evaluate at (default: now)")
    ap.add_argument("--codexbar", default=None, help="codexbar last.json path")
    ap.add_argument("--config", default=None, help="posture config path (default ~/.z0int/config/posture.local.json)")
    ap.add_argument("--no-kerdoios", action="store_true")
    ap.add_argument("--log", action="store_true", help="append a snapshot to ~/.z0int/state/posture/history.jsonl")
    a = ap.parse_args(argv)
    now = parse_time(a.now) if a.now else None
    if a.now and now is None:
        ap.error("--now must be ISO-8601")
    cfg = load_config(Path(a.config).expanduser()) if a.config else None
    p = current_posture(now, config=cfg, codexbar=Path(a.codexbar).expanduser() if a.codexbar else None,
                        kerdoios=not a.no_kerdoios)
    if a.log:
        log_snapshot(p)
    print(json.dumps(p, indent=2) if a.as_json else render_human(p))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
