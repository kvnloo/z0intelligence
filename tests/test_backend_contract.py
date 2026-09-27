from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from z0int.backends.base import (
    DecisionAnswer,
    DecisionOption,
    DecisionQuestion,
    DecisionRequest,
    request_from_mapping,
)
from z0int.backends.registry import (
    create_backend,
    list_backend_specs,
    register_builtin_backends,
    resolve_backend_id,
)


class DecisionContractTests(unittest.TestCase):
    def test_choice_bounds(self):
        with self.assertRaises(ValueError):
            DecisionQuestion(id="q", type="choice", instructions="pick", options=())
        ok = DecisionQuestion(
            id="q",
            type="choice",
            instructions="pick",
            options=(
                DecisionOption("a", "A"),
                DecisionOption("b", "B"),
            ),
        )
        self.assertEqual(len(ok.options), 2)

    def test_choice_max_255(self):
        opts = tuple(DecisionOption(f"o{i}", f"O{i}") for i in range(256))
        with self.assertRaises(ValueError):
            DecisionQuestion(id="q", type="choice", instructions="pick", options=opts)

    def test_duplicate_option_ids_rejected(self):
        with self.assertRaises(ValueError):
            DecisionQuestion(
                id="q",
                type="choice",
                instructions="pick",
                options=(
                    DecisionOption("a", "A"),
                    DecisionOption("a", "B"),
                ),
            )

    def test_score_bounds(self):
        with self.assertRaises(ValueError):
            DecisionQuestion(id="s", type="score", instructions="score", levels=("one",))
        with self.assertRaises(ValueError):
            DecisionQuestion(
                id="s",
                type="score",
                instructions="score",
                levels=tuple(f"l{i}" for i in range(11)),
            )

    def test_duplicate_question_ids_rejected(self):
        q = DecisionQuestion(id="same", type="boolean", instructions="truth?")
        with self.assertRaises(ValueError):
            DecisionRequest(state="x", questions=(q, q))

    def test_nonfinite_state_rejected(self):
        q = DecisionQuestion(id="q", type="boolean", instructions="truth?")
        with self.assertRaises(ValueError):
            DecisionRequest(state={"x": float("nan")}, questions=(q,))

    def test_empty_state_rejected(self):
        q = DecisionQuestion(id="q", type="boolean", instructions="truth?")
        with self.assertRaises(ValueError):
            DecisionRequest(state="", questions=(q,))
        with self.assertRaises(ValueError):
            DecisionRequest(state={}, questions=(q,))

    def test_answer_probs_must_sum(self):
        with self.assertRaises(ValueError):
            DecisionAnswer(
                question_id="q",
                type="boolean",
                probabilities={"false": 0.2, "true": 0.2},
                value=False,
            )

    def test_request_from_mapping_criteria_alias(self):
        req = request_from_mapping(
            {
                "state": {"a": 1},
                "questions": [
                    {
                        "id": "route",
                        "type": "choice",
                        "instructions": "pick",
                        "criteria": [
                            {"id": "x", "description": "X"},
                            {"id": "y", "description": "Y"},
                        ],
                    },
                    {
                        "id": "sc",
                        "type": "score",
                        "instructions": "rate",
                        "criteria": ["low", "high"],
                    },
                ],
            }
        )
        self.assertEqual(req.questions[0].options[0].id, "x")
        self.assertEqual(req.questions[1].levels, ("low", "high"))


class RegistryTests(unittest.TestCase):
    def test_list_does_not_instantiate_twice_and_registers(self):
        register_builtin_backends()
        specs = list_backend_specs()
        ids = [s.id for s in specs]
        self.assertIn("nanojev", ids)
        self.assertEqual(resolve_backend_id("nanojev_06b"), "nanojev")

    def test_unknown_backend(self):
        with self.assertRaises(KeyError):
            create_backend("no-such-backend-xyz")

    def test_alias_not_duplicate_canonical(self):
        specs = list_backend_specs()
        ids = [s.id for s in specs]
        self.assertEqual(ids.count("nanojev"), 1)
        self.assertNotIn("nanojev_06b", ids)


class ModelPlanBackendTests(unittest.TestCase):
    def test_cpu_and_vram_policies(self):
        from z0int import models_mgmt

        cpu = models_mgmt.plan_models(vram_gb=0.0)
        self.assertEqual(cpu["policy"], "cpu_only")
        self.assertNotIn("nanojev_06b", cpu["resident"])
        self.assertNotIn("openjev_4b", cpu["resident"])

        twelve = models_mgmt.plan_models(vram_gb=12.0)
        self.assertEqual(twelve["policy"], "twelve_gb")
        self.assertIn("nanojev_06b", twelve["resident"])
        self.assertIn("local_mb", twelve["resident"])
        # never coreside nanojev with 4b / 06b
        pairs = [tuple(p) for p in twelve["never_coreside"]]
        self.assertIn(("nanojev_06b", "openjev_4b"), pairs)
        self.assertIn(("nanojev_06b", "openjev_06b"), pairs)
        # must not try to keep 0.6B + 4B + NanoJev all resident
        resident = set(twelve["resident"])
        self.assertFalse({"nanojev_06b", "openjev_06b", "openjev_4b"} <= resident)

        high = models_mgmt.plan_models(vram_gb=24.0)
        self.assertEqual(high["policy"], "high_vram")
        self.assertIn("nanojev_06b", high["resident"])


class CLIBackendsTests(unittest.TestCase):
    def test_backends_list_json(self):
        from z0int.cli import main
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = main(["backends", "list", "--json"])
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["schema"], "z0int.backends.v1")
        self.assertTrue(any(b["id"] == "nanojev" for b in payload["backends"]))

    def test_backends_doctor_json_without_weights(self):
        from z0int.cli import main
        import io
        import contextlib

        with tempfile.TemporaryDirectory() as tmp:
            env = {"Z0INT_HOME": tmp}
            buf = io.StringIO()
            with mock.patch.dict("os.environ", env, clear=False):
                with contextlib.redirect_stdout(buf):
                    rc = main(["backends", "doctor", "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["schema"], "z0int.backends.doctor.v1")
            # Look the backend up by id rather than trusting list order. The
            # registry sorts by id, so any backend added alphabetically before
            # `nanojev` (e.g. `decider_2b`) would otherwise break this.
            by_id = {r["id"]: r for r in payload["backends"]}
            self.assertIn("nanojev", by_id)
            row = by_id["nanojev"]
            self.assertEqual(row["id"], "nanojev")
            self.assertFalse(row["ready"])
            self.assertFalse(row["loaded"])


if __name__ == "__main__":
    unittest.main()
