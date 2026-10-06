from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from adapters.hermes_z0int import (
    close_observation,
    join_outcome,
    normalize_envelope,
    resolve_repository_context,
)
from z0int.context_resolve import InformationNeed
from z0int.memory.event_log import EventLog


class HermesAdapterTests(unittest.TestCase):
    def test_normalize_sets_hermes_identity(self):
        env = normalize_envelope(
            {
                "session_id": "s1",
                "turn_id": "t9",
                "text": "hello",
                "capability_id": "coding.edit",
            },
            build_id="test",
        )
        self.assertEqual(env["schema"], "z0int.harness_envelope.v1")
        self.assertEqual(env["identity"]["harness_id"], "hermes")
        self.assertEqual(env["identity"]["session_id"], "s1")
        self.assertEqual(env["identity"]["turn_id"], "t9")
        self.assertNotIn("omp_session_id", env["identity"])

    def test_close_keeps_verified_null(self):
        env = normalize_envelope({"session_id": "s", "text": "x"})
        obs = close_observation(env, execution_completed=True, verified_success=None)
        self.assertTrue(obs["execution_completed"])
        self.assertIsNone(obs["verified_success"])
        self.assertEqual(obs["schema"], "z0int.allocation_observation.v1")

    def test_join_outcome_does_not_invent_success(self):
        env = normalize_envelope({"session_id": "s", "text": "x"})
        obs = close_observation(env, execution_completed=True, verified_success=None)
        joined = join_outcome(obs)
        self.assertIsNone(joined["verified_success"])
        joined2 = join_outcome(obs, gold_verified=True)
        self.assertTrue(joined2["verified_success"])

    def test_repository_context_uses_shared_packet_and_event_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            (root / "README.md").write_text("hermes evidence\n", encoding="utf-8")
            log = EventLog(root=Path(tmp) / "memory")
            env = normalize_envelope(
                {"session_id": "s1", "trace_id": "trace1", "text": "find evidence"}
            )
            out = resolve_repository_context(
                env,
                project_root=root,
                needs=[
                    InformationNeed(
                        id="p1",
                        description="README",
                        kind="exact_path",
                        path="README.md",
                    )
                ],
                allow_qmd=False,
                event_log=log,
            )
            self.assertEqual(out["harness_id"], "hermes")
            self.assertEqual(out["packet"]["evidence"][0]["locator"], str(root / "README.md"))
            event = log.get(out["event_id"])
            self.assertEqual(event.event_type, "context.resolve")
            self.assertEqual(event.source, "harness:hermes")
            self.assertEqual(event.session_id, "s1")
            self.assertIsNone(event.payload["verified_success"])

    def test_isolation_fields_differ_per_session(self):
        a = normalize_envelope({"session_id": "A", "text": "1"})
        b = normalize_envelope({"session_id": "B", "text": "2"})
        self.assertNotEqual(a["identity"]["session_id"], b["identity"]["session_id"])


if __name__ == "__main__":
    unittest.main()
