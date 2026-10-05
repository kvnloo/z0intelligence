"""Correctness gates for the retrieval-replay slice. No promotion from simulation."""

from __future__ import annotations

import json
import unittest
from unittest import mock

from z0int.autoresearch.retrieval_exec import (
    execute_recipe,
    promotion_allowed,
    recipe_for,
    shadow_retrieval,
    unavailable_arm,
    verify_evidence,
)
from z0int.autoresearch.replay import _simulate_context_cost


class VerifierTests(unittest.TestCase):
    def test_missing_required_event_fails(self) -> None:
        got = verify_evidence(["other"], ["needed"])
        self.assertFalse(got["verified_success"])
        self.assertEqual(got["missing"], ["needed"])

    def test_matching_event_passes(self) -> None:
        got = verify_evidence(["needed", "extra"], ["needed"])
        self.assertTrue(got["verified_success"])

    def test_empty_required_set_is_not_success(self) -> None:
        self.assertFalse(verify_evidence(["x"], [])["verified_success"])


class CreditTests(unittest.TestCase):
    def test_simulation_has_no_promotion_credit(self) -> None:
        sim = _simulate_context_cost({"ops": ["exact_path"], "role": "champion"}, {"required_ops": ["exact_path"]})
        row = {**sim, "measurement_source": "simulation", "promotion_credit": False, "verified_success": True}
        self.assertFalse(promotion_allowed(row))
        self.assertNotIn("measured", json.dumps(sim))

    def test_unavailable_arm_cannot_promote(self) -> None:
        row = unavailable_arm("qmd", "source_absent")
        row["verified_success"] = True
        self.assertFalse(promotion_allowed(row))

    def test_executed_pass_can_promote(self) -> None:
        row = {
            "measurement_source": "executed",
            "promotion_credit": True,
            "verified_success": True,
        }
        self.assertTrue(promotion_allowed(row))


class ShadowTests(unittest.TestCase):
    def test_shadow_does_not_change_model_io(self) -> None:
        model_io = {"input": "live prompt", "output": "live answer", "n": 1}

        def run() -> dict:
            return {"event_ids": ["evt-1"]}

        out = shadow_retrieval(model_io, run)
        self.assertTrue(out["model_io_unchanged"])
        self.assertFalse(out["student_changed_execution"])
        self.assertFalse(out["injected"])
        self.assertEqual(model_io, {"input": "live prompt", "output": "live answer", "n": 1})
        self.assertFalse(out["promotion_credit"])

    def test_shadow_detects_a_write_into_model_io(self) -> None:
        model_io = {"input": "live", "output": ""}

        def run() -> dict:
            model_io["output"] = "challenger"
            return {"event_ids": []}

        out = shadow_retrieval(model_io, run)
        self.assertFalse(out["model_io_unchanged"])
        self.assertTrue(out["student_changed_execution"])


class ExecutorTests(unittest.TestCase):
    def test_show_without_locator_stays_unavailable(self) -> None:
        recipe = recipe_for(({"op": "ctx_show_event"},), capability_id="exact_locator", epochs={})
        out = execute_recipe(recipe, query="bridge hot-reload", locator=None, ctx_bin="ctx")
        self.assertTrue(out["unavailable"])
        self.assertEqual(out["steps"][0]["reason"], "locator_absent_at_decision_point")
        self.assertFalse(promotion_allowed({**out, "promotion_credit": True, "verified_success": True}))

    def test_qmd_is_not_mocked(self) -> None:
        recipe = recipe_for(({"op": "qmd", "reason": "source_absent"},), capability_id="qmd", epochs={})
        out = execute_recipe(recipe, query="x", locator=None)
        self.assertEqual(out["steps"][0]["reason"], "source_absent")
        self.assertEqual(out["event_ids"], [])

    def test_lexical_uses_installed_flags(self) -> None:
        payload = json.dumps({"results": [{"ctx_event_id": "evt-9"}], "retrieval": {"generation_id": "g"}})
        with mock.patch("z0int.autoresearch.retrieval_exec._run", return_value={
            "returncode": 0, "stdout": payload, "stderr": "", "wall_ms": 3.0,
        }) as run:
            recipe = recipe_for(({"op": "ctx_lexical", "limit": 1},), capability_id="lexical", epochs={"ctx": "g"})
            out = execute_recipe(recipe, query="bridge hot-reload", locator=None, ctx_bin="ctx")
        argv = run.call_args.args[0]
        self.assertIn("--refresh", argv)
        self.assertIn("off", argv)
        self.assertIn("--backend", argv)
        self.assertIn("lexical", argv)
        self.assertEqual(out["event_ids"], ["evt-9"])
        self.assertEqual(out["measurement_source"], "executed")


if __name__ == "__main__":
    unittest.main()
