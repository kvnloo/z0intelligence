"""The view: a list of nodes covering the whole chat, oldest first.

Which pairs merge: the most due sibling pair, due = (T - last) / 2^l, oldest
on ties, and only a pair whose parent is built. When: each message appends
one line and nothing else changes; once the view passes `high` bytes, one
batch merges down to at most `low`. A batch that cannot get there yet stays
pending and merges what it can at each new message.

The view is saved and loaded, never rebuilt from the log.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .log import utf8_len
from .tree import Key, name

HIGH, LOW = 128_000, 64_000          # the chat's view
CHIGH, CLOW = 32_000, 16_000         # a compaction's view
PLACEHOLDER = "(not summarized yet: zoom it)"


def pick(lines: list[Key], total: int, built=None) -> int | None:
    """Index of the left line of the most due sibling pair, or None.

    due = (T - last) / 2^l, compared exactly as (T + 1) / 2^l - i (the same
    order, shifted by 2) with integers: scaled by 2^top it is
    ((T + 1) << (top - l)) - (i << top).
    """
    top = max((key[0] for key in lines), default=0)
    best, best_due = None, None
    for at in range(len(lines) - 1):
        (level, index), right = lines[at], lines[at + 1]
        if index & 1 or right != (level, index + 1):
            continue
        if built is not None and not built((level + 1, index >> 1)):
            continue
        due = ((total + 1) << (top - level)) - (index << top)
        if best_due is None or due > best_due:     # strict: the oldest of equal pairs wins
            best, best_due = at, due
    return best


def merge_at(lines: list[Key], at: int) -> Key:
    level, index = lines[at]
    lines[at:at + 2] = [(level + 1, index >> 1)]
    return lines[at]


def line(key: Key, text: str | None) -> str:
    return f"{name(key)}|{(PLACEHOLDER if text is None else text).replace(chr(10), ' ')}"


class View:
    def __init__(self, high: int = HIGH, low: int = LOW, lines=(), pending: bool = False):
        self.high, self.low = high, low
        self.lines: list[Key] = [tuple(pair) for pair in lines]
        self.pending = pending

    def covered(self) -> int:
        """Messages covered so far: the next message's id."""
        if not self.lines:
            return 0
        level, index = self.lines[-1]
        return (index + 1) << level

    def append(self, ident: int) -> None:
        if ident != self.covered():
            raise ValueError(f"line {ident} does not follow the view (expected {self.covered()})")
        self.lines.append((0, ident))

    def size(self, text) -> int:
        """Bytes of the rendered lines, newlines included, tags excluded."""
        return sum(utf8_len(line(key, text(key))) + 1 for key in self.lines)

    def settle(self, total: int, text, built, force: bool = False) -> int:
        """Call after each append. Returns how many pairs merged (0 between batches)."""
        sizes = None
        if not self.pending:
            sizes = [utf8_len(line(key, text(key))) + 1 for key in self.lines]
            if force or sum(sizes) > self.high:
                self.pending = True
        if not self.pending:
            return 0
        if sizes is None:
            sizes = [utf8_len(line(key, text(key))) + 1 for key in self.lines]
        size, merged = sum(sizes), 0
        while size > self.low:
            at = pick(self.lines, total, built)
            if at is None:
                break
            key = merge_at(self.lines, at)
            new = utf8_len(line(key, text(key))) + 1
            size += new - sizes[at] - sizes[at + 1]
            sizes[at:at + 2] = [new]
            merged += 1
        if size <= self.low:
            self.pending = False
        return merged

    def render(self, text, lines: list[Key] | None = None) -> str:
        body = [line(key, text(key)) for key in (self.lines if lines is None else lines)]
        return "\n".join(["<chat>", *body, "</chat>"])


def save_json(path: Path, value) -> None:
    """Atomic: a crash leaves the old file or the new one, never half."""
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w") as fh:
        json.dump(value, fh, separators=(",", ":"))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_json(path: Path):
    return json.loads(path.read_text())
