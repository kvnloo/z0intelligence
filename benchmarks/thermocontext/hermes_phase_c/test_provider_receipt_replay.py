"""Offline receipt audit conformance; mutations are never provider trials."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from provider_receipt_replay import audit_route, canonical, digest, replay_run, HERE


class ProviderReceiptReplayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "run"
        self.run.mkdir()
        (self.run / "prepared").mkdir()
        self.snapshots = self.root / "snapshots"
        self.snapshots.mkdir()
        self.catalog = self.root / "catalog.json"
        self.original = HERE / "results/live-baseline-03-resource4096"
        (self.snapshots / "baseline_check.py").write_bytes((HERE / "baseline_check.py").read_bytes())
        (self.snapshots / "live_baseline.py").write_bytes((HERE / "live_baseline.py").read_bytes())
        self.selection = json.loads((self.original / "prepared/selection.json").read_text())
        self.prompt = (self.original / "prompt.txt").read_text()
        (self.run / "prompt.txt").write_text(self.prompt)
        self.write("prepared/selection.json", self.selection)
        self.answer = {
            "tested_source": "a0bca744067d04f05904319d3d919be30c336556",
            "baseline_verified": 40, "baseline_attempted": 40,
            "guarded_verified": 40, "guarded_attempted": 40,
            "baseline_provider_requests_per_task": 2,
            "guarded_provider_requests_per_task": 1,
            "hermes_speedup_established": False, "scope": "single_fixture_python_typesafe",
            "citations": ["limits", "source-identity", "baseline", "guarded"],
        }
        self.verdict = {"verified_success": True, "problems": [],
                        "scope": "one_inspected_development_claim_not_population_noninferiority"}
        self.build()

    def write(self, name, value):
        (self.run / name).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")

    def read(self, name):
        return json.loads((self.run / name).read_text())

    def build(self, provider="nous", paid=False, cost=0):
        model = "test/fixed-model" if paid else "test/fixed-model:free"
        endpoint = ("https://inference-api.nousresearch.com/v1/chat/completions" if provider == "nous"
                    else "https://openrouter.ai/api/v1/chat/completions")
        catalog_row = {"id": model, "pricing": {"prompt": "0.00000005" if paid else "0",
                                                "completion": "0.0000002" if paid else "0"}}
        self.catalog.write_text(json.dumps({"data": [catalog_row]}))
        route = {"schema_version": 1, "provider": provider, "model": model, "endpoint": endpoint,
                 "pricing": {"prompt_usd_per_million": .05 if paid else 0,
                             "completion_usd_per_million": .2 if paid else 0,
                             "source_url": endpoint.removesuffix("chat/completions") + "models",
                             "retrieved_at": "2026-10-02T17:00:00+00:00",
                             "catalog_sha256": digest(self.catalog.read_bytes())},
                 "max_estimated_cost_usd": .02 if provider == "nous" else 0}
        estimate = .0018192 if paid else 0
        freeze = self.read_original_freeze()
        freeze.update(route_spec=route, route_sha256=digest(canonical(route)), model=model, endpoint=endpoint,
                      driver_sha256=digest((self.snapshots / "live_baseline.py").read_bytes()),
                      estimated_cost_at_token_caps_usd=estimate, max_paid_cost_usd=0 if provider == "openrouter" else None,
                      cost_control="openrouter_max_price_zero" if provider == "openrouter" else "fixed_model_catalog_estimate_only",
                      catalog_estimate_is_billing_cap=False)
        native = {"model": model, "max_tokens": 4096, "stream": False,
                  "messages": [{"role": "user", "content": self.prompt + "\n\n" + self.selection["context"]}]}
        forwarded = copy.deepcopy(native)
        if provider == "openrouter":
            forwarded.update(provider={"allow_fallbacks": False, "max_price": {"prompt": 0, "completion": 0}},
                             usage={"include": True})
        usage = {"prompt_tokens": 7500, "completion_tokens": 200, "total_tokens": 7700}
        if cost is not None:
            usage["cost"] = cost
        response = {"model": model, "usage": usage,
                    "choices": [{"message": {"content": json.dumps(self.answer)}, "finish_reason": "stop"}]}
        if provider == "openrouter":
            response["provider"] = "Reported Vendor"
        call = {"kind": "inference", "method": "POST", "endpoint": endpoint,
                "physical_attempt": 1, "upstream_sent": True, "status_code": 200,
                "native_request": native, "forwarded_request": forwarded,
                "response": response, "usage": usage, "served_model": model,
                "served_provider": response.get("provider")}
        receipt = {"answer_parsed": True, "outcome": self.verdict, "provider_calls": 1,
                   "local_metadata_calls": 0, "served_model": model, "served_provider": response.get("provider"),
                   "requested_provider": provider, "requested_model": model,
                   "provider_identity_source": "response" if response.get("provider") else "configured_endpoint_only",
                   "route_sha256": freeze["route_sha256"], "estimated_cost_at_token_caps_usd": estimate,
                   "usage": usage, "reported_cost": cost, "reported_zero_cost": cost == 0 if cost is not None else None,
                   "resource_comparison_eligible": cost is not None and cost <= route["max_estimated_cost_usd"],
                   "configured_max_output_tokens": 4096, "exact_selected_context_in_forwarded_request": True,
                   "sampler_enabled": False, "speedup_or_noninferiority_claim": False}
        self.write("route.json", route)
        self.write("freeze.json", freeze)
        self.write("receipt.json", receipt)
        self.write("physical-calls.json", [call])
        self.write("answer.json", self.answer)
        self.write("worker-result.json", {"result": {"final_response": json.dumps(self.answer)}})
        self.refresh_wire()
        self.refresh_freeze()

    def read_original_freeze(self):
        return json.loads((self.original / "freeze.json").read_text())

    def refresh_freeze(self):
        receipt = self.read("receipt.json")
        receipt["freeze_sha256"] = digest((self.run / "freeze.json").read_bytes())
        self.write("receipt.json", receipt)

    def refresh_route(self):
        frozen = self.read("freeze.json")
        frozen["route_sha256"] = digest(canonical(frozen["route_spec"]))
        self.write("freeze.json", frozen)
        self.write("route.json", frozen["route_spec"])
        receipt = self.read("receipt.json")
        receipt["route_sha256"] = frozen["route_sha256"]
        self.write("receipt.json", receipt)
        self.refresh_freeze()

    def refresh_wire(self):
        calls = self.read("physical-calls.json")
        for prefix in ("native", "forwarded"):
            raw = json.dumps(calls[0][prefix + "_request"]).encode()
            (self.run / (prefix + "-request.bin")).write_bytes(raw)
            calls[0][prefix + "_wire_sha256"] = digest(raw)
            calls[0][prefix + "_wire_bytes"] = len(raw)
        self.write("physical-calls.json", calls)

    def audit(self):
        # Even an accidental networking change must fail these offline tests.
        with patch("socket.socket", side_effect=AssertionError("network forbidden")):
            return replay_run(self.run, self.snapshots, [self.catalog])

    def assert_failed(self, key, section="comparisons"):
        report = self.audit()
        self.assertFalse(report["replay_matches_saved_evidence"])
        self.assertFalse(report[section][key])

    def test_nous_free_nous_paid_and_named_openrouter_replay(self):
        for provider, paid, cost in (("nous", False, 0), ("nous", True, .0004), ("openrouter", False, 0)):
            with self.subTest(provider=provider, paid=paid):
                self.build(provider, paid, cost)
                report = self.audit()
                self.assertTrue(report["replay_matches_saved_evidence"], report)
                self.assertTrue(report["verified_success"])
                self.assertTrue(report["resource_comparison_eligible_recomputed"])
                self.assertEqual(report["provider_calls_during_replay"], 0)

    def test_missing_cost_does_not_hide_factual_success_or_become_zero(self):
        self.build(cost=None)
        report = self.audit()
        self.assertTrue(report["replay_matches_saved_evidence"])
        self.assertTrue(report["verified_success"])
        self.assertIsNone(report["reported_zero_cost"])
        self.assertFalse(report["resource_comparison_eligible_recomputed"])
        receipt = self.read("receipt.json")
        receipt.update(reported_cost=0, reported_zero_cost=True, resource_comparison_eligible=True)
        self.write("receipt.json", receipt)
        self.assert_failed("cost")

    def test_positive_cost_outside_policy_is_ineligible(self):
        self.build(paid=True, cost=.03)
        report = self.audit()
        self.assertTrue(report["replay_matches_saved_evidence"])
        self.assertFalse(report["resource_comparison_eligible_recomputed"])
        self.assertIn("REPORTED_COST_OUTSIDE_POLICY", report["disposition"])

    def test_self_consistent_price_edit_still_requires_raw_catalog_row(self):
        self.build(paid=True, cost=.0004)
        frozen = self.read("freeze.json")
        frozen["route_spec"]["pricing"]["prompt_usd_per_million"] = 0
        self.write("freeze.json", frozen)
        self.refresh_route()
        with self.assertRaisesRegex(ValueError, "raw catalog"):
            self.audit()

    def test_freeze_estimate_is_recomputed_independently(self):
        self.build(paid=True, cost=.0004)
        frozen = self.read("freeze.json")
        frozen["estimated_cost_at_token_caps_usd"] = 0
        self.write("freeze.json", frozen)
        self.refresh_freeze()
        self.assert_failed("catalog_estimate")

    def test_catalog_estimate_cannot_be_described_as_billing_cap(self):
        frozen = self.read("freeze.json")
        frozen["max_paid_cost_usd"] = .02
        frozen["catalog_estimate_is_billing_cap"] = True
        self.write("freeze.json", frozen)
        self.refresh_freeze()
        self.assert_failed("estimate_not_billing_cap")

    def test_self_consistent_context_change_rejects_original_task_binding(self):
        selection = self.read("prepared/selection.json")
        old_context = selection["context"]
        selection["context"] += " edited evidence"
        selection["context_sha256"] = digest(selection["context"].encode())
        self.write("prepared/selection.json", selection)
        frozen = self.read("freeze.json")
        frozen["context_sha256"] = selection["context_sha256"]
        self.write("freeze.json", frozen)
        calls = self.read("physical-calls.json")
        for key in ("native_request", "forwarded_request"):
            calls[0][key]["messages"][0]["content"] = calls[0][key]["messages"][0]["content"].replace(old_context, selection["context"])
        self.write("physical-calls.json", calls)
        self.refresh_wire()
        self.refresh_freeze()
        self.assert_failed("frozen_original_context", "source_bindings")

    def test_self_consistent_prompt_change_rejects_original_task_binding(self):
        prompt = self.prompt + " Return something else."
        (self.run / "prompt.txt").write_text(prompt)
        frozen = self.read("freeze.json")
        frozen["prompt_sha256"] = digest(prompt.encode())
        self.write("freeze.json", frozen)
        self.refresh_freeze()
        self.assert_failed("frozen_original_prompt", "source_bindings")

    def test_changed_model_on_wire_is_rejected(self):
        calls = self.read("physical-calls.json")
        for key in ("native_request", "forwarded_request"):
            calls[0][key]["model"] = "another/fixed-model"
        self.write("physical-calls.json", calls)
        self.refresh_wire()
        self.assert_failed("route_and_endpoint")

    def test_endpoint_or_unnamed_router_is_rejected(self):
        for field, value in (("endpoint", "https://example.com/v1/chat/completions"), ("model", "openrouter/free")):
            with self.subTest(field=field):
                route = copy.deepcopy(self.read("route.json"))
                route[field] = value
                with self.assertRaises(ValueError):
                    audit_route(route, [self.catalog])

    def test_duplicate_catalog_rows_and_missing_prices_are_rejected(self):
        row = json.loads(self.catalog.read_text())["data"][0]
        for rows in ([row, row], [{"id": row["id"], "pricing": {"prompt": "0"}}]):
            with self.subTest(rows=rows):
                self.catalog.write_text(json.dumps({"data": rows}))
                route = self.read("route.json")
                route["pricing"]["catalog_sha256"] = digest(self.catalog.read_bytes())
                with self.assertRaises(ValueError):
                    audit_route(route, [self.catalog])

    def test_changed_raw_catalog_bytes_cannot_match_declared_digest(self):
        self.catalog.write_text(self.catalog.read_text() + "\n")
        with self.assertRaisesRegex(ValueError, "catalog digest"):
            self.audit()

    def test_raw_wire_and_driver_snapshots_are_bound(self):
        raw = self.run / "native-request.bin"
        raw.write_bytes(raw.read_bytes() + b"\n")
        self.assert_failed("native_wire_bytes", "source_bindings")
        self.refresh_wire()
        source = self.snapshots / "live_baseline.py"
        source.write_bytes(source.read_bytes() + b"\n")
        self.assert_failed("exact_driver_bytes", "source_bindings")

    def test_changed_checker_never_executes_even_with_updated_freeze(self):
        checker = self.snapshots / "baseline_check.py"
        checker.write_text("raise AssertionError('untrusted checker executed')\n")
        frozen = self.read("freeze.json")
        frozen["checker_sha256"] = digest(checker.read_bytes())
        self.write("freeze.json", frozen)
        self.refresh_freeze()
        with self.assertRaisesRegex(ValueError, "refusing to execute"):
            self.audit()

    def test_duplicate_physical_post_and_local_retries_are_rejected(self):
        calls = self.read("physical-calls.json")
        self.write("physical-calls.json", calls * 2)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.audit()
        self.write("physical-calls.json", calls + [{"kind": "blocked_inference", "upstream_sent": False}])
        self.assert_failed("no_repeated_local_inference")

    def test_forged_provider_identity_is_rejected(self):
        receipt = self.read("receipt.json")
        receipt["served_provider"] = "nous"
        receipt["provider_identity_source"] = "response"
        self.write("receipt.json", receipt)
        self.assert_failed("served_identity")

    def test_worker_answer_cannot_replace_actual_provider_output(self):
        calls = self.read("physical-calls.json")
        wrong = {**self.answer, "guarded_verified": 39}
        calls[0]["response"]["choices"][0]["message"]["content"] = json.dumps(wrong)
        self.write("physical-calls.json", calls)
        self.assert_failed("provider_output_binding")

    def test_saved_grade_is_not_trusted(self):
        wrong = {**self.answer, "guarded_verified": 39}
        calls = self.read("physical-calls.json")
        calls[0]["response"]["choices"][0]["message"]["content"] = json.dumps(wrong)
        self.write("physical-calls.json", calls)
        self.write("answer.json", wrong)
        self.write("worker-result.json", {"result": {"final_response": json.dumps(wrong)}})
        report = self.audit()
        self.assertFalse(report["verified_success"])
        self.assertFalse(report["comparisons"]["independent_outcome"])

    def test_nous_cannot_inherit_openrouter_fields(self):
        calls = self.read("physical-calls.json")
        for key in ("native_request", "forwarded_request"):
            calls[0][key]["usage"] = {"include": True}
        self.write("physical-calls.json", calls)
        self.refresh_wire()
        self.assert_failed("provider_specific_forwarding")

    def test_json_object_mode_is_opt_in_and_exact(self):
        calls = self.read("physical-calls.json")
        for key in ("native_request", "forwarded_request"):
            calls[0][key]["response_format"] = {"type": "json_object"}
        self.write("physical-calls.json", calls)
        self.refresh_wire()
        self.assert_failed("explicit_response_format")
        frozen = self.read("freeze.json")
        frozen["response_format_requested"] = {"type": "json_object"}
        self.write("freeze.json", frozen)
        self.refresh_freeze()
        self.assertTrue(self.audit()["replay_matches_saved_evidence"])
        calls = self.read("physical-calls.json")
        calls[0]["native_request"]["response_format"] = {"type": "text"}
        self.write("physical-calls.json", calls)
        self.refresh_wire()
        self.assert_failed("explicit_response_format")


if __name__ == "__main__":
    unittest.main()
