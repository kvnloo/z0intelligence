"""One chat root: the log, the tree's ready queues, the view and the compaction view.

    main/ tree/ view.json     as the gist says
    lock                      the single writer's flock
    config.json               agent name and the view limits (fixed at init)
    cview.json                the compaction view and both pending-batch flags
"""

from __future__ import annotations

import threading
from pathlib import Path

from . import tree
from .prompt import system_prompt
from .log import ChatLog, Message, clip_echo, split_text, utf8_len
from .tree import Key, Ready
from .view import CHIGH, CLOW, HIGH, LOW, PLACEHOLDER, View, line, load_json, save_json


SLACK = 4_096      # how far past its high mark a rendered view may be before a run is refused


class NoView(RuntimeError):
    """The log has messages the saved view does not cover. The view is never rebuilt."""


class ViewOver(RuntimeError):
    """A rendered view is far past its high mark: the chat was not fed in live order.

    The view merges only at an append, and only pairs whose parent is built. So
    messages must arrive as in a real chat: one turn's messages, compact to idle,
    then the next. A bulk load leaves a view of several hundred KB.
    """


class Chat:
    @staticmethod
    def init(root: Path, *, name: str = "Unii", high: int = HIGH, low: int = LOW,
             chigh: int = CHIGH, clow: int = CLOW) -> None:
        root = Path(root)
        if (root / "config.json").exists() or (root / "main").exists():
            raise FileExistsError(f"{root} already holds a chat")
        root.mkdir(parents=True, exist_ok=True)
        save_json(root / "config.json", {"name": name, "high": high, "low": low, "chigh": chigh, "clow": clow})
        save_json(root / "view.json", [])
        save_json(root / "cview.json", {"lines": [], "pending": False, "chat_pending": False, "epoch": 0})

    def __init__(self, root: Path, *, write: bool = False, clock=None, fsync: bool = True):
        self.root = Path(root)
        if not (self.root / "config.json").exists():
            raise FileNotFoundError(f"{self.root} is not a chat root: run init")
        self.config = load_json(self.root / "config.json")
        self.name = self.config["name"]
        self.lock = threading.RLock()
        self.writer = write
        self.log = ChatLog(self.root, write=write, clock=clock, fsync=fsync)
        self.ready = Ready()
        self.merges = 0                 # view merges done by this process (for tests and status)
        self._load_views()
        if write:
            self._recover()

    def close(self) -> None:
        self.log.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- state -----------------------------------------------------------

    @property
    def total(self) -> int:
        return self.log.total

    def text(self, key: Key) -> str | None:
        return self.log.nodes.get(key)

    def built(self, key: Key) -> bool:
        return key in self.log.nodes

    def unbuilt_leaves(self) -> int:
        return self.total - self.log.leaves

    def _load_views(self) -> None:
        path = self.root / "view.json"
        if not path.exists():
            if self.log.total:
                raise NoView(f"{path} is missing and the view is never rebuilt from the log")
            lines = []
        else:
            lines = load_json(path)
        saved = load_json(self.root / "cview.json") if (self.root / "cview.json").exists() else {}
        self.view = View(self.config["high"], self.config["low"], lines, saved.get("chat_pending", False))
        self.cview = View(self.config["chigh"], self.config["clow"], saved.get("lines", lines),
                          saved.get("pending", False))
        self.epoch = saved.get("epoch", 0)   # bumps when the compaction view is rewritten

    def _save(self) -> None:
        # cview.json first: it holds both pending flags. A crash between the two saves
        # leaves view.json one line short, which the next start appends; no flag is lost.
        save_json(self.root / "cview.json", {"lines": [list(key) for key in self.cview.lines],
                                             "pending": self.cview.pending,
                                             "chat_pending": self.view.pending, "epoch": self.epoch})
        save_json(self.root / "view.json", [list(key) for key in self.view.lines])

    def _recover(self) -> None:
        """One linear pass at start: refill the queues, finish an interrupted append."""
        for message in self.log.messages:
            if (0, message.i) not in self.log.nodes:
                free = tree.free_leaf(message)
                if free is None:
                    self.ready.add_leaf(message.i)
                else:
                    self.commit((0, message.i), free)
        for key in sorted(self.log.nodes):
            if not key[1] & 1:
                self._after(key)
        late = False
        for view, file in ((self.view, "view.json"), (self.cview, "cview.json")):
            missing = self.total - view.covered()
            if missing > 1:                 # a crash loses at most the last message's line
                raise NoView(f"{self.root / file} lacks the last {missing} messages, and the view "
                             "is never rebuilt from the log")
            for ident in range(view.covered(), self.total):
                view.append(ident)          # a line per message: an append, not a rebuild
                late = True
        if late:
            self._save()

    def refresh(self) -> None:
        """For readers: pick up what the writer has added."""
        with self.lock:
            self.log.refresh()
            self._load_views()

    # -- writing ---------------------------------------------------------

    def append(self, kind: str, text: str) -> list[Message]:
        """Log one message, or several in a row when the text is long."""
        parts = [clip_echo(text)] if kind == "echo" else split_text(text)
        out = []
        with self.lock:
            for part in parts:
                message = self.log.add_message(kind, part)
                out.append(message)
                self.ready.retry()
                free = tree.free_leaf(message)
                if free is None:
                    self.ready.add_leaf(message.i)
                else:
                    self.commit((0, message.i), free)
                self.view.append(message.i)
                self.cview.append(message.i)
                merged = self.view.settle(self.total, self.text, self.built)
                self.merges += merged
                if self.cview.settle(self.total, self.text, self.built, force=bool(merged)):
                    self.epoch += 1
                self._save()
        return out

    def settle(self) -> int:
        """Run a due batch now, on the lines' real sizes. Returns how many pairs merged.

        `append` settles while the newest lines are still placeholders (29 bytes
        each, whatever their real size turns out to be), so a view can pass its
        high mark after the last append of a turn, once its lines are built,
        with no append left to notice. A turn calls this once every line before
        its message is built and before it renders: the batch that the view's
        real size calls for happens then, not one turn late. Which pairs merge
        is unchanged.
        """
        with self.lock:
            merged = self.view.settle(self.total, self.text, self.built)
            self.merges += merged
            moved = self.cview.settle(self.total, self.text, self.built, force=bool(merged))
            if moved:
                self.epoch += 1
            if merged or moved:
                self._save()
            return merged

    def commit(self, key: Key, text: str) -> None:
        """Log a built node once, then queue or freely build what it completes."""
        with self.lock:
            self.log.add_node(key[0], key[1], text)
            self.ready.built(key)
            self._after(key)

    def _after(self, key: Key) -> None:
        up = tree.parent(key)
        left, right = tree.halves(up)
        if up in self.log.nodes or left not in self.log.nodes or right not in self.log.nodes:
            return
        joined = tree.free_merge(self.log.nodes[left], self.log.nodes[right])
        if joined is None:
            self.ready.add_merge(up)
        else:
            self.commit(up, joined)

    # -- reading ---------------------------------------------------------

    def system(self) -> str:
        """The gist's prompt under this agent's name, then the user's own instructions."""
        path = self.root / "instructions.md"
        return system_prompt(self.name, instructions=path.read_text() if path.exists() else "")

    def render(self) -> str:
        with self.lock:
            return self.view.render(self.text)

    def gist_limits(self) -> bool:
        return [self.view.high, self.view.low, self.cview.high, self.cview.low] == [HIGH, LOW, CHIGH, CLOW]

    def over(self, slack: int = SLACK) -> int:
        """Bytes by which the rendered view passes its high mark plus the slack (0 if it does not)."""
        return max(0, utf8_len(self.render()) - self.view.high - slack)

    def check(self, slack: int = SLACK) -> None:
        over = self.over(slack)
        if over:
            raise ViewOver(f"the view is {over + self.view.high + slack} bytes, over {self.view.high} + {slack}: "
                           "feed the chat in live order (one turn's messages, compact to idle, the next)")

    def context(self, key: Key) -> str:
        """A compaction's view: the lines before the message, or up to the merge's
        last message, stopping at the first unbuilt line."""
        bound = key[1] if key[0] == 0 else tree.end(key) + 1
        with self.lock:
            lines = []
            for item in self.cview.lines:
                if tree.end(item) >= bound or item not in self.log.nodes:
                    break
                lines.append(item)
            return self.cview.render(self.text, lines)

    def zoom(self, ident: int, n: int) -> str:
        try:
            key = tree.key_of(ident, n)
        except ValueError as error:
            return str(error)
        with self.lock:
            if ident >= self.total:
                return f"No line {ident}+{n}: the chat has messages 0 to {self.total - 1}."
            if n == 1:
                return self.log.messages[ident].source()
            return "\n".join(line(half, self.text(half)) if tree.start(half) < self.total
                             else f"{tree.name(half)}|(no messages yet)" for half in tree.halves(key))

    def date(self, ident: int) -> str:
        with self.lock:
            if type(ident) is not int or not 0 <= ident < self.total:
                return f"No message {ident}."
            return self.log.messages[ident].date

    def status(self) -> dict:
        with self.lock:
            return {
                "root": str(self.root), "name": self.name, "messages": self.total,
                "nodes": len(self.log.nodes), "unbuilt_leaves": self.unbuilt_leaves(),
                # the queues live in the writer: a reader has none to show
                "ready_merges": len(self.ready.merges) if self.writer else None,
                "failed": len(self.ready.failed) if self.writer else None,
                "gist_limits": self.gist_limits(), "view_over": self.over(),
                "view_lines": len(self.view.lines), "view_bytes": self.view.size(self.text),
                "view_limits": [self.view.high, self.view.low], "view_pending": self.view.pending,
                "cview_lines": len(self.cview.lines), "cview_bytes": self.cview.size(self.text),
                "cview_limits": [self.cview.high, self.cview.low], "torn": self.log.torn,
                "placeholder": PLACEHOLDER,
            }
