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
            self.assertIn("(not summarized yet)", rendered)
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
            self.assertIn("(not summarized yet)", chat.render())

            def fake(messages):
                return "echo: a long tool result was capped and not copied"

            built = pump(chat, fake, limit=2)
            self.assertEqual(built, 1)
            self.assertNotIn("(not summarized yet)", chat.render())
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


if __name__ == "__main__":
    unittest.main()
