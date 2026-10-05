"""OptChat log, free nodes, and zoom. No model, no second database."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from z0int.optchat.log import NODE, ChatLog, cap_echo


class LogTests(unittest.TestCase):
    def test_short_message_is_its_own_line(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            msg = chat.append("user", "keep the bridge off the hot path")
            self.assertLessEqual(msg.size, NODE)
            self.assertIn((0, 0), chat.nodes)
            self.assertIn("0+1|user: keep the bridge off the hot path", chat.render())

    def test_long_message_is_not_cut_into_the_view(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("echo", "x" * (NODE + 50))
            rendered = chat.render()
            self.assertIn("not summarized yet", rendered)
            self.assertNotIn("x" * 40, rendered)

    def test_echo_cap_keeps_head_and_tail(self) -> None:
        text = "H" * 20000 + "M" * 20000 + "T" * 20000
        capped = cap_echo(text, 100)
        self.assertLessEqual(len(capped), 160)
        self.assertTrue(capped.startswith("H"))
        self.assertTrue(capped.endswith("T"))
        self.assertIn("cut", capped)

    def test_reload_skips_a_torn_line(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "main"
            main.mkdir()
            (main / "2026-10-05.jsonl").write_bytes(
                b'{"kind":"user","text":"kept","size":1,"date":"t"}\n{"kind":\n'
            )
            chat = ChatLog(root)
            chat.load()
            self.assertEqual(len(chat.messages), 1)
            self.assertEqual(chat.torn, 1)
            self.assertTrue((main / "2026-10-05.jsonl").read_bytes().endswith(b"\n"))

    def test_zoom_returns_the_whole_message(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("user", "line one\nline two")
            opened = chat.zoom(0, 1)
            self.assertIn("line one\nline two", opened)
            self.assertEqual(chat.zoom(0, 3), "No line 0+3.")

    def test_pump_replaces_placeholder(self) -> None:
        from z0int.optchat.compact import pump
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("echo", "x" * (NODE + 80))
            self.assertIn("not summarized yet", chat.render())

            def fake(messages):
                return "echo: a long tool result was capped and not copied"

            built = pump(chat, fake, limit=2)
            self.assertEqual(built, 1)
            self.assertNotIn("not summarized yet", chat.render())
            self.assertIn("echo: a long tool result", chat.render())


    def test_fsync_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = ChatLog(root)
            first.append("note", "from continuity, not a replacement")
            second = ChatLog(root)
            second.load()
            self.assertEqual(second.messages[0].kind, "note")
            row = json.loads((root / "main").glob("*.jsonl").__iter__().__next__().read_text().strip())
            self.assertEqual(row["text"], "from continuity, not a replacement")


    def test_compactor_call_has_context_and_no_ids(self) -> None:
        from z0int.optchat.compact import NODE, SCALE, compactor_messages
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("user", "keep this verbatim")
            chat.append("echo", "y" * (NODE + 40))
            messages = compactor_messages(chat, 0, 1)
            user = messages[1]["content"]
            self.assertIsInstance(user, list)
            self.assertEqual(len(user), 2)
            joined = user[0]["text"] + "\n" + user[1]["text"]
            self.assertIn("<chat>", joined)
            self.assertIn(SCALE, joined)
            self.assertNotIn("+1|", joined)
            self.assertEqual(len(SCALE.encode()), 512)

    def test_switch_defaults_off(self) -> None:
        from z0int.optchat.switch import enabled, set_enabled
        with TemporaryDirectory() as tmp:
            import os
            os.environ["Z0INT_HOME"] = tmp
            try:
                self.assertFalse(enabled())
                set_enabled(True)
                self.assertTrue(enabled())
                set_enabled(False)
                self.assertFalse(enabled())
            finally:
                os.environ.pop("Z0INT_HOME", None)


class TurnContractTests(unittest.TestCase):
    def test_begin_renders_view_before_logging_the_new_text(self) -> None:
        from z0int.optchat.turn import begin

        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("user", "keep the bridge off the hot path")
            before = len(chat.messages)
            opened = begin(chat, ["ping the fresh call", "and the second line"], "AGENTS: no history")
            self.assertTrue(opened["ready"])
            self.assertNotIn("ping the fresh call", opened["view"])
            self.assertEqual(opened["blocks"], [opened["view"], "ping the fresh call\n\nand the second line"])
            self.assertEqual(len(chat.messages), before + 2)
            self.assertEqual(chat.messages[-1].text, "and the second line")
            self.assertEqual(chat.messages[-1].kind, "user")
            self.assertIn("You are OptChat, an AI agent that works for one user in a single chat that", opened["system"])
            self.assertIn("zoom(id, n) opens line id+n into the two lines of n/2", opened["system"])
            self.assertTrue(opened["system"].endswith("AGENTS: no history"))
            self.assertNotIn("messages", opened)

    def test_begin_does_not_log_when_the_view_is_unsettled(self) -> None:
        from z0int.optchat.turn import begin

        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("echo", "x" * (NODE + 40))
            self.assertIn("(not summarized yet: zoom it)", chat.render())
            before = len(chat.messages)
            opened = begin(chat, ["do not log me"], "")
            self.assertFalse(opened["ready"])
            self.assertEqual(len(chat.messages), before)
            self.assertNotIn("blocks", opened)

    def test_record_logs_talk_and_drops_thoughts(self) -> None:
        from z0int.optchat.turn import record

        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            skipped = record(chat, "thought", "hidden reasoning")
            self.assertFalse(skipped["logged"])
            self.assertEqual(chat.messages, [])
            saved = record(chat, "talk", "pong")
            self.assertTrue(saved["logged"])
            self.assertEqual(chat.messages[0].kind, "talk")
            self.assertEqual(chat.messages[0].text, "pong")

    def test_cache_pieces_cut_at_line_ends_before_the_marks(self) -> None:
        from z0int.optchat.turn import MARKS, cache_pieces

        line = "0+1|user: " + ("word " * 20)
        view = "<chat>\n" + "\n".join(line for _ in range(4000)) + "\n</chat>"
        self.assertGreater(len(view), MARKS[-1])
        pieces = cache_pieces(view)
        self.assertGreaterEqual(len(pieces), 2)
        joined = "".join(pieces)
        self.assertEqual(joined, view)
        cursor = 0
        for piece, mark in zip(pieces, MARKS):
            cursor += len(piece)
            self.assertLessEqual(cursor, mark)
            self.assertTrue(piece.endswith("\n"))
        self.assertEqual("".join(pieces), view)

    def test_jobs_is_eight(self) -> None:
        from z0int.optchat.compact import JOBS

        self.assertEqual(JOBS, 8)


class GistRemainderTests(unittest.TestCase):
    def test_utf8_cut_drops_a_split_character(self) -> None:
        from z0int.optchat.compact import cut_utf8

        self.assertEqual(cut_utf8("é", 1), "")
        self.assertNotIn("\ufffd", cut_utf8("é" * 10, 3))

    def test_node_failure_is_reported_once(self) -> None:
        from z0int.optchat.compact import note_failure, reported, reports

        reported.clear()
        reports.clear()
        self.assertTrue(note_failure(0, 4, RuntimeError("boom")))
        self.assertFalse(note_failure(0, 4, RuntimeError("boom")))
        self.assertEqual(len(reports), 1)

    def test_running_call_injects_and_idle_call_starts(self) -> None:
        from z0int.optchat.turn import Queue

        queue = Queue()
        self.assertEqual(queue.submit("first"), "turn")
        self.assertEqual(queue.take(), ["first"])
        self.assertEqual(queue.submit("while working"), "inject")
        self.assertEqual(queue.boundary(), ["while working"])
        self.assertEqual(queue.finish(), [])

    def test_unanswered_cancel_stays_in_the_log(self) -> None:
        from z0int.optchat.turn import leave_unanswered

        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            self.assertEqual(leave_unanswered(chat, ["kept"]), 1)
            self.assertEqual(chat.messages[0].kind, "user")
            self.assertEqual(chat.messages[0].text, "kept")

    def test_zoom_opens_two_children_and_date_is_the_stored_time(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("user", "alpha", date="2026-10-05T01:02:03-0500")
            chat.append("talk", "beta")
            self.assertEqual(chat.date_of(0), "2026-10-05T01:02:03-0500")
            opened = chat.zoom(0, 2)
            self.assertIn("0+1|", opened)
            self.assertIn("alpha", opened)
            self.assertIn("1+1|", opened)
            self.assertIn("beta", opened)

    def test_default_echo_cap_is_30000(self) -> None:
        self.assertEqual(cap_echo("x" * 30000), "x" * 30000)
        capped = cap_echo("H" * 20000 + "T" * 20000)
        self.assertTrue(capped.startswith("H" * 15000))
        self.assertTrue(capped.endswith("T" * 15000))
        self.assertIn("cut", capped)

    def test_html_page_has_view_log_and_a_level(self) -> None:
        from z0int.optchat.browse import render_html

        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            chat.append("user", "visible")
            page = render_html(chat)
            self.assertIn("visible", page)
            self.assertIn("<h1>view</h1>", page)
            self.assertIn("<h1>log</h1>", page)
            self.assertIn("level 0", page)

    def test_persist_commits_the_chat_directory(self) -> None:
        from z0int.optchat.persist import persist

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main").mkdir()
            (root / "main" / "note.txt").write_text("kept\n")
            rev = persist(root)
            self.assertTrue(rev)
            self.assertTrue((root / ".git").is_dir())

    def test_older_pair_is_the_one_merged(self) -> None:
        with TemporaryDirectory() as tmp:
            chat = ChatLog(Path(tmp))
            for i in range(4):
                chat.append("user", f"m{i}")
                chat._store_node(0, i, f"m{i} " + ("z" * 40000))
            chat._store_node(1, 0, "older parent " + ("p" * 40000))
            chat._store_node(1, 1, "newer parent " + ("p" * 40000))
            chat.fit()
            self.assertEqual(chat.view[0].level, 1)
            self.assertEqual(chat.view[0].index, 0)

    def test_subagent_report_is_a_user_message(self) -> None:
        from z0int.optchat.turn import subagent_text

        self.assertEqual(subagent_text("7", "done"), "[7] done")



if __name__ == "__main__":
    unittest.main()
