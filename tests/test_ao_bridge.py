import tempfile
import unittest
from pathlib import Path

from z0int.ao_bridge import (
    OUTCOME_SCHEMA,
    SPAWN_SCHEMA,
    join_ao_outcome,
    spawn_decision,
)
from z0int.receipt import find_receipt


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


def outcome_event(session_id="proj-1"):
    return {
        "schema": OUTCOME_SCHEMA,
        "trace_id": "ao-spawn-s1",
        "outcome_id": "ao-outcome-proj-1-terminated",
        "session_id": session_id,
        "outcome": {
            "execution_completed": True,
            "pr_merged": True,
            "source": "agent-orchestrator",
            "verification_source": "ao-pr-merge",
        },
        "evidence": {
            "project_id": "proj",
            "kind": "worker",
            "harness": "codex",
            "mode": "chat",
            "model": "gpt-5",
            "activity": "idle",
            "terminated": True,
            "scm_complete": True,
            "prs": [{
                "url": "https://github.com/example/repo/pull/7",
                "number": 7,
                "draft": False,
                "merged": True,
                "closed": False,
                "ci": "passing",
                "review": "approved",
                "mergeability": "mergeable",
                "review_comments": False,
                "external_approved": True,
                "external_changes_requested": False,
                "external_comments": False,
                "head_sha": "abc123",
            }],
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
        event = outcome_event()
        first = join_ao_outcome(event, root=self.root)
        second = join_ao_outcome(event, root=self.root)
        self.assertEqual(first["outcome_tier"], "gold")
        self.assertEqual(first["outcome_id"], event["outcome_id"])
        self.assertFalse(first["replayed"])
        self.assertEqual(second["outcome_tier"], "gold")
        self.assertTrue(second["replayed"])

        stored = find_receipt("ao-spawn-s1", root=self.root)
        self.assertEqual(stored["extra"]["ao_outcome_id"], event["outcome_id"])
        self.assertTrue(stored["extra"]["ao_outcome_evidence"]["prs"][0]["merged"])

    def test_outcome_cannot_cross_session_boundary(self):
        spawn_decision(request(), root=self.root)
        event = outcome_event(session_id="other")
        with self.assertRaisesRegex(ValueError, "session"):
            join_ao_outcome(event, root=self.root)

    def test_outcome_rejects_unknown_evidence_fields(self):
        spawn_decision(request(), root=self.root)
        event = outcome_event()
        event["evidence"]["transcript"] = "must never cross this boundary"
        with self.assertRaisesRegex(ValueError, "evidence"):
            join_ao_outcome(event, root=self.root)

    def test_outcome_replay_rejects_changed_evidence(self):
        spawn_decision(request(), root=self.root)
        event = outcome_event()
        join_ao_outcome(event, root=self.root)
        changed = outcome_event()
        changed["evidence"]["prs"][0]["ci"] = "failing"
        with self.assertRaisesRegex(ValueError, "different payload"):
            join_ao_outcome(changed, root=self.root)


if __name__ == "__main__":
    unittest.main()
