"""``python -m z0int.carly_nav`` / ``z0int carly-nav``.

decide   stdin {utterance, candidates:[{id,label}]} -> stdout decision (hot path)
record   stdin {kind: episode|outcome|universe, ...}  (inlet events from Tern)
learn    mine episodes + Carly transcripts; promote, retire, refresh exemplars
tune     live replay to size the option budget (makes Jev calls)
status   catalog, policy and inlet counts
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Any

from . import store

OUTCOMES = {"applied", "fallback", "undone", "error"}


def _stdin_json() -> dict[str, Any]:
    try:
        value = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def cmd_decide() -> dict[str, Any]:
    from .decide import NONE_ID, decide

    request = _stdin_json()
    candidates = [c for c in request.get("candidates") or [] if isinstance(c, dict)]
    try:
        return decide(str(request.get("utterance") or ""), candidates)
    except Exception as error:  # the hot path must always answer; Carly takes over
        return {"action": NONE_ID, "reason": f"error: {type(error).__name__}"}


def cmd_record() -> dict[str, Any]:
    event = _stdin_json()
    kind = event.get("kind")
    if kind == "episode" and event.get("id") and event.get("utterance"):
        store.append_jsonl("episodes.jsonl", {
            "id": str(event["id"]), "ts": store.now(), "backend": str(event.get("backend") or "local"),
            "utterance": str(event["utterance"])[:500], "offered": [],
            "decision": event.get("decision") or {}, "latency_ms": event.get("latency_ms"),
        })
    elif kind == "outcome" and event.get("episode") and event.get("outcome") in OUTCOMES:
        store.append_jsonl("outcomes.jsonl", {
            "episode": str(event["episode"]), "outcome": event["outcome"], "ts": store.now(),
        })
    elif kind == "universe" and isinstance(event.get("actions"), list):
        actions = [{"id": str(a["id"]), "label": str(a.get("label") or "")}
                   for a in event["actions"] if isinstance(a, dict) and a.get("id")]
        store.write_json("universe.json", {"ts": store.now(), "actions": actions})
    else:
        return {"ok": False, "error": "unknown or incomplete event"}
    return {"ok": True}


def cmd_status() -> dict[str, Any]:
    catalog = store.load_catalog()
    outcomes = Counter(row.get("outcome") for row in store.read_jsonl("outcomes.jsonl"))
    episodes = Counter(row.get("backend") for row in store.read_jsonl("episodes.jsonl"))
    universe = store.read_json("universe.json") or {}
    return {
        "state": str(store.root()),
        "policy": catalog["policy"],
        "actions": {aid: {k: spec.get(k) for k in ("status", "source", "support", "exemplars")}
                    for aid, spec in catalog["actions"].items()},
        "episodes": dict(episodes),
        "outcomes": dict(outcomes),
        "universe_actions": len(universe.get("actions") or []),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="z0int carly-nav", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("decide")
    sub.add_parser("record")
    sub.add_parser("learn")
    tune_parser = sub.add_parser("tune")
    tune_parser.add_argument("--max-rows", type=int, default=40)
    tune_parser.add_argument("--dry-run", action="store_true", help="measure without changing the policy")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    if args.cmd == "decide":
        result = cmd_decide()
    elif args.cmd == "record":
        result = cmd_record()
    elif args.cmd == "learn":
        from .learn import learn

        result = learn()
    elif args.cmd == "tune":
        from .tune import tune

        result = tune(max_rows=args.max_rows, apply=not args.dry_run)
    else:
        result = cmd_status()
    json.dump(result, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
