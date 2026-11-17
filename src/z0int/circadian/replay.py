"""Shadow replay (spec §5.2, R11): run the policy over past desk activity and report,
per owner day, what it would have done. Read-only. The receipt is private (0600) and
holds counts and minutes only; it is the gate before quiet-phase enforcement."""

from __future__ import annotations

import bisect
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from .. import human_posture as hp
from . import adapters
from .service import _write_private, owner_date, policy_config


def replay(cfg: Mapping[str, Any], now: datetime, d: Path, days: int = 30, step_min: int = 10) -> dict[str, Any]:
    aw = adapters.activitywatch(cfg, now, hours=24 * (days + 2))
    if not aw.get("available"):
        return {"ok": False, "reason": f"activitywatch unavailable: {aw.get('reason')}"}
    rows = sorted(aw["intervals"], key=lambda r: r["start"])
    starts = [hp.parse_ts(r["start"]) for r in rows]
    tz = ZoneInfo(cfg["tz"])
    pcfg = policy_config(cfg)
    per: dict[str, dict[str, Any]] = defaultdict(lambda: {"phaseMin": defaultdict(int), "activeAfterFreezeMin": 0,
                                                           "activeInQuietMin": 0, "quality": set()})
    t = now - timedelta(days=days)
    step = timedelta(minutes=step_min)
    while t <= now:
        lo = bisect.bisect_left(starts, t - timedelta(hours=40))
        hi = bisect.bisect_right(starts, t)
        facts = {"activity": {"available": True, "intervals": rows[lo:hi]}}
        state = hp.evaluate_human_state(facts, hp.fmt(t), pcfg)
        dec = hp.policy(state, hp.fmt(t))
        key = owner_date(state, tz)
        day = per[key]
        day["phaseMin"][dec["phase"]] += step_min
        day["quality"].add(state["quality"])
        last = state.get("lastActivityAt")
        active = bool(last) and t - hp.parse_ts(last) <= step
        if active and t >= hp.parse_ts(state["anchors"]["freezeAt"]):
            day["activeAfterFreezeMin"] += step_min
        if active and dec["phase"] in hp.QUIET:
            day["activeInQuietMin"] += step_min
        t += step
    nights = []
    for key in sorted(per):
        v = per[key]
        nights.append({"ownerDate": key, "phaseMin": dict(sorted(v["phaseMin"].items())),
                       "activeAfterFreezeMin": v["activeAfterFreezeMin"],
                       "activeInQuietMin": v["activeInQuietMin"], "quality": sorted(v["quality"])})
    receipt = {"schema": "z0.circadian_replay/v1", "generatedAt": hp.fmt(now), "days": days, "stepMin": step_min,
               "source": "activitywatch desk only (phone history is not kept beyond 24 h)",
               "nights": nights,
               "summary": {"ownerDays": len(nights),
                           "medianActiveAfterFreezeMin": _median([n["activeAfterFreezeMin"] for n in nights]),
                           "daysWithProxyWake": sum(1 for n in nights if "proxy" in n["quality"])}}
    out = d / "replay"
    out.mkdir(exist_ok=True, mode=0o700)
    path = out / f"replay-{now.strftime('%Y%m%dT%H%M')}.json"
    _write_private(path, json.dumps(receipt, indent=1, sort_keys=True))
    return {"ok": True, "path": str(path), "summary": receipt["summary"]}


def _median(xs: list[int]) -> int:
    if not xs:
        return 0
    s = sorted(xs)
    return s[len(s) // 2]
