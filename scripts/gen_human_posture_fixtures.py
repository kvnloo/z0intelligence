#!/usr/bin/env python3
"""Generate the golden fixtures shared by the Python and TypeScript twins (law C4).

Writes tests/fixtures/human_posture/cases.json. Every timestamp and activity
interval is SYNTHETIC: no owner data (law C8). Copy the file unchanged to dash
``shared/fixtures/human-posture/cases.json``; both suites compare canonical
JSON byte for byte. Kinds:

  clock    evaluate_human_state with no activity facts (the bridge's fallback)
  evaluate evaluate_human_state with synthetic activity / wearable facts (Python only)
  policy   policy(state, now)
  route    route(decision, notice, now)
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from z0int import human_posture as hp  # noqa: E402

OUT = ROOT / "tests" / "fixtures" / "human_posture" / "cases.json"
CFG = dict(hp.DEFAULT_CONFIG)


def z(s: str) -> str:
    return hp.fmt(hp.parse_ts(s))


def plus(ts: str, minutes: int) -> str:
    return hp.fmt(hp.parse_ts(ts) + timedelta(minutes=minutes))


def iv(start: str, end: str, source: str = "desk") -> dict:
    return {"start": z(start), "end": z(end), "source": source}


cases: list[dict] = []


def clock(name: str, now: str, facts: dict | None = None, config: dict | None = None) -> dict:
    cfg = {**CFG, **(config or {})}
    f = facts or {}
    state = hp.evaluate_human_state(f, z(now), cfg)
    cases.append({"name": name, "kind": "clock", "input": {"facts": f, "now": z(now), "config": cfg}, "expected": state})
    policy(name + " / policy", state, z(now))
    return state


def evaluate(name: str, now: str, facts: dict) -> dict:
    state = hp.evaluate_human_state(facts, z(now), CFG)
    cases.append({"name": name, "kind": "evaluate", "input": {"facts": facts, "now": z(now), "config": CFG}, "expected": state})
    policy(name + " / policy", state, z(now))
    return state


def policy(name: str, state: dict, now: str) -> dict:
    d = hp.policy(state, now)
    cases.append({"name": name, "kind": "policy", "input": {"state": state, "now": now}, "expected": d})
    return d


def route(name: str, decision: dict, notice: dict, now: str) -> None:
    r = hp.route(decision, notice, now)
    cases.append({"name": name, "kind": "route", "input": {"decision": decision, "notice": notice, "now": now}, "expected": r})


# All anchors below are the SYNTHETIC defaults (America/New_York, wake 07:30, night mode
# 22:00, bed 00:30 -> freeze 21:30). Owner day 2026-10-27 (EDT, UTC-4): wake 11:30Z,
# peak 12:15Z, dip 17:30Z, second 19:30Z, freeze 01:30Z, pre-sleep 03:30Z, bed 04:30Z,
# next wake 11:30Z.
WAKE = "2026-10-27T11:30:00Z"
for label, m in [("wake-1", -1), ("wake", 0), ("inertia end-1", 44), ("peak start", 45), ("peak end-1", 359),
                 ("dip start", 360), ("dip end-1", 479), ("second start", 480), ("freeze-1", 839), ("freeze", 840),
                 ("pre-sleep-1", 959), ("pre-sleep", 960), ("bed-1", 1019), ("bed", 1020), ("next wake-1", 1439)]:
    clock(f"clock day {label}", plus(WAKE, m))

# --- DST night 2026-11-01 (02:00 EDT -> 01:00 EST), owner day 10-31 (C17)
for label, ts in [("23:59 EDT", "2026-11-01T03:59:00Z"), ("00:59 EDT", "2026-11-01T04:59:00Z"),
                  ("01:00 EDT first", "2026-11-01T05:00:00Z"), ("01:00 EST second", "2026-11-01T06:00:00Z"),
                  ("02:00 EST", "2026-11-01T07:00:00Z"), ("07:30 EST wake", "2026-11-01T12:30:00Z"),
                  ("21:30 EST freeze", "2026-11-02T02:30:00Z")]:
    clock(f"clock DST {label}", ts)
# an anchor inside the repeated hour takes its first occurrence (fold=0): bed 01:30 EDT = 05:30Z
for label, ts in [("bed in repeated hour -1", "2026-11-01T05:29:00Z"), ("bed in repeated hour", "2026-11-01T05:30:00Z"),
                  ("bed in repeated hour, second 01:30", "2026-11-01T06:30:00Z")]:
    clock(f"clock DST {label}", ts, config={"bedTarget": "01:30"})

# --- night mode binds the freeze when bed is late (bed 02:00 -> freeze = night mode 22:00 = 02:00Z)
clock("clock bed 02:00 before freeze", "2026-10-28T01:59:00Z", config={"bedTarget": "02:00"})
clock("clock bed 02:00 freeze at night mode", "2026-10-28T02:00:00Z", config={"bedTarget": "02:00"})

# --- done for today (C10) and its expiry at the next wake target
clock("done at 22:30 -> disengaged", "2026-10-28T02:45:00Z", facts={"doneAt": "2026-10-28T02:30:00Z"})
clock("done at 17:00 -> disengaged by day", "2026-10-27T21:30:00Z", facts={"doneAt": "2026-10-27T21:00:00Z"})
clock("done from yesterday ignored", "2026-10-28T13:30:00Z", facts={"doneAt": "2026-10-28T02:30:00Z"})

# --- counters: budget and interrupt cap (C3)
peak_now = plus(WAKE, 120)
clock("peak cap used up", peak_now, facts={"counters": {"hourKey": peak_now[:13], "delivered": 2,
                                                         "blockStart": plus(WAKE, 45), "interrupts": 3}})
clock("peak counters from another hour", peak_now, facts={"counters": {"hourKey": "2026-10-27T10", "delivered": 6}})
clock("critical opt-in config", "2026-10-28T06:00:00Z", config={"criticalOptIn": True})

# --- activity-based wake (proxy), real-time sleep rule (R2), phone blips (§1.2)
night = [iv("2026-10-26T23:00:00Z", "2026-10-27T04:00:00Z"),          # evening until 00:00 local
         iv("2026-10-27T12:00:00Z", "2026-10-27T13:30:00Z")]           # wake 08:00 local
evaluate("proxy wake", "2026-10-27T12:20:00Z", {"activity": {"available": True, "intervals": night}})
evaluate("proxy wake: peak", "2026-10-27T13:00:00Z", {"activity": {"available": True, "intervals": night}})
blip = night[:1] + [iv("2026-10-27T09:00:00Z", "2026-10-27T09:03:00Z", "phone"),
                    iv("2026-10-27T12:00:00Z", "2026-10-27T12:40:00Z", "phone")]
evaluate("phone blip does not end the night", "2026-10-27T12:30:00Z", {"activity": {"available": True, "intervals": blip}})
late = [iv("2026-10-27T11:00:00Z", "2026-10-28T04:40:00Z")]            # awake until 00:40 local, after bed
evaluate("active after bed: pre-sleep", "2026-10-28T04:50:00Z", {"activity": {"available": True, "intervals": late}})
evaluate("30 min after last activity: sleep", "2026-10-28T05:11:00Z", {"activity": {"available": True, "intervals": late}})
outing = [iv("2026-10-27T11:00:00Z", "2026-10-27T15:00:00Z"), iv("2026-10-27T19:00:00Z", "2026-10-27T21:00:00Z")]
evaluate("afternoon gap is not a night", "2026-10-27T21:00:00Z", {"activity": {"available": True, "intervals": outing}})
allnight = [iv("2026-10-27T11:00:00Z", "2026-10-28T13:00:00Z")]       # no gap through the next morning
evaluate("all-nighter: still the old day before the wake target", "2026-10-28T11:00:00Z",
         {"activity": {"available": True, "intervals": allnight}})
evaluate("all-nighter: rolls over at the wake target", "2026-10-28T11:45:00Z",
         {"activity": {"available": True, "intervals": allnight}})
asleep = [iv("2026-10-27T11:00:00Z", "2026-10-28T05:00:00Z")]          # nothing after 01:00 local
evaluate("asleep past the wake target stays asleep", "2026-10-28T13:00:00Z",
         {"activity": {"available": True, "intervals": asleep}})
evaluate("activity source down: clock default", "2026-10-27T14:00:00Z", {"activity": {"available": False}})
evaluate("no wearable, no adb", "2026-10-28T02:00:00Z", {})

# --- wearable: measured when fresh, ignored when stale (C9)
wear = {"available": True, "lastSessionStart": "2026-10-27T04:10:00Z", "lastSessionEnd": "2026-10-27T11:05:00Z"}
evaluate("wearable fresh -> measured", "2026-10-27T12:00:00Z", {"activity": {"available": True, "intervals": night}, "wearable": wear})
evaluate("wearable stale -> proxy", "2026-10-29T12:20:00Z", {
    "activity": {"available": True, "intervals": [iv("2026-10-28T23:00:00Z", "2026-10-29T04:00:00Z"),
                                                  iv("2026-10-29T12:00:00Z", "2026-10-29T12:20:00Z")]},
    "wearable": wear})


# --- route: every class in representative phases (C2, C3, C5, C10, C14)
def decision_at(now: str, facts: dict | None = None, config: dict | None = None) -> dict:
    st = hp.evaluate_human_state(facts or {}, now, {**CFG, **(config or {})})
    return hp.policy(st, now)


points = {
    "inertia": plus(WAKE, 10), "peak": plus(WAKE, 120), "dip": plus(WAKE, 400), "second": plus(WAKE, 600),
    "wind-down": plus(WAKE, 870), "pre-sleep": plus(WAKE, 980), "sleep": plus(WAKE, 1100),
}
notices = [
    ("report", {"cls": "report", "producer": "dash-notify"}),
    ("default class", {"producer": "z0-phone-notify"}),
    ("decision", {"cls": "decision"}),
    ("decision tonight plan", {"cls": "decision", "tonightPlan": True}),
    ("digest", {"cls": "digest"}),
    ("critical from agent", {"cls": "critical", "producer": "dash-notify", "irreversible": True}),
    ("critical allowlisted", {"cls": "critical", "producer": "backup-guard", "irreversible": True}),
    ("critical allowlisted reversible", {"cls": "critical", "producer": "gate-executor"}),
    ("unknown class", {"cls": "shout"}),
]
for pname, now in points.items():
    dec = decision_at(now)
    for nname, n in notices:
        route(f"route {pname} {nname}", dec, n, now)
full = decision_at(peak_now, {"counters": {"hourKey": peak_now[:13], "delivered": 3,
                                           "blockStart": plus(WAKE, 45), "interrupts": 3}})
for nname, n in notices[:5]:
    route(f"route peak cap used {nname}", full, n, peak_now)
opt = decision_at(points["sleep"], config={"criticalOptIn": True})
route("route sleep critical opt-in", opt, {"cls": "critical", "producer": "fsm-supervisor", "irreversible": True}, points["sleep"])
second_full = decision_at(points["second"], {"counters": {"hourKey": points["second"][:13], "delivered": 2}})
route("route second plan no budget", second_full, {"cls": "decision", "tonightPlan": True}, points["second"])
done_dec = decision_at("2026-10-28T03:00:00Z", {"doneAt": "2026-10-28T02:30:00Z"})
route("route after done report", done_dec, {"cls": "report"}, "2026-10-28T03:00:00Z")


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = {"schema": "dash.human_posture_fixtures/v1", "note": "SYNTHETIC: generated by scripts/gen_human_posture_fixtures.py",
           "cases": cases}
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"{len(cases)} cases -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
