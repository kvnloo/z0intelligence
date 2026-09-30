from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from z0int.memory.event_log import EventLog, EventLogCorruption


class EventLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.log = EventLog(self.root, blob_threshold=256)

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_assigns_monotonic_ids_and_parents(self):
        a = self.log.append("user.message", {"text": "hello"}, source="chatgpt")
        b = self.log.append(
            "worker.result",
            {"ok": True},
            source="hermes",
            parent_event_ids=[a.event_id],
            project="z0",
            session_id="s1",
        )
        self.assertEqual((a.event_id, b.event_id), (0, 1))
        self.assertEqual(b.parent_event_ids, (0,))
        self.assertEqual(self.log.get(1).project, "z0")
        self.assertEqual(self.log.verify()["events"], 2)

    def test_large_payload_is_content_addressed_blob(self):
        payload = {"text": "x" * 5000, "nested": {"value": 7}}
        event = self.log.append("tool.result", payload, source="omp")
        self.assertIsNone(event.payload)
        self.assertIsNotNone(event.blob)
        self.assertEqual(self.log.get(0, resolve_blob=True).payload, payload)
        self.assertEqual(self.log.verify()["blobs"], 1)

    def test_rebuild_index_does_not_mutate_event_log(self):
        for i in range(5):
            self.log.append("test.event", {"i": i}, source="test")
        before = self.log.events_path.read_bytes()
        self.log.index_path.unlink()
        self.assertEqual(self.log.rebuild_index(), 5)
        self.assertEqual(self.log.events_path.read_bytes(), before)
        self.assertEqual(self.log.get(4).payload, {"i": 4})

    def test_stale_index_recovers_from_canonical_events(self):
        for i in range(3):
            self.log.append("test.event", {"i": i}, source="test")
        lines = self.log.index_path.read_text().splitlines()
        self.log.index_path.write_text(lines[0] + "\n")
        self.assertEqual(self.log.get(2).payload, {"i": 2})


    def test_append_repairs_truncated_index_before_writing_next_row(self):
        for i in range(3):
            self.log.append("test.event", {"i": i}, source="test")
        lines = self.log.index_path.read_text().splitlines()
        self.log.index_path.write_text(lines[0] + "\n")
        fourth = self.log.append("test.event", {"i": 3}, source="test")
        self.assertEqual(fourth.event_id, 3)
        self.assertEqual(len(self.log.index_path.read_text().splitlines()), 4)
        self.assertEqual(self.log.get(2).payload, {"i": 2})
        self.assertEqual(self.log.get(3).payload, {"i": 3})

    def test_checksum_tamper_fails_closed(self):
        self.log.append("test.event", {"value": "safe"}, source="test")
        row = json.loads(self.log.events_path.read_text())
        row["payload"]["value"] = "tampered"
        self.log.events_path.write_text(json.dumps(row) + "\n")
        with self.assertRaisesRegex(EventLogCorruption, "checksum"):
            list(self.log.iter_events())

    def test_incomplete_trailing_write_is_ignored_for_read_but_blocks_append(self):
        self.log.append("test.event", {"value": 1}, source="test")
        with self.log.events_path.open("ab") as out:
            out.write(b'{"schema":"z0int.memory.event.v1"')
        self.assertEqual(len(list(self.log.iter_events())), 1)
        with self.assertRaisesRegex(EventLogCorruption, "incomplete trailing"):
            self.log.append("test.event", {"value": 2}, source="test")

    def test_committed_malformed_line_fails_closed(self):
        self.log.append("test.event", {"value": 1}, source="test")
        with self.log.events_path.open("ab") as out:
            out.write(b'not-json\n')
        with self.assertRaisesRegex(EventLogCorruption, "malformed committed"):
            list(self.log.iter_events())

    def test_parent_must_reference_prior_event(self):
        with self.assertRaises(ValueError):
            self.log.append(
                "worker.result",
                {"ok": True},
                source="hermes",
                parent_event_ids=[0],
            )


if __name__ == "__main__":
    unittest.main()
