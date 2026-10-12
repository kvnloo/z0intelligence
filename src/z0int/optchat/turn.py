"""A turn: one fresh call, [tools] [system] [view] [message].

    wait until every message before m is summarized
    render the view, then log m
    call in a fresh session; log each reply, tool call and result as it happens
    run the compactor until idle

Nothing carries over between turns: the view is the only continuity.

The gist's compactor runs in the background all the time. Here nothing runs
between commands, so the turn itself runs it to idle once the call is over:
leaves and merges. If only the leaves were built (the wait before the call),
merges would pile up, batches would find no built parent, and the view would
grow far past 128,000 bytes.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .chat import SLACK
from .claude_cli import SERVER

MAX_REQUESTS = 16      # the most API requests one turn may make (its replies plus its tool calls)


class NotReady(RuntimeError):
    """Some message before this one still has no line."""


def tools_config(root: Path, *, callable_: bool = True) -> dict:
    """zoom and date over this chat root. A compaction gets the same tools, never called."""
    src = str(Path(__file__).resolve().parents[2])
    return {
        "mcp": {SERVER: {"command": sys.executable,
                         "args": ["-m", "z0int.optchat", "mcp", "--root", str(Path(root).resolve())],
                         "env": {"PYTHONPATH": src}}},
        "allowed": [f"mcp__{SERVER}__zoom", f"mcp__{SERVER}__date"] if callable_ else [],
    }


def run_turn(chat, backend, model: str, text: str, *, compactor=None, tools: dict | None = None,
             show=None, after: bool = True, slack: int = SLACK, max_requests: int = MAX_REQUESTS,
             background=None) -> dict:
    """`background`: a started compact.Background. The turn then only waits for the
    lines before its message; leaves and merges of this turn are built behind it,
    while the call runs, and `compactor`/`after` are not used."""
    with chat.lock:
        chat.ready.retry()                  # m is a new message: failed nodes are tried again
    waited = None
    if background is not None:
        waited = background.wait_leaves()
        compaction, compactor = None, None
        if chat.unbuilt_leaves():
            why = f": {background.stopped}" if background.stopped else ""
            raise NotReady(f"{chat.unbuilt_leaves()} messages before this one are not summarized{why}")
    else:
        compaction = compactor.run("leaves") if compactor else None
        if chat.unbuilt_leaves():
            why = f": {compaction['stopped']}" if compaction and compaction["stopped"] else ""
            raise NotReady(f"{chat.unbuilt_leaves()} messages before this one are not summarized{why}")
    batch = chat.settle()                   # every line is built: a batch due on real sizes happens now
    chat.check(slack)                       # a view far past 128,000 bytes is refused, before anything is logged
    view = chat.render()                    # before logging m: the view never holds m
    first = chat.append("user", text)[0].i

    def on_block(kind: str, body: str) -> None:
        chat.append(kind, body)
        if show:
            show(kind, body)

    with backend.open(model, chat.system(), view, tools, label=f"turn {first}",
                      max_requests=max_requests) as session:
        reply = session.send(text, on_block=on_block)
    logged_to = chat.total - 1
    return {"message": first, "view_bytes": len(view.encode("utf-8")), "reply": reply.text,
            "usage": reply.usage, "requests": reply.requests, "wall_s": reply.wall_s,
            "model": reply.model, "logged_to": logged_to, "compaction": compaction,
            "waited_s": waited, "batch_merges": batch,
            "cut": bool(reply.row.get("max_turns_hit")),      # stopped at max_requests, maybe mid-work
            "after": compactor.run("idle") if compactor and after else None}
