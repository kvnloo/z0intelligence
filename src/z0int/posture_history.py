"""Resource posture v1 — recent burn rate from the snapshot history.

v0 had one rate per pool: the window average since the window started
(``used / elapsed``). A single codexbar snapshot supports nothing better, and it
is blind to bursts: on 2026-09-30 Claude weekly went 81% -> 70% remaining in
4.4h (~2.5 pp/h) while the window average read 0.12-0.19 pp/h.

v1 reads the hourly snapshot log (``~/.z0int/state/posture/history.jsonl``,
v0 and v1 rows alike) and estimates a *recent* rate per pool:

* the current reset window only (a reset or a remaining% that jumps up starts
  a new window, so pre-reset usage never leaks into the new window);
* points are thinned to >= ``min_interval_hours`` apart (newest kept), then
  each consecutive pair is an interval with usage
  ``max(0, remaining[i-1] - remaining[i])`` over ``dt`` hours;
* EWMA by interval age: ``w = exp(-(now - interval_midpoint) / horizon_hours)``,
  ``rate = sum(w * used) / sum(w * dt)`` (a usage-weighted mean, so a long
  quiet gap and a short burst are weighed by the time they cover);
* an uncertainty band from interval dispersion (net of the variance that
  whole-percent rounding alone produces) plus the quantization of the whole
  span: ``half = z * se + quantum / weighted_hours``;
* gating: fewer than ``min_intervals`` intervals, or less than
  ``min_span_hours`` of history in the window -> fall back to the window
  average (with its own quantization band), and say so in ``rate_source``.

Everything here is pure over (points, now, config) except ``load_history``.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

RATE_METHODS = ("ewma", "window_average")


@dataclass(frozen=True)
class RateConfig:
    # "ewma" (v1 default) or "window_average" (v0 behaviour, no history)
    method: str = "ewma"
    # EWMA time constant: an interval this old weighs 1/e of a current one
    horizon_hours: float = 6.0
    # ignore snapshot history older than this
    lookback_hours: float = 48.0
    # need at least this many intervals (>= min_intervals + 1 points) in the current window ...
    min_intervals: int = 2
    # ... spanning at least this many hours, else fall back to the window average
    min_span_hours: float = 1.0
    # points closer than this to the next kept point are dropped (newest kept): with whole-percent usage a
    # 10-minute interval's rate is mostly quantization noise and would blow up the dispersion band
    min_interval_hours: float = 0.75
    # usage is reported in whole percent; each endpoint can be off by this much
    quantum: float = 1.0
    # band width in standard errors
    z: float = 1.96
    # attach a rate band (v1 gating). False reproduces v0 exactly: no band, no band gating.
    band: bool = True
    # two resets_at values closer than this belong to the same window (5h windows jitter by seconds)
    reset_tolerance_minutes: float = 15.0

    @classmethod
    def from_dict(cls, raw: Any) -> "RateConfig":
        if not isinstance(raw, dict):
            return cls()
        known: dict[str, Any] = {}
        for k, v in raw.items():
            if k not in cls.__dataclass_fields__:
                continue
            if k == "method":
                if v in RATE_METHODS:
                    known[k] = v
            elif k == "band":
                if isinstance(v, bool):
                    known[k] = v
            elif k == "min_intervals":
                if isinstance(v, int) and not isinstance(v, bool) and v >= 1:
                    known[k] = v
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
                known[k] = float(v)
        return cls(**known)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse(value: Any) -> datetime | None:
    from .posture import parse_time
    return parse_time(value)


# ---------------------------------------------------------------------------
# history I/O (read-only, fail-open)
# ---------------------------------------------------------------------------


def load_history(path: Path | None = None, *, since: datetime | None = None,
                 until: datetime | None = None) -> list[dict[str, Any]]:
    """Snapshot rows (v0 or v1 schema) sorted by time. Unparseable lines are skipped."""
    if path is None:
        from .posture import history_path
        path = history_path()
    rows: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict) or not isinstance(row.get("pools"), list):
            continue
        t = _parse(row.get("now"))
        if t is None or (since and t < since) or (until and t > until):
            continue
        rows.append(row)
    rows.sort(key=lambda r: _parse(r["now"]))
    return rows


def points_for(rows: Iterable[dict[str, Any]], pool_id: str) -> list[tuple[datetime, float, datetime | None]]:
    """(time, remaining, resets_at) for one pool across snapshot rows."""
    pts = []
    for row in rows:
        t = _parse(row.get("now"))
        for p in row.get("pools") or []:
            if isinstance(p, dict) and p.get("id") == pool_id and isinstance(p.get("remaining"), (int, float)):
                pts.append((t, float(p["remaining"]), _parse(p.get("resets_at"))))
    pts.sort(key=lambda x: x[0])
    return pts


def current_window(points: list[tuple[datetime, float, datetime | None]],
                   cfg: RateConfig) -> list[tuple[datetime, float, datetime | None]]:
    """Trailing run of points in the same reset window as the last point."""
    if not points:
        return []
    tol = timedelta(minutes=cfg.reset_tolerance_minutes)
    out = [points[-1]]
    for prev in reversed(points[:-1]):
        cur = out[-1]
        same_reset = (prev[2] is None and cur[2] is None) or (
            prev[2] is not None and cur[2] is not None and abs(prev[2] - cur[2]) <= tol)
        if not same_reset or cur[1] > prev[1] + cfg.quantum or (prev[2] is not None and prev[0] >= prev[2]):
            break  # a reset between prev and cur (remaining jumped up, or the reset time moved/passed)
        out.append(prev)
    out.reverse()
    return out


# ---------------------------------------------------------------------------
# rate estimation (pure)
# ---------------------------------------------------------------------------


def recent_rate(points: list[tuple[datetime, float, datetime | None]], now: datetime,
                cfg: RateConfig = RateConfig()) -> dict[str, Any]:
    """EWMA burn rate over the current window. Returns ``{"ok": False, "reason": ...}`` when gated."""
    window = [p for p in current_window(points, cfg) if p[0] <= now
              and (now - p[0]).total_seconds() / 3600 <= cfg.lookback_hours]
    thinned: list[tuple[datetime, float, datetime | None]] = []
    for p in reversed(window):  # keep the newest point, then every point >= min_interval_hours before the last kept
        if not thinned or (thinned[-1][0] - p[0]).total_seconds() / 3600 >= cfg.min_interval_hours:
            thinned.append(p)
    window = list(reversed(thinned))
    intervals = []
    for (t0, r0, _), (t1, r1, _) in zip(window, window[1:]):
        dt = (t1 - t0).total_seconds() / 3600
        if dt <= 0:
            continue
        mid_age = (now - (t0 + (t1 - t0) / 2)).total_seconds() / 3600
        intervals.append((dt, max(0.0, r0 - r1), mid_age))
    span = (window[-1][0] - window[0][0]).total_seconds() / 3600 if len(window) > 1 else 0.0
    base = {"intervals": len(intervals), "span_hours": round(span, 3), "points": len(window)}
    if len(intervals) < cfg.min_intervals:
        return {"ok": False, "reason": f"history has {len(intervals)} interval(s) in this window (< {cfg.min_intervals})", **base}
    if span < cfg.min_span_hours:
        return {"ok": False, "reason": f"history spans {span:.2f}h in this window (< {cfg.min_span_hours:g}h)", **base}
    tau = max(cfg.horizon_hours, 1e-6)
    ws = [math.exp(-max(age, 0.0) / tau) for _, _, age in intervals]
    wdt = sum(w * dt for w, (dt, _, _) in zip(ws, intervals))
    if wdt <= 0:
        return {"ok": False, "reason": "zero weighted time", **base}
    rate = sum(w * used for w, (_, used, _) in zip(ws, intervals)) / wdt
    # Dispersion of per-interval rates around the EWMA, weighted by w*dt (time-weighted), and effective n.
    # Whole-percent reporting alone makes hourly intervals read 0 or 1 at a steady 0.5/h: each endpoint's
    # rounding error is ~uniform(+-q/2), so an interval's rate carries (q^2/6)/dt^2 of pure quantization
    # variance. That part is subtracted (it is covered once, over the whole span, by quantum/span below);
    # what remains is real burstiness.
    tw = [w * dt for w, (dt, _, _) in zip(ws, intervals)]
    var_obs = sum(t * ((used / dt) - rate) ** 2 for t, (dt, used, _) in zip(tw, intervals)) / wdt
    var_q = sum(t * (cfg.quantum ** 2 / 6) / dt ** 2 for t, (dt, _, _) in zip(tw, intervals)) / wdt
    var = max(0.0, var_obs - var_q)
    n_eff = wdt ** 2 / sum(t * t for t in tw)
    se = math.sqrt(var / n_eff) if n_eff > 0 else 0.0
    eff_span = wdt  # weighted hours actually informing the estimate
    half = cfg.z * se + cfg.quantum / max(eff_span, 1e-6)
    return {"ok": True, "rate": rate, "lo": max(0.0, rate - half), "hi": rate + half, "se": se,
            "n_eff": round(n_eff, 2), "weighted_hours": round(eff_span, 3),
            "source": f"ewma_recent_{cfg.horizon_hours:g}h", **base}


def window_average_band(rate: float | None, observed_hours: float | None, cfg: RateConfig = RateConfig()):
    """Quantization band for the v0 single-snapshot window average."""
    if rate is None or not observed_hours or observed_hours <= 0:
        return None, None
    half = cfg.quantum / observed_hours
    return max(0.0, rate - half), rate + half


def previous_postures(pools: list[Any], prev_row: dict[str, Any] | None, cfg: RateConfig = RateConfig()) -> None:
    """Set ``prev_posture`` from the previous snapshot row, same pool and same reset window only."""
    if not prev_row:
        return
    tol = timedelta(minutes=cfg.reset_tolerance_minutes)
    by_id = {p.get("id"): p for p in prev_row.get("pools") or [] if isinstance(p, dict)}
    for pool in pools:
        prev = by_id.get(pool.id)
        if not prev or prev.get("posture") not in ("BURN", "OFFLOAD"):
            continue
        a, b = _parse(prev.get("resets_at")), _parse(pool.resets_at)
        if a is not None and b is not None and abs(a - b) <= tol:
            pool.prev_posture = prev["posture"]


def apply_recent_rates(pools: list[Any], rows: list[dict[str, Any]], now: datetime,
                       cfg: RateConfig = RateConfig()) -> list[dict[str, Any]]:
    """Set each metered, perishable pool's rate from history (EWMA) or keep the window average (fallback).

    Mutates ``pools`` (``z0int.posture.Pool``) in place; returns one note per pool for the sources block.
    The pool's own current observation is appended to the history series, so a live snapshot counts.
    """
    notes = []
    for pool in pools:
        if pool.remaining is None or pool.unit != "percent" or not pool.resets_at:
            continue
        pool.rate_window_avg = pool.burn_rate_per_hour
        lo, hi = window_average_band(pool.burn_rate_per_hour, pool.rate_observed_hours, cfg) if cfg.band else (None, None)
        if cfg.method != "ewma":
            pool.rate_lo, pool.rate_hi = lo, hi
            continue
        pts = points_for(rows, pool.id)
        obs = _parse(pool.observed_at) or now
        if not pts or obs > pts[-1][0]:
            pts.append((obs, float(pool.remaining), _parse(pool.resets_at)))
        est = recent_rate(pts, now, cfg)
        if est["ok"]:
            pool.burn_rate_per_hour = est["rate"]
            pool.rate_lo, pool.rate_hi = (est["lo"], est["hi"]) if cfg.band else (None, None)
            pool.rate_observed_hours = est["span_hours"]
            pool.rate_source = est["source"]
            notes.append({"pool": pool.id, "rate": "ewma", "intervals": est["intervals"], "span_hours": est["span_hours"]})
        else:
            pool.rate_lo, pool.rate_hi = lo, hi
            if pool.rate_source:
                pool.rate_source = f"{pool.rate_source} (fallback: {est['reason']})"
            notes.append({"pool": pool.id, "rate": "window_average", "reason": est["reason"]})
    return notes
