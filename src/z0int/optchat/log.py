"""Append-only OptChat log and the folded view.

Sizes are UTF-8 bytes. A torn last line is skipped and the file is
closed with a newline so the next write is whole. Each append is one
write plus fsync.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

NODE = 512
VIEW = 128_000
CAP = 30_000
PLACEHOLDER = "(not summarized yet: zoom it)"
KINDS = frozenset({"user", "talk", "tool", "echo", "note"})


def utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def one_line(text: str) -> str:
    return " ".join(text.split())


def cap_echo(text: str, limit: int = CAP) -> str:
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    cut = len(text) - limit
    return f"{text[:head]}\n… {cut} characters cut …\n{text[-tail:]}"


def message_size(kind: str, text: str) -> int:
    return utf8_len(f"{kind}: {text}")


@dataclass
class Message:
    i: int
    kind: str
    text: str
    size: int
    date: str

    def source_line(self) -> str:
        return f"{self.kind}: {one_line(self.text)}"


@dataclass
class Part:
    level: int
    index: int

    @property
    def start(self) -> int:
        return self.index * (1 << self.level)

    @property
    def n(self) -> int:
        return 1 << self.level


@dataclass
class ChatLog:
    root: Path
    messages: list[Message] = field(default_factory=list)
    nodes: dict[tuple[int, int], str] = field(default_factory=dict)
    view: list[Part] = field(default_factory=list)
    torn: int = 0

    def load(self) -> None:
        self.messages.clear()
        self.nodes.clear()
        self.torn = 0
        main = self.root / "main"
        tree = self.root / "tree"
        if main.is_dir():
            for path in sorted(main.glob("*.jsonl")):
                self._load_jsonl(path, self._take_message)
        if tree.is_dir():
            for path in sorted(tree.glob("*.jsonl")):
                self._load_jsonl(path, self._take_node)
        self.refold()

    def _load_jsonl(self, path: Path, take) -> None:
        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            with path.open("ab") as fh:
                fh.write(b"\n")
                fh.flush()
                os.fsync(fh.fileno())
            raw += b"\n"
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                self.torn += 1
                continue
            if isinstance(row, dict):
                take(row)

    def _take_message(self, row: dict[str, Any]) -> None:
        kind = row.get("kind")
        text = row.get("text")
        if kind not in KINDS or not isinstance(text, str):
            self.torn += 1
            return
        i = len(self.messages)
        self.messages.append(Message(
            i=i,
            kind=kind,
            text=text,
            size=message_size(kind, text),
            date=str(row.get("date") or ""),
        ))

    def _take_node(self, row: dict[str, Any]) -> None:
        if not isinstance(row.get("l"), int) or not isinstance(row.get("i"), int):
            return
        if not isinstance(row.get("text"), str):
            return
        self.nodes[(row["l"], row["i"])] = row["text"]

    def append(self, kind: str, text: str, *, date: str | None = None) -> Message:
        if kind not in KINDS:
            raise ValueError(f"bad kind: {kind}")
        if kind == "echo":
            text = cap_echo(text)
        msg = Message(
            i=len(self.messages),
            kind=kind,
            text=text,
            size=message_size(kind, text),
            date=date or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        )
        day = time.strftime("%Y-%m-%d")
        path = self.root / "main" / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({
            "i": msg.i, "kind": msg.kind, "text": msg.text, "size": msg.size, "date": msg.date,
        }, ensure_ascii=False) + "\n"
        with path.open("ab") as fh:
            fh.write(line.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())
        self.messages.append(msg)
        if msg.size <= NODE:
            self._store_node(0, msg.i, msg.source_line())
        self.view.append(Part(0, msg.i))
        self.fit()
        return msg

    def _store_node(self, level: int, index: int, text: str) -> None:
        self.nodes[(level, index)] = text
        day = time.strftime("%Y-%m-%d")
        path = self.root / "tree" / f"{day}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({"l": level, "i": index, "text": text, "size": utf8_len(text)}, ensure_ascii=False) + "\n"
        with path.open("ab") as fh:
            fh.write(line.encode("utf-8"))
            fh.flush()
            os.fsync(fh.fileno())

    def part_text(self, part: Part) -> str:
        return self.nodes.get((part.level, part.index), PLACEHOLDER)

    def built(self, level: int, index: int) -> bool:
        return (level, index) in self.nodes

    def view_size(self) -> int:
        return sum(utf8_len(self.part_text(part)) for part in self.view)

    def fit(self) -> None:
        total = len(self.messages)
        while self.view_size() > VIEW and len(self.view) >= 2:
            best_at = -1
            best_due = -1.0
            for at, (left, right) in enumerate(zip(self.view, self.view[1:])):
                if left.level != right.level or left.index % 2 or right.index != left.index + 1:
                    continue
                parent = left.index // 2
                if not self.built(left.level + 1, parent):
                    continue
                weight = 1 << (left.level + 2)
                due = (total - left.start) / weight
                if due > best_due:
                    best_due = due
                    best_at = at
            if best_at < 0:
                break
            left = self.view[best_at]
            self.view[best_at:best_at + 2] = [Part(left.level + 1, left.index // 2)]

    def refold(self) -> None:
        self.view = []
        for msg in self.messages:
            if msg.size <= NODE and not self.built(0, msg.i):
                self.nodes[(0, msg.i)] = msg.source_line()
            self.view.append(Part(0, msg.i))
            self.fit()

    def render(self) -> str:
        lines = ["<chat>"]
        for part in self.view:
            lines.append(f"{part.start}+{part.n}|{self.part_text(part)}")
        lines.append("</chat>")
        return "\n".join(lines)

    def zoom(self, ident: int, n: int) -> str:
        total = len(self.messages)
        if n < 1 or n & (n - 1) or ident % n or ident + n > total:
            return f"No line {ident}+{n}."
        if n == 1:
            msg = self.messages[ident]
            return f"{ident}+0|{msg.kind}: {msg.text}"
        level = n.bit_length() - 2
        base = (2 * ident) // n
        left = self.nodes.get((level, base))
        right = self.nodes.get((level, base + 1))
        if left is None or right is None:
            return f"No line {ident}+{n}."
        span = 1 << level
        return f"{ident}+{span}|{left}\n{ident + span}+{span}|{right}"

    def date_of(self, ident: int) -> str:
        if ident < 0 or ident >= len(self.messages):
            return f"No message {ident}."
        return self.messages[ident].date or "unknown"


def load_chat(root: Path | None = None) -> ChatLog:
    home = Path(os.environ.get("Z0INT_HOME", Path.home() / ".z0int"))
    chat = ChatLog(root or home / "optchat" / "chat")
    chat.load()
    return chat


def append_message(kind: str, text: str, root: Path | None = None) -> Message:
    chat = load_chat(root)
    return chat.append(kind, text)
