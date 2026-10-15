"""One HTML page of the memory: the view, every message, and each tree level."""
from __future__ import annotations

from html import escape
from pathlib import Path

from .log import ChatLog, utf8_len


def render_html(chat: ChatLog) -> str:
    levels: dict[int, list[str]] = {}
    for (level, index), text in sorted(chat.nodes.items()):
        start = index * (1 << level)
        end = start + (1 << level) - 1
        span = f"{chat.date_of(start)} – {chat.date_of(end)}"
        entry = f"{start}+{1 << level} {span} {utf8_len(text)} bytes {text}"
        levels.setdefault(level, []).append(f"<li>{escape(entry)}</li>")
    level_html = "\n".join(
        f"<h2>level {level}</h2><ul>{''.join(items)}</ul>" for level, items in sorted(levels.items())
    )
    messages = "\n".join(
        f"<li>{escape(f'{msg.i}+1 {msg.kind} {msg.date} {msg.size} bytes {msg.text}')}</li>" for msg in chat.messages
    )
    return (
        "<!doctype html><meta charset=utf-8><title>OptChat</title>"
        f"<h1>view</h1><pre>{escape(chat.render())}</pre>"
        f"<h1>log</h1><ul>{messages}</ul>"
        f"{level_html}"
    )


def write_html(chat: ChatLog, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(chat), encoding="utf-8")
    return path
