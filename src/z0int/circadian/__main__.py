"""circadian CLI.

  circadian serve                 run circadiand (tick loop + loopback GET /posture)
  circadian tick [--no-phone]     one tick now; prints the posture summary
  circadian show [--json]         the current posture (no adapters run)
  circadian route --title T --body B [--cls report] [--source S] [--tag X] [--producer P]
                  [--irreversible] [--tonight-plan]
                                  exit 0: deliver now; exit 10: held for the morning digest
  circadian done | undone         "done for today" (C10) and its undo
  circadian release [--dry-run] [--force]
  circadian replay [--days 30]    shadow replay receipt over ActivityWatch history
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from . import service


def _now(arg: str | None) -> datetime:
    if arg:
        from ..human_posture import parse_ts
        return parse_ts(arg)
    return datetime.now(timezone.utc).replace(microsecond=0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="circadian")
    ap.add_argument("--at", default=None, help="evaluate at this ISO time (tests/replay)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve")
    t = sub.add_parser("tick")
    t.add_argument("--no-phone", action="store_true")
    s = sub.add_parser("show")
    s.add_argument("--json", action="store_true")
    r = sub.add_parser("route")
    r.add_argument("--title", required=True)
    r.add_argument("--body", default="")
    r.add_argument("--cls", default="report", choices=["critical", "decision", "report", "digest"])
    r.add_argument("--source", default="cli")
    r.add_argument("--tag", default=None)
    r.add_argument("--producer", default=None)
    r.add_argument("--irreversible", action="store_true")
    r.add_argument("--tonight-plan", action="store_true")
    sub.add_parser("done")
    sub.add_parser("undone")
    rl = sub.add_parser("release")
    rl.add_argument("--dry-run", action="store_true")
    rl.add_argument("--force", action="store_true")
    rp = sub.add_parser("replay")
    rp.add_argument("--days", type=int, default=30)
    args = ap.parse_args(argv)

    cfg = service.load_config()
    d = service.state_dir()
    now = _now(args.at)
    if args.cmd == "serve":
        service.serve(cfg, d)
        return 0
    if args.cmd == "tick":
        post = service.tick(cfg, now, d, phone=False if args.no_phone else None)
        print(json.dumps({"phase": post["decision"]["phase"], "local": post["local"], "sources": post["sources"],
                          "enforcement": post["enforcement"], "release": post["release"]}, indent=1))
        return 0
    if args.cmd == "show":
        post = service.current_posture(cfg, now, d)
        if args.json:
            print(json.dumps(post, indent=1, sort_keys=True))
        else:
            dec = post["decision"]
            print(f"{dec['phase']} ({dec['posture']}), decisions={dec['allowDecisions']}, batch={dec['batchWindow']}, "
                  f"channel={dec['channel']}, next={post['local'].get('nextTransitionAt')}, "
                  f"enforcing={post['enforcement']['enforcingNow']}")
            print(dec["arithmetic"])
        return 0
    if args.cmd == "route":
        notice = {"cls": args.cls, "title": args.title, "body": args.body, "source": args.source, "tag": args.tag,
                  "producer": args.producer or args.source, "irreversible": args.irreversible,
                  "tonightPlan": args.tonight_plan}
        res = service.route_notice(cfg, notice, now, d)
        print(json.dumps(res, sort_keys=True))
        return 0 if res["deliver"] else 10
    if args.cmd == "done":
        print(json.dumps(service.set_done(d, now)))
        return 0
    if args.cmd == "undone":
        print(json.dumps(service.clear_done(d, now)))
        return 0
    if args.cmd == "release":
        print(json.dumps(service.release(cfg, now, d, dry_run=args.dry_run, force=args.force)))
        return 0
    if args.cmd == "replay":
        from .replay import replay
        print(json.dumps(replay(cfg, now, d, days=args.days), indent=1))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
