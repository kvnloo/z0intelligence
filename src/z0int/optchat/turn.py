"""A turn: one fresh call, [tools] [system] [view] [message].

    wait until every message before m is summarized
    render the view, then log m
    call in a fresh session; log each reply, tool call and result as it happens

Nothing carries over between turns: the view is the only continuity.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .claude_cli import SERVER


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
             show=None) -> dict:
    chat.ready.retry()                      # m is a new message: failed nodes are tried again
    compaction = compactor.run("leaves") if compactor else None
    if chat.unbuilt_leaves():
        raise NotReady(f"{chat.unbuilt_leaves()} messages before this one are not summarized")
    view = chat.render()                    # before logging m: the view never holds m
    first = chat.append("user", text)[0].i

    def on_block(kind: str, body: str) -> None:
        chat.append(kind, body)
        if show:
            show(kind, body)

    with backend.open(model, chat.system(), view, tools, label=f"turn {first}") as session:
        reply = session.send(text, on_block=on_block)
    return {"message": first, "view_bytes": len(view.encode("utf-8")), "reply": reply.text,
            "usage": reply.usage, "requests": reply.requests, "wall_s": reply.wall_s,
            "model": reply.model, "logged_to": chat.total - 1, "compaction": compaction}
