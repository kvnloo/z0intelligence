"""The tree: node(l, i), its id+n name, free nodes, and the ready queues.

    node(0, i) = message i, in at most 512 bytes
    node(l, i) = node(l-1, 2i) and node(l-1, 2i+1) merged, in at most 512 bytes

Nothing here scans the tree. A leaf waits in id order behind a gate; a merge
is queued at the moment its second half is built.
"""

from __future__ import annotations

from collections import deque

from .log import NODE, Message, utf8_len

GATE = 8
Key = tuple[int, int]


def start(key: Key) -> int:
    return key[1] << key[0]

def span(key: Key) -> int:
    return 1 << key[0]

def end(key: Key) -> int:
    """The node's last message."""
    return ((key[1] + 1) << key[0]) - 1

def name(key: Key) -> str:
    return f"{start(key)}+{span(key)}"

def key_of(ident: int, n: int) -> Key:
    """zoom's (id, n) as a node: n a power of 2, id a multiple of n."""
    if type(ident) is not int or type(n) is not int or ident < 0 or n < 1 or n & (n - 1) or ident % n:
        raise ValueError(f"no line {ident}+{n}: n is a power of 2 and id a multiple of n")
    level = n.bit_length() - 1
    return (level, ident >> level)

def parent(key: Key) -> Key:
    return (key[0] + 1, key[1] >> 1)

def sibling(key: Key) -> Key:
    return (key[0], key[1] ^ 1)

def halves(key: Key) -> tuple[Key, Key]:
    return (key[0] - 1, key[1] * 2), (key[0] - 1, key[1] * 2 + 1)


def free_leaf(message: Message, limit: int = NODE) -> str | None:
    """A short message (`kind: text`) is its own node, word for word."""
    source = message.source()
    return source if utf8_len(source) <= limit else None

def free_merge(a: str, b: str, limit: int = NODE) -> str | None:
    """Two short lines are joined by a newline."""
    joined = f"{a}\n{b}"
    return joined if utf8_len(joined) <= limit else None


class Ready:
    """Nodes ready to build. Leaves first, then merges.

    `out` holds the leaves that were released and are still unbuilt. Leaves
    are released in id order, so `out` is exactly "the lines before the next
    leaf that are still unbuilt": the next leaf starts once fewer than `gate`
    of them remain. A failed node waits in `failed` until the next message.
    """

    def __init__(self, gate: int = GATE):
        self.gate = gate
        self.leaves: deque[int] = deque()
        self.merges: deque[Key] = deque()
        self.redo: deque[Key] = deque()
        self.failed: list[Key] = []
        self.out: set[int] = set()
        self.queued: set[Key] = set()

    def add_leaf(self, ident: int) -> None:
        self.leaves.append(ident)

    def add_merge(self, key: Key) -> None:
        if key not in self.queued:          # a node is queued once, so it is built once
            self.queued.add(key)
            self.merges.append(key)

    def pop(self) -> Key | None:
        if self.redo:
            return self.redo.popleft()
        if self.leaves and len(self.out) < self.gate:
            ident = self.leaves.popleft()
            self.out.add(ident)
            return (0, ident)
        if self.merges:
            return self.merges.popleft()
        return None

    def built(self, key: Key) -> None:
        if key[0] == 0:
            self.out.discard(key[1])

    def fail(self, key: Key) -> None:
        self.failed.append(key)

    def retry(self) -> None:
        """A new message arrived: failed nodes are tried again."""
        self.redo.extend(self.failed)
        self.failed.clear()

    def idle(self) -> bool:
        return not (self.redo or self.merges or (self.leaves and len(self.out) < self.gate))
