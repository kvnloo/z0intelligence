"""Creator-spec behavior with isolated logs, controlled model calls and native RPC."""
from __future__ import annotations

import io
import json
import os
import queue
import random
import subprocess
import sys
import threading
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from z0int.optchat.__main__ import Resident
from z0int.optchat.compact import (
    CompactorMessages, Scheduler, SonnetBridge, commit_line, compactor_messages,
    cut_utf8, due, enforce, ollama_complete, pump,
)
from z0int.optchat.log import NODE, PLACEHOLDER, ChatLog, Part, cap_echo, utf8_len
from z0int.optchat.turn import Queue, begin, cache_pieces, record, wait_ready


def step(messages: CompactorMessages) -> str:
    content = messages[1]["content"]
    assert isinstance(content, list)
    return str(content[1]["text"])


def wait_built(chat: ChatLog, level: int, index: int) -> bool:
    with chat.condition:
        return chat.condition.wait_for(lambda: chat.built(level, index), timeout=3)


class LogTests(unittest.TestCase):
    def test_short_source_is_verbatim_but_view_and_zoom_children_are_one_line(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            text = "x  y\n\t spaced"
            chat.append("user", text)
            chat.append("talk", "beta\n  continuation")
            calls: list[CompactorMessages] = []
            pump(chat, lambda messages: calls.append(messages) or "wrong")
            self.assertEqual(calls, [])
            self.assertEqual(chat.nodes[(0, 0)], "user: " + text)
            self.assertEqual(chat.nodes[(1, 0)], "user: " + text + "\ntalk: beta\n  continuation")
            chat.view = [Part(1, 0)]
            self.assertEqual(chat.render(), "<chat>\n0+2|user: x  y \t spaced talk: beta   continuation\n</chat>")
            self.assertEqual(chat.zoom(0, 2), "0+1|user: x  y \t spaced\n1+1|talk: beta   continuation")
            self.assertEqual(chat.zoom(0, 1), "0+0|user: " + text)
            loaded = ChatLog(chat.root)
            loaded.load()
            self.assertEqual(loaded.nodes[(0, 0)], "user: " + text)

    def test_long_message_is_never_cut_into_the_view(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("echo", "x" * (NODE + 50))
            self.assertIn(PLACEHOLDER, chat.render())
            self.assertNotIn("x" * 40, chat.render())
            self.assertEqual(chat.zoom(0, 1), "0+0|echo: " + "x" * (NODE + 50))
            self.assertEqual(chat.zoom(0, 3), "No line 0+3.")
            self.assertEqual(chat.zoom(-1, 1), "No line -1+1.")

    def test_torn_row_preserves_ids_dates_tree_and_next_append(self) -> None:
        from z0int.optchat.browse import render_html
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            chat = ChatLog(root)
            for text in ("one", "two", "three"):
                chat.append("user", text, date="time-" + text)
            path = next((root / "main").glob("*.jsonl"))
            rows = path.read_bytes().splitlines()
            path.write_bytes(rows[0] + b"\n{broken\n" + rows[2])
            loaded = ChatLog(root)
            loaded.load()
            self.assertEqual([msg.i for msg in loaded.messages], [0, 2])
            self.assertEqual(loaded.torn, 1)
            self.assertTrue(path.read_bytes().endswith(b"\n"))
            self.assertEqual(loaded.zoom(2, 1), "2+0|user: three")
            self.assertEqual(loaded.zoom(1, 1), "No line 1+1.")
            self.assertEqual(loaded.date_of(2), "time-three")
            self.assertEqual(loaded.date_of(1), "No message 1.")
            self.assertEqual(loaded.nodes[(0, 2)], "user: three")
            self.assertIn("2+1 user time-three", render_html(loaded))
            self.assertEqual(loaded.append("user", "four").i, 3)
            reloaded = ChatLog(root)
            reloaded.load()
            self.assertEqual([msg.i for msg in reloaded.messages], [0, 2, 3])
            self.assertEqual(reloaded.zoom(3, 1), "3+0|user: four")
            self.assertIn((1, 1), due(reloaded))
            self.assertEqual(compactor_messages(reloaded, 1, 1)[1]["content"][0]["text"],
                             "<chat>\nuser: one\nuser: three\nuser: four\n</chat>")

    def test_missing_last_source_cannot_reuse_a_persisted_tree_id(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("user", "one")
            chat.append("user", "two")
            path = next((chat.root / "main").glob("*.jsonl"))
            path.write_bytes(path.read_bytes().splitlines()[0] + b"\n{torn")
            loaded = ChatLog(chat.root)
            loaded.load()
            self.assertEqual(loaded.append("user", "new").i, 2)
            self.assertEqual(loaded.zoom(1, 1), "No line 1+1.")

    def test_fsync_precedes_message_and_node_visibility(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            observations: list[tuple[int, bool]] = []
            real_fsync = os.fsync

            def observe(fd: int) -> None:
                observations.append((len(chat.messages), chat.built(0, 0)))
                real_fsync(fd)

            with patch("z0int.optchat.log.os.fsync", side_effect=observe):
                chat.append("user", "kept")
            self.assertEqual(observations, [(0, False), (1, False)])
            self.assertTrue(chat.built(0, 0))
            loaded = ChatLog(chat.root)
            loaded.load()
            self.assertEqual(loaded.zoom(0, 1), "0+0|user: kept")
            self.assertEqual(loaded.nodes[(0, 0)], "user: kept")

    def test_failed_node_fsync_does_not_expose_a_built_node(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("user", "long " + "x" * 700)
            with patch("z0int.optchat.log.os.fsync", side_effect=OSError("disk failed")):
                with self.assertRaisesRegex(OSError, "disk failed"):
                    commit_line(chat, 0, 0, "user: compacted")
            self.assertFalse(chat.built(0, 0))
            self.assertFalse(begin(chat, ["next"])["ready"])

    def test_load_folds_using_each_prefix_age_not_the_final_age(self) -> None:
        def fit(parts: list[Part], nodes: dict[tuple[int, int], str], total: int, budget: int) -> None:
            while sum(utf8_len(nodes[(part.level, part.index)]) for part in parts) > budget:
                eligible = [
                    ((total - left.start) / (1 << (left.level + 2)), at)
                    for at, (left, right) in enumerate(zip(parts, parts[1:]))
                    if left.level == right.level and left.index % 2 == 0
                    and right.index == left.index + 1 and (left.level + 1, left.index // 2) in nodes
                ]
                if not eligible:
                    return
                _, at = max(eligible, key=lambda pair: pair[0])
                left = parts[at]
                parts[at:at + 2] = [Part(left.level + 1, left.index // 2)]

        with TemporaryDirectory() as tmp, patch("z0int.optchat.log.VIEW", 1800):
            chat = ChatLog(Path(tmp))
            rng = random.Random(731)
            for ident in range(80):
                chat.append("user", f"u{ident} " + "x" * rng.randint(200, 490))
            for level in range(1, 7):
                for index in range(80 // (1 << level)):
                    chat._store_node(level, index, f"parent{level},{index} " + "p" * rng.randint(30, 490))
            expected: list[Part] = []
            for total in range(1, 81):
                expected.append(Part(0, total - 1))
                fit(expected, chat.nodes, total, 1800)
            loaded = ChatLog(chat.root)
            loaded.load()
            self.assertEqual(loaded.view, expected)

    def test_browse_preserves_ranges_dates_sizes_and_source_text(self) -> None:
        from z0int.optchat.browse import render_html
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("user", "visible <content>", date="time-one")
            page = render_html(chat)
            self.assertIn("<h1>view</h1>", page)
            self.assertIn("<h1>log</h1>", page)
            self.assertIn("level 0", page)
            self.assertIn("0+1 time-one", page)
            self.assertIn("bytes", page)
            self.assertIn("visible &lt;content&gt;", page)


class CompactorTests(unittest.TestCase):
    def test_complete_source_and_summarized_context_are_never_truncated(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            sources = [f"CONTEXT_FACT_{ident} " + "x" * 420 for ident in range(8)]
            for text in sources:
                chat.append("user", text)
            source = "HEAD\n" + "x" * 3000 + "\nMIDDLE_DECISION: blue not red\n" + "y" * 3000 + "\nTAIL"
            chat.append("user", source)
            messages = compactor_messages(chat, 0, 8)
            content = messages[1]["content"]
            self.assertEqual(content[0]["text"], "<chat>\n" + "\n".join("user: " + text for text in sources) + "\n</chat>")
            self.assertTrue(content[1]["text"].endswith("user: " + source))
            self.assertNotIn("+1|", content[0]["text"])
            self.assertNotIn("... cut ...", content[1]["text"])

    def test_placeholder_literal_is_built_context_not_a_missing_node(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("user", PLACEHOLDER)
            self.assertTrue(wait_ready(chat, 0)["ready"])
            opened = begin(chat, ["next"])
            self.assertTrue(opened["ready"])
            chat.append("user", "x" * 700)
            self.assertIn("user: " + PLACEHOLDER, compactor_messages(chat, 0, 2)[1]["content"][0]["text"])

    def test_http400_preserves_full_input_and_surfaces_provider_error(self) -> None:
        messages: CompactorMessages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": [{"type": "text", "text": "full\n" + "z" * 7000}]},
        ]
        error = urllib.error.HTTPError("http://local", 400, "bad context", {}, io.BytesIO(b"context size too small"))
        with patch("z0int.optchat.compact.HOST", "http://local"), patch(
            "z0int.optchat.compact.urllib.request.urlopen", side_effect=error,
        ) as send:
            with self.assertRaisesRegex(RuntimeError, "HTTP 400: context size too small"):
                ollama_complete(messages)
        self.assertEqual(send.call_count, 1)
        self.assertEqual(json.loads(send.call_args.args[0].data)["messages"], messages)

    def test_size_attempts_keep_same_conversation_and_shortest_of_five(self) -> None:
        replies = ["a" * 700, "b" * 600, "c" * 650, "d" * 620, "e" * 630]
        calls: list[CompactorMessages] = []

        def complete(messages: CompactorMessages) -> str:
            calls.append(list(messages))
            return replies[len(calls) - 1]

        initial: CompactorMessages = [{"role": "system", "content": "system"}, {"role": "user", "content": "source"}]
        self.assertEqual(enforce(initial, complete), replies[1])
        self.assertEqual([len(messages) for messages in calls], [2, 4, 6, 8, 10])
        for attempt in range(1, 5):
            self.assertEqual(calls[attempt][:2], initial)
            self.assertEqual(calls[attempt][-2]["content"], replies[attempt - 1])
            self.assertIn("| ← LIMIT", calls[attempt][-1]["content"])
        self.assertEqual(len(initial), 2)

    def test_empty_response_fails_even_after_an_oversized_response(self) -> None:
        for replies in (["   \n"], ["x" * 700, "\t\n  "]):
            with self.subTest(replies=len(replies)):
                answers = iter(replies)
                with self.assertRaisesRegex(RuntimeError, "returned empty"):
                    enforce([], lambda _: next(answers))

    def test_response_only_trims_edges_and_utf8_feedback_never_splits(self) -> None:
        expected = "user: keep    two spaces; echo: line\ncontinuation"
        self.assertEqual(enforce([], lambda _: " \n" + expected + "\t "), expected)
        self.assertEqual(cut_utf8("é", 1), "")
        self.assertNotIn("\ufffd", cut_utf8("é" * 10, 3))

    def test_long_leaf_frontier_stops_later_leaves_and_unready_merges(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("user", "A" * 700)
            chat.append("user", "B" * 700)
            self.assertEqual(due(chat), [(0, 0)])
            pump(chat, lambda _: "user: summarized A", limit=1)
            self.assertEqual(due(chat), [(0, 1)])


class TurnTests(unittest.TestCase):
    def test_fresh_begin_renders_before_logging_and_drops_reasoning(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            record(chat, "thought", "hidden")
            record(chat, "thinking", "hidden too")
            self.assertEqual(chat.messages, [])
            chat.append("user", "prior")
            opened = begin(chat, ["new", "second"], "AGENTS: instructions")
            self.assertTrue(opened["ready"])
            self.assertEqual(opened["blocks"], ["<chat>\n0+1|user: prior\n</chat>", "new\n\nsecond"])
            self.assertEqual([msg.text for msg in chat.messages], ["prior", "new", "second"])
            self.assertTrue(opened["system"].endswith("AGENTS: instructions"))

    def test_unready_begin_does_not_log_and_empty_view_needs_no_compactor(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            self.assertTrue(wait_ready(chat)["ready"])
            chat.append("user", "long " + "x" * 700)
            self.assertFalse(begin(chat, ["must not log"])["ready"])
            self.assertEqual(len(chat.messages), 1)
            self.assertFalse(wait_ready(chat, 0)["ready"])

    def test_record_returns_exact_canonical_capped_echo_for_the_model(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            source = "H" * 20000 + "M" * 20000 + "T" * 20000
            saved = record(chat, "echo", source)
            self.assertTrue(saved["logged"])
            self.assertEqual(saved["text"], chat.messages[0].text)
            self.assertLessEqual(len(saved["text"]), 30000)
            self.assertTrue(saved["text"].startswith("H" * 100))
            self.assertTrue(saved["text"].endswith("T" * 100))
            for source in ("x" * 30000, "😀" * 30001):
                with self.subTest(characters=len(source)):
                    capped = cap_echo(source)
                    self.assertLessEqual(len(capped), 30000)
                    if len(source) <= 30000:
                        self.assertEqual(capped, source)
                    else:
                        self.assertTrue(capped.startswith("😀") and capped.endswith("😀"))

    def test_cache_pieces_preserve_bytes_at_stable_line_end_marks(self) -> None:
        from z0int.optchat.turn import MARKS
        view = "<chat>\n" + ("0+1|user: " + "word " * 20 + "\n") * 4000 + "</chat>"
        pieces = cache_pieces(view)
        self.assertEqual("".join(pieces), view)
        cursor = 0
        for piece, mark in zip(pieces, MARKS):
            cursor += len(piece)
            self.assertLessEqual(cursor, mark)
            self.assertTrue(piece.endswith("\n"))

    def test_queue_boundaries_preserve_each_submitted_occurrence(self) -> None:
        pending = Queue()
        self.assertEqual(pending.submit("first"), "turn")
        self.assertEqual(pending.take(), ["first"])
        self.assertEqual(pending.submit("during"), "inject")
        self.assertEqual(pending.boundary(), ["during"])
        pending.submit("same")
        pending.submit("same")
        self.assertEqual(pending.finish(), ["same", "same"])
        self.assertEqual(pending.cancel(), ["same", "same"])
        self.assertEqual(pending.cancel(), [])


class RetainedBehaviorTests(unittest.TestCase):
    def test_switch_defaults_off_without_mutating_the_callers_home(self) -> None:
        from z0int.optchat.switch import enabled, set_enabled
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"Z0INT_HOME": tmp}):
            self.assertFalse(enabled())
            set_enabled(True)
            self.assertTrue(enabled())
            set_enabled(False)
            self.assertFalse(enabled())

    def test_subagent_report_is_logged_as_the_users_named_work(self) -> None:
        from z0int.optchat.turn import subagent_text
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            msg = chat.append("user", subagent_text("7", "done"))
            self.assertEqual(msg.text, "[7] done")
            self.assertEqual(chat.zoom(msg.i, 1), "0+0|user: [7] done")

    def test_persist_commits_the_isolated_chat_directory(self) -> None:
        from z0int.optchat.persist import persist
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            chat = ChatLog(root)
            chat.append("note", "kept")
            self.assertTrue(persist(root))
            self.assertTrue((root / ".git").is_dir())


class SchedulerTests(unittest.TestCase):
    def test_idle_append_and_enable_wake_immediately_and_short_joins_are_free(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            started = threading.Event()
            calls: list[str] = []

            def complete(messages: CompactorMessages) -> str:
                calls.append(step(messages))
                started.set()
                return "user: compacted"

            scheduler = Scheduler(chat, complete, on=False)
            scheduler.start()
            try:
                chat.append("user", "alpha")
                chat.append("talk", "beta")
                chat.append("user", "long " + "x" * 700)
                scheduler.set_enabled(True)
                self.assertTrue(started.wait(1))
                self.assertTrue(wait_built(chat, 1, 0))
                self.assertEqual(len(calls), 1)
                self.assertIn("Compress this message", calls[0])
                self.assertTrue(wait_ready(chat, 1)["ready"])
                started.clear()
                chat.append("user", "another " + "y" * 700)
                self.assertTrue(started.wait(1))
                self.assertTrue(wait_ready(chat, 1)["ready"])
            finally:
                scheduler.stop()
            self.assertFalse(scheduler.thread.is_alive())

    def test_eight_parallel_merges_overlap_without_duplicate_or_ninth_dispatch(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            for ident in range(20):
                chat.append("user", f"M{ident} " + "x" * 300)
            gate = threading.Event()
            changed = threading.Condition()
            starts: list[str] = []
            active = 0
            maximum = 0
            def complete(messages: CompactorMessages) -> str:
                nonlocal active, maximum
                source = step(messages)
                with changed:
                    starts.append(source)
                    active += 1
                    maximum = max(maximum, active)
                    changed.notify_all()
                gate.wait(5)
                with changed:
                    active -= 1
                return "user: merged"

            scheduler = Scheduler(chat, complete)
            scheduler.start()
            try:
                with changed:
                    self.assertTrue(changed.wait_for(lambda: len(starts) >= 8, timeout=2))
                    self.assertEqual(len(starts), 8)
                    self.assertEqual(maximum, 8)
                # Main turn work can acquire the chat lock during all model I/O.
                self.assertTrue(begin(chat, ["while models work"])["ready"])
                gate.set()
                with chat.condition:
                    self.assertTrue(chat.condition.wait_for(
                        lambda: all(chat.built(1, index) for index in range(10)),
                        timeout=3,
                    ))
                self.assertEqual(len(starts), 10)
                self.assertEqual(len(set(starts)), 10)
                self.assertLessEqual(maximum, 8)
            finally:
                gate.set()
                scheduler.stop()

    def test_failed_leaf_retry_does_not_block_unrelated_merge_or_later_retry(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("user", "A " + "x" * 300)
            chat.append("user", "B " + "x" * 300)
            chat.append("user", "long " + "y" * 700)
            leaf_failed = threading.Event()
            merge_started = threading.Event()
            attempts = 0

            def complete(messages: CompactorMessages) -> str:
                nonlocal attempts
                if "Compress this message" in step(messages):
                    attempts += 1
                    if attempts <= 2:
                        leaf_failed.set()
                        raise RuntimeError("provider failed visibly")
                    return "user: compacted"
                merge_started.set()
                return "user: merged"

            # Only retry duration is accelerated; no idle loop or model scheduling is mocked.
            with patch("z0int.optchat.compact.RETRY_S", 0.15):
                scheduler = Scheduler(chat, complete)
                scheduler.start()
                try:
                    self.assertTrue(leaf_failed.wait(1))
                    self.assertTrue(merge_started.wait(1))
                    self.assertTrue(wait_ready(chat, 2)["ready"])
                    self.assertEqual(attempts, 3)
                    self.assertEqual(len(scheduler.reports), 1)
                    self.assertIn("provider failed visibly", scheduler.reports[0])
                    self.assertEqual(scheduler.errors, {})
                finally:
                    scheduler.stop()

    def test_only_first_long_leaf_runs_until_its_context_is_built(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("user", "A" * 700)
            chat.append("user", "B" * 700)
            first = threading.Event()
            second = threading.Event()
            release = threading.Event()

            def complete(messages: CompactorMessages) -> str:
                source = step(messages)
                if source.endswith("A" * 700):
                    first.set()
                    release.wait(5)
                    return "user: summarized A"
                self.assertTrue(chat.built(0, 0))
                self.assertIn("user: summarized A", messages[1]["content"][0]["text"])
                second.set()
                return "user: summarized B"

            scheduler = Scheduler(chat, complete)
            scheduler.start()
            try:
                self.assertTrue(first.wait(1))
                self.assertFalse(second.is_set())
                release.set()
                self.assertTrue(second.wait(1))
                self.assertTrue(wait_ready(chat, 1)["ready"])
            finally:
                release.set()
                scheduler.stop()


class ResidentTests(unittest.TestCase):
    def test_first_failure_is_visible_without_resolving_an_indefinite_wait(self) -> None:
        with TemporaryDirectory() as tmp, patch("z0int.optchat.compact.RETRY_S", 0.05):
            chat = ChatLog(Path(tmp))
            chat.append("user", "long " + "x" * 700)
            emitted: queue.Queue[dict[str, object]] = queue.Queue()
            retried = threading.Event()
            attempts = 0
            error = "No native Anthropic credentials. Run omp login anthropic."

            def complete(_: CompactorMessages) -> str:
                nonlocal attempts
                attempts += 1
                if attempts >= 2:
                    retried.set()
                raise RuntimeError(error)

            scheduler = Scheduler(chat, complete)
            resident = Resident(chat, emitted.put, scheduler)
            resident.dispatch({"id": "wait", "op": "settle", "wait_id": "w"})
            scheduler.start()
            try:
                event = emitted.get(timeout=1)
                self.assertEqual(event["event"], "compactor_error")
                self.assertNotIn("id", event)
                self.assertEqual(event["error"], error)
                self.assertTrue(retried.wait(1))
                self.assertTrue(emitted.empty())
                status = resident.dispatch({"op": "status"})
                self.assertEqual(status["errors"], [event])
                self.assertEqual(status["backend"], event["backend"])
                self.assertEqual(status["model"], event["model"])
                self.assertFalse(status["ready"])
                resident.dispatch({"op": "cancel", "wait_id": "w"})
                self.assertTrue(emitted.get(timeout=1)["cancelled"])
            finally:
                resident.close()

    def test_settle_keeps_status_responsive_and_cancel_logs_pending_once(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("user", "long " + "x" * 700)
            emitted: queue.Queue[dict[str, object]] = queue.Queue()
            release = threading.Event()
            started = threading.Event()

            def complete(_: CompactorMessages) -> str:
                started.set()
                release.wait(5)
                return "user: compacted"

            scheduler = Scheduler(chat, complete)
            resident = Resident(chat, emitted.put, scheduler)
            scheduler.start()
            try:
                self.assertTrue(started.wait(1))
                resident.dispatch({"op": "submit", "text": "pending"})
                self.assertIsNone(resident.dispatch({"id": "wait", "op": "settle", "wait_id": "w"}))
                status = resident.dispatch({"op": "status"})
                self.assertFalse(status["ready"])
                self.assertEqual(status["pending"], 1)
                self.assertEqual(status["placeholders"], 1)
                self.assertTrue(emitted.empty())
                cancelled = resident.dispatch({"op": "cancel", "wait_id": "w"})
                self.assertEqual(cancelled["logged"], 1)
                reply = emitted.get(timeout=1)
                self.assertFalse(reply["ready"])
                self.assertTrue(reply["cancelled"])
                self.assertEqual(resident.dispatch({"op": "abort"})["logged"], 0)
                self.assertEqual(resident.dispatch({"op": "finish"})["texts"], [])
                loaded = ChatLog(chat.root)
                loaded.load()
                self.assertEqual([msg.text for msg in loaded.messages].count("pending"), 1)
            finally:
                release.set()
                resident.close()

    def test_off_cancels_all_waits_but_abort_and_finish_cleanup_remain_available(self) -> None:
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"Z0INT_HOME": tmp}):
            chat = ChatLog(Path(tmp) / "chat")
            chat.append("user", "long " + "x" * 700)
            emitted: queue.Queue[dict[str, object]] = queue.Queue()
            release = threading.Event()
            scheduler = Scheduler(chat, lambda _: release.wait(5) and "user: compacted")
            resident = Resident(chat, emitted.put, scheduler)
            scheduler.start()
            try:
                resident.dispatch({"op": "submit", "text": "caller owned"})
                taken = resident.dispatch({"op": "take"})["texts"]
                resident.dispatch({"op": "submit", "text": "never taken"})
                for ident in ("one", "two"):
                    resident.dispatch({"id": ident, "op": "settle", "wait_id": ident})
                resident.dispatch({"op": "enabled", "on": False})
                replies = [emitted.get(timeout=1), emitted.get(timeout=1)]
                self.assertTrue(all(reply["cancelled"] and not reply["ready"] for reply in replies))
                self.assertEqual(resident.dispatch({"op": "abort", "texts": taken})["logged"], 1)
                self.assertEqual(resident.dispatch({"op": "finish"})["texts"], [])
                self.assertEqual(resident.dispatch({"op": "record", "kind": "talk", "text": "forbidden"})["error"], "optchat_off")
                loaded = ChatLog(chat.root)
                loaded.load()
                texts = [msg.text for msg in loaded.messages]
                self.assertEqual(texts.count("never taken"), 1)
                self.assertEqual(texts.count("caller owned"), 1)
                self.assertNotIn("forbidden", texts)
                status = resident.dispatch({"op": "status"})
                self.assertFalse(status["enabled"])
                self.assertFalse(status["running"])
                self.assertEqual(status["pending"], 0)
            finally:
                release.set()
                resident.close()

    def test_explicit_wait_timeout_is_bounded_and_later_wait_resolves_on_commit(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("user", "long " + "x" * 700)
            emitted: queue.Queue[dict[str, object]] = queue.Queue()
            release = threading.Event()
            scheduler = Scheduler(chat, lambda _: release.wait(5) and "user: compacted")
            resident = Resident(chat, emitted.put, scheduler)
            scheduler.start()
            try:
                resident.dispatch({"id": "probe", "op": "settle", "wait_id": "probe", "timeout": 0})
                self.assertFalse(emitted.get(timeout=1)["ready"])
                resident.dispatch({"id": "real", "op": "settle", "wait_id": "real"})
                self.assertTrue(emitted.empty())
                release.set()
                self.assertTrue(emitted.get(timeout=1)["ready"])
            finally:
                release.set()
                resident.close()

    def test_concurrent_cancel_preserves_pending_occurrences_exactly_once(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("user", "long " + "x" * 700)
            emitted: queue.Queue[dict[str, object]] = queue.Queue()
            release = threading.Event()
            scheduler = Scheduler(chat, lambda _: release.wait(5) and "user: compacted")
            resident = Resident(chat, emitted.put, scheduler)
            scheduler.start()
            try:
                for text in ("same", "same", "other"):
                    resident.dispatch({"op": "submit", "text": text})
                resident.dispatch({"id": "wait", "op": "settle", "wait_id": "w"})
                barrier = threading.Barrier(2)

                def cancel() -> int:
                    barrier.wait(2)
                    reply = resident.dispatch({"op": "cancel", "wait_id": "w"})
                    return int(reply["logged"])

                with ThreadPoolExecutor(max_workers=2) as pool:
                    one = pool.submit(cancel)
                    two = pool.submit(cancel)
                    self.assertEqual(sorted([one.result(timeout=3), two.result(timeout=3)]), [0, 3])
                self.assertTrue(emitted.get(timeout=1)["cancelled"])
                resident.dispatch({"op": "abort"})
                loaded = ChatLog(chat.root)
                loaded.load()
                texts = [msg.text for msg in loaded.messages]
                self.assertEqual(texts.count("same"), 2)
                self.assertEqual(texts.count("other"), 1)
                self.assertEqual(resident.dispatch({"op": "status"})["pending"], 0)
            finally:
                release.set()
                resident.close()

    def test_literal_placeholder_status_counts_only_unbuilt_nodes(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("user", PLACEHOLDER)
            scheduler = Scheduler(chat, lambda _: "unused")
            resident = Resident(chat, lambda _: None, scheduler)
            scheduler.start()
            try:
                status = resident.dispatch({"op": "status"})
                self.assertTrue(status["ready"])
                self.assertEqual(status["placeholders"], 0)
                self.assertEqual(status["messages"], 1)
            finally:
                resident.close()


class NativeBridgeTests(unittest.TestCase):
    def test_multiplexes_jobs_and_preserves_same_job_retry_session(self) -> None:
        # A local protocol fixture, not a model or credential stub: the actual client
        # reads out-of-order replies from a subprocess and must keep jobs distinct.
        fixture = '''import json, sys, threading
lock = threading.Lock()
barrier = threading.Barrier(2)
def reply(row):
    try:
        if len(row["messages"]) == 2: barrier.wait(3)
        result = {"id": row["id"], "ok": True, "text": row["session"]}
    except threading.BrokenBarrierError:
        result = {"id": row["id"], "ok": False, "error": "parallel request never arrived"}
    with lock:
        print(json.dumps(result), flush=True)
workers = []
for line in sys.stdin:
    worker = threading.Thread(target=reply, args=(json.loads(line),))
    workers.append(worker); worker.start()
for worker in workers: worker.join()
'''
        popen = subprocess.Popen

        def launch(_command: list[str], **kwargs) -> subprocess.Popen[str]:
            return popen([sys.executable, "-u", "-c", fixture], **kwargs)

        initial: CompactorMessages = [{"role": "system", "content": "system"}, {"role": "user", "content": "source"}]
        bridge = SonnetBridge()

        def job() -> tuple[str, str]:
            first = bridge.complete(initial)
            retry = bridge.complete(initial + [{"role": "assistant", "content": "oversized"}, {"role": "user", "content": "shorter"}])
            return first, retry

        try:
            with patch("z0int.optchat.compact.subprocess.Popen", side_effect=launch), ThreadPoolExecutor(max_workers=2) as pool:
                one = pool.submit(job)
                two = pool.submit(job)
                results = [one.result(timeout=5), two.result(timeout=5)]
            self.assertEqual(results[0][0], results[0][1])
            self.assertEqual(results[1][0], results[1][1])
            self.assertNotEqual(results[0][0], results[1][0])
        finally:
            bridge.close()


if __name__ == "__main__":
    unittest.main()
