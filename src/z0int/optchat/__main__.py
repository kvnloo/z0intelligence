"""python -m z0int.optchat <command> --root <chat root> ...

    init      make a chat root (the gist's limits; other limits are a labelled deviation)
    append    log one message (text from --text, --file or stdin)
    compact   run the compactor until idle, with --model
    feed      log a prepared conversation in live order: one turn's messages,
              compact to idle, the next turn (--file: one JSON list of
              {kind, text} per line, one line per turn)
    view      print the view            status    print counts and sizes
    turn      one fresh call with zoom/date attached; logs reply, tool calls and
              results, then compacts to idle
    zoom, date   what the model's tools return
    mcp       serve zoom/date over stdio (Claude Code starts this)
    usage     sum the usage of a calls.jsonl

There is no default chat root and no default ledger: --root is always
required, and every command that calls a model needs the one shared --ledger.
Compactions go out at effort xhigh (the gist) unless told otherwise.

Exit codes: 0 done; 2 stopped (call cap, rate limit, or a view far past its
high mark); 3 done, but the view is past its high mark by more than --view-slack.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .chat import SLACK, Chat, ViewOver
from .claude_cli import PLACEMENTS, SYSTEM_MODES, ClaudeCLI, LimitError
from .compact import JOBS, Compactor
from .log import KINDS
from .turn import MAX_REQUESTS, NotReady, run_turn, tools_config
from .view import CHIGH, CLOW, HIGH, LOW

KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def effort_of(value: str | None) -> str | None:
    return None if value in (None, "default") else value


def backend_of(chat: Chat, args, effort: str | None = None) -> ClaudeCLI:
    root = Path(args.root).resolve()
    work = Path(args.work) if args.work else root.with_name(root.name + ".work")
    results = Path(args.results) if args.results else work
    limits = [chat.view.high, chat.view.low, chat.cview.high, chat.cview.low]
    return ClaudeCLI(work, calls=results / "calls.jsonl", ledger=Path(args.ledger),
                     experiment=args.experiment, placement=args.placement,
                     system_mode=args.system_mode, effort=effort_of(effort), binary=args.claude_binary,
                     meta={"limits": limits, "gist_limits": chat.gist_limits()})


def compactor_of(chat: Chat, args, model: str) -> Compactor:
    effort = args.compact_effort if args.command == "turn" else args.effort
    return Compactor(chat, backend_of(chat, args, effort), model, system=chat.system(),
                     tools=tools_config(chat.root, callable_=False), jobs=args.jobs,
                     stop_on=(LimitError, ViewOver), slack=args.view_slack)


def brief(stats: dict) -> dict:
    return {**stats, "built": len(stats["built"])}


def done(chat: Chat, args, stopped) -> int:
    """The exit code, with a line on stderr when the run must not pass as a good one."""
    over = chat.over(args.view_slack)
    if stopped:
        print(f"optchat: stopped: {stopped}", file=sys.stderr)
        return 2
    if over:
        print(f"optchat: the view is {over} bytes past {chat.view.high} + {args.view_slack}: "
              "feed the chat in live order (see `feed`)", file=sys.stderr)
        return 3
    return 0


def read_text(args) -> str:
    if args.text is not None:
        return args.text
    return Path(args.file).read_text() if args.file else sys.stdin.read()


def summarize(path: Path) -> dict:
    """Totals per model, kind of call, effort and limits. A group that is not the
    gist's (default effort on compactions, scaled limits) says so in its name.
    `tool_violations` counts compactions that called a tool: more than one
    request, or stopped at the one request they are allowed."""
    out: dict = {}
    for raw in path.read_text().splitlines():
        row = json.loads(raw)
        kind = (row.get("label") or "?").split(" ")[0]
        name = f"{row.get('model') or row.get('model_requested')} {kind} effort={row.get('effort') or 'default'}"
        if kind == "compact" and row.get("effort") != "xhigh":
            name += " [deviation: not xhigh]"
        if row.get("gist_limits") is False:
            name += f" [deviation: limits {'/'.join(map(str, row.get('limits', [])))}]"
        slot = out.setdefault(name, {"sends": 0, "requests": 0, "errors": 0, "tool_violations": 0,
                                     "wall_s": 0.0, **{k: 0 for k in KEYS}})
        slot["sends"] += 1
        slot["requests"] += row.get("requests", 1)
        slot["errors"] += bool(row.get("is_error"))
        slot["tool_violations"] += kind == "compact" and (row.get("requests", 1) > 1 or bool(row.get("max_turns_hit")))
        slot["wall_s"] = round(slot["wall_s"] + row.get("wall_s", 0), 3)
        for key in KEYS:
            slot[key] += (row.get("usage") or {}).get(key, 0) or 0
    for slot in out.values():
        seen = slot["cache_read_input_tokens"] + slot["cache_creation_input_tokens"] + slot["input_tokens"]
        slot["cache_read_share"] = round(slot["cache_read_input_tokens"] / seen, 4) if seen else None
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m z0int.optchat", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name, model=False):
        p = sub.add_parser(name)
        p.add_argument("--root", required=True)
        if model:
            p.add_argument("--model", required=True)
            p.add_argument("--placement", choices=PLACEMENTS, default="user-blocks",
                           help="where the view goes: user-blocks = start of the user message in 4-line "
                                "blocks with a cache mark (the gist's shape); user = one block; system = "
                                "appended to the system prompt")
            p.add_argument("--system-mode", choices=SYSTEM_MODES, default="replace")
            p.add_argument("--jobs", type=int, default=JOBS)
            p.add_argument("--work", help="scratch directory (default: <root>.work)")
            p.add_argument("--results", help="directory for calls.jsonl (default: the work directory)")
            p.add_argument("--ledger", required=True,
                           help="the one call-cap ledger shared by every run of the trial")
            p.add_argument("--experiment", default="")
            p.add_argument("--claude-binary", default="claude", help=argparse.SUPPRESS)
            p.add_argument("--view-slack", type=int, default=SLACK,
                           help="bytes a view may pass its high mark before the run is refused")
        return p

    xhigh = "the gist: xhigh; 'default' sends no --effort and is a deviation"
    p = command("init")
    p.add_argument("--name", default="Unii")
    for flag, default in (("--high", HIGH), ("--low", LOW), ("--chigh", CHIGH), ("--clow", CLOW)):
        p.add_argument(flag, type=int, default=default,
                       help=f"the gist: {default}; any other value is a deviation, marked in every row")
    p = command("append")
    p.add_argument("--kind", choices=KINDS, default="user")
    p.add_argument("--text")
    p.add_argument("--file")
    p = command("compact", model=True)
    p.add_argument("--effort", default="xhigh", help=xhigh)
    p.add_argument("--until", choices=("idle", "leaves"), default="idle")
    p = command("feed", model=True)
    p.add_argument("--effort", default="xhigh", help=xhigh)
    p.add_argument("--file", required=True)
    p.add_argument("--skip", type=int, default=0, help="turns of the file already fed")
    p.add_argument("--turns", type=int, help="feed at most this many turns")
    command("view")
    command("status")
    p = command("turn", model=True)
    p.add_argument("--text")
    p.add_argument("--file")
    p.add_argument("--effort", help="effort for the turn's own call (the gist names none)")
    p.add_argument("--compact-model", required=True,
                   help="model that summarizes: before the call what is unbuilt, after it until idle")
    p.add_argument("--compact-effort", default="xhigh", help=xhigh)
    p.add_argument("--no-compact-after", action="store_true",
                   help="deviation: leave the turn's messages and the ready merges unbuilt")
    p.add_argument("--max-requests", type=int, default=MAX_REQUESTS,
                   help="the most API requests the turn may make; reserved against the cap before it starts")
    p = command("zoom")
    p.add_argument("id", type=int)
    p.add_argument("n", type=int)
    p = command("date")
    p.add_argument("id", type=int)
    command("mcp")
    p = sub.add_parser("usage")
    p.add_argument("--calls", required=True)
    args = parser.parse_args(argv)

    if args.command == "usage":
        print(json.dumps(summarize(Path(args.calls)), indent=1))
        return 0
    root = Path(args.root)
    if args.command == "init":
        Chat.init(root, name=args.name, high=args.high, low=args.low, chigh=args.chigh, clow=args.clow)
        print(root)
    elif args.command == "mcp":
        from .mcp import serve
        serve(root)
    elif args.command == "view":
        print(Chat(root).render())
    elif args.command == "status":
        print(json.dumps(Chat(root).status(), indent=1))
    elif args.command == "zoom":
        print(Chat(root).zoom(args.id, args.n))
    elif args.command == "date":
        print(Chat(root).date(args.id))
    elif args.command == "append":
        with Chat(root, write=True) as chat:
            print(" ".join(str(m.i) for m in chat.append(args.kind, read_text(args))))
    elif args.command == "compact":
        with Chat(root, write=True) as chat:
            chat.ready.retry()
            stats = compactor_of(chat, args, args.model).run(args.until)
            print(json.dumps({**brief(stats), "status": chat.status()}, indent=1))
            return done(chat, args, stats["stopped"])
    elif args.command == "feed":
        turns = [json.loads(raw) for raw in Path(args.file).read_text().splitlines() if raw.strip()]
        last = len(turns) if args.turns is None else min(len(turns), args.skip + args.turns)
        with Chat(root, write=True) as chat:
            compactor = compactor_of(chat, args, args.model)
            for at in range(args.skip, last):
                first = chat.total
                for message in turns[at]:
                    chat.append(message["kind"], message["text"])
                stats = compactor.run("idle")
                print(json.dumps({"turn": at, "messages": [first, chat.total - 1], **brief(stats),
                                  "view_bytes": chat.status()["view_bytes"],
                                  "cview_bytes": chat.status()["cview_bytes"]}), flush=True)
                code = done(chat, args, stats["stopped"])
                if code:
                    return code
    elif args.command == "turn":
        with Chat(root, write=True) as chat:
            compactor = compactor_of(chat, args, args.compact_model)
            try:
                out = run_turn(chat, backend_of(chat, args, args.effort), args.model, read_text(args),
                               compactor=compactor, tools=tools_config(root), after=not args.no_compact_after,
                               slack=args.view_slack, max_requests=args.max_requests,
                               show=lambda kind, body: print(f"[{kind}] {body[:2000]}", file=sys.stderr, flush=True))
            except (NotReady, ViewOver, LimitError) as error:
                return done(chat, args, f"{type(error).__name__}: {error}")
            for key in ("compaction", "after"):
                if out[key]:
                    out[key] = brief(out[key])
            print(json.dumps(out, indent=1, ensure_ascii=False))
            return done(chat, args, out["compaction"]["stopped"] or (out["after"] or {}).get("stopped"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
