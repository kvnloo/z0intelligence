"""python -m z0int.optchat <command> --root <chat root> ...

    init      make a chat root (limits are fixed here; scale them for a trial)
    append    log one message (text from --text, --file or stdin)
    compact   run the compactor until idle, with --model
    view      print the view            status    print counts and sizes
    turn      one fresh call with zoom/date attached; logs reply, tool calls, results
    zoom, date   what the model's tools return
    mcp       serve zoom/date over stdio (Claude Code starts this)
    usage     sum the usage of a calls.jsonl

There is no default chat root: --root is always required.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .chat import Chat
from .claude_cli import PLACEMENTS, SYSTEM_MODES, ClaudeCLI, LimitError
from .compact import JOBS, Compactor
from .log import KINDS
from .turn import run_turn, tools_config
from .view import CHIGH, CLOW, HIGH, LOW

KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


def backend_of(args, effort: str | None = None) -> ClaudeCLI:
    root = Path(args.root).resolve()
    work = Path(args.work) if args.work else root.with_name(root.name + ".work")
    results = Path(args.results) if args.results else work
    return ClaudeCLI(work, calls=results / "calls.jsonl",
                     ledger=Path(args.ledger) if args.ledger else results / "ledger.jsonl",
                     experiment=args.experiment, placement=args.placement,
                     system_mode=args.system_mode, effort=effort)


def compactor_of(chat: Chat, args, model: str) -> Compactor:
    effort = args.effort if args.command == "compact" else args.compact_effort
    return Compactor(chat, backend_of(args, effort), model, system=chat.system(),
                     tools=tools_config(chat.root, callable_=False), jobs=args.jobs, stop_on=(LimitError,))


def read_text(args) -> str:
    if args.text is not None:
        return args.text
    return Path(args.file).read_text() if args.file else sys.stdin.read()


def summarize(path: Path) -> dict:
    out: dict = {}
    for raw in path.read_text().splitlines():
        row = json.loads(raw)
        kind = (row.get("label") or "?").split(" ")[0]
        slot = out.setdefault(f"{row.get('model') or row.get('model_requested')} {kind}",
                              {"sends": 0, "requests": 0, "wall_s": 0.0, **{k: 0 for k in KEYS}})
        slot["sends"] += 1
        slot["requests"] += row.get("requests", 1)
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
            p.add_argument("--effort")
            p.add_argument("--jobs", type=int, default=JOBS)
            p.add_argument("--work", help="scratch directory (default: <root>.work)")
            p.add_argument("--results", help="directory for calls.jsonl (default: the work directory)")
            p.add_argument("--ledger", help="call-cap ledger (default: <results>/ledger.jsonl)")
            p.add_argument("--experiment", default="")
        return p

    p = command("init")
    p.add_argument("--name", default="Unii")
    for flag, default in (("--high", HIGH), ("--low", LOW), ("--chigh", CHIGH), ("--clow", CLOW)):
        p.add_argument(flag, type=int, default=default)
    p = command("append")
    p.add_argument("--kind", choices=KINDS, default="user")
    p.add_argument("--text")
    p.add_argument("--file")
    p = command("compact", model=True)
    p.add_argument("--until", choices=("idle", "leaves"), default="idle")
    command("view")
    command("status")
    p = command("turn", model=True)
    p.add_argument("--text")
    p.add_argument("--file")
    p.add_argument("--compact-model", help="model that summarizes what is still unbuilt before the turn")
    p.add_argument("--compact-effort", help="effort for those compactions (the gist: Haiku at xhigh)")
    p.add_argument("--then-compact", action="store_true", help="also compact the turn's own messages after it")
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
            print(json.dumps({**{k: v for k, v in stats.items() if k != "built"}, "built": len(stats["built"]),
                              "status": chat.status()}, indent=1))
            return 2 if stats["stopped"] else 0
    elif args.command == "turn":
        with Chat(root, write=True) as chat:
            compactor = compactor_of(chat, args, args.compact_model) if args.compact_model else None
            out = run_turn(chat, backend_of(args, args.effort), args.model, read_text(args), compactor=compactor,
                           tools=tools_config(root),
                           show=lambda kind, body: print(f"[{kind}] {body[:2000]}", file=sys.stderr, flush=True))
            if args.then_compact and compactor:
                out["after"] = {k: (len(v) if k == "built" else v) for k, v in compactor.run("idle").items()}
            if out["compaction"]:
                out["compaction"]["built"] = len(out["compaction"]["built"])
            print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
