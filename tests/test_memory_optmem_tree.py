from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from z0int.memory.event_log import EventLog
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
        self.assertTrue((tree.nodes_dir / "L5-0.json").exists())

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
        self.assertEqual(zoom["events"][0]["payload"], payload)
        self.assertEqual(zoom["source_event_ids"], [0])

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


if __name__ == "__main__":
    unittest.main()
