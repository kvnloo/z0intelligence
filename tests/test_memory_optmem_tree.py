from __future__ import annotations

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from z0int.memory.event_log import EventLog, MemoryEvent
from z0int.memory.optmem_tree import CoverBudgetExceeded, OptMemTree


class OptMemTreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.log = EventLog(self.root / "memory", blob_threshold=512)

    def tearDown(self):
        self.tmp.cleanup()

    def seed(self, n: int = 32) -> None:
        for i in range(n):
            self.log.append(
                "conversation.turn",
                {"text": f"turn-{i}", "value": i},
                source="fixture",
                project="z0",
                session_id="s1",
            )

    def test_nap_builds_aligned_deterministic_tree(self):
        self.seed(32)
        tree = OptMemTree(self.log, raw_tail_events=4)
        first = tree.nap()
        second = tree.rebuild()
        self.assertEqual(first["coarse_history_hash"], second["coarse_history_hash"])
        self.assertEqual(first["coarse_end"], 28)
        self.assertLess(first["coarse_node_count"], 8)
        self.assertEqual(
            len(list(tree.nodes_dir.glob("*.json"))),
            first["coarse_node_count"],
        )

    def test_rebuild_never_mutates_canonical_events(self):
        self.seed(20)
        tree = OptMemTree(self.log, raw_tail_events=4)
        tree.nap()
        before = self.log.events_path.read_bytes()
        tree.rebuild()
        self.assertEqual(self.log.events_path.read_bytes(), before)

    def test_cover_keeps_recent_tail_raw(self):
        self.seed(24)
        tree = OptMemTree(self.log, raw_tail_events=5)
        tree.nap()
        cover = tree.cover(max_tokens=10000)
        raw_ids = [
            item["event"]["event_id"]
            for item in cover["items"]
            if item["kind"] == "raw"
        ]
        self.assertEqual(raw_ids, [19, 20, 21, 22, 23])
        self.assertTrue(any(item["kind"] == "summary" for item in cover["items"]))

    def test_append_after_nap_does_not_change_coarse_tree_revision(self):
        self.seed(16)
        tree = OptMemTree(self.log, raw_tail_events=4)
        manifest = tree.nap()
        old_hash = manifest["coarse_history_hash"]
        for i in range(16, 19):
            self.log.append("conversation.turn", {"text": f"turn-{i}"}, source="fixture")
        cover = tree.cover(max_tokens=10000)
        self.assertEqual(cover["tree_revision"], old_hash)
        raw_ids = [
            item["event"]["event_id"]
            for item in cover["items"]
            if item["kind"] == "raw"
        ]
        self.assertEqual(raw_ids, [12, 13, 14, 15, 16, 17, 18])

    def test_sticky_historical_event_expands_local_path(self):
        self.seed(32)
        tree = OptMemTree(self.log, raw_tail_events=4)
        tree.nap()
        cover = tree.cover(max_tokens=12000, sticky_event_ids=[7])
        sticky = [
            item
            for item in cover["items"]
            if item["kind"] == "raw" and item.get("sticky")
        ]
        self.assertEqual([item["event"]["event_id"] for item in sticky], [7])
        self.assertTrue(any(item["kind"] == "summary" for item in cover["items"]))

    def test_zoom_recovers_exact_large_payload(self):
        payload = {"text": "critical-" + "x" * 5000, "nested": {"k": 9}}
        event = self.log.append("tool.result", payload, source="hermes")
        tree = OptMemTree(self.log, raw_tail_events=0)
        tree.nap()
        zoom = tree.zoom(event.event_id, event.event_id + 1)
        self.assertEqual(zoom["events"][0]["resolved_payload"], payload)
        self.assertEqual(zoom["source_event_ids"], [0])

    def test_cover_and_zoom_keep_canonical_events_separate_from_resolved_payloads(self):
        payloads = [
            {"text": "sticky-" + "x" * 5000},
            {"text": "inline"},
            {"text": "tail-" + "y" * 5000},
        ]
        for payload in payloads:
            self.log.append("tool.result", payload, source="fixture")
        tree = OptMemTree(self.log, raw_tail_events=1)
        tree.nap()

        cover = tree.cover(max_tokens=20000, sticky_event_ids=[0])
        raw = [item for item in cover["items"] if item["kind"] == "raw"]
        self.assertEqual([item["event"]["event_id"] for item in raw], [0, 2])
        zoom = tree.zoom(0, 3)
        self.assertEqual(zoom["source_event_ids"], [0, 1, 2])
        for entries in (raw, zoom["events"]):
            for entry in entries:
                event = MemoryEvent.from_dict(entry["event"])
                self.assertEqual(event.to_dict(), self.log.get(event.event_id).to_dict())
                self.assertEqual(entry["resolved_payload"], payloads[event.event_id])

    def test_cover_rejects_substituted_node_with_valid_other_ledger_digest(self):
        self.seed(4)
        tree = OptMemTree(self.log, raw_tail_events=0)
        tree.nap()
        foreign_log = EventLog(self.root / "foreign")
        for i in range(4):
            foreign_log.append("foreign.event", {"i": i}, source="other")
        foreign_tree = OptMemTree(foreign_log, raw_tail_events=0)
        foreign_manifest = foreign_tree.nap()
        tree._node_path(2, 0).write_bytes(foreign_tree._node_path(2, 0).read_bytes())
        manifest = json.loads(tree.manifest_path.read_text())
        manifest["coarse_history_hash"] = foreign_manifest["coarse_history_hash"]
        tree.manifest_path.write_text(json.dumps(manifest))
        with self.assertRaises(RuntimeError):
            tree.cover(max_tokens=10000)

    def test_cover_rejects_node_with_substituted_address(self):
        self.seed(4)
        tree = OptMemTree(self.log, raw_tail_events=0)
        tree.nap()
        path = tree._node_path(2, 0)
        row = json.loads(path.read_text())
        row["address"] = "L2:4"
        row["start_event_id"] = 4
        row["end_event_id"] = 8
        path.write_text(json.dumps(row))
        with self.assertRaises(RuntimeError):
            tree.cover(max_tokens=10000)

    def test_failed_nap_preserves_published_cover_and_canonical_events(self):
        self.seed(4)
        tree = OptMemTree(self.log, raw_tail_events=0)
        tree.nap()
        self.seed(1)
        before_events = self.log.events_path.read_bytes()
        before_cover = tree.cover(max_tokens=10000)
        with patch.object(tree, "_summarize_segment", side_effect=RuntimeError("injected failure")):
            with self.assertRaisesRegex(RuntimeError, "injected failure"):
                tree.nap()
        self.assertEqual(tree.cover(max_tokens=10000), before_cover)
        self.assertEqual(self.log.events_path.read_bytes(), before_events)

    def test_cover_holds_generation_until_concurrent_nap_publishes(self):
        self.seed(4)
        tree = OptMemTree(self.log, raw_tail_events=0)
        old_manifest = tree.nap()
        self.seed(4)
        reader_entered = threading.Event()
        release_reader = threading.Event()
        writer_entered = threading.Event()
        writer_finished = threading.Event()
        load_node = tree._load_node

        def paused_load(*args, **kwargs):
            reader_entered.set()
            if not release_reader.wait(5):
                raise RuntimeError("reader barrier timed out")
            return load_node(*args, **kwargs)

        def publish():
            writer_entered.set()
            try:
                return tree.nap()
            finally:
                writer_finished.set()

        with patch.object(tree, "_load_node", side_effect=paused_load):
            with ThreadPoolExecutor(max_workers=2) as pool:
                reader = pool.submit(tree.cover, max_tokens=10000)
                try:
                    self.assertTrue(reader_entered.wait(5))
                    writer = pool.submit(publish)
                    self.assertTrue(writer_entered.wait(5))
                    self.assertFalse(writer_finished.wait(0.25))
                finally:
                    release_reader.set()
                old_cover = reader.result(timeout=5)
                new_manifest = writer.result(timeout=5)
        self.assertEqual(old_cover["tree_revision"], old_manifest["coarse_history_hash"])
        self.assertEqual(old_cover["nap_event_count"], 4)
        self.assertEqual(new_manifest["event_count"], 8)
        self.assertEqual(tree.cover(max_tokens=10000)["tree_revision"], new_manifest["coarse_history_hash"])
        self.assertEqual(len(list(tree.generations_dir.iterdir())), 1)

    def test_hard_budget_fails_closed_instead_of_truncating_raw_tail(self):
        self.seed(8)
        tree = OptMemTree(self.log, raw_tail_events=8)
        tree.nap()
        with self.assertRaises(CoverBudgetExceeded):
            tree.cover(max_tokens=4)

    def test_ten_thousand_structural_events_fit_bounded_cover(self):
        for i in range(10_000):
            self.log.append("turn", {"i": i}, source="fixture")
        tree = OptMemTree(self.log, raw_tail_events=4)
        tree.nap()
        cover = tree.cover(max_tokens=4096)
        summaries = [item for item in cover["items"] if item["kind"] == "summary"]
        raw = [item for item in cover["items"] if item["kind"] == "raw"]
        self.assertLessEqual(cover["estimated_tokens"], 4096)
        self.assertLess(len(summaries), 32)
        self.assertEqual(len(raw), 4)
        self.assertLess(len(list(tree.nodes_dir.glob("*.json"))), 32)


if __name__ == "__main__":
    unittest.main()
