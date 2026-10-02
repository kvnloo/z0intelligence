import copy
import json
import unittest

from baseline_check import grade
from live_baseline import BudgetRejected, OneRequestPolicy


class BaselinePolicyTests(unittest.TestCase):
    def body(self):
        return {"model": "openrouter/free", "max_tokens": 1024, "stream": False,
                "messages": [{"role": "user", "content": "public frozen task"}]}

    def test_one_attempt_and_free_only_provider_constraints(self):
        policy = OneRequestPolicy()
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
                OneRequestPolicy().admit(json.dumps({**self.body(), field: value}).encode())

    def test_oversized_request_cannot_spend_attempt(self):
        policy = OneRequestPolicy()
        with self.assertRaises(BudgetRejected):
            policy.admit(json.dumps({**self.body(), "messages": [{"content": "x" * 20001}]}).encode())
        self.assertFalse(policy.used)

    def test_permissive_provider_fields_are_replaced(self):
        body = self.body()
        body["provider"] = {"allow_fallbacks": True, "max_price": {"prompt": 100}}
        self.assertEqual(OneRequestPolicy().admit(json.dumps(body).encode())["provider"]["max_price"]["prompt"], 0)


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


if __name__ == "__main__":
    unittest.main()
