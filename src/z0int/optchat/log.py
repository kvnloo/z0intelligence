"""The log: main/ and tree/ day files, append-only, one writer.

    main/YYYY-MM-DD.jsonl   {i, kind, text, size, date}
    tree/YYYY-MM-DD.jsonl   {l, i, text, size}

Sizes are UTF-8 bytes. Each write is one line, flushed and fsynced. Nothing
is ever edited or deleted. The writer holds an exclusive lock on the root;
readers (the zoom/date server, `view`, `status`) take none and `refresh()`
to see new lines.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

NODE = 512          # bytes per tree node
CAP = 30_000        # characters kept of a tool result, head + tail
SPLIT = 30_000      # characters per message for any other long text (the gist names no number)
KINDS = ("user", "unii", "tool", "echo", "work", "note")


class Locked(RuntimeError):
    pass


def utf8_len(text: str) -> int:
    return len(text.encode("utf-8"))


def clip_echo(text: str, limit: int = CAP) -> str:
    """A tool result clipped to its head and tail, `limit` characters in all."""
    if len(text) <= limit:
        return text
    kept = limit
    while True:
        note = f"\n… {len(text) - kept} characters cut …\n"
        available = max(0, limit - len(note))
        if available == kept:
            break
        kept = available
    head = kept // 2
    tail = kept - head
    return text[:head] + note + (text[-tail:] if tail else "")


def split_text(text: str, limit: int = SPLIT) -> list[str]:
    """Long text is never cut: it becomes several messages in a row."""
    return [text[at:at + limit] for at in range(0, len(text), limit)] or [text]


@dataclass(frozen=True)
class Message:
    i: int
    kind: str
    text: str
    size: int
    date: str

    def source(self) -> str:
        return f"{self.kind}: {self.text}"


class ChatLog:
    def __init__(self, root: Path, *, write: bool = False, clock=None, fsync: bool = True):
        self.root = Path(root)
        self.write = write
        self.clock = clock or time.localtime
        self.fsync = fsync
        self.messages: list[Message] = []          # ids are dense: messages[i].i == i
        self.nodes: dict[tuple[int, int], str] = {}
        self.torn = 0
        self.leaves = 0                            # built level-0 nodes
        self._offsets: dict[Path, int] = {}
        self._handles: dict[Path, object] = {}
        self._lock = None
        if write:
            self.root.mkdir(parents=True, exist_ok=True)
            self._lock = (self.root / "lock").open("a")
            try:
                fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                self._lock.close()
                raise Locked(f"another process owns {self.root}") from error
        self.refresh()

    @property
    def total(self) -> int:
        return len(self.messages)

    def close(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()
        if self._lock is not None:
            fcntl.flock(self._lock, fcntl.LOCK_UN)
            self._lock.close()
            self._lock = None

    def refresh(self) -> None:
        """Read whole lines added since the last read. Never writes."""
        for sub, take in (("main", self._take_message), ("tree", self._take_node)):
            folder = self.root / sub
            if not folder.is_dir():
                continue
            for path in sorted(folder.glob("*.jsonl")):
                start = self._offsets.get(path, 0)
                if path.stat().st_size <= start:
                    continue
                with path.open("rb") as fh:
                    fh.seek(start)
                    data = fh.read()
                end = data.rfind(b"\n") + 1      # a torn last line is left for later
                self._offsets[path] = start + end
                for line in data[:end].splitlines():
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        self.torn += 1
                        continue
                    take(row)

    def _take_message(self, row: dict) -> None:
        if row.get("i") != self.total or row.get("kind") not in KINDS or not isinstance(row.get("text"), str):
            self.torn += 1
            return
        self.messages.append(Message(row["i"], row["kind"], row["text"],
                                     int(row.get("size", utf8_len(row["text"]))), str(row.get("date", ""))))

    def _take_node(self, row: dict) -> None:
        key = (row.get("l"), row.get("i"))
        if type(key[0]) is int and type(key[1]) is int and isinstance(row.get("text"), str):
            if key not in self.nodes:              # a node is built once: the first row wins
                self.nodes[key] = row["text"]
                self.leaves += key[0] == 0

    def _append(self, sub: str, row: dict) -> None:
        if not self.write:
            raise Locked("this ChatLog was opened read-only")
        path = self.root / sub / f"{time.strftime('%Y-%m-%d', self.clock())}.jsonl"
        handle = self._handles.get(path)
        if handle is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("ab")
            if handle.tell() and path.read_bytes()[-1:] != b"\n":
                handle.write(b"\n")               # close a torn line by appending, never by editing
            self._handles[path] = handle
        handle.write((json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
        handle.flush()
        if self.fsync:
            os.fsync(handle.fileno())
        self._offsets[path] = handle.tell()

    def add_message(self, kind: str, text: str) -> Message:
        """Append exactly one message row. Clipping and splitting happen in Chat.append."""
        if kind not in KINDS:
            raise ValueError(f"bad kind: {kind!r} (thoughts are never logged)")
        date = time.strftime("%Y-%m-%dT%H:%M:%S%z", self.clock())
        message = Message(self.total, kind, text, utf8_len(text), date)
        self._append("main", {"i": message.i, "kind": kind, "text": text, "size": message.size, "date": date})
        self.messages.append(message)
        return message

    def add_node(self, level: int, index: int, text: str) -> None:
        if (level, index) in self.nodes:
            raise ValueError(f"node({level}, {index}) is already built")
        self._append("tree", {"l": level, "i": index, "text": text, "size": utf8_len(text)})
        self.nodes[(level, index)] = text
        self.leaves += level == 0
