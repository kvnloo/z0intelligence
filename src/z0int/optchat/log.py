"""Append-only OptChat log and the folded view.

Sizes are UTF-8 bytes. A torn last line is skipped and the file is
closed with a newline so the next write is whole. Each append is one
write plus fsync.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

NODE = 512
VIEW = 128_000
CAP = 30_000
PLACEHOLDER = "(not summarized yet: zoom it)"
KINDS = frozenset({"user", "talk", "tool", "echo", "note"})


def utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def one_line(text: str) -> str:
    return text.replace("\n", " ")


def cap_echo(text: str, limit: int = CAP) -> str:
    if len(text) <= limit:
        return text
    kept = limit
    while True:
        note = f"\n… {len(text) - kept} characters cut …\n"
        available = max(0, limit - len(note))
        if available == kept:
            break
        kept = available
    if not kept:
        return note[:limit]
    head = kept // 2
    tail = kept - head
    return text[:head] + note + (text[-tail:] if tail else "")


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
        return f"{self.kind}: {self.text}"


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
    condition: threading.Condition = field(default_factory=threading.Condition, repr=False)
    _by_id: dict[int, Message] = field(default_factory=dict, repr=False)
    _next_id: int = 0

    @property
    def total(self) -> int:
        """Permanent address frontier, including gaps left by torn source rows."""
        return self._next_id

    def message(self, ident: int) -> Message | None:
        return self._by_id.get(ident)

    def missing(self) -> int:
        return sum(not self.built(part.level, part.index) for part in self.view)

    def load(self) -> None:
        with self.condition:
            self.messages.clear()
            self._by_id.clear()
            self._next_id = 0
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
            self.messages.sort(key=lambda msg: msg.i)
            self.refold()

    def _load_jsonl(self, path: Path, take: Callable[[dict[str, object]], None]) -> None:
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

    def _take_message(self, row: dict[str, object]) -> None:
        ident = row.get("i")
        kind = row.get("kind")
        text = row.get("text")
        if (
            type(ident) is not int or ident < 0 or ident in self._by_id
            or not isinstance(kind, str) or kind not in KINDS or not isinstance(text, str)
        ):
            self.torn += 1
            return
        msg = Message(
            i=ident,
            kind=kind,
            text=text,
            size=message_size(kind, text),
            date=str(row.get("date") or ""),
        )
        self.messages.append(msg)
        self._by_id[ident] = msg
        self._next_id = max(self._next_id, ident + 1)

    def _take_node(self, row: dict[str, object]) -> None:
        level = row.get("l")
        index = row.get("i")
        text = row.get("text")
        if type(level) is not int or type(index) is not int or level < 0 or index < 0:
            return
        if not isinstance(text, str):
            return
        self.nodes[(level, index)] = text
        self._next_id = max(self._next_id, (index + 1) * (1 << level))

    def append(self, kind: str, text: str, *, date: str | None = None) -> Message:
        with self.condition:
            if kind not in KINDS:
                raise ValueError(f"bad kind: {kind}")
            if kind == "echo":
                text = cap_echo(text)
            msg = Message(
                i=self.total,
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
            self._by_id[msg.i] = msg
            self._next_id = msg.i + 1
            self.view.append(Part(0, msg.i))
            try:
                if msg.size <= NODE:
                    self._store_node(0, msg.i, msg.source_line())
            finally:
                self.fit()
            return msg

    def _store_node(self, level: int, index: int, text: str) -> None:
        with self.condition:
            day = time.strftime("%Y-%m-%d")
            path = self.root / "tree" / f"{day}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps({"l": level, "i": index, "text": text, "size": utf8_len(text)}, ensure_ascii=False) + "\n"
            with path.open("ab") as fh:
                fh.write(line.encode("utf-8"))
                fh.flush()
                os.fsync(fh.fileno())
            self.nodes[(level, index)] = text
            self.condition.notify_all()

    def part_text(self, part: Part) -> str:
        return self.nodes.get((part.level, part.index), PLACEHOLDER)

    def built(self, level: int, index: int) -> bool:
        return (level, index) in self.nodes

    def view_size(self) -> int:
        return sum(utf8_len(self.part_text(part)) for part in self.view)

    def fit(self, total: int | None = None) -> None:
        with self.condition:
            self._fit(self.total if total is None else total)
            self.condition.notify_all()

    def _fit(self, total: int) -> None:
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
        with self.condition:
            self.view = []
            for msg in self.messages:
                if msg.size <= NODE and not self.built(0, msg.i):
                    self._store_node(0, msg.i, msg.source_line())
                self.view.append(Part(0, msg.i))
                self._fit(msg.i + 1)
            self.condition.notify_all()

    def render(self) -> str:
        lines = ["<chat>"]
        for part in self.view:
            lines.append(f"{part.start}+{part.n}|{one_line(self.part_text(part))}")
        lines.append("</chat>")
        return "\n".join(lines)

    def zoom(self, ident: int, n: int) -> str:
        if ident < 0 or n < 1 or n & (n - 1) or ident % n or ident + n > self.total:
            return f"No line {ident}+{n}."
        if n == 1:
            msg = self.message(ident)
            if msg is None:
                return f"No line {ident}+{n}."
            return f"{ident}+0|{msg.kind}: {msg.text}"
        level = n.bit_length() - 2
        base = (2 * ident) // n
        left = self.nodes.get((level, base))
        right = self.nodes.get((level, base + 1))
        if left is None or right is None:
            return f"No line {ident}+{n}."
        span = 1 << level
        return f"{ident}+{span}|{one_line(left)}\n{ident + span}+{span}|{one_line(right)}"

    def date_of(self, ident: int) -> str:
        msg = self.message(ident)
        if msg is None:
            return f"No message {ident}."
        return msg.date or "unknown"


def load_chat(root: Path | None = None) -> ChatLog:
    home = Path(os.environ.get("Z0INT_HOME", Path.home() / ".z0int"))
    chat = ChatLog(root or home / "optchat" / "chat")
    chat.load()
    return chat


def append_message(kind: str, text: str, root: Path | None = None) -> Message:
    chat = load_chat(root)
    return chat.append(kind, text)
