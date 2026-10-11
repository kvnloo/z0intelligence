"""Run the Kerdoios free-fanout plan. Five free slots, never Cursor.

Always free-only: a slot without validated zero-cost evidence is refused, not called.
402 and 429 jump to the next validated free provider in the same call.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .receipt import append_receipt
from .worker_routing import blocked_provider, configuration, execute_plan, free_route

KERDOIOS_ROOT = Path.home() / ".hermes/profiles/chiefstaff/plugins/kerdoios"


def fanout_plan(workers: int = 5) -> dict:
    root = str(KERDOIOS_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    from kerdoios.free_fanout import build_fanout

    return build_fanout([], workers=workers)


def _plan_for(slot: dict) -> dict:
    return {
        "candidates": [{"provider": slot["provider"], "model": slot["model"]}],
        "source": "z0int.free_fanout",
        "category": "probe",
        "reason": "free fanout probe",
        "harness": "omp",
        "caller_trace_id": "free-fanout",
    }


def _one(slot: dict, policy: dict, providers: dict) -> dict:
    if blocked_provider(slot["provider"]):
        return {"slot": slot["slot"], "ok": False, "provider": slot["provider"], "error": "cursor_or_paid_blocked"}
    try:
        if free_route(policy, slot["provider"], slot["model"]) is None:
            return {"slot": slot["slot"], "ok": False, "provider": slot["provider"], "model": slot["model"], "error": "no_validated_free_route"}
        result = execute_plan(
            {"task": "Reply with the single word pong.", "parent_agent": "free-fanout", "max_tokens": 128, "free_only": True},
            policy,
            providers,
            _plan_for(slot),
            receipt_sink=append_receipt,
        )
    except Exception as exc:
        return {"slot": slot["slot"], "ok": False, "provider": slot["provider"], "error": type(exc).__name__}
    return {
        "slot": slot["slot"],
        "ok": result["ok"],
        "provider": result.get("provider"),
        "model": result.get("model"),
        "refusal_reason": result.get("refusal_reason"),
        "attempts": [
            {
                "provider": row.get("provider"),
                "model": row.get("model"),
                "status": (row.get("extra") or {}).get("status"),
                "http_status": (row.get("extra") or {}).get("http_status"),
                "finish": (row.get("extra") or {}).get("finish_reason"),
                "sidestep": (row.get("extra") or {}).get("sidestep"),
            }
            for row in result.get("attempts") or []
        ],
    }


def run(workers: int = 5) -> dict:
    plan = fanout_plan(workers)
    if any(blocked_provider(slot["provider"]) for slot in plan["slots"]):
        raise RuntimeError("fanout selected a paid parent")
    policy, providers = configuration()
    with ThreadPoolExecutor(max_workers=max(1, len(plan["slots"]))) as pool:
        slots = list(pool.map(lambda slot: _one(slot, policy, providers), plan["slots"]))
    working = [slot for slot in slots if slot["ok"]]
    return {
        "schema": "z0int.free_fanout_probe.v1",
        "cursor_used": any(blocked_provider(a["provider"]) for slot in slots for a in slot.get("attempts", [])),
        "requested_workers": workers,
        "working": len(working),
        "slots": slots,
        "plan": plan,
    }


def main(argv: list[str] | None = None) -> int:
    workers = 5
    args = list(sys.argv[1:] if argv is None else argv)
    if "--workers" in args:
        workers = int(args[args.index("--workers") + 1])
    report = run(workers)
    print(json.dumps(report, indent=2))
    return 0 if report["working"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
