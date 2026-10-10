"""Fresh turn request. No session history. The view is rendered before the new text is logged.
"""
from __future__ import annotations

import threading
import time

from .log import ChatLog

MARKS = (50_000, 80_000, 100_000)

MASTER = """You are OptChat, an AI agent that works for one user in a single chat that
never ends. Do the user's tasks yourself, with your tools, following
the user's instructions at the end of this prompt: they say who the
user is, how their files are organized and how they want work done.
Use subagents only when the user asks for them.

You keep no memory between turns. Each turn starts with the view below,
followed by the user's new message. Summaries keep little of tool
output, so say in your reply what you learned that will matter later.
Messages the user sends while you work reach you between tool calls.

Subagents and computer tasks run in the background. Each one's report
reaches you as a message starting "[id] ": between your tool calls
while you work, or as a new turn once yours has ended. So never wait
for one (no sleep, no polling): go on, or end your turn and tell the
user what is running."""

VIEW_DOC = """The view: the whole chat between OptChat and the user, oldest first, inside
<chat> tags, as one-line summaries. Each line is

  id+n|text   the n messages from id on, summarized (newlines shown as spaces)

A summary tags each item with its kind: user (the user's words), talk
(OptChat's replies), tool (OptChat's tool calls), echo (their results), note
(memories from before this chat), or work (the report of a subagent or
a computer task, which the log holds as a user message starting
"[id] "). A short message is its own line, word for word. Recent lines
cover one message each; the older the messages, the more a line covers.
A message not summarized yet shows as "(not summarized yet: zoom it)".
No message appears in full, not even the last ones.

Navigating: zoom(id, n) opens line id+n into the two lines of n/2
messages it was made from; zoom(id, 1) gives message id in full. Zoom
whenever a summary only mentions something you need, such as what your
last reply said, a decision, a past attempt or where a file is, before
you act, guess or ask. date(id) gives the date and time of message id."""


def ready(chat: ChatLog) -> bool:
    with chat.condition:
        return chat.missing() == 0


def wait_ready(
    chat: ChatLog,
    timeout: float | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, object]:
    """Wait on fit/append/abort notifications, without polling or model calls."""
    deadline = None if timeout is None else time.monotonic() + max(0.0, timeout)
    with chat.condition:
        while True:
            missing = chat.missing()
            cancelled = cancel is not None and cancel.is_set()
            if cancelled or missing == 0:
                return {
                    "ready": not cancelled and missing == 0,
                    "cancelled": cancelled,
                    "placeholders": missing,
                    "messages": len(chat.messages),
                }
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                return {"ready": False, "cancelled": False, "placeholders": missing, "messages": len(chat.messages)}
            chat.condition.wait(remaining)


def system_prompt(agents: str) -> str:
    parts = [MASTER, VIEW_DOC]
    extra = agents.strip()
    if extra:
        parts.append(extra)
    return "\n\n".join(parts)


def cache_pieces(view: str) -> list[str]:
    """Split the view at the last line end before each mark. Marks past the end are skipped."""
    cuts: list[int] = []
    for mark in MARKS:
        if mark >= len(view):
            continue
        newline = view.rfind("\n", 0, mark)
        if newline < 0:
            continue
        end = newline + 1
        if cuts and end <= cuts[-1]:
            continue
        cuts.append(end)
    pieces: list[str] = []
    start = 0
    for end in cuts:
        pieces.append(view[start:end])
        start = end
    pieces.append(view[start:])
    return pieces


def begin(chat: ChatLog, texts: list[str], agents: str = "") -> dict[str, object]:
    """Render, then log. Refuse when any view node is still unbuilt."""
    with chat.condition:
        if not ready(chat):
            return {"ready": False}
        view = chat.render()
        for text in texts:
            chat.append("user", text)
        return {
            "ready": True,
            "view": view,
            "system": system_prompt(agents),
            "blocks": [view, "\n\n".join(texts)],
            "pieces": cache_pieces(view),
        }



class Queue:
    """One turn queue. A running call receives new text at the next tool boundary."""

    def __init__(self) -> None:
        self.pending: list[str] = []
        self.running = False
        self._lock = threading.Lock()

    def submit(self, text: str) -> str:
        with self._lock:
            self.pending.append(text)
            return "inject" if self.running else "turn"

    def take(self) -> list[str]:
        with self._lock:
            texts = self.pending
            self.pending = []
            self.running = bool(texts)
            return texts

    def boundary(self) -> list[str]:
        with self._lock:
            texts = self.pending
            self.pending = []
            return texts

    def finish(self) -> list[str]:
        with self._lock:
            self.running = False
            return list(self.pending)

    def cancel(self) -> list[str]:
        with self._lock:
            texts = self.pending
            self.pending = []
            self.running = False
            return texts


def leave_unanswered(chat: ChatLog, texts: list[str]) -> int:
    with chat.condition:
        for text in texts:
            chat.append("user", text)
        return len(texts)


def subagent_text(ident: str, report: str) -> str:
    return f"[{ident}] {report}"


def record(chat: ChatLog, kind: str, text: str) -> dict[str, object]:
    if kind in {"thought", "thinking"}:
        return {"logged": False}
    msg = chat.append(kind, text)
    return {"logged": True, "i": msg.i, "kind": msg.kind, "text": msg.text}
