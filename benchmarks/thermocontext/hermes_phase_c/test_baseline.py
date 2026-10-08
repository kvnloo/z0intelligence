import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from baseline_check import grade
from live_baseline import BudgetRejected, OneRequestPolicy, prepare_inputs, study_spill_config


class BaselinePolicyTests(unittest.TestCase):
    def body(self):
        return {"model": "openrouter/free", "max_tokens": 1024, "stream": False,
                "messages": [{"role": "user", "content": "public frozen task"}]}

    def test_one_attempt_and_free_only_provider_constraints(self):
        policy = OneRequestPolicy(expected_context="public frozen task")
        body = policy.admit(json.dumps(self.body()).encode())
        self.assertEqual(body["provider"], {"allow_fallbacks": False, "max_price": {"prompt": 0, "completion": 0}})
        self.assertEqual(body["usage"], {"include": True})
        with self.assertRaises(BudgetRejected):
            policy.admit(json.dumps(self.body()).encode())

    def test_paid_route_tools_streaming_output_and_fallback_are_rejected(self):
        for field, value in (("model", "paid/model"), ("tools", [{"name": "write"}]),
                             ("stream", True), ("max_tokens", 1025), ("max_tokens", None),
                             ("models", ["fallback/model"]), ("route", "fallback")):
            with self.subTest(field=field, value=value), self.assertRaises(BudgetRejected):
                OneRequestPolicy(expected_context="public frozen task").admit(json.dumps({**self.body(), field: value}).encode())

    def test_oversized_request_cannot_spend_attempt(self):
        policy = OneRequestPolicy(expected_context="public frozen task")
        with self.assertRaises(BudgetRejected):
            policy.admit(json.dumps({**self.body(), "messages": [{"content": "x" * 20001}]}).encode())
        self.assertFalse(policy.used)

    def test_permissive_provider_fields_are_replaced(self):
        body = self.body()
        body["provider"] = {"allow_fallbacks": True, "max_price": {"prompt": 100}}
        self.assertEqual(OneRequestPolicy(expected_context="public frozen task").admit(json.dumps(body).encode())["provider"]["max_price"]["prompt"], 0)

    def test_resource_adequacy_cap_is_explicit_and_default_remains_1024(self):
        larger = {**self.body(), "max_tokens": 4096}
        with self.assertRaises(BudgetRejected):
            OneRequestPolicy(expected_context="public frozen task").admit(json.dumps(larger).encode())
        admitted = OneRequestPolicy(expected_context="public frozen task", max_output_tokens=4096).admit(json.dumps(larger).encode())
        self.assertEqual(admitted["max_tokens"], 4096)
        with self.assertRaises(BudgetRejected):
            OneRequestPolicy(expected_context="public frozen task", max_output_tokens=4096).admit(
                json.dumps({**larger, "max_tokens": 4097}).encode())

    def test_invalid_resource_caps_are_rejected_before_use(self):
        for cap in (0, -1, 4097, True, "4096", None):
            with self.subTest(cap=cap), self.assertRaises(BudgetRejected):
                OneRequestPolicy(expected_context="public frozen task", max_output_tokens=cap)

    def test_missing_changed_or_duplicate_context_cannot_spend_attempt(self):
        for content in ("preview and inaccessible file", "public changed task", "public frozen task" * 2):
            policy = OneRequestPolicy(expected_context="public frozen task")
            with self.subTest(content=content), self.assertRaises(BudgetRejected):
                policy.admit(json.dumps({**self.body(), "messages": [{"role": "user", "content": content}]}).encode())
            self.assertFalse(policy.used)

    def test_exact_multimodal_text_block_passes_without_stringifying_python(self):
        body = self.body()
        body["messages"][0]["content"] = [{"type": "text", "text": "public frozen task"}]
        self.assertTrue(OneRequestPolicy(expected_context="public frozen task").admit(json.dumps(body).encode()))

    @unittest.skipUnless(os.environ.get("HERMES_SOURCE_TREE"), "Native Hermes spill-path regression")
    def test_actual_native_hook_default_spills_and_study_override_preserves_context(self):
        import sys
        sys.path.insert(0, os.environ["HERMES_SOURCE_TREE"])
        from agent.turn_context import _collect_pre_llm_call_context
        context = "H" * 6000 + "decisive-evidence-middle" + "T" * 6000
        agent = SimpleNamespace(session_id="admission-regression", model="NOT_RUN")
        def collect(config):
            with patch("hermes_cli.lifecycle.invoke_hook", return_value=[{"context": context}]), \
                    patch("tools.hook_output_spill.get_spill_config", return_value=config):
                return _collect_pre_llm_call_context(agent, effective_task_id="offline", turn_id="1",
                    original_user_message="frozen task", messages=[], conversation_history=[])
        with tempfile.TemporaryDirectory() as tmp:
            default = {"enabled": True, "max_chars": 10000, "preview_head": 500, "preview_tail": 500, "directory": tmp}
            lost = collect(default)
            self.assertIn("output truncated", lost)
            self.assertNotIn("decisive-evidence-middle", lost)
            policy = OneRequestPolicy(expected_context=context)
            with self.assertRaises(BudgetRejected):
                policy.admit(json.dumps({**self.body(), "messages": [{"role": "user", "content": lost}]}).encode())
            self.assertFalse(policy.used)
            kept = collect({**study_spill_config(context), "directory": tmp})
            self.assertEqual(kept, context)
            self.assertTrue(policy.admit(json.dumps({**self.body(), "messages": [{"role": "user", "content": kept}]}).encode()))


class BaselineCheckerTests(unittest.TestCase):
    def answer(self):
        return {"tested_source": "a0bca744067d04f05904319d3d919be30c336556",
                "baseline_verified": 40, "baseline_attempted": 40,
                "guarded_verified": 40, "guarded_attempted": 40,
                "baseline_provider_requests_per_task": 2,
                "guarded_provider_requests_per_task": 1,
                "hermes_speedup_established": False,
                "scope": "single_fixture_python_typesafe",
                "citations": ["limits", "source-identity", "baseline", "guarded"]}

    def test_supported_facts_pass(self):
        self.assertTrue(grade(self.answer())["verified_success"])

    def test_schema_valid_wrong_scope_wrong_source_and_wrong_facts_fail(self):
        for field, value in (("hermes_speedup_established", True), ("scope", "general_hermes_production"),
                             ("tested_source", "6bab214abb707a3db9e8a7d641d160c3f58e08d5"),
                             ("guarded_verified", 39), ("guarded_provider_requests_per_task", True)):
            with self.subTest(field=field):
                self.assertFalse(grade({**self.answer(), field: value})["verified_success"])

    def test_unknown_or_missing_citations_and_extra_claims_fail(self):
        for citations in ([], ["invented"], None):
            self.assertFalse(grade({**self.answer(), "citations": citations})["verified_success"])
        self.assertFalse(grade({**self.answer(), "new_claim": "production ready"})["verified_success"])

    def test_malformed_answer_fails(self):
        self.assertFalse(grade(None)["verified_success"])


class PreparedCaseTests(unittest.TestCase):
    def case(self, checker_source):
        return {"selection": {"context": "public evidence", "context_sha256": hashlib.sha256(b"public evidence").hexdigest()},
                "prompt": "separately frozen question", "checker": lambda answer: {"verified_success": False},
                "checker_source": checker_source, "metadata": {"case_id": "separate-control"}}

    def test_host_checker_and_metadata_stay_outside_model_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            source = out / "private_checker.py"
            source.write_text("SECRET_REFERENCE_SENTINEL = True\n")
            case = self.case(source)
            with patch("replay_bridge.verify_native_sources") as verifier:
                selection, prompt, callback, actual_source, metadata = prepare_inputs(
                    SimpleNamespace(out=out, hermes_repo=out), case)
            self.assertEqual(prompt, "separately frozen question")
            self.assertEqual(selection["context"], "public evidence")
            self.assertNotIn("SECRET_REFERENCE_SENTINEL", prompt + selection["context"])
            self.assertIs(callback, case["checker"])
            self.assertEqual(actual_source, source)
            self.assertEqual(metadata, {"case_id": "separate-control"})
            verifier.assert_called_once()

    def test_case_cannot_override_route_or_supply_changed_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            source = out / "private_checker.py"
            source.write_text("reference = True\n")
            for mutation in ("route", "digest"):
                case = self.case(source)
                if mutation == "route":
                    case["model"] = "paid/override"
                else:
                    case["selection"]["context"] = "changed"
                with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                    prepare_inputs(SimpleNamespace(out=out, hermes_repo=out), case)


if __name__ == "__main__":
    unittest.main()
