"""Factory job dispatcher: cheapest lane that is good enough and has capacity now.

Input is a batch of typed jobs (kind, size, required quality, privacy, deadline).
Capacity comes from Kerdoios (``kerdoios.capacity.v1``: burn-pace pacing, Nous
spend cap, rate-limit evidence, free-tier limits, live cooldowns). Quality comes
from per-lane priors that the ledger's recorded outcomes keep correcting.

Cost is a shadow price, not a list price:

* a paid subscription that burn-pace marks ``under-burn`` has quota that perishes
  at reset, so using it inside its hourly headroom has *negative* cost and is
  filled first; past the headroom it costs more than free lanes;
* free lanes cost a little, more as their window empties;
* the capped Nous lane costs its dollars plus pressure on the remaining cap;
* hard-throttled / avoided pools, spent windows and cooldowns are infeasible.

Hard rules: code jobs only go to opus/sol (free models do bad work on code);
privacy never exceeds what a provider's data policy allows; a QA job never runs
on the family it is checking. Every decision and outcome lands in the ledger.

Stdlib only, so runners can call ``python -m z0int.dispatch`` without the ML stack.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

KINDS = ("scan", "label", "qa", "review", "test", "code")
PRIVACY = ("public", "internal", "private")
SIZE_FACTOR = {"S": 0.5, "M": 1.0, "L": 2.0}
INF = float("inf")

# What each lane can do. ``family`` keeps QA independent of the lane it checks.
LANE_CAPS: dict[str, dict[str, Any]] = {
    "opus": {"family": "anthropic", "kinds": set(KINDS) - {"test"}, "agentic": True},
    "sol": {"family": "openai", "kinds": set(KINDS) - {"test"}, "agentic": True},
    "haiku": {"family": "anthropic", "kinds": {"scan", "label", "qa", "review"}, "agentic": True},
    "luna": {"family": "openai", "kinds": {"scan", "label", "qa", "review"}, "agentic": True},
    "ds": {"family": "deepseek", "kinds": {"scan", "label", "qa", "review"}, "agentic": True},
    "glm": {"family": "zhipu", "kinds": {"scan", "label", "qa", "review"}, "agentic": True},
    "minimax": {"family": "minimax", "kinds": {"scan", "label", "review"}, "agentic": True},
    # One-shot API lanes cannot open files, so they take a scan/qa/review only when the job
    # carries its evidence inline (``context: "inline"``: the factory scan pod packs the repo's
    # README, contract files and CI config into the prompt). One request per job is also what
    # keeps NIM's 40 RPM / 10k RPD useful: an agentic harness spends ~20 requests per scan.
    "groq": {"family": "openai-oss", "kinds": {"label", "scan", "qa", "review"}, "agentic": False},
    "nim": {"family": "nvidia", "kinds": {"label", "scan", "qa", "review"}, "agentic": False},
    "cerebras": {"family": "openai-oss", "kinds": {"label", "scan", "qa", "review"}, "agentic": False},
    "local": {"family": "host", "kinds": {"test"}, "agentic": False},
}

# Provider data policy -> most sensitive privacy level it may see.
POLICY_PRIVACY = {"subscription": "private", "zero-retention": "internal", "unstated": "public",
                  "trial-no-confidential": "public", "may-train-and-human-review": "public"}

# Seed quality per lane (probability a result is usable and agrees with QA).
# Owner ranking: Opus highest. The ledger replaces these as outcomes arrive.
QUALITY_PRIOR = {"opus": 0.92, "sol": 0.88, "haiku": 0.8, "luna": 0.8, "ds": 0.66, "glm": 0.62,
                 "minimax": 0.55, "groq": 0.62, "nim": 0.62, "cerebras": 0.62, "local": 0.95}
PRIOR_WEIGHT = 6.0
# Seed share of a weekly subscription window one M-size agentic job consumes; learned from
# burn-pace deltas per batch. Opus evidence (2026-10-20, Hermes state.db): a read-only repo
# scan costs about $0.32 API-equivalent (166k cache-read, 37k cache-write, 5k output tokens),
# and Hermes alone logged $520 of Claude usage this week at 52% used, so 1% >= $10 and a
# scan <= 0.032%. Sol has no measurement yet.
PCT_PER_JOB_PRIOR = {"opus": 0.03, "sol": 0.05}
LATENCY_PRIOR_S = {"opus": 120, "sol": 150, "haiku": 120, "luna": 150, "ds": 240, "glm": 240,
                   "minimax": 240, "groq": 3, "nim": 8, "cerebras": 2, "local": 60}
DEFAULT_SUB_SHARE = 0.8  # leave a fifth of the headroom to interactive sessions
NOUS_RESERVE_FRACTION = 0.1
NOUS_BATCH_USD = 2.0
BULK_KINDS = {"scan", "label"}
INLINE_KINDS = {"scan", "qa", "review"}  # kinds a one-shot lane may take only with inline evidence
# Outcomes that say nothing about a lane's answer quality.
NO_QUALITY_SIGNAL = ("rate_limited", "quota", "not_run", "harness_error")
# A job waits for a busy cheaper lane only when the alternative is this much dearer:
# capped bulk (about +1.0) waits, while free lanes still add speed beside a busy paid pool (0.52).
WAIT_MARGIN = 0.6


@dataclass
class Job:
    id: str
    kind: str
    size: str = "M"
    quality: float = 0.6
    privacy: str = "internal"
    deadline_s: float | None = None
    avoid_family: str | None = None
    tokens: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Job":
        kind = raw.get("kind", "scan")
        if kind not in KINDS:
            raise ValueError(f"job {raw.get('id')}: unknown kind {kind!r}")
        privacy = raw.get("privacy", "internal")
        if privacy not in PRIVACY:
            raise ValueError(f"job {raw.get('id')}: unknown privacy {privacy!r}")
        known = {"id", "kind", "size", "quality", "privacy", "deadline_s", "avoid_family", "tokens"}
        return cls(id=str(raw["id"]), kind=kind, size=raw.get("size", "M"), quality=float(raw.get("quality", 0.6)),
                   privacy=privacy, deadline_s=raw.get("deadline_s"), avoid_family=raw.get("avoid_family"),
                   tokens=raw.get("tokens"), payload={k: v for k, v in raw.items() if k not in known})


# ---------------------------------------------------------------- ledger

def ledger_path() -> Path:
    return Path(os.environ.get("Z0INT_DISPATCH_LEDGER", Path.home() / ".z0int/state/dispatch/ledger.sqlite"))


def connect(path: Path | None = None) -> sqlite3.Connection:
    target = path or ledger_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(target, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
    CREATE TABLE IF NOT EXISTS decisions(
      id TEXT PRIMARY KEY, ts REAL, batch TEXT, job_id TEXT, kind TEXT, size TEXT, quality_req REAL,
      privacy TEXT, deadline_s REAL, lane TEXT, cost REAL, quality_est REAL, pct_est REAL, usd_est REAL,
      reason TEXT, alternatives TEXT, capacity_at REAL);
    CREATE TABLE IF NOT EXISTS outcomes(
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, decision_id TEXT, batch TEXT, job_id TEXT, lane TEXT,
      kind TEXT, status TEXT, schema_valid INTEGER, qa_agree REAL, seconds REAL, usd REAL, note TEXT);
    CREATE TABLE IF NOT EXISTS batches(
      batch TEXT PRIMARY KEY, started REAL, ended REAL, pace_start TEXT, pace_end TEXT, nous_start TEXT,
      nous_end TEXT, note TEXT);
    CREATE INDEX IF NOT EXISTS decisions_lane_ts ON decisions(lane, ts);
    CREATE INDEX IF NOT EXISTS outcomes_lane_kind ON outcomes(lane, kind);
    """)
    return con


def quality_posterior(con: sqlite3.Connection) -> dict[tuple[str, str], tuple[float, int]]:
    """Beta-style posterior per (lane, kind): prior pseudo-counts plus observed usefulness.

    An outcome scores 1 when the job finished with a schema-valid result; a QA
    agreement fraction, when present, replaces that score because it is the
    stronger signal. Rate limits say nothing about quality and are skipped.
    """
    sums: dict[tuple[str, str], list[float]] = {}
    for lane, kind, status, valid, agree in con.execute(
            "SELECT lane, kind, status, schema_valid, qa_agree FROM outcomes "
            f"WHERE status NOT IN ({','.join(repr(s) for s in NO_QUALITY_SIGNAL)})"):
        score = agree if agree is not None else float(status == "ok" and bool(valid))
        row = sums.setdefault((lane, kind), [0.0, 0])
        row[0] += score
        row[1] += 1
    out = {}
    for (lane, kind), (s, n) in sums.items():
        prior = QUALITY_PRIOR.get(lane, 0.5)
        out[(lane, kind)] = ((prior * PRIOR_WEIGHT + s) / (PRIOR_WEIGHT + n), int(n))
    return out


def latency_estimates(con: sqlite3.Connection) -> dict[tuple[str, str], float]:
    out = {}
    for lane, kind, avg in con.execute(
            "SELECT lane, kind, avg(seconds) FROM outcomes WHERE status = 'ok' AND seconds > 0 GROUP BY lane, kind"):
        out[(lane, kind)] = float(avg)
    return out


def pct_per_job(con: sqlite3.Connection, lane: str) -> tuple[float, str]:
    """Weekly-window percent one job costs, learned from batch burn-pace deltas."""
    pool = {"opus": "claude", "sol": "codex"}.get(lane)
    num, den = 0.0, 0
    for batch, ps, pe, t0, t1 in con.execute(
            "SELECT batch, pace_start, pace_end, started, ended FROM batches WHERE pace_end IS NOT NULL"):
        try:
            start = json.loads(ps)
            a = start["lanes"][pool]["weekly_used_pct"]
            b = json.loads(pe)["lanes"][pool]["weekly_used_pct"]
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        # Subtract what the pool's other consumers were burning when the batch began.
        background = float((start.get("recent_pct_per_h") or {}).get(pool) or 0)
        b = max(a, b - background * max(0.0, (t1 or 0) - (t0 or 0)) / 3600)
        jobs = con.execute("SELECT count(*) FROM outcomes WHERE batch = ? AND lane = ? "
                           "AND status NOT IN ('rate_limited', 'quota')",
                           (batch, lane)).fetchone()[0]
        if jobs >= 5 and b >= a:
            num += b - a
            den += jobs
    prior = PCT_PER_JOB_PRIOR.get(lane, 0.05)
    if den == 0:
        return prior, "prior"
    # Background subtraction is approximate and the window reports whole percents; blend with the prior.
    return (prior * 10 + num) / (10 + den), f"learned from {den} jobs (background-subtracted pool delta)"


def usd_per_job(con: sqlite3.Connection, lanes: Iterable[str] = ("haiku", "luna")) -> float | None:
    """Dollars per capped-lane job, from the Nous cap notices snapshotted at batch start and end.

    Other sessions can spend the same cap during a batch, so this is an upper bound.
    """
    spent, jobs = 0.0, 0
    marks = ",".join("?" * len(tuple(lanes)))
    for batch, ns, ne in con.execute("SELECT batch, nous_start, nous_end FROM batches WHERE nous_end IS NOT NULL"):
        try:
            delta = float(json.loads(ne)["used_usd"]) - float(json.loads(ns)["used_usd"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        n = con.execute(f"SELECT count(*) FROM outcomes WHERE batch = ? AND lane IN ({marks}) AND status = 'ok'",
                        (batch, *lanes)).fetchone()[0]
        if n and delta >= 0:
            spent += delta
            jobs += n
    return round(spent / jobs, 4) if jobs else None


# ---------------------------------------------------------------- capacity

def load_capacity(path: str | None = None) -> dict[str, Any]:
    """Kerdoios capacity: a JSON file, else the importable package, else the CLI."""
    if path:
        return json.loads(Path(path).read_text())
    src = os.environ.get("KERDOIOS_SRC")
    if src and src not in sys.path:
        sys.path.insert(0, src)
    try:
        from kerdoios.capacity import current_capacity
    except ImportError:
        out = subprocess.run([sys.executable, "-m", "kerdoios", "capacity"], capture_output=True, text=True,
                             timeout=180, check=True).stdout
        return json.loads(out)
    return current_capacity(live_model_check=os.environ.get("Z0INT_DISPATCH_LIVE_MODELS") == "1",
                            allow_metered=os.environ.get("Z0INT_DISPATCH_ALLOW_METERED") == "1")


def observe_capacity(lane: str, status: str, *, retry_after: float | None = None,
                     nous: tuple[float, float] | None = None, seconds: float | None = None) -> None:
    """Report an outcome to Kerdoios so the next capacity read sees throttles and spend."""
    src = os.environ.get("KERDOIOS_SRC")
    if src and src not in sys.path:
        sys.path.insert(0, src)
    try:
        from kerdoios.capacity import observe
    except ImportError:
        return
    kw = {"seconds": seconds} if seconds else {}  # older kerdoios has no latency feedback
    try:
        observe(lane, status, retry_after_s=retry_after,
                nous_used=nous[0] if nous else None, nous_cap=nous[1] if nous else None, **kw)
    except TypeError:
        observe(lane, status, retry_after_s=retry_after,
                nous_used=nous[0] if nous else None, nous_cap=nous[1] if nous else None)


# ---------------------------------------------------------------- decision

@dataclass
class LaneView:
    lane: str
    slots: int
    cost_class: str
    privacy_max: str
    reason: str
    pacing: dict[str, Any] | None
    spend: dict[str, Any] | None
    window_left: int | None
    window_total: int | None
    tpm: float | None
    latency_s: float
    jobs_per_min: float | None = None


def lane_views(capacity: Mapping[str, Any], in_flight: Mapping[str, int],
               ceiling: Mapping[str, int] | None = None) -> dict[str, LaneView]:
    """Lanes as the dispatcher sees them: kerdoios slots, minus work in flight, capped by the runner."""
    views = {}
    for lane, row in (capacity.get("lanes") or {}).items():
        if lane not in LANE_CAPS:
            continue
        limits = row.get("limits") or {}
        window = (limits.get("window_jobs") or {}).get("value")
        tpm = (limits.get("tpm") or {}).get("value")
        lat = row.get("latency_prior_ms")
        views[lane] = LaneView(
            lane=lane, slots=max(0, min(int(row.get("slots_now") or 0),
                                        int((ceiling or {}).get(lane, 10**6))) - int(in_flight.get(lane, 0))),
            cost_class=row.get("cost_class", "free"),
            privacy_max=POLICY_PRIVACY.get(row.get("data_policy") or "unstated", "public"),
            reason=row.get("reason", ""), pacing=row.get("pacing"), spend=row.get("spend"),
            window_left=row.get("window_jobs_left"), window_total=window, tpm=tpm,
            latency_s=LATENCY_PRIOR_S.get(lane, (lat or 60000) / 1000),
            jobs_per_min=(limits.get("jobs_per_min") or {}).get("value"))
    if "local" not in views:
        cpus = os.cpu_count() or 2
        views["local"] = LaneView("local", max(1, cpus // 4) - int(in_flight.get("local", 0)), "free", "private",
                                  "host CPU", None, None, None, None, None, LATENCY_PRIOR_S["local"])
    return views


@dataclass
class Budget:
    """What this dispatcher may still spend in the current hour/batch."""

    sub_pct_left: dict[str, float]
    nous_usd_left: float


def make_budget(con: sqlite3.Connection, views: Mapping[str, LaneView], batch: str | None,
                now: float, share: float = DEFAULT_SUB_SHARE) -> Budget:
    sub_left = {}
    for lane, v in views.items():
        if v.cost_class != "subscription" or not v.pacing:
            continue
        headroom = float(v.pacing.get("headroom_pct_per_h") or 0) * share
        spent = con.execute(
            "SELECT coalesce(sum(pct_est), 0) FROM decisions d WHERE lane = ? AND ts > ? AND NOT EXISTS ("
            "SELECT 1 FROM outcomes o WHERE o.decision_id = d.id AND o.status IN ('not_run', 'rate_limited', 'quota'))",
            (lane, now - 3600)).fetchone()[0]
        sub_left[lane] = headroom - float(spent)
    nous_left = 0.0
    spend = next((v.spend for v in views.values() if v.cost_class == "capped" and v.spend), None)
    if spend and spend.get("remaining_usd") is not None:
        reserve = float(spend.get("cap_usd") or 0) * NOUS_RESERVE_FRACTION
        batch_spent = con.execute("SELECT coalesce(sum(usd_est), 0) FROM decisions WHERE batch = ? AND usd_est > 0",
                                  (batch or "",)).fetchone()[0]
        nous_left = min(float(spend["remaining_usd"]) - reserve, NOUS_BATCH_USD - float(batch_spent))
    return Budget(sub_left, nous_left)


def candidates(job: Job, views: Mapping[str, LaneView], budget: Budget,
               quality: Mapping[tuple[str, str], tuple[float, int]],
               latency: Mapping[tuple[str, str], float], pct_cost: Mapping[str, float],
               *, need_slot: bool = True, learned_usd: float | None = None) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Every lane scored for ``job``; infeasible lanes come back with the reason."""
    rows, rejected = [], {}
    f = SIZE_FACTOR.get(job.size, 1.0)
    for lane, v in views.items():
        caps = LANE_CAPS[lane]
        why = None
        q, n = quality.get((lane, job.kind), (QUALITY_PRIOR.get(lane, 0.5), 0))
        lat = latency.get((lane, job.kind), v.latency_s) * (f if caps["agentic"] else 1)
        if job.kind not in caps["kinds"]:
            why = f"cannot run {job.kind}"
        elif job.kind in INLINE_KINDS and not caps["agentic"] and job.payload.get("context") != "inline":
            why = f"one-shot lane needs inline evidence for {job.kind}"
        elif job.kind == "code" and lane not in ("opus", "sol"):
            why = "code goes to opus/sol only"
        elif PRIVACY.index(job.privacy) > PRIVACY.index(v.privacy_max):
            why = f"privacy {job.privacy} exceeds provider policy ({v.privacy_max})"
        elif job.avoid_family and caps["family"] == job.avoid_family:
            why = f"same family as the work under QA ({job.avoid_family})"
        elif q < job.quality:
            why = f"quality {q:.2f} < required {job.quality:.2f}"
        elif need_slot and v.slots <= 0:
            why = f"no slot: {v.reason}" if v.reason != "ok" else "no free slot"
        elif v.window_left is not None and v.window_left <= 0:
            why = "window spent"
        elif v.tpm and (job.tokens or 2500) * f > v.tpm:
            why = f"job tokens exceed {int(v.tpm)} TPM"
        elif job.deadline_s is not None and lat > job.deadline_s:
            why = f"latency {lat:.0f}s > deadline {job.deadline_s:.0f}s"
        if why:
            rejected[lane] = why
            continue
        pct = usd = 0.0
        if v.cost_class == "subscription":
            band = (v.pacing or {}).get("band")
            pct = pct_cost.get(lane, 0.05) * f
            if band == "under-burn" and budget.sub_pct_left.get(lane, 0) >= pct:
                cost = -0.5
            elif band == "on-pace" and budget.sub_pct_left.get(lane, 0) >= pct:
                cost = 0.2
            else:
                rejected[lane] = f"{band}: hourly headroom spent ({budget.sub_pct_left.get(lane, 0):.2f}%)"
                continue
        elif v.cost_class == "capped":
            usd = float(learned_usd or (v.spend or {}).get("usd_per_job_prior") or 0.05) * f
            remaining = float((v.spend or {}).get("remaining_usd") or 0)
            if usd > budget.nous_usd_left:
                rejected[lane] = f"Nous batch/reserve budget left ${budget.nous_usd_left:.2f}"
                continue
            cost = usd + 100 * usd / max(remaining, 1e-9) * 0.05
            if job.kind in BULK_KINDS:
                # Owner rule: the capped Nous plan is for spot checks, not bulk volume.
                cost += 1.0
        elif v.cost_class == "metered":
            usd = float((v.spend or {}).get("usd_per_job_prior") or 0.01) * f
            cost = 0.1 + usd
        else:
            scarcity = 0.0
            if v.window_total and v.window_left is not None:
                scarcity = 1 - v.window_left / v.window_total
            cost = 0.02 + 0.1 * scarcity
        if job.kind == "label" and caps["agentic"]:
            # A one-shot label through an agent harness pays its whole startup context.
            cost += 0.6
        rows.append({"lane": lane, "cost": round(cost, 4), "quality": round(q, 3), "quality_n": n,
                     "latency_s": round(lat, 1), "pct": round(pct, 4), "usd": round(usd, 4)})
    # Cheapest first; among equals prefer quality, then speed.
    rows.sort(key=lambda r: (r["cost"], -r["quality"], r["latency_s"]))
    return rows, rejected


def order_jobs(jobs: Iterable[Job]) -> list[Job]:
    """Code before reviews before bulk, tight deadlines and high bars first."""
    rank = {"code": 0, "qa": 1, "review": 2, "test": 3, "scan": 4, "label": 5}
    return sorted(jobs, key=lambda j: (j.deadline_s if j.deadline_s is not None else INF,
                                       rank.get(j.kind, 9), -j.quality))


def assign(jobs: list[Job], capacity: Mapping[str, Any], *, in_flight: Mapping[str, int] | None = None,
           ceiling: Mapping[str, int] | None = None, batch: str | None = None, con: sqlite3.Connection | None = None, now: float | None = None,
           record: bool = True, share: float = DEFAULT_SUB_SHARE) -> dict[str, Any]:
    """Place as many jobs as have a slot right now; the rest wait with the reason."""
    clock = time.time() if now is None else now
    con = con or connect()
    views = lane_views(capacity, in_flight or {}, ceiling)
    if ceiling is not None:
        # A runner that names its lanes can only drive those.
        views = {lane: v for lane, v in views.items() if lane in ceiling}
    budget = make_budget(con, views, batch, clock, share)
    quality, latency = quality_posterior(con), latency_estimates(con)
    pct_cost = {lane: pct_per_job(con, lane)[0] for lane in ("opus", "sol")}
    learned = usd_per_job(con)
    placed, waiting = [], []
    for job in order_jobs(jobs):
        rows, rejected = candidates(job, views, budget, quality, latency, pct_cost, learned_usd=learned)
        if not rows:
            # Would any lane take it once a slot frees up?
            later, _ = candidates(job, views, budget, quality, latency, pct_cost, need_slot=False,
                                  learned_usd=learned)
            waiting.append({"job_id": job.id, "next_best": later[0]["lane"] if later else None,
                            "rejected": rejected, "infeasible": not later})
            continue
        best = rows[0]
        if job.deadline_s is None:
            later, _ = candidates(job, views, budget, quality, latency, pct_cost, need_slot=False,
                                  learned_usd=learned)
            if later and later[0]["cost"] + WAIT_MARGIN < best["cost"]:
                waiting.append({"job_id": job.id, "next_best": later[0]["lane"], "infeasible": False,
                                "rejected": {**rejected, best["lane"]: f"waits for cheaper {later[0]['lane']}"}})
                continue
        v = views[best["lane"]]
        v.slots -= 1
        if v.window_left is not None:
            v.window_left -= 1
        if best["pct"]:
            budget.sub_pct_left[best["lane"]] = budget.sub_pct_left.get(best["lane"], 0) - best["pct"]
        if best["usd"] and v.cost_class == "capped":
            budget.nous_usd_left -= best["usd"]
        did = uuid.uuid4().hex[:16]
        decision = {"decision_id": did, "job_id": job.id, "lane": best["lane"], "kind": job.kind,
                    "pace_s": round(60 / v.jobs_per_min, 1) if v.jobs_per_min else None,
                    "cost": best["cost"], "quality_est": best["quality"], "alternatives": rows[1:4],
                    "rejected": rejected}
        placed.append(decision)
        if record:
            con.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                did, clock, batch or "", job.id, job.kind, job.size, job.quality, job.privacy, job.deadline_s,
                best["lane"], best["cost"], best["quality"], best["pct"], best["usd"], "cheapest feasible",
                json.dumps({"next": rows[1:4], "rejected": rejected}), capacity.get("at")))
    if record:
        con.commit()
    return {"schema": "z0int.dispatch_assignment.v1", "at": clock, "batch": batch, "assign": placed,
            "wait": waiting,
            "budget": {"subscription_pct_left_this_hour": {k: round(v, 3) for k, v in budget.sub_pct_left.items()},
                       "nous_usd_left": round(budget.nous_usd_left, 2)}}


def plan(jobs: list[Job], capacity: Mapping[str, Any], *, horizon_h: float = 1.0,
         con: sqlite3.Connection | None = None, share: float = DEFAULT_SUB_SHARE) -> dict[str, Any]:
    """Dry-run the whole batch over ``horizon_h``: each lane absorbs slots x horizon / latency jobs."""
    con = con or connect()
    views = lane_views(capacity, {})
    quality, latency = quality_posterior(con), latency_estimates(con)
    pct_cost = {lane: pct_per_job(con, lane)[0] for lane in ("opus", "sol")}
    budget = make_budget(con, views, None, time.time(), share)
    learned = usd_per_job(con)
    budget.sub_pct_left = {k: v * horizon_h for k, v in budget.sub_pct_left.items()}
    for v in views.values():
        throughput = v.slots * horizon_h * 3600 / max(v.latency_s, 1)
        v.slots = int(min(throughput, v.window_left if v.window_left is not None else INF))
    counts: dict[str, int] = {}
    unplaced = []
    for job in order_jobs(jobs):
        rows, rejected = candidates(job, views, budget, quality, latency, pct_cost, learned_usd=learned)
        if not rows:
            unplaced.append({"job_id": job.id, "rejected": rejected})
            continue
        best = rows[0]
        views[best["lane"]].slots -= 1
        budget.sub_pct_left[best["lane"]] = budget.sub_pct_left.get(best["lane"], 0) - best["pct"]
        if views[best["lane"]].cost_class == "capped":
            budget.nous_usd_left -= best["usd"]
        counts[best["lane"]] = counts.get(best["lane"], 0) + 1
    return {"schema": "z0int.dispatch_plan.v1", "horizon_h": horizon_h, "per_lane": counts,
            "unplaced": unplaced, "jobs": len(jobs)}


def record_outcome(con: sqlite3.Connection, outcome: Mapping[str, Any], *, now: float | None = None) -> None:
    """Store an executor outcome and forward throttles/spend to Kerdoios."""
    clock = time.time() if now is None else now
    row = con.execute("SELECT batch, job_id, lane, kind FROM decisions WHERE id = ?",
                      (outcome.get("decision_id"),)).fetchone()
    batch, job_id, lane, kind = row if row else (outcome.get("batch"), outcome.get("job_id"),
                                                 outcome.get("lane"), outcome.get("kind"))
    status = outcome.get("status", "error")
    con.execute("INSERT INTO outcomes(ts, decision_id, batch, job_id, lane, kind, status, schema_valid, qa_agree,"
                " seconds, usd, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (clock, outcome.get("decision_id"), batch, job_id, lane, kind, status,
                 int(bool(outcome.get("schema_valid"))), outcome.get("qa_agree"), outcome.get("seconds"),
                 outcome.get("usd"), outcome.get("note")))
    con.commit()
    nous = outcome.get("nous_notice")
    if not lane or status == "not_run":
        return
    # "quota" (a spent daily/credit window, retry_class stop_lane) rests the lane at once in
    # Kerdoios instead of waiting for three throttles.
    observe_capacity(lane, {"ok": "ok", "rate_limited": "rate_limited", "quota": "quota"}.get(status, "error"),
                     retry_after=outcome.get("retry_after"), nous=tuple(nous) if nous else None,
                     seconds=outcome.get("seconds") if status == "ok" else None)


def record_qa(con: sqlite3.Connection, job_id: str, agree: float, *, qa_lane: str | None = None) -> int:
    """Attach a QA agreement fraction to the latest finished outcome of ``job_id``."""
    cur = con.execute("UPDATE outcomes SET qa_agree = ?, note = coalesce(note, '') || ? WHERE id = ("
                      "SELECT id FROM outcomes WHERE job_id = ? AND status = 'ok' ORDER BY ts DESC LIMIT 1)",
                      (agree, f" qa_by={qa_lane}" if qa_lane else "", job_id))
    con.commit()
    return cur.rowcount


def report(con: sqlite3.Connection, batch: str | None = None) -> dict[str, Any]:
    where, args = ("WHERE batch = ?", (batch,)) if batch else ("", ())
    lanes = {}
    for lane, kind, n, ok, valid, rl, secs, usd, qa_n, qa in con.execute(
            f"SELECT lane, kind, count(*), sum(status='ok'), sum(status='ok' AND schema_valid=1), "
            f"sum(status='rate_limited'), avg(CASE WHEN status='ok' THEN seconds END), sum(coalesce(usd,0)), "
            f"count(qa_agree), avg(qa_agree) FROM outcomes {where} GROUP BY lane, kind", args):
        lanes[f"{lane}/{kind}"] = {"jobs": n, "ok": ok, "schema_valid": valid, "rate_limited": rl,
                                   "mean_seconds": round(secs or 0, 1), "usd": round(usd or 0, 4),
                                   "qa_checked": qa_n, "qa_agreement": None if qa is None else round(qa, 3)}
    span = con.execute(f"SELECT min(ts), max(ts), count(*) FROM outcomes {where}", args).fetchone()
    hours = max(((span[1] or 0) - (span[0] or 0)) / 3600, 1e-9) if span[2] else None
    out = {"batch": batch, "per_lane": lanes, "outcomes": span[2],
           "jobs_per_hour": round(span[2] / hours, 1) if hours else None,
           "quality_posterior": {f"{k[0]}/{k[1]}": {"mean": round(v[0], 3), "n": v[1]}
                                 for k, v in quality_posterior(con).items()},
           "pct_per_job": {lane: pct_per_job(con, lane) for lane in ("opus", "sol")},
           "nous_usd_per_job": usd_per_job(con)}
    if batch:
        b = con.execute("SELECT started, ended, pace_start, pace_end, nous_start, nous_end FROM batches "
                        "WHERE batch = ?", (batch,)).fetchone()
        if b:
            out["batch_window"] = {"started": b[0], "ended": b[1]}
            for key, raw in (("pace_start", b[2]), ("pace_end", b[3])):
                pace = json.loads(raw) if raw else {}
                out[key] = {pool: {k: v.get(k) for k in ("weekly_used_pct", "band", "pace_ratio", "hours_to_reset")}
                            for pool, v in (pace.get("lanes") or {}).items()}
            out["nous_start"], out["nous_end"] = (json.loads(b[4]) if b[4] else None,
                                                  json.loads(b[5]) if b[5] else None)
    return out


def batch_mark(con: sqlite3.Connection, batch: str, phase: str, capacity: Mapping[str, Any] | None = None,
               note: str | None = None) -> None:
    pace = subprocess.run(["burn-pace", "--json"], capture_output=True, text=True, timeout=120).stdout or "{}"
    try:
        doc = json.loads(pace)
        # The pool's burn rate just before the batch: other sessions keep spending during it.
        doc["recent_pct_per_h"] = {row.get("pool"): (row.get("pacing") or {}).get("recent_pct_per_h")
                                   for row in ((capacity or {}).get("lanes") or {}).values() if row.get("pacing")}
        pace = json.dumps(doc)
    except json.JSONDecodeError:
        pass
    nous = json.dumps((capacity or {}).get("nous_cap"))
    if phase == "start":
        # A resumed batch keeps its first snapshot, so burn-pace deltas span the whole batch.
        con.execute("INSERT OR IGNORE INTO batches(batch, started, pace_start, nous_start, note) VALUES (?,?,?,?,?)",
                    (batch, time.time(), pace, nous, note))
    else:
        con.execute("UPDATE batches SET ended = ?, pace_end = ?, nous_end = ? WHERE batch = ?",
                    (time.time(), pace, nous, batch))
    con.commit()


# ---------------------------------------------------------------- CLI

def _read_jobs(path: str) -> list[Job]:
    text = sys.stdin.read() if path == "-" else Path(path).read_text()
    text = text.strip()
    rows = json.loads(text) if text.startswith("[") else [json.loads(l) for l in text.splitlines() if l.strip()]
    return [Job.from_dict(r) for r in rows]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="z0int dispatch", description="Factory job dispatcher")
    sub = p.add_subparsers(dest="cmd", required=True)
    cap = sub.add_parser("capacity", help="Kerdoios capacity per lane, as the dispatcher sees it")
    cap.add_argument("--json", action="store_true")
    for name in ("plan", "assign"):
        sp = sub.add_parser(name, help="dry-run a batch over a horizon" if name == "plan" else
                            "place jobs on lanes with a slot now and record the decisions")
        sp.add_argument("--jobs", required=True, help="jobs JSONL/JSON array, or - for stdin")
        sp.add_argument("--capacity", default=None, help="kerdoios.capacity.v1 JSON (default: read live)")
        sp.add_argument("--share", type=float, default=DEFAULT_SUB_SHARE,
                        help="fraction of a paid pool's hourly headroom this dispatcher may use")
        if name == "plan":
            sp.add_argument("--horizon-h", type=float, default=1.0)
        else:
            sp.add_argument("--in-flight", default="{}", help='JSON {"lane": running_jobs}')
            sp.add_argument("--ceiling", default=None, help='JSON {"lane": max_concurrent} the runner can drive')
            sp.add_argument("--batch", default=None)
    rec = sub.add_parser("record", help="record one outcome (JSON on stdin or --json-arg)")
    rec.add_argument("--json-arg", default=None)
    qa = sub.add_parser("qa", help="attach a QA agreement fraction to a finished job")
    qa.add_argument("--job-id", required=True)
    qa.add_argument("--agree", type=float, required=True)
    qa.add_argument("--qa-lane", default=None)
    bt = sub.add_parser("batch", help="mark batch start/end (snapshots burn-pace and the Nous cap)")
    bt.add_argument("phase", choices=["start", "end"])
    bt.add_argument("--batch", required=True)
    bt.add_argument("--note", default=None)
    rp = sub.add_parser("report", help="per-lane outcomes, jobs/hour, QA agreement, learned priors")
    rp.add_argument("--batch", default=None)
    a = p.parse_args(argv)
    con = connect()
    if a.cmd == "capacity":
        doc = load_capacity()
        if a.json:
            print(json.dumps(doc, indent=1, default=str))
        else:
            for lane, row in doc["lanes"].items():
                print(f"{lane:9} {row['cost_class']:12} slots={row['slots_now']:<3} {row['reason']}")
        return 0
    if a.cmd in ("plan", "assign"):
        jobs = _read_jobs(a.jobs)
        capacity = load_capacity(a.capacity)
        if a.cmd == "plan":
            out = plan(jobs, capacity, horizon_h=a.horizon_h, con=con, share=a.share)
        else:
            out = assign(jobs, capacity, in_flight=json.loads(a.in_flight),
                         ceiling=json.loads(a.ceiling) if a.ceiling else None, batch=a.batch, con=con, share=a.share)
        print(json.dumps(out, indent=1, default=str))
        return 0
    if a.cmd == "record":
        record_outcome(con, json.loads(a.json_arg or sys.stdin.read()))
        return 0
    if a.cmd == "qa":
        print(record_qa(con, a.job_id, a.agree, qa_lane=a.qa_lane))
        return 0
    if a.cmd == "batch":
        batch_mark(con, a.batch, a.phase, load_capacity(), a.note)
        return 0
    if a.cmd == "report":
        print(json.dumps(report(con, a.batch), indent=1, default=str))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
