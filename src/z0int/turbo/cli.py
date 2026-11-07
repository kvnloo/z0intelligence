"""Opt-in Turbo commands. Imports remain lazy for the ordinary lifecycle CLI."""

from __future__ import annotations
import contextlib
import fcntl
import json
from pathlib import Path
import time

from . import runtime


def register(sub):
    mode = sub.add_parser("mode", help="Bounded explicit execution modes")
    modes = mode.add_subparsers(dest="mode_name", required=True)
    turbo_mode = modes.add_parser("turbo")
    turbo_mode.add_argument("action", choices=("on", "off", "status"))
    turbo_mode.add_argument(
        "--duration", help="Time-box instead of one next task, max 1h"
    )
    turbo_mode.add_argument("--json", action="store_true", dest="as_json")
    turbo = sub.add_parser("turbo", help="Experimental bounded parallel execution")
    commands = turbo.add_subparsers(dest="turbo_cmd", required=True)
    inv = commands.add_parser(
        "inventory", help="Live catalogs, optionally measured inference"
    )
    inv.add_argument("--probe", action="store_true")
    inv.add_argument("--json", action="store_true", dest="as_json")
    status = commands.add_parser("status")
    status.add_argument("run_id", nargs="?")
    status.add_argument("--json", action="store_true", dest="as_json")
    stop = commands.add_parser("stop")
    stop.add_argument("run_id")
    stop.add_argument("--json", action="store_true", dest="as_json")
    for run in (
        commands.add_parser("run"),
        sub.add_parser("run", help="Run a task using the explicit Turbo mode flag"),
    ):
        _run_options(run)


def _run_options(run):
    run.add_argument("--task", required=True)
    run.add_argument("--repo", type=Path)
    run.add_argument("--deadline", default="10m")
    run.add_argument(
        "--context-file",
        type=Path,
        help="Owner-supplied source context, never credentials",
    )
    run.add_argument(
        "--checks-file",
        type=Path,
        help="JSON list of owner-supplied argv arrays, never shell",
    )
    run.add_argument("--max-tokens", type=int, default=12000)
    run.add_argument("--max-requests", type=int, default=10)
    run.add_argument("--concurrency", type=int, default=3)
    run.add_argument(
        "--normal",
        action="store_true",
        help="Matched single-agent Sol reference, no planner/fanout",
    )
    run.add_argument(
        "--no-code-workers",
        action="store_true",
        help="Keep repo source native; free workers get public task/context only",
    )
    run.add_argument("--jsonl", action="store_true")
    run.add_argument("--json", action="store_true", dest="as_json")


@contextlib.contextmanager
def mode_lock():
    with (runtime.root() / "mode.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def mode_state():
    try:
        state = json.loads((runtime.root() / "mode.json").read_text())
    except (OSError, ValueError):
        state = {"enabled": False}
    if state.get("expires_at") and state["expires_at"] <= time.time():
        state["enabled"] = False
        state["reason"] = "expired"
    return state


def mode(action, duration=None):
    if duration and action != "on":
        raise ValueError("--duration is only valid with on")
    with mode_lock():
        if action == "on":
            state = {
                "enabled": True,
                "scope": "duration" if duration else "next_task",
                "expires_at": time.time() + runtime.duration(duration)
                if duration
                else None,
            }
            runtime.save(runtime.root() / "mode.json", state)
        elif action == "off":
            state = {"enabled": False, "reason": "owner_off"}
            runtime.save(runtime.root() / "mode.json", state)
        else:
            state = mode_state()
    if action == "off":
        state["stop_requests"] = [
            runtime.stop(row["run_id"])
            for row in runtime.status()["runs"]
            if row["state"] not in runtime.TERMINAL
        ]
    return {"mode": "turbo", **state}


def main(args):
    try:
        if args.cmd == "mode":
            result = mode(args.action, args.duration)
        elif getattr(args, "turbo_cmd", None) == "inventory":
            result = runtime.inventory(args.probe)
        elif getattr(args, "turbo_cmd", None) == "status":
            result = runtime.status(args.run_id)
        elif getattr(args, "turbo_cmd", None) == "stop":
            result = runtime.stop(args.run_id)
        else:
            context = args.context_file.read_text() if args.context_file else ""
            checks = (
                json.loads(args.checks_file.read_text()) if args.checks_file else []
            )
            if not isinstance(checks, list) or any(
                not isinstance(a, list)
                or not a
                or any(not isinstance(x, str) or "\0" in x for x in a)
                for a in checks
            ):
                raise ValueError("checks must be a list of nonempty argv arrays")
            seconds = runtime.duration(args.deadline)

            def emit_json(row):
                print(json.dumps(row), flush=True)

            emit = emit_json if args.jsonl else None
            # An explicit run is consent even when the opt-in flag is absent/off.
            with mode_lock():
                state = mode_state()
                if state.get("enabled") and state.get("scope") == "next_task":
                    runtime.save(
                        runtime.root() / "mode.json",
                        {"enabled": False, "reason": "consumed"},
                    )
            normal = args.normal or (args.cmd == "run" and not state.get("enabled"))
            runner = runtime.Runner(
                args.task,
                args.repo,
                seconds,
                args.max_tokens,
                args.max_requests,
                args.concurrency,
                context,
                checks,
                normal,
                emit,
                no_code_workers=args.no_code_workers,
            )
            result = runner.execute()
            if not args.jsonl:
                print(json.dumps(result, indent=2))
            return 0 if result["quality"].get("verified") is True else 1
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, ImportError) as exc:
        print(json.dumps({"error": str(exc), "verified": False}))
        return 2
