import json
import tempfile
import unittest
from pathlib import Path

from z0int import paths
from z0int.ao_bridge import SPAWN_SCHEMA, spawn_decision
from z0int.ao_experiment import (
    REFERENCE_CAPABILITY,
    list_pairs,
    materialize_reference_snapshot,
    register_pair,
)
from z0int.counterfactual import SNAPSHOTS_NAME
from z0int.receipt import DecisionReceipt, append_receipt, find_receipt


def spawn_request(trace_id: str = "ao-spawn-candidate"):
    return {
        "schema": SPAWN_SCHEMA,
        "trace_id": trace_id,
        "session_id": "candidate",
        "project_id": "proj",
        "kind": "worker",
        "task": "fix the retry race",
        "current": {
            "harness": "codex",
            "model": "gpt-5",
            "mode": "chat",
            "permission": "default",
        },
        "constraints": {
            "explicit_harness": False,
            "explicit_model": False,
            "explicit_mode": False,
        },
    }


def write_snapshot(root: Path, snapshot_id: str = "task-1") -> None:
    replay_dir = paths.ensure_layout(root)["replay"]
    row = {
        "schema": "z0int.task_snapshot.v1",
        "task_snapshot_id": snapshot_id,
        "arm_id": "reference",
        "selection_policy": "historical_replay",
        "replay_grade": "B",
        "session_id": "historic-session",
        "provider": "xai",
        "model": "grok-reference",
        "treatment_hash": "treatment-1",
        "prompt_hash": "prompt-1",
        "output_hash": "output-1",
        "reference_requested": True,
        "reason_for_reference": "historical_grok_corpus",
        "usage": {
            "input": 30,
            "output": 20,
            "cacheRead": 5,
            "totalTokens": 50,
            "cost_total": 0.05,
        },
    }
    path = replay_dir / SNAPSHOTS_NAME
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


class AOExperimentRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        write_snapshot(self.root)
        spawn_decision(spawn_request(), root=self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_materialize_reference_snapshot_is_deterministic_and_measured(self):
        first = materialize_reference_snapshot("task-1", root=self.root)
        second = materialize_reference_snapshot("task-1", root=self.root)

        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["trace_id"], "ao-reference-task-1")
        self.assertEqual(second["trace_id"], first["trace_id"])

        stored = find_receipt(first["trace_id"], root=self.root)
        self.assertEqual(stored["capability_id"], REFERENCE_CAPABILITY)
        self.assertEqual(stored["provider"], "xai")
        self.assertEqual(stored["model"], "grok-reference")
        self.assertEqual(stored["measured_frontier_tokens"], 50)
        self.assertEqual(stored["measurement_state"], "partial")
        self.assertEqual(
            stored["extra"]["decision_measurement"]["cost_usd"],
            0.05,
        )
        self.assertEqual(stored["extra"]["task_snapshot_id"], "task-1")

    def test_register_pair_auto_materializes_reference_and_replays(self):
        first = register_pair(
            experiment_id="exp-1",
            pair_id="pair-1",
            task_snapshot_id="task-1",
            candidate_trace_id="ao-spawn-candidate",
            root=self.root,
        )
        second = register_pair(
            experiment_id="exp-1",
            pair_id="pair-1",
            task_snapshot_id="task-1",
            candidate_trace_id="ao-spawn-candidate",
            root=self.root,
        )

        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["reference_trace_id"], "ao-reference-task-1")
        self.assertEqual(first["pair_sha256"], second["pair_sha256"])
        pairs = list_pairs(root=self.root)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0]["candidate_trace_id"], "ao-spawn-candidate")

    def test_register_pair_rejects_conflicting_pair_identity(self):
        register_pair(
            experiment_id="exp-1",
            pair_id="pair-1",
            task_snapshot_id="task-1",
            candidate_trace_id="ao-spawn-candidate",
            root=self.root,
        )
        spawn_decision(spawn_request("ao-spawn-other"), root=self.root)
        with self.assertRaisesRegex(ValueError, "reused"):
            register_pair(
                experiment_id="exp-1",
                pair_id="pair-1",
                task_snapshot_id="task-1",
                candidate_trace_id="ao-spawn-other",
                root=self.root,
            )

    def test_register_pair_requires_ao_candidate(self):
        append_receipt(
            DecisionReceipt(
                trace_id="generic-candidate",
                capability_id="generic.capability",
                prediction="candidate",
            ),
            root=self.root,
        )
        with self.assertRaisesRegex(ValueError, "not an AO decision"):
            register_pair(
                experiment_id="exp-2",
                pair_id="pair-2",
                task_snapshot_id="task-1",
                candidate_trace_id="generic-candidate",
                root=self.root,
            )


if __name__ == "__main__":
    unittest.main()
