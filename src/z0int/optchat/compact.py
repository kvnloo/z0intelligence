"""Compactions: each builds one node, with a call shaped like a turn.

    [tools] [system prompt] [<chat> compaction view </chat>] [task]

The task and the too-long retry are the gist's text, byte for byte. Up to 8
run at once, fed only from the ready queues. A reply over 512 bytes is sent
back in the same conversation, at most 5 tries, and the shortest line is
kept. A failed node waits for the next message.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from . import tree
from .chat import SLACK, ViewOver
from .claude_cli import ToolCalled
from .log import NODE, utf8_len
from .tree import Key
from .view import line as view_line

TRIES = 5
JOBS = 8
RULER = "-" * NODE

LEAF_TASK = """Compaction: compress message {id} into one line of at most 512 bytes
(about 70 words), the length of this ruler:
{ruler}
<input>
{kind}: {the message, whole}
</input>"""

MERGE_TASK = """Compaction: merge lines {a} and {b}, adjacent, into one line of at most
512 bytes (about 70 words), the length of this ruler:
{ruler}
<chat> may hold their messages, {id} to {end}, in more detail: take details
of them from there too.
<input>
{line a}
{line b}
</input>"""

TOO_LONG = """Too long: your line is {N} bytes, over the 512-byte limit. Write
the whole line again for the same <input>, cutting just enough of the
least valuable items to fit before this cut:
{its first 512 bytes}| ← LIMIT"""


def fill(template: str, **values: str) -> str:
    """Substitute every slot in one pass, so a message's own braces stay as they are."""
    out, at = [], 0
    while True:
        opened = template.find("{", at)
        closed = template.find("}", opened)
        if opened < 0 or closed < 0:
            out.append(template[at:])
            return "".join(out)
        out.append(template[at:opened])
        out.append(values[template[opened + 1:closed]])
        at = closed + 1


def leaf_task(ident: int, kind: str, text: str) -> str:
    return fill(LEAF_TASK, **{"id": str(ident), "ruler": RULER, "kind": kind, "the message, whole": text})


def merge_task(a: Key, b: Key, text_a: str, text_b: str) -> str:
    return fill(MERGE_TASK, **{"a": tree.name(a), "b": tree.name(b), "ruler": RULER,
                               "id": str(tree.start(a)), "end": str(tree.end(b)),
                               "line a": view_line(a, text_a), "line b": view_line(b, text_b)})


def first_bytes(text: str, limit: int = NODE) -> str:
    """The first `limit` bytes, never half a character."""
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def too_long(text: str) -> str:
    return fill(TOO_LONG, **{"N": str(utf8_len(text)), "its first 512 bytes": first_bytes(text)})


def build_line(session, task: str, tries: int = TRIES, on_start=None) -> tuple[str, int]:
    """Ask for the line; while it is over the limit, say so in the same conversation."""
    best, text = None, task
    for attempt in range(1, tries + 1):
        reply = session.send(text, on_start=on_start if attempt == 1 else None)
        if reply.requests > 1:              # the tools are there to match the turns' cache, never to be called
            raise ToolCalled(f"{session.label}: {reply.requests} requests")
        got = reply.text.strip()
        if not got:
            raise RuntimeError("empty compaction reply")
        if best is None or utf8_len(got) < utf8_len(best):
            best = got
        if utf8_len(got) <= NODE:
            break
        text = too_long(got)
    return best, attempt


class Compactor:
    def __init__(self, chat, backend, model: str, *, system: str, tools: dict | None = None,
                 jobs: int = JOBS, tries: int = TRIES, stop_on=(), slack: int = SLACK):
        self.chat, self.backend, self.model = chat, backend, model
        self.system, self.tools, self.jobs, self.tries, self.slack = system, tools, jobs, tries, slack
        self.stop_on = tuple(stop_on)     # errors that end the run at once (rate limits)
        self.warm = None                  # the compaction-view epoch whose prefix is in the cache
        self.peak = 0
        self.poll = None                  # seconds between looks at the queues while calls are in flight;
                                          # None: nothing is appended during a run (Background sets it)

    def _job(self, key: Key) -> tuple[str, str]:
        chat = self.chat
        if key[0] == 0:
            message = chat.log.messages[key[1]]
            task = leaf_task(message.i, message.kind, message.text)
        else:
            a, b = tree.halves(key)
            task = merge_task(a, b, chat.text(a), chat.text(b))
        return chat.context(key), task

    def _build(self, key: Key, view: str, task: str, started) -> tuple[str, int]:
        try:
            with self.backend.open(self.model, self.system, view, self.tools,
                                   label=f"compact {tree.name(key)}", max_requests=1) as session:
                return build_line(session, task, self.tries, started.set if started else None)
        finally:
            if started:
                started.set()

    def run(self, until: str = "idle") -> dict:
        """Build ready nodes until none is left ("idle") or every message has its line ("leaves").

        A compaction view more than `slack` bytes past its high mark stops the run
        before the call is sent (the chat was bulk loaded). A compaction that calls
        a tool fails and is counted in `tool_calls`.
        """
        chat = self.chat
        stats = {"built": [], "failed": [], "over": 0, "retries": 0, "stopped": None,
                 "tool_calls": 0, "context_max": 0}
        inflight, warming, stop = {}, None, None
        pool = ThreadPoolExecutor(self.jobs)
        try:
            while True:
                with chat.lock:
                    if warming and warming[0].is_set():
                        self.warm, warming = warming[1], None
                    while stop is None and len(inflight) < self.jobs:
                        if until == "leaves" and chat.unbuilt_leaves() == 0:
                            break
                        cold = self.warm != chat.epoch
                        if cold and warming:
                            break       # another call is writing this prefix: wait for its response
                        key = chat.ready.pop()
                        if key is None:
                            break
                        if chat.built(key):
                            chat.ready.built(key)
                            continue
                        started = threading.Event() if cold else None
                        if cold:
                            warming = (started, chat.epoch)
                        view, task = self._job(key)
                        size = utf8_len(view)
                        if size > chat.cview.high + self.slack:
                            chat.ready.fail(key)
                            warming = None if cold else warming
                            stop = ViewOver(f"the compaction view for {tree.name(key)} is {size} bytes, over "
                                            f"{chat.cview.high} + {self.slack}: feed the chat in live order")
                            break
                        stats["context_max"] = max(stats["context_max"], size)
                        inflight[pool.submit(self._build, key, view, task, started)] = key
                    self.peak = max(self.peak, len(inflight))
                if not inflight:
                    break
                done, _ = wait(inflight, timeout=0.05 if warming else self.poll, return_when=FIRST_COMPLETED)
                for future in done:
                    key = inflight.pop(future)
                    try:
                        text, tries = future.result()
                    except Exception as error:      # a failed call is tried again at the next message
                        chat.ready.fail(key)
                        stats["failed"].append({"node": tree.name(key), "error": f"{type(error).__name__}: {error}"[:500]})
                        stats["tool_calls"] += isinstance(error, ToolCalled)
                        if isinstance(error, self.stop_on):
                            stop = error
                        continue
                    chat.commit(key, text)
                    size = utf8_len(text)
                    stats["built"].append({"node": tree.name(key), "size": size, "tries": tries})
                    stats["retries"] += tries - 1
                    stats["over"] += size > NODE
        finally:
            pool.shutdown(wait=True)
        if stop is not None:
            stats["stopped"] = str(stop)[:500]
        return stats


class Background:
    """The gist's background compactor: one thread of the chat's own process that
    keeps building ready nodes while turns run.

    `Compactor.run` ends when nothing is ready; this calls it again whenever
    something is. A turn waits only for the lines before its message
    (`wait_leaves`); merges go on behind it. Still one writer, one lock, up to
    `jobs` calls at once.
    """

    def __init__(self, compactor: Compactor, poll: float = 0.05):
        self.compactor, self.chat, self.poll = compactor, compactor.chat, poll
        compactor.poll = poll             # messages arrive while calls are in flight: keep looking
        self.runs: list[dict] = []        # one entry per run: {"t0", "t1", stats...}
        self.busy = False
        self.busy_s = 0.0
        self.stopped: str | None = None   # why the compactor gave up (a limit, an oversized view)
        self._halt = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "Background":
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="compactor", daemon=True)
            self._thread.start()
        return self

    def _loop(self) -> None:
        chat = self.chat
        while not self._halt.is_set():
            with chat.lock:
                self.busy = not chat.ready.idle()
            if not self.busy:
                self._halt.wait(self.poll)
                continue
            began = time.time()
            try:
                stats = self.compactor.run("idle")
            except Exception as error:                 # never die silently: a turn would wait for ever
                stats = {"built": [], "failed": [], "over": 0, "retries": 0, "tool_calls": 0,
                         "context_max": 0, "stopped": f"{type(error).__name__}: {error}"[:500]}
            ended = time.time()
            with chat.lock:
                self.busy_s += ended - began
                self.runs.append({"t0": began, "t1": ended, **stats})
                if stats["stopped"]:
                    self.stopped = stats["stopped"]
                    self.busy = False
                    return

    def quiet(self) -> bool:
        """Nothing running and nothing ready (failed nodes may still be parked)."""
        with self.chat.lock:
            return not self.busy and self.chat.ready.idle()

    def wait_leaves(self, timeout: float | None = None) -> float:
        """Block until every message has its line, or no more can be built now. Seconds waited."""
        began = time.time()
        while self.chat.unbuilt_leaves() and not self.stopped and not self.quiet():
            if timeout is not None and time.time() - began > timeout:
                break
            time.sleep(self.poll)
        return time.time() - began

    def wait_idle(self, timeout: float | None = None) -> float:
        began = time.time()
        while not self.stopped and not self.quiet():
            if timeout is not None and time.time() - began > timeout:
                break
            time.sleep(self.poll)
        return time.time() - began

    def drain(self) -> list[dict]:
        """The runs finished since the last drain."""
        with self.chat.lock:
            out, self.runs = self.runs, []
            return out

    def stop(self) -> None:
        """Let the calls in flight finish, then end the thread."""
        self._halt.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
