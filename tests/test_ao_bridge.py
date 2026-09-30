import tempfile
import unittest
from pathlib import Path

from z0int.ao_bridge import (
    OUTCOME_SCHEMA,
    SPAWN_SCHEMA,
    join_ao_outcome,
    spawn_decision,
)


def request(trace="ao-spawn-s1"):
    return {
        "schema": SPAWN_SCHEMA,
        "trace_id": trace,
        "session_id": "proj-1",
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


class AOBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_spawn_shadow_is_durable_and_idempotent(self):
        first = spawn_decision(request(), root=self.root)
        second = spawn_decision(request(), root=self.root)
        self.assertEqual(first["action"], "abstain")
        self.assertEqual(first["recommendation"]["harness"], "codex")
        self.assertFalse(first["replayed"])
        self.assertEqual(second["decision_id"], first["decision_id"])
        self.assertTrue(second["replayed"])

    def test_spawn_trace_conflict_fails_closed(self):
        spawn_decision(request(), root=self.root)
        changed = request()
        changed["task"] = "different task"
        with self.assertRaisesRegex(ValueError, "reused"):
            spawn_decision(changed, root=self.root)

    def test_outcome_join_is_session_bound_and_idempotent(self):
        spawn_decision(request(), root=self.root)
        event = {
            "schema": OUTCOME_SCHEMA,
            "trace_id": "ao-spawn-s1",
            "session_id": "proj-1",
            "outcome": {
                "execution_completed": True,
                "test_pass": True,
                "verification_source": "ao-ci",
            },
        }
        first = join_ao_outcome(event, root=self.root)
        second = join_ao_outcome(event, root=self.root)
        self.assertEqual(first["outcome_tier"], "gold")
        self.assertFalse(first["replayed"])
        self.assertEqual(second["outcome_tier"], "gold")
        self.assertTrue(second["replayed"])

    def test_outcome_cannot_cross_session_boundary(self):
        spawn_decision(request(), root=self.root)
        event = {
            "schema": OUTCOME_SCHEMA,
            "trace_id": "ao-spawn-s1",
            "session_id": "other",
            "outcome": {"execution_completed": True},
        }
        with self.assertRaisesRegex(ValueError, "session"):
            join_ao_outcome(event, root=self.root)


if __name__ == "__main__":
    unittest.main()
