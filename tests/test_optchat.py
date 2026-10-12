"""UniiChat core against the gist (tests/fixtures/uniichat_gist.md). No model is called."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from z0int.optchat import tree
from z0int.optchat.chat import SLACK, Chat, NoView, ViewOver
from z0int.optchat.claude_cli import BackendError, ClaudeCLI, LimitError, Reply, ToolCalled, blocks_of
from z0int.optchat.compact import (LEAF_TASK, MERGE_TASK, RULER, TOO_LONG, Compactor, build_line,
                                   first_bytes, leaf_task, merge_task, too_long)
from z0int.optchat.log import CAP, SPLIT, ChatLog, Locked, clip_echo, split_text
from z0int.optchat.mcp import handle
from z0int.optchat.prompt import DATE, GIST_PROMPT, TOOLS, ZOOM, system_prompt
from z0int.optchat.turn import NotReady, run_turn
from z0int.optchat.view import PLACEHOLDER, View, merge_at, pick

GIST = (Path(__file__).parent / "fixtures" / "uniichat_gist.md").read_text().split("\n")
SRC = str(Path(__file__).resolve().parents[1] / "src")


def gist(first: int, last: int) -> str:
    """Lines first..last of the gist, 1-based and inclusive."""
    return "\n".join(GIST[first - 1:last])


# -- Taelin's push, and the merge order ----------------------------------------

def push(states: list, new) -> None:
    """rollback_state_list.js `push` on a newest-first list of [keep, state] (life stays 0)."""
    at = 0
    while True:
        if at == len(states):
            states.append([0, new])
            return
        if states[at][0] == 0:
            states[at][0] = 1          # a 0 absorbs: the new state is dropped
            return
        new, states[at] = states[at][1], [0, new]   # a 1 carries
        at += 1


def push_lines(states: list, total: int) -> list:
    """Each state starts a line that runs up to the next newer state (the newest, to now)."""
    lines, until = [], total
    for _, state in states:
        n = until - state
        lines.append(tree.key_of(state, n))
        until = state
    return lines[::-1]


def pick_first(lines, total):
    """The wrong recipe: age measured from the pair's first message."""
    top = max(key[0] for key in lines)
    best, best_due = None, None
    for at in range(len(lines) - 1):
        (level, index), right = lines[at], lines[at + 1]
        if index & 1 or right != (level, index + 1):
            continue
        due = (total - (index << level)) << (top - level)
        if best_due is None or due > best_due:
            best, best_due = at, due
    return best


def test_push_matches_the_gist_table():
    states, seen = [], []
    for t in range(10):
        push(states, t)
        seen.append(", ".join(("+" if keep else "-") + str(state) for keep, state in states))
    assert seen == ["-0", "+0", "-2, -0", "+2, -0", "-4, +0", "+4, +0", "-6, -4, -0", "+6, -4, -0",
                    "-8, +4, -0", "+8, +4, -0"]
    assert push_lines(states, 10) == [tree.key_of(0, 4), tree.key_of(4, 4), tree.key_of(8, 2)]


def test_merge_order_is_push_for_t_0_to_20000():
    states, lines, wrong, wrong_ok = [], [], [], 0
    for t in range(20_001):
        push(states, t)
        target = push_lines(states, t + 1)
        for view, chooser in ((lines, pick), (wrong, pick_first)):
            view.append((0, t))
            while len(view) > len(states):
                merge_at(view, chooser(view, t + 1))
        assert lines == target, t
        wrong_ok += wrong == target
    assert wrong_ok == 481          # the gist: the first-message recipe matches at only 481 steps


def test_t10_counterexample():
    lines = [tree.key_of(0, 4), tree.key_of(4, 4), (0, 8), (0, 9)]
    assert lines[pick(lines, 10)] == (0, 8)            # push merges 8-9
    assert lines[pick_first(lines, 10)] == (2, 0)      # the first-message recipe merges 0-7


def test_ties_pick_the_oldest_and_an_unbuilt_parent_is_skipped():
    lines = [(0, 0), (0, 1), (0, 2), (0, 3)]
    # T=4: pair 0-1 has due (4-1)/1 = 3, pair 2-3 has due 1
    assert pick(lines, 4) == 0
    assert pick(lines, 4, built=lambda key: key != (1, 0)) == 2
    assert pick(lines, 4, built=lambda key: False) is None
    # equal due at T=7: 0+2|2+2 ended 4 ago in size 2 (due 2); 4+1|5+1 ended 2 ago in size 1 (due 2)
    lines = [(1, 0), (1, 1), (0, 4), (0, 5), (0, 6)]
    assert pick(lines, 7) == 0
    assert pick(lines, 8) == 2                         # one message later the newer pair is more due
    assert pick([(0, 0), (0, 1)], 2, built=None) == 0
    assert pick([(0, 1), (0, 2)], 3) is None           # adjacent but not siblings


# -- the view's sawtooth --------------------------------------------------------

def test_batches_from_high_down_to_low_and_nothing_between():
    text = lambda key: "x" * 90
    built = lambda key: True
    view = View(high=16_000, low=8_000)
    batches, peak = 0, 0
    for t in range(6_000):
        before = list(view.lines)
        view.append(t)
        merged = view.settle(t + 1, text, built)
        size = view.size(text)
        if merged:
            batches += 1
            assert size <= 8_000 and not view.pending
        else:
            assert view.lines[:-1] == before             # one line appended, nothing else changed
            assert size <= 16_000
        peak = max(peak, size)
        assert view.covered() == t + 1
    assert batches > 10 and 15_800 < peak <= 16_000


def test_blocked_batch_stays_pending_and_resumes_at_each_message():
    text = lambda key: "x" * 90
    frontier = [0]
    built = lambda key: tree.end(key) < frontier[0]
    view = View(high=4_000, low=2_000)
    t = 0
    while not view.pending:
        view.append(t)
        assert view.settle(t + 1, text, built) == 0
        t += 1
    assert view.size(text) > 4_000                       # no parent is built: nothing may merge
    frontier[0] = 16
    view.append(t)
    assert view.settle(t + 1, text, built) == 15         # what it can: every pair under messages 0..15
    assert view.pending and view.lines[:3] == [(4, 0), (0, 16), (0, 17)]
    t += 1
    frontier[0] = 10 ** 9
    view.append(t)
    assert view.settle(t + 1, text, built) > 0
    assert not view.pending and view.size(text) <= 2_000


def rewrite_cost(batched: bool, messages: int = 30_000) -> float:
    """Lines sent uncached per message: every line from the first changed one on."""
    text = lambda key: "x" * 500
    built = lambda key: True
    view = View(high=128_000, low=64_000) if batched else View(high=96_000, low=96_000)
    cost = 0
    for t in range(messages):
        before = list(view.lines)
        view.append(t)
        view.settle(t + 1, text, built)
        same = 0
        while same < len(before) and same < len(view.lines) and before[same] == view.lines[same]:
            same += 1
        cost += len(view.lines) - same
    return cost / messages


def test_batching_rewrites_far_fewer_lines_than_merging_at_every_message():
    batched, every = rewrite_cost(True, 6_000), rewrite_cost(False, 6_000)
    assert batched * 2 < every, (batched, every)


# -- the log --------------------------------------------------------------------

def new_chat(tmp_path, **limits) -> Chat:
    root = tmp_path / "chat"
    Chat.init(root, **limits)
    return Chat(root, write=True, fsync=False)


def digest(root: Path) -> str:
    out = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out.update(path.name.encode() + path.read_bytes())
    return out.hexdigest()


def test_record_shapes_kinds_and_day_files(tmp_path):
    chat = new_chat(tmp_path)
    for kind in ("user", "unii", "tool", "echo", "work", "note"):
        chat.append(kind, f"hello {kind}")
    with pytest.raises(ValueError):
        chat.append("thought", "thoughts are never logged")
    with pytest.raises(ValueError):
        chat.append("talk", "old kind")
    chat.close()
    day = time.strftime("%Y-%m-%d")
    rows = [json.loads(raw) for raw in (tmp_path / "chat" / "main" / f"{day}.jsonl").read_text().splitlines()]
    assert [list(row) for row in rows] == [["i", "kind", "text", "size", "date"]] * 6
    assert [row["i"] for row in rows] == list(range(6)) and rows[1]["size"] == len("hello unii")
    nodes = [json.loads(raw) for raw in (tmp_path / "chat" / "tree" / f"{day}.jsonl").read_text().splitlines()]
    assert all(list(node) == ["l", "i", "text", "size"] for node in nodes)
    assert nodes[0] == {"l": 0, "i": 0, "text": "user: hello user", "size": 16}
    assert json.loads((tmp_path / "chat" / "view.json").read_text())[-1] == [0, 5]


def test_echo_is_clipped_head_and_tail_and_other_long_text_is_split(tmp_path):
    chat = new_chat(tmp_path)
    echo = "H" * 40_000 + "T" * 40_000
    (message,) = chat.append("echo", echo)
    assert len(message.text) == CAP == 30_000
    assert message.text.startswith("HHHH") and message.text.endswith("TTTT") and "characters cut" in message.text
    assert clip_echo("short") == "short"
    long = "".join(chr(0x4E00 + i % 500) for i in range(SPLIT * 2 + 17))
    parts = chat.append("user", long)
    assert [m.i for m in parts] == [1, 2, 3] and all(m.kind == "user" for m in parts)
    assert "".join(m.text for m in parts) == long            # never cut
    assert split_text("") == [""]
    assert parts[0].size == len(parts[0].text.encode())


def test_single_writer_lock_and_readers_never_write(tmp_path):
    chat = new_chat(tmp_path)
    chat.append("user", "one")
    with pytest.raises(Locked):
        Chat(tmp_path / "chat", write=True)
    before = digest(tmp_path / "chat")
    reader = Chat(tmp_path / "chat")
    assert reader.total == 1 and reader.render() == chat.render()
    with pytest.raises(Locked):
        reader.append("user", "no")
    chat.append("user", "two")
    reader.refresh()
    assert reader.total == 2 and reader.zoom(1, 1) == "user: two"
    assert digest(tmp_path / "chat") != before
    after = digest(tmp_path / "chat")
    Chat(tmp_path / "chat").status()
    assert digest(tmp_path / "chat") == after
    chat.close()
    Chat(tmp_path / "chat", write=True).close()             # the lock is free again


def test_each_write_is_on_disk_at_once_and_a_torn_line_is_skipped(tmp_path):
    chat = new_chat(tmp_path)
    chat.append("user", "kept")
    path = next((tmp_path / "chat" / "main").glob("*.jsonl"))
    assert json.loads(path.read_text().splitlines()[-1])["text"] == "kept"   # flushed, no close needed
    chat.close()
    with path.open("ab") as fh:
        fh.write(b'{"i": 1, "kind": "user", "te')
    chat = Chat(tmp_path / "chat", write=True, fsync=False)
    assert chat.total == 1
    assert chat.append("user", "next")[0].i == 1
    chat.close()
    assert Chat(tmp_path / "chat").zoom(1, 1) == "user: next"
    assert path.read_bytes().count(b'"te\n') == 1            # the torn line was closed, never edited


# -- the tree -------------------------------------------------------------------

def test_names_and_zoom_addresses():
    assert tree.name((3, 5)) == "40+8" and tree.key_of(40, 8) == (3, 5)
    assert tree.start((3, 5)) == 40 and tree.end((3, 5)) == 47 and tree.halves((3, 5)) == ((2, 10), (2, 11))
    for bad in ((40, 3), (41, 8), (-8, 8), (0, 0)):
        with pytest.raises(ValueError):
            tree.key_of(*bad)


def test_free_leaf_and_free_merge_need_no_model(tmp_path):
    chat = new_chat(tmp_path)
    chat.append("user", "fix the parser")
    chat.append("unii", "done:\nparser.py line 9")
    assert chat.text((0, 0)) == "user: fix the parser"
    assert chat.text((1, 0)) == "user: fix the parser\nunii: done:\nparser.py line 9"   # joined by a newline
    assert chat.ready.pop() is None
    exact = "x" * (512 - len("user: "))
    chat.append("user", exact)
    assert chat.text((0, 2)) == "user: " + exact
    chat.append("user", exact + "y")                         # 513 bytes: needs a model
    assert chat.text((0, 3)) is None and chat.ready.pop() == (0, 3)
    assert chat.render().splitlines()[-2] == f"3+1|{PLACEHOLDER}"
    with pytest.raises(ValueError):
        chat.log.add_node(0, 0, "again")                     # each node is built once


def test_leaf_gate_and_merge_queue(tmp_path):
    chat = new_chat(tmp_path)
    long = "word " * 200
    for _ in range(20):
        chat.append("user", long)
    popped = [chat.ready.pop() for _ in range(9)]
    assert popped[:8] == [(0, i) for i in range(8)] and popped[8] is None   # 8 unbuilt lines before 8
    assert chat.ready.idle()
    chat.commit((0, 3), "line 3 " + "z" * 300)
    assert chat.ready.pop() == (0, 8) and chat.ready.pop() is None
    assert not chat.ready.merges                              # 2's half is still unbuilt
    chat.commit((0, 2), "line 2 " + "z" * 300)
    assert list(chat.ready.merges) == [(1, 1)]                # queued when its second half was built
    chat.commit((0, 0), "a")
    chat.commit((0, 1), "b")
    assert chat.text((1, 0)) == "a\nb" and list(chat.ready.merges) == [(1, 1)]
    chat.ready.fail((0, 4))
    assert (0, 4) not in [chat.ready.pop() for _ in range(4)]   # a failed node waits...
    chat.append("user", "short")
    assert chat.ready.pop() == (0, 4)                            # ...for the next message


def test_queues_do_no_scanning(tmp_path):
    chat = new_chat(tmp_path)
    looked = [0]

    class Counting(dict):
        def __contains__(self, key):
            looked[0] += 1
            return dict.__contains__(self, key)

    chat.log.nodes = Counting(chat.log.nodes)
    per_append = []
    for i in range(3_000):
        before = looked[0]
        chat.append("user", f"message {i} " + "pad " * 150)   # one model-sized node per message
        if chat.ready.pop() is not None:
            chat.commit((0, i), f"m{i} " + "s" * 400)
            while (key := chat.ready.pop()) is not None:
                chat.commit(key, f"n{key} " + "s" * 400)
        per_append.append(looked[0] - before)
    assert max(per_append[2_000:]) <= max(per_append[:500]) + 8   # work per message does not grow with the chat
    assert len(chat.log.nodes) == 2 * 3_000 - bin(3_000).count("1")


# -- view.json ------------------------------------------------------------------

def test_view_is_saved_and_loaded_never_rebuilt(tmp_path):
    chat = new_chat(tmp_path, high=4_000, low=2_000, chigh=1_000, clow=500)
    for i in range(300):
        chat.append("user", f"message {i}: " + "detail " * 8)
    assert chat.merges > 0
    lines, rendered, cview = list(chat.view.lines), chat.render(), list(chat.cview.lines)
    chat.close()
    saved = json.loads((tmp_path / "chat" / "view.json").read_text())
    assert [tuple(pair) for pair in saved] == lines and all(len(pair) == 2 for pair in saved)
    again = Chat(tmp_path / "chat", write=True, fsync=False)
    assert again.view.lines == lines and again.render() == rendered and again.cview.lines == cview
    again.close()
    (tmp_path / "chat" / "view.json").unlink()
    with pytest.raises(NoView):
        Chat(tmp_path / "chat")


def test_an_interrupted_append_is_finished_by_appending(tmp_path):
    chat = new_chat(tmp_path)
    chat.append("user", "a")
    chat.log.add_message("user", "logged, then the process died")
    chat.close()
    again = Chat(tmp_path / "chat", write=True, fsync=False)
    assert again.view.lines == [(1, 0)] or again.view.lines == [(0, 0), (0, 1)]
    assert again.view.covered() == 2 and again.text((0, 1)) is not None


def test_compaction_view_sawtooth_and_remerge_with_the_chat_view(tmp_path):
    chat = new_chat(tmp_path, high=8_000, low=4_000, chigh=2_000, clow=1_000)
    epochs, chat_merges = set(), 0
    for i in range(600):
        before = chat.merges
        chat.append("user", f"message {i}: " + "detail " * 8)
        while (key := chat.ready.pop()) is not None:   # a stand-in compactor that keeps up
            chat.commit(key, f"merged {tree.name(key)} " + "m" * 80)
        size = chat.cview.size(chat.text)
        assert size <= 2_000 + 120                  # it passes the limit by at most the line that tripped it
        if chat.merges > before:
            chat_merges += 1
            assert size <= 1_000 or chat.cview.pending   # the chat's view merged: so did the compaction view
        epochs.add(chat.epoch)
        assert chat.cview.covered() == chat.view.covered() == i + 1
    assert chat_merges >= 3 and len(epochs) > chat_merges
    assert len(chat.cview.lines) < len(chat.view.lines)


# -- compactions ----------------------------------------------------------------

def test_task_and_retry_texts_are_the_gists_byte_for_byte():
    ruler_shown = "------------…------------"
    assert RULER == "-" * 512
    assert LEAF_TASK.replace("{ruler}", ruler_shown) == gist(265, 270)
    assert MERGE_TASK.replace("{ruler}", ruler_shown) == gist(274, 282)
    assert TOO_LONG == gist(290, 293)
    assert leaf_task(7, "user", "do {it} now") == gist(265, 270).replace(ruler_shown, RULER).replace(
        "{id}", "7").replace("{kind}: {the message, whole}", "user: do {it} now")
    task = merge_task((2, 10), (2, 11), "left {b}\nline", "right")
    assert task == gist(274, 282).replace(ruler_shown, RULER).replace("{a}", "40+4").replace(
        "{b} and", "{b} and").replace("lines 40+4 and {b}", "lines 40+4 and 44+4").replace(
        "{id} to {end}", "40 to 47").replace("{line a}", "40+4|left {b} line").replace("{line b}", "44+4|right")
    line = "é" * 300
    assert too_long(line) == gist(290, 293).replace("{N}", "600").replace("{its first 512 bytes}", "é" * 256)
    assert first_bytes("a" + "é" * 300) == "a" + "é" * 255        # never half a character


class FakeSession:
    def __init__(self, backend, model, system, view, tools, label):
        self.backend, self.model, self.system, self.view, self.tools, self.label = (
            backend, model, system, view, tools, label)
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def send(self, text, on_start=None, on_block=None):
        self.sent.append(text)
        return Reply(self.backend.answer(self, on_start, on_block), {})


class FakeBackend:
    def __init__(self, answer):
        self.answer, self.sessions, self.lock = answer, [], threading.Lock()

    def open(self, model, system, view="", tools=None, label="", effort=None, max_requests=1):
        session = FakeSession(self, model, system, view, tools, label)
        session.max_requests = max_requests
        with self.lock:
            self.sessions.append(session)
        return session


def test_too_long_is_retried_in_the_same_conversation_five_times_keeping_the_shortest():
    sizes = iter([600, 530, 560, 520, 515, 100])
    backend = FakeBackend(lambda session, *_: "y" * next(sizes))
    session = backend.open("m", "s")
    line, tries = build_line(session, "TASK")
    assert tries == 5 and len(line) == 515 and len(session.sent) == 5
    assert session.sent[0] == "TASK" and session.sent[1] == too_long("y" * 600)
    assert session.sent[1].startswith("Too long: your line is 600 bytes, over the 512-byte limit.")
    assert session.sent[1].endswith("y" * 512 + "| ← LIMIT")
    fits = FakeBackend(lambda session, *_: " fits \n").open("m", "s")
    assert build_line(fits, "TASK") == ("fits", 1) and len(fits.sent) == 1
    with pytest.raises(RuntimeError):
        build_line(FakeBackend(lambda session, *_: "  ").open("m", "s"), "TASK")


def test_compactor_contexts_concurrency_and_retry_at_next_message(tmp_path):
    chat = new_chat(tmp_path, high=64_000, low=32_000, chigh=6_000, clow=3_000)
    long = "word " * 200
    for i in range(40):
        chat.append("user", f"m{i} {long}")
    running, peak, fail = [0], [0], {12}
    gate = threading.Lock()

    def answer(session, on_start, on_block):
        with gate:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
        if on_start:
            time.sleep(0.05)
            on_start()
        time.sleep(0.01)
        with gate:
            running[0] -= 1
        if session.label == "compact 12+1" and fail:
            fail.clear()
            raise OSError("boom")
        return f"line for {session.label} " + "s" * 300

    backend = FakeBackend(answer)
    compactor = Compactor(chat, backend, "cheap", system="SYS", tools={"t": 1})
    stats = compactor.run()
    assert 1 < peak[0] <= 8 and compactor.peak <= 8
    assert [f["node"] for f in stats["failed"]] == ["12+1"]
    assert chat.text((0, 12)) is None and chat.unbuilt_leaves() == 1
    first = backend.sessions[0]
    assert first.view == "<chat>\n</chat>" and first.system == "SYS" and first.tools == {"t": 1}
    for session in backend.sessions:
        assert PLACEHOLDER not in session.view                 # no call ever sees a placeholder
        node = session.label.split()[1]
        ident, n = (int(part) for part in node.split("+"))
        heads = [row.split("|")[0] for row in session.view.splitlines()[1:-1]]
        ends = [int(h.split("+")[0]) + int(h.split("+")[1]) for h in heads]
        if n == 1:
            assert all(e <= ident for e in ends)               # the lines before the message
            assert session.sent[0].startswith(f"Compaction: compress message {ident} into one line")
        else:
            assert all(e <= ident + n for e in ends)           # up to the merge's last message
            assert ends[-1] in (ident + n, 12)                 # its halves are in the view (or it stopped at unbuilt 12)
            assert session.sent[0].startswith(f"Compaction: merge lines {ident}+{n // 2} and {ident + n // 2}+{n // 2},")
        starts = [int(h.split("+")[0]) for h in heads]
        assert starts == sorted(starts) and (not starts or starts[0] == 0)
        assert all(a + (e - a) == b for a, e, b in zip(starts, ends, starts[1:]))   # no gap: it stops at the first unbuilt line
    after_13 = [s for s in backend.sessions if s.label == "compact 13+1"][0]
    assert after_13.view.count("\n") - 1 <= 12                 # stops at unbuilt 12
    calls = len(backend.sessions)
    assert compactor.run()["built"] == [] and len(backend.sessions) == calls   # not retried on its own
    chat.append("user", "next message")
    again = compactor.run()
    assert "12+1" in [b["node"] for b in again["built"]] and chat.unbuilt_leaves() == 0
    assert chat.ready.idle() and not chat.ready.failed
    built = [s.label for s in backend.sessions]
    assert len(built) - 1 == len(set(built))                   # every node built once (12+1 tried twice)


def test_prefix_gate_one_call_writes_the_prefix_before_the_rest_start(tmp_path):
    chat = new_chat(tmp_path)
    for i in range(12):
        chat.append("user", f"m{i} " + "word " * 200)
    order, gate = [], threading.Lock()

    def answer(session, on_start, on_block):
        with gate:
            order.append(("in", session.label, bool(on_start)))
        time.sleep(0.15 if on_start else 0.02)
        if on_start:
            with gate:
                order.append(("started", session.label, True))
            on_start()
        return "line " + "s" * 200

    Compactor(chat, FakeBackend(answer), "cheap", system="SYS").run("leaves")
    assert order[0] == ("in", "compact 0+1", True) and order[1] == ("started", "compact 0+1", True)
    assert sum(1 for _, _, warm in order if warm) == 2          # only the first call waited on nobody
    assert chat.unbuilt_leaves() == 0


# -- the prompt and the tools ---------------------------------------------------

def test_system_prompt_is_the_gists_verbatim():
    assert GIST_PROMPT == gist(321, 409)
    assert system_prompt("Unii", computers=True) == gist(321, 409)
    ours = system_prompt("Unii")
    assert ours == gist(321, 370) + "\n" + gist(376, 409)        # the paragraph on computers dropped
    assert "computers" not in ours and "# Compactions" in ours and "\n\n\n" not in ours
    named = system_prompt("Zed")
    assert "Unii" not in named and named.startswith("You are Zed, an AI agent")
    assert "- unii: Zed's replies" in named                      # the kind keeps its name
    assert system_prompt("Zed", instructions="Be terse.\n") == named + "\n\nBe terse."
    assert len({system_prompt("Zed") for _ in range(100)}) == 1
    assert ZOOM == ("Open the line id+n of the view into the two lines of n/2 under it; "
                    "n = 1 gives the message whole.")
    assert ZOOM in gist(445, 446).replace("\n  ", " ") and DATE in gist(448, 448)
    assert [tool["name"] for tool in TOOLS] == ["zoom", "date"]


def test_view_blocks_of_four_lines_with_a_mark_on_the_last_whole_block():
    view = "<chat>\n" + "\n".join(f"{i}+1|line {i}" for i in range(10)) + "\n</chat>"
    blocks = blocks_of(view)
    assert "".join(block["text"] for block in blocks) == view
    assert [block["text"].count("\n") for block in blocks] == [5, 4, 2]
    assert [("cache_control" in block) for block in blocks] == [False, True, False]
    longer = blocks_of(view.replace("\n</chat>", "\n10+1|line 10\n11+1|line 11\n12+1|line 12\n</chat>"))
    assert [b["text"] for b in longer[:2]] == [b["text"] for b in blocks[:2]]   # the old marked prefix, byte for byte
    assert "cache_control" in longer[2] and len(longer) == 4
    assert "".join(b["text"] for b in blocks_of("<chat>\n</chat>")) == "<chat>\n</chat>"


# -- a turn, and zoom/date ------------------------------------------------------

def test_turn_renders_the_view_before_logging_the_message(tmp_path):
    chat = new_chat(tmp_path)
    chat.append("user", "the deploy key lives in vault path kv/ops/deploy")
    chat.append("unii", "noted")

    def answer(session, on_start, on_block):
        assert chat.log.messages[2].kind == "user" and chat.log.messages[2].text == "where is the key?"
        on_block("tool", 'zoom {"id": 0, "n": 1}')
        on_block("echo", chat.zoom(0, 1))
        on_block("unii", "kv/ops/deploy")
        return "kv/ops/deploy"

    backend = FakeBackend(answer)
    out = run_turn(chat, backend, "smart", "where is the key?", tools={"t": 1})
    (session,) = backend.sessions
    assert session.view == "<chat>\n0+1|user: the deploy key lives in vault path kv/ops/deploy\n1+1|unii: noted\n</chat>"
    assert "where is the key?" not in session.view and session.sent == ["where is the key?"]
    assert session.system == system_prompt("Unii") and "20" + time.strftime("%y") not in session.system
    assert [(m.kind, m.text) for m in chat.log.messages[2:]] == [
        ("user", "where is the key?"), ("tool", 'zoom {"id": 0, "n": 1}'),
        ("echo", "user: the deploy key lives in vault path kv/ops/deploy"), ("unii", "kv/ops/deploy")]
    assert out["message"] == 2 and out["reply"] == "kv/ops/deploy"


def test_turn_waits_until_everything_before_the_message_is_summarized(tmp_path):
    chat = new_chat(tmp_path)
    chat.append("echo", "result " * 400)
    turns = FakeBackend(lambda *a: "ok")
    with pytest.raises(NotReady):
        run_turn(chat, turns, "smart", "go on")
    assert chat.total == 1 and not turns.sessions               # nothing was logged or sent
    cheap = FakeBackend(lambda *a: "echo: a long result")
    out = run_turn(chat, turns, "smart", "go on",
                   compactor=Compactor(chat, cheap, "cheap", system=chat.system()))
    assert turns.sessions[0].view == "<chat>\n0+1|echo: a long result\n</chat>"
    assert out["compaction"]["built"][0]["node"] == "0+1"
    assert cheap.sessions[0].system == turns.sessions[0].system   # one system prompt for both


def test_zoom_and_date(tmp_path):
    chat = new_chat(tmp_path)
    for i in range(6):
        chat.append("user", f"fact {i}")
    assert chat.zoom(4, 1) == "user: fact 4"
    assert chat.zoom(0, 4) == "0+2|user: fact 0 user: fact 1\n2+2|user: fact 2 user: fact 3"
    assert chat.zoom(0, 2) == "0+1|user: fact 0\n1+1|user: fact 1"
    assert chat.zoom(0, 8) == f"0+4|{chat.text((2, 0)).replace(chr(10), ' ')}\n4+4|{PLACEHOLDER}"
    assert chat.zoom(8, 8).startswith("No line 8+8") and "power of 2" in chat.zoom(0, 3)
    assert chat.date(2) == chat.log.messages[2].date and chat.date(2)[:4] == time.strftime("%Y")
    assert chat.date(99) == "No message 99."
    reply = handle(chat, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": "zoom", "arguments": {"id": 4, "n": 2}}})
    assert reply["result"]["content"][0]["text"] == "4+1|user: fact 4\n5+1|user: fact 5"
    assert handle(chat, {"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_mcp_server_round_trip_over_stdio(tmp_path):
    chat = new_chat(tmp_path)
    chat.append("user", "the port is 8443")
    proc = subprocess.Popen([sys.executable, "-m", "z0int.optchat", "mcp", "--root", str(tmp_path / "chat")],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env={"PYTHONPATH": SRC})

    def ask(method, params=None, ident=[0]):
        ident[0] += 1
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": ident[0], "method": method, "params": params or {}}) + "\n")
        proc.stdin.flush()
        return json.loads(proc.stdout.readline())["result"]

    try:
        assert ask("initialize", {"protocolVersion": "2025-06-18"})["protocolVersion"] == "2025-06-18"
        tools = ask("tools/list")["tools"]
        assert [(t["name"], t["description"]) for t in tools] == [("zoom", ZOOM), ("date", DATE)]
        assert ask("tools/call", {"name": "zoom", "arguments": {"id": 0, "n": 1}})["content"][0]["text"] == "user: the port is 8443"
        chat.append("unii", "written while the server runs")       # the turn's process is the writer
        assert ask("tools/call", {"name": "zoom", "arguments": {"id": 1, "n": 1}})["content"][0]["text"] == "unii: written while the server runs"
        assert ask("tools/call", {"name": "date", "arguments": {"id": 1}})["content"][0]["text"] == chat.date(1)
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)


def test_cli_init_append_view_status(tmp_path, capsys):
    from z0int.optchat.__main__ import main
    root = str(tmp_path / "c")
    assert main(["init", "--root", root, "--name", "Zed", "--high", "16000", "--low", "8000"]) == 0
    with pytest.raises(FileExistsError):
        main(["init", "--root", root])
    main(["append", "--root", root, "--kind", "note", "--text", "imported memory"])
    main(["append", "--root", root, "--text", "hello"])
    capsys.readouterr()
    main(["view", "--root", root])
    assert capsys.readouterr().out == "<chat>\n0+1|note: imported memory\n1+1|user: hello\n</chat>\n"
    main(["status", "--root", root])
    status = json.loads(capsys.readouterr().out)
    assert status["messages"] == 2 and status["view_limits"] == [16000, 8000] and status["name"] == "Zed"
    main(["zoom", "--root", root, "0", "2"])
    assert capsys.readouterr().out == "0+1|note: imported memory\n1+1|user: hello\n"


# -- how a trial drives the build (the review's findings) -----------------------

FAKE = str(Path(__file__).parent / "fixtures" / "fake_claude.py")
LONG = "word " * 200                                             # 1,000 bytes: needs a model call


def summarizer():
    return FakeBackend(lambda session, *_: f"line for {session.label} " + "s" * 440)


def live_turn(chat, turns, cheap, i, steps=1, slack=SLACK, **kw):
    """One turn of a real chat: the user's message, then tool calls, their results and a reply."""
    def answer(session, on_start, on_block):
        for _ in range(steps):
            on_block("tool", f"shell {LONG}")
            on_block("echo", f"result {i} {LONG}")
        on_block("unii", f"reply {i} {LONG}")
        return "ok"
    turns.answer = answer
    return run_turn(chat, turns, "smart", f"ask {i} {LONG}", slack=slack,
                    compactor=Compactor(chat, cheap, "cheap", system=chat.system(), slack=slack), **kw)


def test_turn_runs_the_compactor_to_idle_so_merges_never_pile_up(tmp_path):
    chat = new_chat(tmp_path)
    turns, cheap = FakeBackend(None), summarizer()
    out = live_turn(chat, turns, cheap, 0)
    assert out["after"]["stopped"] is None and chat.unbuilt_leaves() == 0
    assert chat.ready.idle() and not chat.ready.merges         # leaves and merges: nothing is left ready
    assert chat.built((1, 0)) and chat.built((1, 1)) and chat.built((2, 0))
    assert turns.sessions[0].max_requests > 1                  # a turn may use its tools ...
    assert {s.max_requests for s in cheap.sessions} == {1}     # ... a compaction gets one request
    out = live_turn(chat, turns, cheap, 1, after=False)        # the deviation: leaves only, at the next turn
    assert out["after"] is None and chat.unbuilt_leaves() == 4 and chat.total == 8


def test_live_order_keeps_the_real_limits_and_only_idle_compaction_does(tmp_path):
    """The gist's sizes, 128,000/64,000 and 32,000/16,000, with 4 messages a turn."""
    chat = new_chat(tmp_path)
    assert chat.gist_limits() and chat.status()["view_limits"] == [128_000, 64_000]
    turns, cheap = FakeBackend(None), summarizer()
    views, batches = [], 0
    for i in range(160):                                       # 640 messages: past the first batch
        before = chat.merges
        views.append(live_turn(chat, turns, cheap, i)["view_bytes"])
        batches += chat.merges > before
    assert max(views) <= 128_000 + SLACK and batches >= 1
    assert max(views) > 120_000 and min(views[-20:]) > 60_000   # the sawtooth: up to 128 KB, back to 64 KB
    contexts = [len(s.view.encode()) for s in cheap.sessions]
    assert max(contexts) <= 32_000 + SLACK and not any(PLACEHOLDER in s.view for s in cheap.sessions)

    # 26 long messages a turn, as the reviewer ran it. A view counts an unbuilt line at
    # its placeholder's size, so such turns need more slack (about 480 bytes a message).
    wide = 16_000
    busy = new_chat(tmp_path / "busy")
    turns, cheap = FakeBackend(None), summarizer()
    views = [live_turn(busy, turns, cheap, i, steps=12, slack=wide)["view_bytes"] for i in range(30)]
    assert 120_000 < max(views) <= 128_000 + wide and busy.merges
    assert max(len(s.view.encode()) for s in cheap.sessions) <= 32_000 + wide

    lazy = new_chat(tmp_path / "lazy")                         # the same chat, leaves only before each call
    turns, cheap = FakeBackend(None), summarizer()
    with pytest.raises((ViewOver, NotReady), match="live order"):
        for i in range(30):
            live_turn(lazy, turns, cheap, i, steps=12, slack=wide, after=False)
    total = lazy.total
    assert lazy.cview.pending and len(lazy.ready.merges) > 8   # a blocked batch, merges piled up
    assert lazy.log.messages[-1].kind == "unii"                # refused before the next message was logged
    with pytest.raises((ViewOver, NotReady), match="live order"):
        run_turn(lazy, turns, "smart", "one more", compactor=Compactor(lazy, cheap, "cheap", system="S"))
    assert lazy.total == total


def test_a_bulk_load_is_refused_loudly_by_compact_and_by_turn(tmp_path, capsys):
    chat = new_chat(tmp_path)
    for i in range(400):                                       # a whole conversation appended at once
        chat.append("user", f"m{i} {LONG}")
    cheap = summarizer()
    stats = Compactor(chat, cheap, "cheap", system="SYS", stop_on=(ViewOver,)).run()
    assert "compaction view" in stats["stopped"] and "live order" in stats["stopped"]
    assert stats["context_max"] <= 32_000 + SLACK              # no oversized call was sent
    assert max(len(s.view.encode()) for s in cheap.sessions) <= 32_000 + SLACK
    assert chat.unbuilt_leaves() > 300
    for i in range(400):                                       # even with every line built, the view is 200 KB
        if not chat.built((0, i)):
            chat.commit((0, i), "x" * 500)
    assert chat.over() > 60_000 and chat.status()["view_over"] == chat.over()
    with pytest.raises(ViewOver):
        run_turn(chat, FakeBackend(lambda *a: "ok"), "smart", "go on")
    assert chat.total == 400
    chat.close()

    from z0int.optchat.__main__ import main                    # the commands end with a non-zero code
    common = ["--root", str(tmp_path / "chat"), "--ledger", str(tmp_path / "ledger.jsonl"),
              "--claude-binary", FAKE, "--model", "haiku"]
    assert main(["compact", *common]) in (2, 3)
    assert "live order" in capsys.readouterr().err
    assert main(["turn", *common, "--compact-model", "haiku", "--text", "go on"]) == 2
    assert "ViewOver" in capsys.readouterr().err
    with Chat(tmp_path / "chat") as reader:
        assert reader.total == 400


def cli(tmp_path, **kw) -> ClaudeCLI:
    return ClaudeCLI(tmp_path / "work", ledger=tmp_path / "ledger.jsonl", calls=tmp_path / "calls.jsonl",
                     binary=FAKE, **kw)


def ledger_sums(tmp_path) -> list[int]:
    out, total = [], 0
    for raw in (tmp_path / "ledger.jsonl").read_text().splitlines():
        total += json.loads(raw)["n"]
        out.append(total)
    return out


def test_call_caps_are_reserved_before_a_send_and_cannot_be_passed(tmp_path, monkeypatch):
    with pytest.raises((TypeError, ValueError)):
        ClaudeCLI(tmp_path / "w0", ledger=None)                # no ledger, no backend
    monkeypatch.setenv("FAKE_SLEEP", "0.3")
    backend = cli(tmp_path, caps={"haiku": 5})
    results = []

    def one(i):
        try:
            with backend.open("haiku", "SYS", label=f"compact {i}+1") as session:
                results.append(session.send("task").text[:4])
        except LimitError:
            results.append("cap")

    threads = [threading.Thread(target=one, args=(i,)) for i in range(8)]   # 8 at once on one cap
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["cap"] * 3 + ["line"] * 5
    assert backend.spent("haiku") == 5 and max(ledger_sums(tmp_path)) == 5
    assert len((tmp_path / "calls.jsonl").read_text().splitlines()) == 5   # the refused ones never ran

    other = cli(tmp_path, caps={"haiku": 5, "opus": 10})       # a second backend on the same ledger
    with pytest.raises(LimitError):
        other.complete("haiku", "SYS", "task")
    monkeypatch.setenv("FAKE_SLEEP", "0")
    monkeypatch.setenv("FAKE_REQUESTS", "3")                   # a turn that uses tools: 3 requests
    reply = other.complete("claude-opus-x", "SYS", "ask", label="turn 0", max_requests=4)
    assert reply.requests == 3 and reply.row["reserved"] == 4 and other.spent("opus") == 3
    reply = other.complete("opus", "SYS", "ask", label="turn 1", max_requests=4)
    assert other.spent("opus") == 6
    monkeypatch.setenv("FAKE_REQUESTS", "9")                   # wants 9: 4 are left, so it is held to 4
    reply = other.complete("opus", "SYS", "ask", label="turn 2", max_requests=16)
    assert reply.row["reserved"] == 4 and reply.row["argv"][-2:] == ["--max-turns", "4"]
    assert reply.requests == 4 and reply.row["max_turns_hit"] and other.spent("opus") == 10
    assert reply.text == ""                                    # cut before it answered: reported, not raised
    with pytest.raises(LimitError):
        other.complete("opus", "SYS", "ask", label="turn 3", max_requests=16)
    assert other.spent("opus") == 10 and max(ledger_sums(tmp_path)) <= 15   # 5 haiku + 10 opus, never more


def test_calls_are_isolated_from_the_parent_session_and_from_tmp(tmp_path, monkeypatch):
    log = tmp_path / "fake.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(log))
    for name in ("CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_EFFORT", "CLAUDECODE",
                 "Z0INT_CLAUDE_CODE_PACKET"):
        monkeypatch.setenv(name, "parent")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/login/dir")
    backend = cli(tmp_path)
    backend.complete("haiku", "SYS", "task", label="compact 0+1")
    seen = json.loads(log.read_text().splitlines()[0])
    env, argv = seen["env"], seen["argv"]
    assert not [k for k in env if k.startswith(("CLAUDE", "Z0INT_")) and env[k] == "parent"]
    assert env["CLAUDE_CONFIG_DIR"] == "/login/dir"            # the login is kept as inherited
    work = str((tmp_path / "work").resolve())
    assert env["TMPDIR"] == env["CLAUDE_CODE_TMPDIR"] == work + "/tmp" and seen["cwd"] == work + "/cwd"
    assert argv[argv.index("--permission-mode") + 1] == "dontAsk"   # a tool off the allow list is denied
    assert argv[argv.index("--setting-sources") + 1] == "" and "--strict-mcp-config" in argv
    assert "--no-session-persistence" in argv and "--resume" not in argv
    assert argv[argv.index("--max-turns") + 1] == "1"
    with pytest.raises(BackendError, match="free space"):      # a full temp directory is refused up front
        cli(tmp_path, min_free=1 << 60)


def test_compactions_go_out_at_xhigh_and_rows_name_effort_and_limits(tmp_path, capsys, monkeypatch):
    from z0int.optchat.__main__ import main, summarize
    log = tmp_path / "fake.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(log))

    def args(name, *more):
        return ["--root", str(tmp_path / name), "--ledger", str(tmp_path / "ledger.jsonl"),
                "--results", str(tmp_path / "results"), "--claude-binary", FAKE, *more]

    def efforts():
        out = [(a["argv"][a["argv"].index("--effort") + 1] if "--effort" in a["argv"] else None)
               for a in map(json.loads, log.read_text().splitlines())]
        log.write_text("")
        return out

    main(["init", "--root", str(tmp_path / "real")])
    main(["append", "--root", str(tmp_path / "real"), "--text", LONG])
    assert main(["compact", *args("real"), "--model", "haiku"]) == 0
    assert efforts() == ["xhigh"]                              # the gist: Haiku at xhigh, without asking
    assert main(["turn", *args("real"), "--model", "sonnet", "--compact-model", "haiku", "--text", LONG]) == 0
    assert sorted(efforts(), key=str) == [None, "xhigh"]       # the turn's call; then its message, compacted
    main(["append", "--root", str(tmp_path / "real"), "--text", LONG])
    assert main(["compact", *args("real"), "--model", "haiku", "--effort", "default"]) == 0
    assert set(efforts()) == {None}

    main(["init", "--root", str(tmp_path / "small"), "--high", "16000", "--low", "8000",
          "--chigh", "4000", "--clow", "2000"])
    main(["append", "--root", str(tmp_path / "small"), "--text", LONG])
    assert main(["compact", *args("small"), "--model", "haiku"]) == 0
    capsys.readouterr()
    rows = [json.loads(raw) for raw in (tmp_path / "results" / "calls.jsonl").read_text().splitlines()]
    assert [row["effort"] for row in rows] == ["xhigh", None, "xhigh", None, None, "xhigh"]
    assert [row["gist_limits"] for row in rows] == [True] * 5 + [False]
    assert rows[0]["limits"] == [128_000, 64_000, 32_000, 16_000] and rows[5]["limits"] == [16000, 8000, 4000, 2000]
    groups = summarize(tmp_path / "results" / "calls.jsonl")
    assert sorted(groups) == [
        "fake-haiku compact effort=default [deviation: not xhigh]",
        "fake-haiku compact effort=xhigh",
        "fake-haiku compact effort=xhigh [deviation: limits 16000/8000/4000/2000]",
        "fake-haiku turn effort=default"]
    assert groups["fake-haiku compact effort=xhigh"]["sends"] == 2
    with pytest.raises(SystemExit):                            # no ledger, no model command
        main(["compact", "--root", str(tmp_path / "real"), "--model", "haiku"])


def test_a_compaction_that_calls_a_tool_fails_and_is_counted(tmp_path, monkeypatch):
    from z0int.optchat.__main__ import summarize
    twice = type("S", (), {"label": "compact 0+1",
                           "send": lambda self, text, on_start=None: Reply("line", {}, requests=2)})()
    with pytest.raises(ToolCalled):                            # two requests in one compaction: a tool was used
        build_line(twice, "TASK")
    chat = new_chat(tmp_path)
    chat.append("user", LONG)
    monkeypatch.setenv("FAKE_REQUESTS", "2")                   # the model calls zoom before it answers
    backend = cli(tmp_path)
    compactor = Compactor(chat, backend, "haiku", system=chat.system(),
                          tools={"mcp": {}, "allowed": []})
    stats = compactor.run()
    assert stats["tool_calls"] == 1 and stats["built"] == [] and chat.unbuilt_leaves() == 1
    assert "ToolCalled" in stats["failed"][0]["error"]
    assert backend.spent("haiku") == 1                         # held to its one request by --max-turns 1
    (row,) = [json.loads(raw) for raw in (tmp_path / "calls.jsonl").read_text().splitlines()]
    assert row["max_turns_hit"] and row["requests"] == 1 and "--allowedTools" not in row["argv"]
    assert summarize(tmp_path / "calls.jsonl")["fake-haiku compact effort=default [deviation: not xhigh]"]["tool_violations"] == 1
    monkeypatch.setenv("FAKE_REQUESTS", "1")
    chat.append("user", "next")                                # tried again at the next message
    assert Compactor(chat, backend, "haiku", system=chat.system()).run()["tool_calls"] == 0
    assert chat.unbuilt_leaves() == 0


def test_feed_logs_a_conversation_in_live_order(tmp_path, capsys):
    from z0int.optchat.__main__ import main
    root, script = tmp_path / "c", tmp_path / "turns.jsonl"
    turn = [{"kind": "user", "text": LONG}, {"kind": "tool", "text": "shell ls"},
            {"kind": "echo", "text": LONG}, {"kind": "unii", "text": LONG}]
    script.write_text("".join(json.dumps(turn) + "\n" for _ in range(3)))
    main(["init", "--root", str(root)])
    common = ["--root", str(root), "--ledger", str(tmp_path / "ledger.jsonl"), "--claude-binary", FAKE,
              "--model", "haiku", "--file", str(script)]
    capsys.readouterr()
    assert main(["feed", *common, "--turns", "2"]) == 0
    assert main(["feed", *common, "--skip", "2"]) == 0
    lines = [json.loads(raw) for raw in capsys.readouterr().out.splitlines()]
    assert [line["messages"] for line in lines] == [[0, 3], [4, 7], [8, 11]]
    assert all(line["stopped"] is None and line["tool_calls"] == 0 for line in lines)
    with Chat(root) as chat:
        assert chat.total == 12 and chat.unbuilt_leaves() == 0 and chat.built((3, 0)) and chat.built((2, 2))
    rows = [json.loads(raw) for raw in (tmp_path / "c.work" / "calls.jsonl").read_text().splitlines()]
    order = [row["label"] for row in rows]
    assert max(order.index(f"compact {i}+1") for i in (0, 2, 3)) < order.index("compact 4+1")   # turn by turn


# -- smaller points from the review ---------------------------------------------

def test_a_view_file_that_lost_more_than_one_line_is_refused_not_refilled(tmp_path):
    chat = new_chat(tmp_path)
    for i in range(5):
        chat.append("user", f"m{i}")
    chat.close()
    root = tmp_path / "chat"
    saved = (root / "view.json").read_text()
    (root / "view.json").write_text("[]")
    with pytest.raises(NoView):
        Chat(root, write=True)
    (root / "view.json").write_text(json.dumps(json.loads(saved)[:-2]))
    with pytest.raises(NoView):
        Chat(root, write=True)
    (root / "view.json").write_text(json.dumps(json.loads(saved)[:-1]))   # one line short: an interrupted append
    with Chat(root, write=True) as again:
        assert again.view.covered() == 5


def test_a_crash_between_the_two_view_saves_keeps_the_pending_batch(tmp_path, monkeypatch):
    from z0int.optchat import chat as chat_module
    chat = new_chat(tmp_path, high=300, low=150, chigh=200, clow=100)
    for i in range(16):
        chat.append("user", f"m{i} {LONG}")                    # unbuilt: the batch stays blocked
    assert chat.view.pending
    real = chat_module.save_json

    def crash(path, value):
        if path.name == "view.json":
            raise OSError("power cut")
        real(path, value)

    monkeypatch.setattr(chat_module, "save_json", crash)
    with pytest.raises(OSError):
        chat.append("user", f"m16 {LONG}")
    monkeypatch.setattr(chat_module, "save_json", real)
    chat.close()
    with Chat(tmp_path / "chat", write=True) as again:
        assert again.view.pending and again.cview.pending and again.view.covered() == 17


def test_zoom_past_the_end_and_a_readers_status(tmp_path):
    chat = new_chat(tmp_path)
    for i in range(5):
        chat.append("user", f"fact {i}")
    chat.append("user", LONG)
    assert chat.zoom(4, 4) == f"4+2|{PLACEHOLDER}\n6+2|(no messages yet)"
    assert chat.zoom(8, 8).startswith("No line 8+8")
    assert chat.status()["failed"] == 0 and chat.status()["ready_merges"] == 0
    with Chat(tmp_path / "chat") as reader:
        status = reader.status()
        assert status["ready_merges"] is None and status["failed"] is None and status["unbuilt_leaves"] == 1
