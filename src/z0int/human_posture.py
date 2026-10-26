"""Human posture v1: the owner's day phase, and what agents may ask of the owner now.

Spec: workspace ``.design/circadian/CIRCADIAN-SPEC.md`` (private), WP1 with the
Revisions section applied (R2, R3, R5, R7, R11).

Three pure functions, the Python twin of dash ``shared/human-posture.ts`` and
``shared/interaction-policy.ts``:

* ``evaluate_human_state(facts, now, config) -> HumanState``
  builds the owner-day anchors (wake, freeze, bed) from activity facts with a
  fallback ladder (wearable ``measured`` -> activity ``proxy`` -> clock
  ``default``). A night with no data is never scored as a good night.
* ``policy(state, now) -> Decision``: phase, posture and notification budgets.
* ``route(decision, notice, now) -> Route``: deliver, defer or digest one notice.

``gate`` is the finite core of ``route``. ``bend/circadian`` holds the same
function in Bend 2 with the laws proven over it; ``tests/test_circadian_bend.py``
checks the two agree on all 1024 inputs.

Purity (law C4): no clock, disk, network, environment or randomness inside
these functions. ``zoneinfo`` is the one exception: it reads the tz database,
which is static data. Every timestamp leaves as UTC ``YYYY-MM-DDTHH:MM:SSZ``;
every number is an integer; ``canonical`` gives sorted-key compact JSON, so the
TypeScript twin can compare byte for byte.

Privacy (law C8, R4): the state holds times of day, which are health-derived
data. It stays on this host. Only ``public_view`` (phase, decisions allowed,
next peak) may go to a packet that leaves the host.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

STATE_SCHEMA = "dash.human_state/v1"
DECISION_SCHEMA = "dash.interaction_decision/v1"
ROUTE_SCHEMA = "dash.notice_route/v1"

PHASES = ("SLEEP", "INERTIA", "PEAK", "DIP", "SECOND", "WIND_DOWN", "PRE_SLEEP", "DISENGAGED")
QUIET = frozenset({"WIND_DOWN", "PRE_SLEEP", "SLEEP", "DISENGAGED"})
DAY = frozenset({"INERTIA", "PEAK", "DIP", "SECOND"})
CLASSES = ("critical", "decision", "report", "digest")
ACTIONS = ("deliver", "defer", "digest")
CRITICAL_ALLOWLIST = frozenset({"gate-executor", "fsm-supervisor", "backup-guard"})

# SYNTHETIC neutral defaults. The owner's real anchors are private (R4) and come only
# from the host config (~/.config/circadian/config.json), never from this file.
DEFAULT_CONFIG: dict[str, Any] = {
    "tz": "America/New_York",
    "wakeTarget": "07:30",
    "bedTarget": "00:30",
    "nightModeAt": "22:00",
    "criticalOptIn": False,   # R2: no critical sound in SLEEP unless the owner opts in
    "enforce": "quiet_only",  # C12: only the conservative rules enforce in WP1
    "arm": "B",
}

# Phase lengths from wake, in minutes (§2.6). PROVISIONAL (R5).
INERTIA_MIN = 45
PEAK_END_MIN = 360
DIP_END_MIN = 480
PRE_SLEEP_MIN = 60
FREEZE_BEFORE_BED_MIN = 180
SLEEP_AFTER_ACTIVITY_MIN = 30   # R2 real-time sleep rule
SLEEP_GAP_MIN = 180             # a no-activity gap this long can be a night
PHONE_SUSTAINED_MIN = 10        # §1.2: phone use counts toward wake only when sustained
WEARABLE_FRESH_H = 36           # C9 freshness, PROVISIONAL
DAY_PIVOT_HOUR = 4              # a wake before 04:00 local belongs to the previous owner day

# §4.2 default rows. readiness is always "unknown" in WP1, so the "normal" row is used (R5).
ROWS: dict[str, dict[str, Any]] = {
    "SLEEP": {"posture": "OFFLOAD", "notify": 0, "cap": 0, "decisions": "none", "batch": "morning_digest",
              "verbosity": "digest_only", "channel": "dash-quiet", "work": "none"},
    "INERTIA": {"posture": "RESERVE", "notify": 1, "cap": 0, "decisions": "none", "batch": "morning_digest",
                "verbosity": "digest_only", "channel": "dash-updates", "work": "none"},
    "PEAK": {"posture": "BURN", "notify": 6, "cap": 3, "decisions": "all", "batch": "now",
             "verbosity": "full", "channel": "dash-focus", "work": "hard"},
    "DIP": {"posture": "BALANCED", "notify": 3, "cap": 1, "decisions": "bulk_only", "batch": "block_boundary",
            "verbosity": "terse", "channel": "dash-updates", "work": "light"},
    "SECOND": {"posture": "BALANCED", "notify": 2, "cap": 1, "decisions": "tonight_plans_only", "batch": "hourly",
               "verbosity": "terse", "channel": "dash-updates", "work": "normal"},
    "WIND_DOWN": {"posture": "OFFLOAD", "notify": 0, "cap": 0, "decisions": "none", "batch": "morning_digest",
                  "verbosity": "digest_only", "channel": "dash-quiet", "work": "none"},
    "PRE_SLEEP": {"posture": "OFFLOAD", "notify": 0, "cap": 0, "decisions": "none", "batch": "morning_digest",
                  "verbosity": "digest_only", "channel": "dash-quiet", "work": "none"},
    "DISENGAGED": {"posture": "OFFLOAD", "notify": 0, "cap": 0, "decisions": "none", "batch": "morning_digest",
                   "verbosity": "digest_only", "channel": "dash-quiet", "work": "none"},
}
# C14: only dash-focus may make a sound, and only PEAK uses it.
SOUND_CHANNELS = frozenset({"dash-focus"})


# ---------------------------------------------------------------- time helpers

def parse_ts(value: str) -> datetime:
    """ISO 8601 with an explicit offset (``Z`` allowed) -> aware UTC datetime."""
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a non-empty string")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"timestamp needs an explicit offset: {value!r}")
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hhmm(value: str) -> tuple[int, int]:
    hh, mm = value.split(":")
    h, m = int(hh), int(mm)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"bad clock time {value!r}")
    return h, m


def local_at(day: date, clock: str, tz: ZoneInfo) -> datetime:
    """Wall time on a local date -> UTC. Ambiguous times take the first occurrence (fold=0)."""
    h, m = _hhmm(clock)
    return datetime.combine(day, time(h, m), tzinfo=tz).astimezone(timezone.utc)


def _minutes(a: datetime, b: datetime) -> int:
    """Whole minutes from a to b (floor)."""
    return int((b - a).total_seconds() // 60)


def canonical(obj: Any) -> str:
    """Sorted keys, compact separators, no floats: the C4 byte form shared with TypeScript."""
    _no_floats(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _no_floats(obj: Any) -> None:
    if isinstance(obj, float):
        raise TypeError("canonical form has no floats")
    if isinstance(obj, Mapping):
        for v in obj.values():
            _no_floats(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _no_floats(v)


# ---------------------------------------------------------------- evaluate

def _merge(intervals: Sequence[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    out: list[tuple[datetime, datetime]] = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1] + timedelta(minutes=1):
            if e > out[-1][1]:
                out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def _is_night_gap(start: datetime, end: datetime, tz: ZoneInfo) -> bool:
    """A long gap is a night if it starts 21:00-06:00 local or covers a 06:00 local. PROVISIONAL."""
    if _minutes(start, end) < SLEEP_GAP_MIN:
        return False
    ls = start.astimezone(tz)
    if ls.hour >= 21 or ls.hour < 6:
        return True
    d = ls.date()
    while True:
        six = local_at(d, "06:00", tz)
        if six > end:
            return False
        if six >= start:
            return True
        d += timedelta(days=1)


def _owner_date(wake: datetime, tz: ZoneInfo) -> date:
    lw = wake.astimezone(tz)
    return lw.date() if lw.hour >= DAY_PIVOT_HOUR else lw.date() - timedelta(days=1)


def _clock_on_owner_day(owner_date: date, clock: str, tz: ZoneInfo) -> datetime:
    """Clock times before noon belong to the night after the owner date (bed 00:30 -> next date)."""
    h, _ = _hhmm(clock)
    return local_at(owner_date + timedelta(days=1) if h < 12 else owner_date, clock, tz)


def _activity(facts: Mapping[str, Any], now: datetime) -> tuple[bool, list[tuple[datetime, datetime, str]]]:
    act = facts.get("activity") or {}
    if not act.get("available"):
        return False, []
    rows: list[tuple[datetime, datetime, str]] = []
    for item in act.get("intervals") or []:
        s, e = parse_ts(item["start"]), parse_ts(item["end"])
        if e < s or s > now:
            continue
        rows.append((s, min(e, now), str(item.get("source") or "desk")))
    return True, rows


def evaluate_human_state(facts: Mapping[str, Any], now_iso: str, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Facts from the adapters -> ``dash.human_state/v1``. Pure."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    tz = ZoneInfo(cfg["tz"])
    now = parse_ts(now_iso)
    available, rows = _activity(facts, now)

    # all owner activity (any surface) decides "awake now"; only sustained activity ends a night
    all_iv = _merge([(s, e) for s, e, _ in rows])
    sustained = _merge([(s, e) for s, e, src in rows
                        if src != "phone" or _minutes(s, e) >= PHONE_SUSTAINED_MIN])
    last_activity = all_iv[-1][1] if all_iv else None

    wake: datetime | None = None
    onset: datetime | None = None
    quality, source = "default", "clock"
    horizon = now - timedelta(hours=36)
    # the latest completed night gap inside the last 36 h (gap end = first sustained activity)
    for i in range(len(sustained) - 1, 0, -1):
        gs, ge = sustained[i - 1][1], sustained[i][0]
        if ge < horizon:
            break
        if _is_night_gap(gs, ge, tz):
            wake, onset, quality, source = ge, gs, "proxy", "activity"
            break
    if wake is None and sustained and sustained[0][0] >= horizon + timedelta(minutes=SLEEP_GAP_MIN):
        # the first activity in the window follows a long silence before the window edge
        if _is_night_gap(horizon, sustained[0][0], tz):
            wake, onset, quality, source = sustained[0][0], None, "proxy", "activity"

    wear = facts.get("wearable") or {}
    if wear.get("available") and wear.get("lastSessionEnd"):
        ws, we = parse_ts(wear["lastSessionStart"]), parse_ts(wear["lastSessionEnd"])
        fresh = timedelta(0) <= now - we <= timedelta(hours=WEARABLE_FRESH_H)
        # measured wins only for the same night (or a later one) as the proxy
        if fresh and we <= now and (wake is None or we >= wake - timedelta(hours=6)):
            wake, onset, quality, source = we, ws, "measured", "wearable"

    if wake is None:
        d = now.astimezone(tz).date()
        wake = local_at(d, cfg["wakeTarget"], tz)
        if wake > now:
            wake = local_at(d - timedelta(days=1), cfg["wakeTarget"], tz)

    od = _owner_date(wake, tz)
    wake_target_next = local_at(od + timedelta(days=1), cfg["wakeTarget"], tz)
    # Rollover (R2: SLEEP ends at the first owner activity). No night gap was seen,
    # but the clock has passed the next wake target and the owner is active again
    # (an all-nighter, or a night the desk did not see): that activity starts a new day.
    for _ in range(3):
        if now < wake_target_next:
            break
        after = [max(s, wake_target_next) for s, e in sustained if e > wake_target_next and s <= now]
        if not after:
            break
        wake, onset, quality, source = after[0], None, "proxy", "rollover"
        od = _owner_date(wake, tz)
        wake_target_next = local_at(od + timedelta(days=1), cfg["wakeTarget"], tz)
    bed = _clock_on_owner_day(od, cfg["bedTarget"], tz)
    night_mode = _clock_on_owner_day(od, cfg["nightModeAt"], tz)
    freeze = min(night_mode, bed - timedelta(minutes=FREEZE_BEFORE_BED_MIN))

    done = facts.get("doneAt")
    disengaged = None
    if done:
        d_at = parse_ts(done)
        if wake <= d_at <= now and now < wake_target_next:
            disengaged = fmt(wake_target_next)

    counters = facts.get("counters") or {}
    hour_key = now.strftime("%Y-%m-%dT%H")
    delivered = int(counters.get("delivered", 0)) if counters.get("hourKey") == hour_key else 0
    peak_start = wake + timedelta(minutes=INERTIA_MIN)
    interrupts = int(counters.get("interrupts", 0)) if counters.get("blockStart") == fmt(peak_start) else 0

    return {
        "schema": STATE_SCHEMA,
        "anchors": {
            "wakeAt": fmt(wake), "freezeAt": fmt(freeze), "bedTarget": fmt(bed),
            "nightModeAt": fmt(night_mode), "wakeTarget": fmt(wake_target_next),
        },
        "sleepOnsetLast": fmt(onset) if onset else None,
        "quality": quality,
        "wakeSource": source,
        "readiness": "unknown",
        "activityAvailable": bool(available),
        "lastActivityAt": fmt(last_activity) if last_activity else None,
        "disengagedUntil": disengaged,
        "deliveredThisHour": max(0, delivered),
        "interruptsThisBlock": max(0, interrupts),
        "hardBlocksUsed": 0,
        "arm": str(cfg["arm"]),
        "enforce": str(cfg["enforce"]),
        "criticalOptIn": bool(cfg["criticalOptIn"]),
    }


def clock_only_state(now_iso: str, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """No adapters at all: the clock-only state (quality ``default``) the Dash bridge also computes."""
    return evaluate_human_state({}, now_iso, config)


# ---------------------------------------------------------------- policy

def _sleep_start(state: Mapping[str, Any]) -> datetime:
    bed = parse_ts(state["anchors"]["bedTarget"])
    last = state.get("lastActivityAt")
    if last:
        return max(bed, parse_ts(last) + timedelta(minutes=SLEEP_AFTER_ACTIVITY_MIN))
    return bed


def phase_of(state: Mapping[str, Any], now: datetime) -> str:
    a = state["anchors"]
    wake, freeze, bed = parse_ts(a["wakeAt"]), parse_ts(a["freezeAt"]), parse_ts(a["bedTarget"])
    if now < wake or now >= _sleep_start(state):
        return "SLEEP"
    until = state.get("disengagedUntil")
    if until and now < parse_ts(until):
        return "DISENGAGED"
    if now >= bed - timedelta(minutes=PRE_SLEEP_MIN):
        return "PRE_SLEEP"
    if now >= freeze:
        return "WIND_DOWN"
    e = _minutes(wake, now)
    if e < INERTIA_MIN:
        return "INERTIA"
    if e < PEAK_END_MIN:
        return "PEAK"
    if e < DIP_END_MIN:
        return "DIP"
    return "SECOND"


def _next_transition(phase: str, state: Mapping[str, Any], now: datetime) -> datetime:
    a = state["anchors"]
    wake, freeze, bed = parse_ts(a["wakeAt"]), parse_ts(a["freezeAt"]), parse_ts(a["bedTarget"])
    sleep_at = _sleep_start(state)
    if phase == "SLEEP":
        wt = parse_ts(a["wakeTarget"])
        return wt if wt > now else now
    if phase == "DISENGAGED":
        return min(parse_ts(state["disengagedUntil"]), sleep_at)
    if phase == "PRE_SLEEP":
        return sleep_at
    if phase == "WIND_DOWN":
        return bed - timedelta(minutes=PRE_SLEEP_MIN)
    ends = {"INERTIA": INERTIA_MIN, "PEAK": PEAK_END_MIN, "DIP": DIP_END_MIN}
    if phase in ends:
        return min(wake + timedelta(minutes=ends[phase]), freeze)
    return freeze


def _clock_or_none(ts: str | None) -> str:
    return f"{ts[11:16]}Z" if ts else "none"


def policy(state: Mapping[str, Any], now_iso: str) -> dict[str, Any]:
    """``dash.human_state/v1`` -> ``dash.interaction_decision/v1``. Pure, deterministic."""
    now = parse_ts(now_iso)
    phase = phase_of(state, now)
    row = ROWS[phase]
    a = state["anchors"]
    wake = parse_ts(a["wakeAt"])
    peak_start = wake + timedelta(minutes=INERTIA_MIN)
    wake_target = parse_ts(a["wakeTarget"])
    next_peak = peak_start if now < wake + timedelta(minutes=PEAK_END_MIN) and phase in DAY else wake_target + timedelta(minutes=INERTIA_MIN)
    digest_at = wake + timedelta(minutes=30) if phase in DAY else wake_target + timedelta(minutes=30)
    budget, cap = row["notify"], row["cap"]
    left = budget - int(state.get("deliveredThisHour", 0))
    if cap:
        left = min(left, cap - int(state.get("interruptsThisBlock", 0)))
    cap_remaining = max(0, left)
    posture = row["posture"]
    work = row["work"]
    if phase == "SECOND" and int(state.get("hardBlocksUsed", 0)) >= 2:
        posture, work = "RESERVE", "light"
    nxt = _next_transition(phase, state, now)
    e = _minutes(wake, now)
    arithmetic = (
        f"phase={phase}: wake {fmt(wake)[11:16]}Z ({state.get('quality', 'default')}) "
        f"+{max(0, e) // 60}h{max(0, e) % 60:02d}m; freeze {a['freezeAt'][11:16]}Z; bed {a['bedTarget'][11:16]}Z; "
        f"last activity {_clock_or_none(state.get('lastActivityAt'))}; "
        f"readiness unknown -> normal row; cap {cap_remaining}/{budget}"
    )
    return {
        "schema": DECISION_SCHEMA,
        "phase": phase,
        "posture": posture,
        "notifyBudgetPerHour": budget,
        "interruptCapPerBlock": cap,
        "capRemaining": cap_remaining,
        "allowDecisions": row["decisions"],
        "batchWindow": row["batch"],
        "verbosity": row["verbosity"],
        "channel": row["channel"],
        "workAssignment": work,
        "captureMode": phase in QUIET,
        "captureEnforced": False,   # R2b: capture is shadow until the owner opts in (H1)
        "breakNudge": "none",
        "nextTransitionAt": fmt(nxt),
        "peakStartAt": fmt(peak_start),
        "nextPeakAt": fmt(next_peak),
        "digestEarliestAt": fmt(digest_at),
        "quality": str(state.get("quality", "default")),
        "enforce": str(state.get("enforce", "quiet_only")),
        "criticalOptIn": bool(state.get("criticalOptIn", False)),
        "arithmetic": arithmetic,
    }


# ---------------------------------------------------------------- route

def gate(phase: str, cls: str, allowlisted: bool, irreversible: bool, optin: bool, plan: bool, capok: bool) -> str:
    """The finite core of ``route``; Bend twin in ``bend/circadian/gate.bend``."""
    quiet = phase in QUIET
    if cls == "critical":
        if allowlisted and irreversible:
            if quiet:
                return "deliver" if optin else "digest"
            return "deliver"
        cls = "report"  # C5: a non-allowlisted critical is a report
    if quiet:
        return "digest"
    if cls == "decision":
        if phase == "PEAK" or (phase == "SECOND" and plan):
            return "deliver" if capok else "defer"
        return "defer"
    if cls == "report":
        if phase == "INERTIA":
            return "digest"
        if phase == "PEAK":
            return "deliver" if capok else "defer"
        return "defer"
    # digest class
    if phase == "INERTIA":
        return "deliver"
    return "deliver" if capok else "defer"


def _next_hour(now: datetime) -> datetime:
    return now.replace(minute=0, second=0) + timedelta(hours=1)


def route(decision: Mapping[str, Any], notice: Mapping[str, Any], now_iso: str) -> dict[str, Any]:
    """One notice -> deliver now, defer to ``releaseAt``, or fold into the morning digest. Pure."""
    now = parse_ts(now_iso)
    cls = notice.get("cls") or "report"   # §4.2: agent reports default to report
    if cls not in CLASSES:
        cls = "report"
    producer = str(notice.get("producer") or "")
    allowlisted = producer in CRITICAL_ALLOWLIST
    irreversible = bool(notice.get("irreversible"))
    plan = bool(notice.get("tonightPlan"))
    phase = decision["phase"]
    capok = int(decision["capRemaining"]) > 0
    action = gate(phase, cls, allowlisted, irreversible, bool(decision.get("criticalOptIn")), plan, capok)
    effective = cls if cls != "critical" or (allowlisted and irreversible) else "report"
    release: str | None = None
    if action == "digest":
        release = decision["digestEarliestAt"]
        reason = "quiet phase: held for the morning digest" if phase in QUIET else "inertia: folded into the morning digest"
    elif action == "defer":
        nxt = parse_ts(decision["nextTransitionAt"])
        if effective == "decision" and not (phase == "PEAK" or (phase == "SECOND" and plan)):
            release = decision["nextPeakAt"]
            reason = f"decisions are not asked in {phase}; next peak"
        elif phase == "DIP":
            release = fmt(nxt)
            reason = "dip: batched to the block boundary"
        else:
            release = fmt(min(_next_hour(now), nxt))
            reason = f"{phase}: budget or hourly batch"
    else:
        reason = "allowlisted irreversible critical" if effective == "critical" else f"{phase}: within budget"
    # C14: the channel is the phase's channel; only PEAK's dash-focus may sound, even for critical
    channel = decision["channel"]
    return {
        "schema": ROUTE_SCHEMA,
        "action": action,
        "cls": effective,
        "channel": channel,
        "releaseAt": release,
        "reason": reason,
    }


def public_view(decision: Mapping[str, Any]) -> dict[str, Any]:
    """R4: the only fields allowed in a packet that may leave this host."""
    return {"allowDecisions": decision["allowDecisions"], "nextPeakAt": decision["nextPeakAt"]}
