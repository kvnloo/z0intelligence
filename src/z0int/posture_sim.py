"""Resource posture replay simulator + the pre-registered 7-day evaluation, run mechanically.

Given the snapshot history (``~/.z0int/state/posture/history.jsonl``) and a
*policy*, ``replay`` recomputes, at every snapshot and using only data at or
before it (no lookahead), which BURN / OFFLOAD decisions the policy would have
made and what it projected. ``outcomes`` reads what actually happened at each
reset. ``evaluate_preregistered`` scores the replay against the criteria in
``docs/resource-posture.md`` (v0) / ``docs/resource-posture-v1.md`` (v1),
including the calibrated Blind-vs-Aware counterfactual when the transcript and
route-receipt streams are supplied.

Policies (``POLICIES``):

* ``v0-logged``  the postures exactly as logged by the live timer (the v0 run);
* ``v0``         replay with the v0 window-average rate (single snapshot, no band gating);
* ``v1``         replay with the v1 EWMA rate + band gating;
* any dict       ``{"name", "mode": "replay"|"logged", "rate": {...}, "thresholds": {...}}``.

Interpretation notes (fixed here, not tuned on data):

* a pool's *window* is its run of snapshots with the same ``resets_at``
  (±15 min); its *outcome* is ``remaining`` at the last snapshot at most
  ``close_hours`` (1h) before the reset — no such snapshot, no outcome;
* projection error clamps the projected surplus at 0 (a pool cannot end below
  empty); the raw error is reported beside it;
* a posture is attributed to a *group* by its binding pool (as in v0);
* the counterfactual charges/credits only groups with a calibrated token
  stream (Claude Code transcripts -> ``claude``); others are listed as
  ``uncalibrated``.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from . import posture as P
from .posture_history import RateConfig, apply_recent_rates, load_history, previous_postures

SCHEMA = "z0int.posture_replay.v1"
POLICIES: dict[str, dict[str, Any]] = {
    "v0-logged": {"name": "v0-logged", "mode": "logged"},
    "v0": {"name": "v0", "mode": "replay", "rate": {"method": "window_average", "band": False}},
    "v1": {"name": "v1", "mode": "replay", "rate": {"method": "ewma"}},
}
# Which posture group a harness / transcript stream draws from.
HARNESS_GROUPS = {"claude-code": "claude", "claude": "claude", "codex": "codex", "cursor": "cursor"}


def _t(value: Any) -> datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    return P.parse_time(value)


def _iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_policy(policy: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(policy, str):
        if policy not in POLICIES:
            raise ValueError(f"unknown policy {policy!r}; one of {sorted(POLICIES)} or a dict")
        return dict(POLICIES[policy])
    return {"name": policy.get("name", "custom"), "mode": policy.get("mode", "replay"), **policy}


# ---------------------------------------------------------------------------
# pools as of one snapshot
# ---------------------------------------------------------------------------


def _group_of(p: dict[str, Any]) -> str:
    return p.get("group") or str(p.get("id", "")).split(":", 1)[0]


def pools_from_row(row: dict[str, Any], now: datetime) -> list[P.Pool]:
    """Rebuild evaluable pools from a logged row (v0 or v1). The window-average rate is recomputed from
    (remaining, resets_at, window_hours) so every policy starts from the same observation."""
    pools: list[P.Pool] = []
    for p in row.get("pools") or []:
        if not isinstance(p, dict) or not isinstance(p.get("remaining"), (int, float)):
            continue
        kind = p.get("kind") if p.get("kind") in P.KINDS else "frontier"
        reset = P.parse_time(p.get("resets_at"))
        wh = p.get("window_hours")
        rate = observed_h = None
        if reset is not None and isinstance(wh, (int, float)) and wh > 0:
            observed_h = wh - (reset - now).total_seconds() / 3600
            if observed_h > 0:
                rate = (100.0 - float(p["remaining"])) / observed_h
        elif reset is None and p.get("burn_rate_per_hour") is not None:
            rate = p["burn_rate_per_hour"]
        pools.append(P.Pool(id=p["id"], kind=kind, group=_group_of(p), unit="percent", capacity=100.0,
                            remaining=float(p["remaining"]), resets_at=p.get("resets_at"),
                            window_hours=wh, burn_rate_per_hour=rate,
                            rate_observed_hours=None if observed_h is None else round(observed_h, 2),
                            rate_source="window_average_since_window_start" if rate is not None else None,
                            observed_at=row.get("now"), source="history"))
    # Offload targets are unmetered, so rows only carry their group names.
    for g in (row.get("groups") or {}):
        if g.startswith(("route:", "kerdoios:")) and not any(x.group == g for x in pools):
            kind = "local" if g in ("route:local", "route:groot", "kerdoios:local") else (
                "fast" if g.split(":", 1)[1] in P.FAST_PROVIDERS else "free")
            pools.append(P.Pool(id=g, kind=kind, group=g, unit="tokens", remaining=None, source="history"))
    return pools


# ---------------------------------------------------------------------------
# replay
# ---------------------------------------------------------------------------


def _decision_from_eval(ev: dict[str, Any]) -> dict[str, Any]:
    return {
        "now": ev["now"], "factory": ev["factory"]["posture"], "rule": ev["factory"].get("rule"),
        "prefer": ev["factory"].get("prefer", []),
        "groups": {g: v["posture"] for g, v in ev["groups"].items()},
        "binding": {g: v["binding_pool"] for g, v in ev["groups"].items()},
        "pools": {r["id"]: {k: r.get(k) for k in (
            "group", "kind", "posture", "remaining", "resets_at", "window_hours", "hours_until_reset",
            "burn_rate_per_hour", "rate_lo", "rate_hi", "rate_source", "projected_surplus_at_reset",
            "projected_surplus_band", "unconfirmed_posture", "reason")} for r in ev["pools"] if r["remaining"] is not None},
    }


def _decision_from_log(row: dict[str, Any]) -> dict[str, Any]:
    pools = {}
    for p in row.get("pools") or []:
        if isinstance(p, dict) and p.get("remaining") is not None:
            pools[p["id"]] = {**{k: p.get(k) for k in ("kind", "posture", "remaining", "resets_at", "window_hours",
                                                       "burn_rate_per_hour", "projected_surplus_at_reset")},
                              "group": _group_of(p)}
    groups = dict(row.get("groups") or {})
    binding: dict[str, str] = {}
    for pid, p in pools.items():  # binding pool by v0 precedence (the log does not carry it)
        g = p["group"]
        key = (P._GROUP_PRECEDENCE.get(p["posture"], 0), p.get("window_hours") or 0)
        if g not in binding or key > (P._GROUP_PRECEDENCE.get(pools[binding[g]]["posture"], 0),
                                      pools[binding[g]].get("window_hours") or 0):
            binding[g] = pid
    prefer = [g for g, v in groups.items() if v == "BURN"] if row.get("factory") == "BURN" else []
    return {"now": row["now"], "factory": row.get("factory"), "rule": "logged", "prefer": sorted(prefer),
            "groups": groups, "binding": binding, "pools": pools}


def replay(rows: list[dict[str, Any]], policy: str | dict[str, Any] = "v1") -> list[dict[str, Any]]:
    """One decision per snapshot. Replay mode sees only rows[:i+1] (no lookahead)."""
    pol = resolve_policy(policy)
    if pol["mode"] == "logged":
        return [_decision_from_log(r) for r in rows]
    th = P.Thresholds.from_dict(pol.get("thresholds"))
    rate_cfg = RateConfig.from_dict(pol.get("rate"))
    out = []
    for i, row in enumerate(rows):
        now = _t(row["now"])
        pools = pools_from_row(row, now)
        since = now - timedelta(hours=rate_cfg.lookback_hours)
        prior = [r for r in rows[:i] if since <= _t(r["now"]) <= now]
        apply_recent_rates(pools, prior, now, rate_cfg)
        if rate_cfg.band and out:
            # hysteresis state is the policy's own previous decision, never another policy's logged posture
            previous_postures(pools, {"pools": [{"id": k, **v} for k, v in out[-1]["pools"].items()]}, rate_cfg)
        out.append(_decision_from_eval(P.evaluate(pools, now, th)))
    return out


# ---------------------------------------------------------------------------
# outcomes (what actually happened at each reset)
# ---------------------------------------------------------------------------


def windows(rows: list[dict[str, Any]], tol_minutes: float = 15.0) -> dict[tuple[str, str], dict[str, Any]]:
    """Per (pool id, reset iso) window: its snapshot series. Resets within tolerance are merged."""
    series: dict[str, list[tuple[datetime, float, datetime, dict[str, Any]]]] = {}
    for row in rows:
        t = _t(row["now"])
        for p in row.get("pools") or []:
            if not isinstance(p, dict) or not isinstance(p.get("remaining"), (int, float)):
                continue
            reset = P.parse_time(p.get("resets_at"))
            if reset is None:
                continue
            series.setdefault(p["id"], []).append((t, float(p["remaining"]), reset, p))
    out: dict[tuple[str, str], dict[str, Any]] = {}
    tol = timedelta(minutes=tol_minutes)
    for pid, pts in series.items():
        pts.sort(key=lambda x: x[0])
        cur: dict[str, Any] | None = None
        for t, rem, reset, p in pts:
            if cur is None or abs(reset - cur["reset"]) > tol or t >= cur["reset"]:
                if t >= reset:
                    continue  # observation predates a reset that already passed: unknown, skip
                cur = {"pool": pid, "group": _group_of(p), "kind": p.get("kind"), "window_hours": p.get("window_hours"),
                       "reset": reset, "points": []}
                out[(pid, _iso(reset))] = cur
            cur["points"].append((t, rem))
    return out


def outcomes(rows: list[dict[str, Any]], *, close_hours: float = 1.0, until: datetime | None = None,
             exhausted_at: float = 0.0, near_full_use: float = 5.0) -> dict[tuple[str, str], dict[str, Any]]:
    """Observed end-of-window outcome per window whose reset has passed (by ``until`` / the last row)."""
    until = until or (_t(rows[-1]["now"]) if rows else None)
    res = {}
    for key, w in windows(rows).items():
        if until is None or w["reset"] > until + timedelta(hours=close_hours):
            complete = False
        else:
            complete = True
        pts = w["points"]
        close = [(t, r) for t, r in pts if timedelta(0) <= w["reset"] - t <= timedelta(hours=close_hours)]
        final = close[-1][1] if close and complete else None
        exhausted = any(r <= exhausted_at for _, r in pts)
        res[key] = {"pool": w["pool"], "group": w["group"], "kind": w["kind"], "window_hours": w["window_hours"],
                    "resets_at": _iso(w["reset"]), "complete": complete, "final_remaining": final,
                    "final_at": _iso(close[-1][0]) if close and complete else None,
                    "min_remaining": min(r for _, r in pts), "exhausted": exhausted, "snapshots": len(pts),
                    "ran_dry": exhausted or (final is not None and final <= near_full_use)}
    return res


def _outcome_for(outs: dict[tuple[str, str], dict[str, Any]], pool_id: str, resets_at: Any,
                 tol_minutes: float = 15.0) -> dict[str, Any] | None:
    reset = P.parse_time(resets_at)
    if reset is None:
        return None
    for (pid, _), o in outs.items():
        if pid == pool_id and abs(P.parse_time(o["resets_at"]) - reset) <= timedelta(minutes=tol_minutes):
            return o
    return None


# ---------------------------------------------------------------------------
# scoring (criteria 1, 3, 4 need only history)
# ---------------------------------------------------------------------------


def score(decisions: list[dict[str, Any]], outs: dict[tuple[str, str], dict[str, Any]], *,
          th: P.Thresholds = P.Thresholds(), frontier_only: bool = True) -> dict[str, Any]:
    errs, raw_errs = [], []
    burn = {"n": 0, "hit": 0}
    off = {"n": 0, "hit": 0, "n_predictive": 0, "hit_predictive": 0}
    for d in decisions:
        for pid, p in d["pools"].items():
            if frontier_only and p.get("kind") != "frontier":
                continue
            o = _outcome_for(outs, pid, p.get("resets_at"))
            if o is None or o["final_remaining"] is None:
                continue
            proj = p.get("projected_surplus_at_reset")
            if proj is not None:
                errs.append(abs(max(0.0, proj) - o["final_remaining"]))
                raw_errs.append(abs(proj - o["final_remaining"]))
        for g, posture in d["groups"].items():
            pid = d["binding"].get(g)
            p = d["pools"].get(pid) if pid else None
            if p is None or (frontier_only and p.get("kind") != "frontier"):
                continue
            o = _outcome_for(outs, pid, p.get("resets_at"))
            if o is None or not o["complete"]:
                continue
            if posture == "BURN" and o["final_remaining"] is not None:
                burn["n"] += 1
                burn["hit"] += o["final_remaining"] >= th.burn_min_surplus_frac * 100
            elif posture == "OFFLOAD":
                off["n"] += 1
                off["hit"] += bool(o["ran_dry"])
                if (p.get("remaining") or 0) > 0:
                    off["n_predictive"] += 1
                    off["hit_predictive"] += bool(o["ran_dry"])
    flips = flips_per_group_day(decisions)
    asserted = sum(1 for d in decisions for p in d["pools"].values()
                   if p.get("kind") == "frontier" and p.get("posture") in ("BURN", "OFFLOAD"))
    withheld = sum(1 for d in decisions for p in d["pools"].values()
                   if p.get("kind") == "frontier" and str(p.get("reason") or "").endswith("_band_unconfirmed"))
    return {
        "withheld": {"asserted": asserted, "withheld": withheld,
                     "rate": withheld / (asserted + withheld) if asserted + withheld else None},
        "projection_error": {"n": len(errs), "median": statistics.median(errs) if errs else None,
                             "median_raw": statistics.median(raw_errs) if raw_errs else None},
        "burn_precision": {**burn, "value": burn["hit"] / burn["n"] if burn["n"] else None},
        "offload_precision": {**off, "value": off["hit"] / off["n"] if off["n"] else None,
                              "value_predictive": off["hit_predictive"] / off["n_predictive"] if off["n_predictive"] else None},
        "flips": flips,
    }


def flips_per_group_day(decisions: list[dict[str, Any]], kinds: Iterable[str] = ("frontier",)) -> dict[str, Any]:
    if len(decisions) < 2:
        return {"per_group": {}, "max": None, "days": 0.0}
    t0, t1 = _t(decisions[0]["now"]), _t(decisions[-1]["now"])
    days = max((t1 - t0).total_seconds() / 86400, 1.0)
    kinds = set(kinds)
    frontier_groups = {p["group"] for d in decisions for p in d["pools"].values() if p.get("kind") in kinds}
    counts = {g: 0 for g in sorted(frontier_groups)}
    for a, b in zip(decisions, decisions[1:]):
        for g in counts:
            if g in a["groups"] and g in b["groups"] and a["groups"][g] != b["groups"][g]:
                counts[g] += 1
    per = {g: round(c / days, 3) for g, c in counts.items()}
    return {"per_group": per, "max": max(per.values()) if per else None, "days": round(days, 3)}


# ---------------------------------------------------------------------------
# token streams: Claude Code transcripts, route receipts, limit events
# ---------------------------------------------------------------------------


@dataclass
class Turn:
    ts: datetime
    group: str
    noncache: float  # input + output + cache_creation tokens
    cache_read: float
    output: float
    tool_calls: int
    local_only: bool = False


def load_transcripts(root: Path | None = None, *, since: datetime | None = None,
                     until: datetime | None = None) -> tuple[list[Turn], list[dict[str, Any]]]:
    """Assistant turns (deduped by message id, last streamed chunk wins) and usage-limit events from
    Claude Code transcripts. Content is never retained — only timestamps, token counts, tool-call counts."""
    root = root or Path.home() / ".claude" / "projects"
    turns: dict[str, Turn] = {}
    limits: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.jsonl")) if root.is_dir() else []:
        try:
            if since and datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) < since:
                continue
            fh = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"assistant"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                ts = _t(row.get("timestamp"))
                if ts is None or (since and ts < since) or (until and ts > until):
                    continue
                msg = row.get("message") if isinstance(row.get("message"), dict) else {}
                if row.get("isApiErrorMessage"):
                    text = " ".join(b.get("text", "") for b in msg.get("content") or [] if isinstance(b, dict))
                    if row.get("error") == "rate_limit" or "limit reached" in text.lower() or "usage limit" in text.lower() \
                            or "hit your limit" in text.lower():
                        reset = None
                        if "|" in text:
                            try:
                                reset = _t(float(text.rsplit("|", 1)[1].strip().split()[0]))
                            except (ValueError, IndexError):
                                reset = None
                        limits.append({"ts": ts, "reset": reset, "group": "claude"})
                    continue
                usage = msg.get("usage")
                if row.get("type") != "assistant" or not isinstance(usage, dict) or msg.get("model") == "<synthetic>":
                    continue
                mid = msg.get("id") or f"{path.name}:{ts.isoformat()}"
                tools = sum(1 for b in msg.get("content") or [] if isinstance(b, dict) and b.get("type") == "tool_use")
                prev = turns.get(mid)
                turns[mid] = Turn(ts=ts, group="claude",
                                  noncache=float((usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0)
                                                 + (usage.get("cache_creation_input_tokens") or 0)),
                                  cache_read=float(usage.get("cache_read_input_tokens") or 0),
                                  output=float(usage.get("output_tokens") or 0),
                                  tool_calls=max(tools, prev.tool_calls if prev else 0))
    return sorted(turns.values(), key=lambda t: t.ts), sorted(limits, key=lambda e: e["ts"])


def load_route_receipts(path: Path | None = None, *, since: datetime | None = None,
                        until: datetime | None = None) -> list[dict[str, Any]]:
    """Completed route_worker attempts with their frontier-token estimate and harness."""
    if path is None:
        from .receipt import receipts_path
        path = receipts_path()
    last: dict[str, dict[str, Any]] = {}
    try:
        fh = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return []
    with fh:
        for line in fh:
            if "codex.delegated_text" not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            extra = row.get("extra") or {}
            if row.get("capability_id") != "codex.delegated_text" or extra.get("status") != "completed":
                continue
            avoided = row.get("estimated_frontier_tokens_avoided")
            ts = _t(row.get("ts"))
            if avoided is None or ts is None or (since and ts < since) or (until and ts > until):
                continue
            last[row.get("trace_id") or f"{ts}"] = {
                "ts": ts, "tokens": float(avoided), "group": HARNESS_GROUPS.get(extra.get("harness") or "codex", "codex"),
                "logged_posture": (extra.get("resource_posture") or {}).get("factory_posture")}
    return sorted(last.values(), key=lambda r: r["ts"])


# ---------------------------------------------------------------------------
# calibration: tokens -> percent per pool
# ---------------------------------------------------------------------------


def calibrate(rows: list[dict[str, Any]], turns: list[Turn], pool_id: str, *, group: str = "claude",
              since: datetime | None = None, until: datetime | None = None) -> dict[str, Any]:
    """Least squares without intercept: d_used_pp ~ a * noncache_tokens + b * cache_read_tokens over
    consecutive snapshots of one window. Falls back to one coefficient if the 2x2 fit is singular/negative."""
    xs = []
    for w in windows(rows).values():
        if w["pool"] != pool_id:
            continue
        pts = [(t, r) for t, r in w["points"] if (since is None or t >= since) and (until is None or t < until)]
        for (t0, r0), (t1, r1) in zip(pts, pts[1:]):
            n = c = 0.0
            for tu in turns:
                if tu.group == group and t0 < tu.ts <= t1:
                    n += tu.noncache
                    c += tu.cache_read
            xs.append((n, c, max(0.0, r0 - r1)))
    if not xs or all(n == 0 and c == 0 for n, c, _ in xs):
        return {"pool": pool_id, "ok": False, "reason": "no token intervals", "n": len(xs)}
    s11 = sum(n * n for n, _, _ in xs); s22 = sum(c * c for _, c, _ in xs); s12 = sum(n * c for n, c, _ in xs)
    s1y = sum(n * y for n, _, y in xs); s2y = sum(c * y for _, c, y in xs)
    det = s11 * s22 - s12 * s12
    a = b = None
    if det > 1e-9 * max(s11 * s22, 1e-30):
        a, b = (s1y * s22 - s2y * s12) / det, (s2y * s11 - s1y * s12) / det
    if a is None or a < 0 or b < 0:
        tot = [(n + c, y) for n, c, y in xs]
        den = sum(x * x for x, _ in tot)
        k = sum(x * y for x, y in tot) / den if den else 0.0
        a = b = max(0.0, k)
        method = "single_coefficient"
    else:
        method = "two_coefficient"
    ybar = sum(y for *_, y in xs) / len(xs)
    ss_tot = sum((y - ybar) ** 2 for *_, y in xs)
    ss_res = sum((y - (a * n + b * c)) ** 2 for n, c, y in xs)
    return {"pool": pool_id, "ok": True, "a_noncache": a, "b_cache_read": b, "method": method, "n": len(xs),
            "r2": None if ss_tot == 0 else 1 - ss_res / ss_tot}


# ---------------------------------------------------------------------------
# counterfactual: Blind (what happened) vs Aware (policy's moves applied)
# ---------------------------------------------------------------------------


def _decision_at(decisions: list[dict[str, Any]], ts: datetime) -> dict[str, Any] | None:
    best = None
    for d in decisions:
        if _t(d["now"]) <= ts:
            best = d
        else:
            break
    return best


def counterfactual(rows: list[dict[str, Any]], decisions: list[dict[str, Any]], *, turns: list[Turn],
                   receipts: list[dict[str, Any]], limits: list[dict[str, Any]],
                   calibration: dict[str, dict[str, Any]], period: tuple[datetime, datetime],
                   bounded: Callable[[Turn], bool] | None = None, close_hours: float = 1.0) -> dict[str, Any]:
    """Pre-registered moves, on calibrated pools only:
    * posture BURN (factory) and a route_worker task offloaded from a BURNING group's parent -> it runs on
      that frontier instead: + a * estimated_frontier_tokens_avoided;
    * posture OFFLOAD (factory) and a bounded frontier turn (no tool calls, <= 2048 output, not local-only)
      -> it moves to the first offload target: - (a * noncache + b * cache_read).
    """
    bounded = bounded or (lambda tu: tu.tool_calls == 0 and tu.output <= 2048 and not tu.local_only)
    start, end = period
    events: list[tuple[datetime, str, float, str]] = []  # (ts, group, token-cost key, kind)
    for rc in receipts:
        if not start <= rc["ts"] < end:
            continue
        d = _decision_at(decisions, rc["ts"])
        if d and d["factory"] == "BURN" and rc["group"] in d.get("prefer", []):
            events.append((rc["ts"], rc["group"], rc["tokens"], "burn_absorb"))
    for tu in turns:
        if not start <= tu.ts < end:
            continue
        d = _decision_at(decisions, tu.ts)
        if d and d["factory"] == "OFFLOAD" and bounded(tu):
            events.append((tu.ts, tu.group, -1.0, "offload_move"))
    wins = windows(rows)
    results, uncalibrated = [], set()
    adjusted: dict[tuple[str, str], list[tuple[datetime, float, float]]] = {}
    for key, w in wins.items():
        cal = calibration.get(w["pool"])
        if not cal or not cal.get("ok"):
            if any(e[1] == w["group"] for e in events):
                uncalibrated.add(w["pool"])
            continue
        begin = w["reset"] - timedelta(hours=w["window_hours"] or 0) if w["window_hours"] else w["points"][0][0]
        deltas: list[tuple[datetime, float]] = []
        for ts, g, tokens, kind in events:
            if g != w["group"] or not begin <= ts < w["reset"]:
                continue
            if kind == "burn_absorb":
                deltas.append((ts, cal["a_noncache"] * tokens))
        for tu in turns:
            if tu.group != w["group"] or not begin <= tu.ts < w["reset"] or not start <= tu.ts < end:
                continue
            d = _decision_at(decisions, tu.ts)
            if d and d["factory"] == "OFFLOAD" and bounded(tu):
                deltas.append((tu.ts, -(cal["a_noncache"] * tu.noncache + cal["b_cache_read"] * tu.cache_read)))
        series = []
        for t, r in w["points"]:
            dsum = sum(v for ts, v in deltas if ts <= t)
            series.append((t, r, min(100.0, max(0.0, r - dsum))))
        adjusted[key] = series
        close = [(t, r, a) for t, r, a in series if timedelta(0) <= w["reset"] - t <= timedelta(hours=close_hours)]
        in_period = start <= w["reset"] < end + timedelta(hours=close_hours)
        results.append({"pool": w["pool"], "group": w["group"], "window_hours": w["window_hours"],
                        "resets_at": _iso(w["reset"]), "in_period": in_period,
                        "W_blind": close[-1][1] if close else None, "W_aware": close[-1][2] if close else None,
                        "delta_pp": round(sum(v for _, v in deltas), 4), "moves": len(deltas)})
    # L: rate-limited hours
    L_blind = L_aware = 0.0
    lim_rows = []
    for ev in limits:
        if not start <= ev["ts"] < end:
            continue
        reset = ev.get("reset") or _limit_reset(wins, ev)
        hours = max(0.0, (reset - ev["ts"]).total_seconds() / 3600) if reset else 0.0
        L_blind += hours
        avoided = _aware_positive_at(adjusted, wins, ev)
        L_aware += 0.0 if avoided else hours
        lim_rows.append({"ts": _iso(ev["ts"]), "reset": _iso(reset), "hours": round(hours, 3), "avoided": avoided})
    # new limit time the aware policy would have caused (adjusted hits 0 where blind did not)
    for key, series in adjusted.items():
        w = wins[key]
        for t, r, a in series:
            if start <= t < end and a <= 0.0 < r:
                L_aware += max(0.0, (w["reset"] - t).total_seconds() / 3600)
                lim_rows.append({"ts": _iso(t), "reset": _iso(w["reset"]), "pool": w["pool"], "caused_by_aware": True})
                break
    return {"windows": results, "L_blind": round(L_blind, 3), "L_aware": round(L_aware, 3), "limits": lim_rows,
            "moves": {"burn_absorb": sum(1 for e in events if e[3] == "burn_absorb"),
                      "offload_move": sum(1 for e in events if e[3] == "offload_move")},
            "uncalibrated_pools": sorted(uncalibrated)}


def _limit_reset(wins: dict[tuple[str, str], dict[str, Any]], ev: dict[str, Any]) -> datetime | None:
    cands = [w for w in wins.values() if w["group"] == ev.get("group", "claude") and w["reset"] > ev["ts"]
             and w["points"][0][0] <= ev["ts"]]
    if not cands:
        return None

    def rem_at(w):
        pts = [r for t, r in w["points"] if t <= ev["ts"]]
        return pts[-1] if pts else 100.0

    return min(cands, key=lambda w: (rem_at(w), w["reset"]))["reset"]


def _aware_positive_at(adjusted, wins, ev) -> bool:
    """At a real limit hit the binding pool is at 0 (blind). Aware avoids it iff the aware moves up to the hit
    left that pool strictly positive: adjusted = 0 - delta(<= ts)."""
    best = None
    for key, series in adjusted.items():
        w = wins[key]
        if w["group"] != ev.get("group", "claude") or not (w["points"][0][0] <= ev["ts"] < w["reset"]):
            continue
        prior = [(t, r, a) for t, r, a in series if t <= ev["ts"]]
        if not prior:
            continue
        t, r, a = prior[-1]
        credit = a - r  # pp the aware policy saved (positive) or spent (negative) by then
        if best is None or r < best[0]:
            best = (r, credit)
    return bool(best and best[1] > 0)


# ---------------------------------------------------------------------------
# the pre-registered evaluation, end to end
# ---------------------------------------------------------------------------


def evaluate_preregistered(rows: list[dict[str, Any]], policy: str | dict[str, Any] = "v0-logged", *,
                           turns: list[Turn] | None = None, receipts: list[dict[str, Any]] | None = None,
                           limits: list[dict[str, Any]] | None = None, start: datetime | None = None,
                           calibration_days: float = 3.0, evaluation_days: float = 4.0,
                           w_margin_pp: float = 10.0, max_projection_error_pp: float = 15.0,
                           min_burn_precision: float = 0.8, min_offload_precision: float = 0.7,
                           max_flips_per_day: float = 2.0) -> dict[str, Any]:
    """Days 1-3 calibrate tokens->percent, days 4-7 are scored. Criteria are the v0 pre-registration's
    (defaults), passed explicitly so a separate pre-registration can reuse this runner without editing them."""
    pol = resolve_policy(policy)
    th = P.Thresholds.from_dict(pol.get("thresholds"))
    if not rows:
        return {"schema": SCHEMA, "policy": pol["name"], "status": "insufficient_data", "reason": "no snapshots"}
    first, last = _t(rows[0]["now"]), _t(rows[-1]["now"])
    start = start or first
    cal_end = start + timedelta(days=calibration_days)
    end = cal_end + timedelta(days=evaluation_days)
    decisions = replay(rows, pol)
    eval_decisions = [d for d in decisions if cal_end <= _t(d["now"]) < end]
    outs = outcomes([r for r in rows if _t(r["now"]) < end + timedelta(hours=1)], until=min(last, end))
    sc = score(eval_decisions, outs, th=th)
    coverage_ok = last >= end - timedelta(hours=1)
    gaps = _gaps(rows, start, end)
    missing: list[str] = []
    if not coverage_ok:
        missing.append(f"history covers {((last - start).total_seconds() / 86400):.2f}d of {calibration_days + evaluation_days:g}d")
    # counterfactual needs token streams
    cf = None
    calibration: dict[str, dict[str, Any]] = {}
    if turns is None or receipts is None or limits is None:
        missing.append("transcripts/receipts/limits not supplied: W_aware and L not computed")
    else:
        pool_ids = sorted({w["pool"] for w in windows(rows).values() if w["group"] == "claude"})
        calibration = {pid: calibrate(rows, turns, pid, since=start, until=cal_end) for pid in pool_ids}
        cf = counterfactual(rows, decisions, turns=turns, receipts=receipts, limits=limits,
                            calibration=calibration, period=(cal_end, end))
    weekly = [w for w in (cf["windows"] if cf else []) if w["in_period"] and (w["window_hours"] or 0) >= 168
              and w["W_blind"] is not None]
    w_blind_all = [o["final_remaining"] for o in outs.values() if o["complete"] and o["final_remaining"] is not None
                   and (o["window_hours"] or 0) >= 168 and o["kind"] == "frontier"
                   and cal_end <= P.parse_time(o["resets_at"]) < end + timedelta(hours=1)]

    def crit(value, ok, threshold, note=None):
        return {"value": value, "threshold": threshold, "pass": ok, **({"note": note} if note else {})}

    c1 = crit(None, None, f"W_aware <= W_blind - {w_margin_pp:g}pp per weekly window (mean)")
    if weekly:
        wb = statistics.mean(w["W_blind"] for w in weekly)
        wa = statistics.mean(w["W_aware"] for w in weekly)
        c1 = crit({"W_blind": round(wb, 3), "W_aware": round(wa, 3), "windows": len(weekly)}, wa <= wb - w_margin_pp,
                  c1["threshold"])
    c2 = crit(None, None, "L_aware <= L_blind")
    if cf:
        c2 = crit({"L_blind": cf["L_blind"], "L_aware": cf["L_aware"]}, cf["L_aware"] <= cf["L_blind"], c2["threshold"])
    pe, bp, op = sc["projection_error"]["median"], sc["burn_precision"]["value"], sc["offload_precision"]["value"]
    parts = [pe is not None and pe <= max_projection_error_pp, bp is None or bp >= min_burn_precision,
             op is None or op >= min_offload_precision]
    c3 = crit({"median_projection_error": pe, "burn_precision": bp, "offload_precision": op,
               "n_error": sc["projection_error"]["n"], "n_burn": sc["burn_precision"]["n"],
               "n_offload": sc["offload_precision"]["n"]},
              None if pe is None else all(parts),
              f"median error <= {max_projection_error_pp:g}pp; BURN precision >= {min_burn_precision:g}; "
              f"OFFLOAD precision >= {min_offload_precision:g}",
              note=("precision with no verdicts of that kind is vacuous (n=0)"
                    if sc["burn_precision"]["n"] == 0 or sc["offload_precision"]["n"] == 0 else None))
    fmax = sc["flips"]["max"]
    c4 = crit(sc["flips"]["per_group"], None if fmax is None else fmax <= max_flips_per_day,
              f"<= {max_flips_per_day:g} flips per group per day")
    criteria = {"1_waste": c1, "2_limits": c2, "3_projection": c3, "4_stability": c4}
    falsified = []
    if w_blind_all and all(w < w_margin_pp for w in w_blind_all):
        falsified.append("nothing_perishes: W_blind < margin in every weekly window -> keep shadow, do not enforce")
    if c2["pass"] is False or c3["pass"] is False:
        falsified.append("projection_untrustworthy: tune thresholds on a NEW window and re-register")
    verdicts = [c["pass"] for c in criteria.values()]
    if missing or any(v is None for v in verdicts):
        status = "insufficient_data"
    else:
        status = "pass" if all(verdicts) else "fail"
    return {"schema": SCHEMA, "policy": pol["name"], "status": status, "missing": missing,
            "periods": {"calibration": [_iso(start), _iso(cal_end)], "evaluation": [_iso(cal_end), _iso(end)],
                        "history": [_iso(first), _iso(last)], "snapshots": len(rows), "gaps_over_2h": gaps},
            "criteria": criteria, "falsified": falsified, "score": sc, "calibration": calibration,
            "counterfactual": cf,
            "decisions": {"total": len(decisions), "in_evaluation": len(eval_decisions),
                          "factory_counts": _counts(d["factory"] for d in eval_decisions)}}


def _counts(xs: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return dict(sorted(out.items()))


def _gaps(rows, start, end, max_gap_h: float = 2.0) -> int:
    ts = [_t(r["now"]) for r in rows if start <= _t(r["now"]) < end]
    return sum(1 for a, b in zip(ts, ts[1:]) if (b - a).total_seconds() / 3600 > max_gap_h)


def v1_preregistered(v0: dict[str, Any], v1: dict[str, Any], *, error_margin_pp: float = 1.0,
                     flip_margin: float = 0.5, min_n_precision: int = 5, max_withheld_rate: float = 0.5) -> dict[str, Any]:
    """The separate v1 pre-registration (docs/resource-posture-v1.md): v1 must pass the v0 criteria unchanged
    (A) and be non-inferior to the v0 replay on the same data (B1-B4)."""
    def val(rep, *path):
        cur = rep
        for k in path:
            cur = (cur or {}).get(k)
        return cur

    out: dict[str, Any] = {"A_v1_passes_v0_criteria": {"value": v1.get("status"), "pass": {
        "pass": True, "fail": False}.get(v1.get("status"))}}
    e0, e1 = val(v0, "score", "projection_error", "median"), val(v1, "score", "projection_error", "median")
    out["B1_projection_error"] = {"value": {"v0": e0, "v1": e1},
                                  "pass": None if e0 is None or e1 is None else e1 <= e0 + error_margin_pp}
    for key, name in (("burn_precision", "B2_burn_precision"), ("offload_precision", "B2_offload_precision")):
        p0, p1 = val(v0, "score", key), val(v1, "score", key)
        scored = (p0 or {}).get("n", 0) >= min_n_precision and (p1 or {}).get("n", 0) >= min_n_precision
        out[name] = {"value": {"v0": (p0 or {}).get("value"), "v1": (p1 or {}).get("value"),
                               "n": [(p0 or {}).get("n"), (p1 or {}).get("n")]},
                     "pass": (p1["value"] >= p0["value"]) if scored else None,
                     **({} if scored else {"note": f"not scored: n < {min_n_precision}"})}
    f0, f1 = val(v0, "score", "flips", "max"), val(v1, "score", "flips", "max")
    out["B3_stability"] = {"value": {"v0": f0, "v1": f1},
                           "pass": None if f0 is None or f1 is None else f1 <= f0 + flip_margin}
    w = val(v1, "score", "withheld", "rate")
    out["B4_withheld_rate"] = {"value": w, "pass": True if w is None else w <= max_withheld_rate}
    required = [out["A_v1_passes_v0_criteria"]["pass"], out["B1_projection_error"]["pass"],
                out["B3_stability"]["pass"], out["B4_withheld_rate"]["pass"]]
    optional = [out["B2_burn_precision"]["pass"], out["B2_offload_precision"]["pass"]]
    if any(x is None for x in required):
        status = "insufficient_data"
    elif all(required) and all(x is not False for x in optional):
        status = "v1_adopted"
    else:
        status = "v0_retained"
    return {"status": status, "criteria": out}


def compare(rows: list[dict[str, Any]], policies: Iterable[str | dict[str, Any]] = ("v0-logged", "v0", "v1"),
            **kw) -> dict[str, Any]:
    return {resolve_policy(p)["name"]: evaluate_preregistered(rows, p, **kw) for p in policies}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def render(report: dict[str, Any]) -> str:
    lines = [f"POSTURE REPLAY [{report['policy']}]: {report['status'].upper()}"]
    per = report.get("periods") or {}
    if per:
        lines.append(f"  history {per['history'][0]} .. {per['history'][1]} ({per['snapshots']} snapshots, "
                     f"{per['gaps_over_2h']} gap(s) > 2h); evaluation {per['evaluation'][0]} .. {per['evaluation'][1]}")
    for m in report.get("missing") or []:
        lines.append(f"  missing: {m}")
    for name, c in (report.get("criteria") or {}).items():
        mark = {True: "PASS", False: "FAIL", None: "n/a "}[c["pass"]]
        lines.append(f"  [{mark}] {name}: {json.dumps(c['value'], default=str)}  ({c['threshold']})")
    for f in report.get("falsified") or []:
        lines.append(f"  falsified: {f}")
    d = report.get("decisions")
    if d:
        lines.append(f"  decisions in evaluation: {d['in_evaluation']} {d['factory_counts']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="z0int posture-replay",
                                 description="Replay posture policies over the snapshot history; run the pre-registered evaluation")
    ap.add_argument("--history", default=None, help="history.jsonl (default ~/.z0int/state/posture/history.jsonl)")
    ap.add_argument("--policy", action="append", default=None, help="v0-logged | v0 | v1 (repeatable; default all three)")
    ap.add_argument("--start", default=None, help="ISO start of day 1 (default: first snapshot)")
    ap.add_argument("--transcripts", default=None, help="Claude Code projects dir (enables the counterfactual)")
    ap.add_argument("--receipts", default=None, help="route receipts jsonl (default ~/.z0int/receipts/decisions.jsonl)")
    ap.add_argument("--decisions", action="store_true", help="print the per-snapshot decisions instead of the evaluation")
    ap.add_argument("--json", action="store_true", dest="as_json")
    a = ap.parse_args(argv)
    rows = load_history(Path(a.history).expanduser() if a.history else None)
    start = P.parse_time(a.start) if a.start else None
    policies = a.policy or ["v0-logged", "v0", "v1"]
    if a.decisions:
        out = {p: replay(rows, p) for p in policies}
        print(json.dumps(out, indent=2, default=str))
        return 0
    kw: dict[str, Any] = {"start": start}
    if a.transcripts:
        since = (start or (_t(rows[0]["now"]) if rows else None))
        turns, limits = load_transcripts(Path(a.transcripts).expanduser(), since=since)
        kw.update(turns=turns, limits=limits,
                  receipts=load_route_receipts(Path(a.receipts).expanduser() if a.receipts else None, since=since))
    reports = {resolve_policy(p)["name"]: evaluate_preregistered(rows, p, **kw) for p in policies}
    if "v0" in reports and "v1" in reports:
        reports["v1_vs_v0"] = v1_preregistered(reports["v0"], reports["v1"])
    if a.as_json:
        print(json.dumps(reports, indent=2, default=str))
    else:
        blocks = [render(r) for k, r in reports.items() if k != "v1_vs_v0"]
        if "v1_vs_v0" in reports:
            cmp_ = reports["v1_vs_v0"]
            blocks.append(f"V1 PRE-REGISTRATION: {cmp_['status'].upper()}\n" + "\n".join(
                f"  [{ {True: 'PASS', False: 'FAIL', None: 'n/a '}[c['pass']] }] {k}: {json.dumps(c['value'], default=str)}"
                for k, c in cmp_["criteria"].items()))
        print("\n\n".join(blocks))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
