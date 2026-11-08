from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def z0home(tmp: str):
    prev = os.environ.get("Z0INT_HOME")
    os.environ["Z0INT_HOME"] = tmp
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("Z0INT_HOME", None)
        else:
            os.environ["Z0INT_HOME"] = prev


class PreflightNoEvolutionLabTests(unittest.TestCase):
    def test_model_fallback_without_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            from z0int.preflight import run_preflight

            d = run_preflight("please implement a feature in the app")
            self.assertEqual(d.route, "model")
            self.assertIsNotNone(d.work_requirement)
            self.assertIn("no_promoted_coverage", d.reasons)

    def test_promoted_routine_route(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            from z0int import paths
            from z0int.preflight import run_preflight

            paths.ensure_layout()
            reg = paths.home() / "config" / "routine_registry.json"
            reg.write_text(
                json.dumps(
                    [
                        {
                            "routine_id": "r1",
                            "capability_id": "coding.needs_verification",
                            "status": "promoted",
                            "action": "VERIFY",
                            "contains_any": ["pytest"],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            d = run_preflight("please run pytest on this change")
            self.assertEqual(d.route, "routine")
            self.assertEqual(d.prediction, "VERIFY")


class ArtifactImportTests(unittest.TestCase):
    def _make_manifest(self, root: Path, *, promoted: bool = False) -> Path:
        weights = root / "weights.npz"
        # minimal bytes
        weights.write_bytes(b"PK\x00\x00fake-npz")
        digest = hashlib.sha256(weights.read_bytes()).hexdigest()
        man = {
            "schema": "z0int.candidate_artifact.v1",
            "artifact_id": "sha256:" + hashlib.sha256(digest.encode()).hexdigest(),
            "producer": {"repo": "kvnloo/evolution-lab", "revision": "abc123"},
            "capability_id": "coding.recovery_action",
            "family": "local_plasticity",
            "runtime": "z0int.backend.mushroom.v1",
            "files": [{"path": "weights.npz", "sha256": digest}],
            "training": {"genome_id": "g1"},
            "claims": {},
            "promotion": {"status": "promoted" if promoted else "candidate"},
        }
        path = root / "manifest.json"
        path.write_text(json.dumps(man), encoding="utf-8")
        return path

    def test_import_forces_candidate_never_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, z0home(str(Path(tmp) / "z0")):
            from z0int.artifacts import get_artifact, import_manifest, inspect_manifest, list_artifacts

            bundle = Path(tmp) / "bundle"
            bundle.mkdir()
            man = self._make_manifest(bundle, promoted=False)
            insp = inspect_manifest(man)
            self.assertTrue(insp["ok"])
            out = import_manifest(man)
            self.assertTrue(out["ok"])
            art = get_artifact(out["artifact_id"])
            assert art is not None
            self.assertEqual(art["promotion"]["status"], "candidate")
            # A producer cannot supply its own promotion decision.
            man2 = self._make_manifest(bundle, promoted=True)
            out2 = import_manifest(man2)
            self.assertFalse(out2["ok"])
            listed = list_artifacts()
            self.assertTrue(any(a.get("promotion", {}).get("status") == "candidate" for a in listed))
            self.assertFalse(any(a.get("promotion", {}).get("status") == "promoted" for a in listed))


class AutoresearchQueueTests(unittest.TestCase):
    def test_execution_only_does_not_enqueue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            from z0int.autoresearch.queue import enqueue_trace, list_queue

            r = enqueue_trace("t1", verified_success=None, verifier_id="v1")
            self.assertFalse(r["enqueued"])
            r2 = enqueue_trace("t2", verified_success=False, verifier_id="v1")
            self.assertFalse(r2["enqueued"])
            self.assertEqual(list_queue(), [])

    def test_verified_enqueues_and_run_once_abab(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            from z0int.autoresearch.daemon import pause, resume, run_once, status
            from z0int.autoresearch.queue import enqueue_trace

            pause()
            r = enqueue_trace(
                "trace-ok",
                verified_success=True,
                verifier_id="unit_verifier",
                payload={
                    "snapshot": {
                        "task_snapshot_id": "snap1",
                        "required_ops": ["exact_path"],
                        "baseline_wall_ms": 500,
                        "frontier_tokens": 100,
                    }
                },
            )
            self.assertTrue(r["enqueued"])
            # duplicate credit blocked
            r2 = enqueue_trace("trace-ok", verified_success=True, verifier_id="unit_verifier")
            self.assertFalse(r2["enqueued"])
            self.assertEqual(r2.get("reason"), "duplicate_credit")
            # paused skips
            out = run_once()
            self.assertTrue(out.get("skipped"))
            resume()
            out2 = run_once(force=True)
            self.assertTrue(out2.get("ok"))
            self.assertIn(out2.get("decision"), {
                "KEEP_CHALLENGER_CANDIDATE",
                "REJECT_CHALLENGER",
                "NO_UPDATE",
            })
            # champion file frozen
            champ = Path(tmp) / "autoresearch" / "champions" / "trace-ok.json"
            self.assertTrue(champ.is_file())
            st = status()
            self.assertIn("queue_depth", st)

    def test_failed_ablation_negative_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, z0home(tmp):
            from z0int.autoresearch.daemon import resume, run_once
            from z0int.autoresearch.queue import enqueue_trace

            resume()
            enqueue_trace(
                "trace-fail",
                verified_success=True,
                verifier_id="unit_verifier",
                payload={
                    "snapshot": {
                        "task_snapshot_id": "s",
                        "required_ops": ["exact_path", "qmd_lexical"],
                        "force_fail_ops": ["qmd_lexical"],
                        "baseline_wall_ms": 400,
                    }
                },
            )
            out = run_once(force=True)
            self.assertEqual(out.get("decision"), "REJECT_CHALLENGER")
            results = (Path(tmp) / "autoresearch" / "results.jsonl").read_text(encoding="utf-8")
            self.assertIn("trace-fail", results or out.get("job_id", ""))


class KerdoiosExportTests(unittest.TestCase):
    def test_null_verified_distinct(self) -> None:
        from z0int.kerdoios_export import receipt_to_observation

        obs = receipt_to_observation(
            {
                "trace_id": "t",
                "execution_completed": True,
                "verified_success": None,
            }
        )
        assert obs is not None
        self.assertIsNone(obs["verified_success"])
        self.assertTrue(obs["execution_completed"])
        obs2 = receipt_to_observation({"trace_id": "t2", "verified_success": False, "execution_completed": True})
        assert obs2 is not None
        self.assertIs(obs2["verified_success"], False)


if __name__ == "__main__":
    unittest.main()
