"""Tests for the Julia-1 DecisionBackend adapter and the shared named-question transport.

The live tests only run when both the pinned Julia interpreter and the complete
checkout are present, so this file is green on a machine with neither.
"""

from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from z0int.backends.base import (
    DecisionOption,
    DecisionQuestion,
    DecisionRequest,
)
from z0int.backends.julia import JuliaBackend, missing_entries
from z0int.backends.named_questions import (
    answer_from_named,
    normalize_probs,
    question_to_named,
)

JULIA_PYTHON = os.environ.get("Z0INT_JULIA_PYTHON", "/home/kvn/tmp/julia-venv/bin/python")
JULIA_MODEL = os.environ.get("Z0INT_JULIA_MODEL_DIR", "/home/kvn/tmp/Julia-1")
REVISION = "a85b127321d580d65176c89ced8273f305745d85"


def _checkout_present() -> bool:
    return Path(JULIA_MODEL, "julia_config.json").is_file()


def _interpreter_present() -> bool:
    return Path(JULIA_PYTHON).exists()


class NamedQuestionTransportTests(unittest.TestCase):
    """The one mapping shared by julia, laya and decider."""

    def test_choice_criteria_are_keyed_by_caller_ids(self):
        q = DecisionQuestion(id="decision", type="choice", instructions="pick",
                             options=(DecisionOption("a", "A"), DecisionOption("b", "B")))
        out = question_to_named(q)
        self.assertEqual(out["type"], "choice")
        self.assertEqual(out["criteria"], {"a": "A", "b": "B"})
        self.assertEqual(out["instructions"], "pick")

    def test_boolean_maps_onto_noul_with_both_criteria(self):
        q = DecisionQuestion(id="d", type="boolean", instructions="verify?",
                             false_criterion="no verify", true_criterion="verify")
        out = question_to_named(q)
        self.assertEqual(out["type"], "noul")
        self.assertEqual(out["criteria"], {"false": "no verify", "true": "verify"})

    def test_boolean_defaults_when_criteria_absent(self):
        q = DecisionQuestion(id="d", type="boolean", instructions="verify?")
        out = question_to_named(q)
        self.assertEqual(sorted(out["criteria"]), ["false", "true"])
        self.assertTrue(out["criteria"]["false"])
        self.assertTrue(out["criteria"]["true"])

    def test_score_criteria_is_the_ordered_level_list(self):
        q = DecisionQuestion(id="d", type="score", instructions="how urgent?",
                             levels=("low", "medium", "high"))
        out = question_to_named(q)
        self.assertEqual(out["type"], "score")
        self.assertEqual(out["criteria"], ["low", "medium", "high"])

    def test_answer_choice_roundtrip(self):
        q = DecisionQuestion(id="d", type="choice", instructions="pick",
                             options=(DecisionOption("a", "A"), DecisionOption("b", "B")))
        ans = answer_from_named(q, {"type": "choice", "choice": "a",
                                    "probabilities": {"a": 0.7, "b": 0.3},
                                    "max_probability": 0.7}, source="test")
        self.assertEqual(ans.type, "choice")
        self.assertEqual(ans.value, "a")
        self.assertAlmostEqual(sum(ans.probabilities.values()), 1.0, places=6)

    def test_answer_boolean_uses_noul_and_returns_real_bool(self):
        q = DecisionQuestion(id="d", type="boolean", instructions="verify?")
        ans = answer_from_named(q, {"type": "noul", "noul": 0.8}, source="test")
        self.assertIsInstance(ans.value, bool)
        self.assertTrue(ans.value)
        self.assertAlmostEqual(ans.probabilities["true"], 0.8)
        self.assertAlmostEqual(ans.probabilities["false"], 0.2)

    def test_answer_boolean_below_half_is_false(self):
        q = DecisionQuestion(id="d", type="boolean", instructions="verify?")
        ans = answer_from_named(q, {"type": "noul", "noul": 0.4513}, source="test")
        self.assertFalse(ans.value)

    def test_answer_score_uses_expected_index_not_argmax(self):
        # Julia returns an unrounded expected rubric index; it must survive as a float.
        q = DecisionQuestion(id="d", type="score", instructions="how urgent?",
                             levels=("a", "b", "c", "d", "e"))
        ans = answer_from_named(q, {"type": "score", "score": 2.3619,
                                    "probabilities": {"0": 0.0051, "1": 0.1247,
                                                      "2": 0.5713, "3": 0.1011,
                                                      "4": 0.1978}}, source="test")
        self.assertEqual(ans.type, "score")
        self.assertAlmostEqual(ans.value, 2.3619, places=4)
        self.assertNotEqual(ans.value, round(ans.value))

    def test_empty_probability_mass_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_probs({"a": 0.0, "b": 0.0}, source="test")

    def test_non_finite_probability_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_probs({"a": float("nan"), "b": 0.5}, source="test")


class JuliaCapabilityTests(unittest.TestCase):
    def test_registry_aliases_resolve_to_julia_1(self):
        from z0int.backends.registry import resolve_backend_id

        self.assertEqual(resolve_backend_id("julia_1"), "julia_1")
        self.assertEqual(resolve_backend_id("julia"), "julia_1")
        self.assertEqual(resolve_backend_id("julia-1"), "julia_1")

    def test_capabilities_declare_truthful_limits(self):
        backend = JuliaBackend(hf="SupersonicLabs/Julia-1", revision=REVISION)
        caps = backend.capabilities
        self.assertTrue(caps.supports_choice)
        self.assertTrue(caps.supports_boolean)
        self.assertTrue(caps.supports_score)
        self.assertTrue(caps.local)
        # Julia's own wire contract; z0int choice allows 255 but Julia does not.
        self.assertEqual(caps.max_choice_options, 20)
        # z0int's DecisionQuestion binds score at 10 before Julia's 20 is reachable.
        self.assertEqual(caps.max_score_levels, 10)
        self.assertFalse(caps.autoregressive_decode)
        self.assertTrue(caps.returns_distribution)

    def test_manifest_entry_is_pinned(self):
        from z0int.models_mgmt import load_manifest

        meta = (load_manifest().get("models") or {}).get("julia_1") or {}
        self.assertEqual(meta.get("hf"), "SupersonicLabs/Julia-1")
        self.assertEqual(meta.get("revision"), REVISION)
        self.assertEqual(len(str(meta.get("revision"))), 40)
        self.assertEqual(meta.get("license"), "apache-2.0")
        self.assertTrue(meta.get("optional"))
        self.assertEqual(meta.get("min_vram_gb"), 0)

    def test_julia_is_in_the_decision_roster(self):
        from z0int.models_mgmt import decision_roster

        roster = decision_roster()
        self.assertIn("julia_1", roster["candidates"])
        self.assertEqual(roster["entries"]["julia_1"]["hf"], "SupersonicLabs/Julia-1")

    def test_required_entries_include_the_runtime_code(self):
        # Julia is not a Transformers AutoModel: the checkout must ship its runtime.
        needed = missing_entries(Path("/nonexistent/julia"))
        self.assertIn("model.safetensors", needed)
        self.assertIn("julia_config.json", needed)
        self.assertIn("julia/__init__.py", needed)

    def test_routing_is_unchanged_by_default(self):
        """Registering the backend must not let the router select it.

        ``intelligence.route`` chooses from manifests/capabilities.v1.json. Julia has
        no capability entry, so it is reachable only when a caller names it
        explicitly. This is the "existing behaviour remains unchanged by default"
        guarantee, asserted rather than assumed.
        """
        import json
        from pathlib import Path as _Path

        manifests = _Path(__file__).resolve().parents[1] / "manifests"
        entries = json.loads((manifests / "capabilities.v1.json").read_text())["entries"]
        providers = {str(e.get("provider")) for e in entries}
        functions = {str(e.get("function")) for e in entries}
        self.assertNotIn("julia", providers)
        self.assertNotIn("julia_1", providers)
        self.assertFalse(any("julia" in f for f in functions),
                         f"a julia capability entry would let the router select it: {functions}")
        # and it is still reachable by name
        from z0int.backends.registry import resolve_backend_id

        self.assertEqual(resolve_backend_id("julia"), "julia_1")


class JuliaBackendOfflineTests(unittest.TestCase):
    def test_unavailable_without_checkout(self):
        backend = JuliaBackend(hf="SupersonicLabs/Julia-1", revision=REVISION,
                               model_dir=Path("/nonexistent/julia"))
        health = backend.health(load=False)
        self.assertFalse(health.ready)
        self.assertFalse(health.loaded)
        self.assertIn("incomplete Julia checkout", health.detail)
        self.assertIn("model.safetensors", health.diagnostics["missing_entries"])

    def test_health_reports_the_pinned_interpreter(self):
        backend = JuliaBackend(hf="SupersonicLabs/Julia-1", revision=REVISION,
                               model_dir=Path(JULIA_MODEL))
        health = backend.health(load=False)
        self.assertIn("python", health.diagnostics)
        if _checkout_present() and _interpreter_present():
            self.assertTrue(health.ready, health.detail)
            self.assertEqual(health.diagnostics["python"], JULIA_PYTHON)

    def test_evaluate_translates_a_worker_reply_without_spawning(self):
        backend = JuliaBackend(hf="SupersonicLabs/Julia-1", revision=REVISION,
                               model_dir=Path("/tmp/julia-mock"))
        fake_worker = mock.Mock()
        fake_worker.requests = 1
        fake_worker.process.pid = 4242
        reply = {
            "id": "t1", "ok": True, "latency_ms": 12.5, "load_ms": 4000.0,
            "device": "cpu",
            "answers": {
                "pick": {"type": "choice", "choice": "native",
                         "probabilities": {"native": 0.6, "worker": 0.4},
                         "max_probability": 0.6},
                "gate": {"type": "noul", "noul": 0.55,
                         "probabilities": {"false": 0.45, "true": 0.55}},
                "urg": {"type": "score", "score": 1.25,
                        "probabilities": {"0": 0.25, "1": 0.5, "2": 0.25}},
            },
        }
        req = DecisionRequest(
            state={"x": 1},
            questions=(
                DecisionQuestion(id="pick", type="choice", instructions="pick",
                                 options=(DecisionOption("native", "native"),
                                          DecisionOption("worker", "worker"))),
                DecisionQuestion(id="gate", type="boolean", instructions="gate?"),
                DecisionQuestion(id="urg", type="score", instructions="urgent?",
                                 levels=("low", "mid", "high")),
            ),
            request_id="t1",
        )
        with mock.patch.object(JuliaBackend, "_ensure_worker", return_value=fake_worker), \
             mock.patch.object(JuliaBackend, "_request", return_value=reply):
            result = backend.evaluate(req)

        by_id = {a.question_id: a for a in result.answers}
        self.assertEqual(by_id["pick"].value, "native")
        self.assertIs(by_id["gate"].value, True)
        self.assertAlmostEqual(by_id["urg"].value, 1.25, places=4)
        self.assertEqual(result.backend, "julia_1")
        self.assertEqual(result.revision, REVISION)
        self.assertIn("model_sha256", result.diagnostics)
        self.assertEqual(result.diagnostics["placement"], "cpu")

    def test_worker_failure_surfaces_as_value_error(self):
        backend = JuliaBackend(hf="SupersonicLabs/Julia-1", revision=REVISION,
                               model_dir=Path("/tmp/julia-mock"))
        req = DecisionRequest(
            state="x",
            questions=(DecisionQuestion(id="d", type="boolean", instructions="?"),),
        )
        with mock.patch.object(JuliaBackend, "_ensure_worker", return_value=mock.Mock()), \
             mock.patch.object(JuliaBackend, "_request",
                               side_effect=ValueError("julia: options must contain 2–20")):
            with self.assertRaises(ValueError):
                backend.evaluate(req)


@unittest.skipUnless(
    _checkout_present() and _interpreter_present(),
    f"needs the pinned Julia interpreter ({JULIA_PYTHON}) and checkout ({JULIA_MODEL})",
)
class JuliaLiveTests(unittest.TestCase):
    """End-to-end through the resident worker. These load ~1GB and take ~15s."""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("Z0INT_JULIA_MODEL_DIR", JULIA_MODEL)
        os.environ.setdefault("Z0INT_JULIA_PYTHON", JULIA_PYTHON)
        cls.backend = JuliaBackend.for_manifest_id("julia_1")
        cls.backend.max_length = 2048
        health = cls.backend.health(load=True)
        if not health.loaded:
            raise unittest.SkipTest(f"Julia did not load: {health.detail}")

    @classmethod
    def tearDownClass(cls):
        cls.backend.close()

    def test_health_reports_loaded_on_cpu(self):
        health = self.backend.health(load=True)
        self.assertTrue(health.ready)
        self.assertTrue(health.loaded)
        self.assertEqual(health.diagnostics["python"], JULIA_PYTHON)
        self.assertIn("5.0", health.diagnostics["python_detail"])

    def test_live_choice_score_and_boolean(self):
        req = DecisionRequest(
            state="I was charged twice for the same order and the second charge is pending.",
            questions=(
                DecisionQuestion(id="team", type="choice",
                                 instructions="Which team should handle this request?",
                                 options=(DecisionOption("billing", "Billing and payment disputes"),
                                          DecisionOption("shipping", "Shipping and delivery"),
                                          DecisionOption("access", "Account access and login"))),
                DecisionQuestion(id="gate", type="boolean",
                                 instructions="Does this request require a refund decision?",
                                 false_criterion="No refund decision is required",
                                 true_criterion="A refund decision is required"),
                DecisionQuestion(id="urg", type="score",
                                 instructions="How urgent is this request?",
                                 levels=("no urgency", "low", "moderate", "high", "immediate")),
            ),
            request_id="live-1",
        )
        result = self.backend.evaluate(req)
        by_id = {a.question_id: a for a in result.answers}

        self.assertEqual(sorted(by_id["team"].probabilities), ["access", "billing", "shipping"])
        self.assertIn(by_id["team"].value, by_id["team"].probabilities)
        self.assertIsInstance(by_id["gate"].value, bool)
        self.assertEqual(sorted(by_id["gate"].probabilities), ["false", "true"])
        self.assertAlmostEqual(sum(by_id["urg"].probabilities.values()), 1.0, places=5)
        for a in result.answers:
            self.assertAlmostEqual(sum(a.probabilities.values()), 1.0, places=5)

        # deterministic across a repeated call
        again = self.backend.evaluate(req)
        self.assertEqual(again.answers[0].value, by_id["team"].value)
        self.assertAlmostEqual(
            again.answers[0].probabilities[by_id["team"].value],
            by_id["team"].probabilities[by_id["team"].value], places=9)

    def test_worker_is_resident_across_calls(self):
        first = self.backend.evaluate(DecisionRequest(
            state="x", questions=(DecisionQuestion(id="d", type="boolean", instructions="?"),)))
        second = self.backend.evaluate(DecisionRequest(
            state="y", questions=(DecisionQuestion(id="d", type="boolean", instructions="?"),)))
        self.assertEqual(first.diagnostics["load_ms"], second.diagnostics["load_ms"])
        self.assertGreater(second.diagnostics["worker_requests"],
                           first.diagnostics["worker_requests"])

    def test_over_twenty_options_is_rejected_not_truncated(self):
        options = tuple(DecisionOption(f"o{i}", f"Option {i}") for i in range(21))
        req = DecisionRequest(
            state="choose", questions=(DecisionQuestion(id="r", type="choice",
                                                        instructions="which?", options=options),))
        with self.assertRaises(ValueError) as ctx:
            self.backend.evaluate(req)
        self.assertIn("2", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
