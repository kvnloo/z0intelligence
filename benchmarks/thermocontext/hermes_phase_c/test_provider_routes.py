"""Offline route and credential-boundary checks; no provider calls or real secrets."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import live_baseline as driver


def route(provider="nous", model="example/small:free", prompt=0, completion=0):
    return {"schema_version": 1, "provider": provider, "model": model,
            "endpoint": driver.ENDPOINTS[provider], "max_estimated_cost_usd": 0.02 if provider == "nous" else 0,
            "pricing": {"prompt_usd_per_million": prompt, "completion_usd_per_million": completion,
                        "source_url": driver.CATALOG_URLS[provider], "retrieved_at": "2026-10-02T00:00:00+00:00",
                        "catalog_sha256": "a" * 64}}


class ProviderRouteTests(unittest.TestCase):
    def body(self, model):
        return {"model": model, "max_tokens": 4096, "stream": False,
                "messages": [{"role": "user", "content": "fixed public evidence"}]}

    def admit(self, chosen, body=None):
        policy = driver.OneRequestPolicy(expected_context="fixed public evidence", max_output_tokens=4096, route=chosen)
        return policy, policy.admit(json.dumps(self.body(chosen["model"]) if body is None else body).encode())

    def test_legacy_defaults_and_named_openrouter_free_keep_price_controls(self):
        self.assertEqual(driver.MODEL, "openrouter/free")
        self.assertEqual(driver.ENDPOINT, "https://openrouter.ai/api/v1/chat/completions")
        for chosen in (driver.default_route(), route("openrouter")):
            policy, admitted = self.admit(chosen)
            self.assertEqual(admitted["provider"], {"allow_fallbacks": False, "max_price": {"prompt": 0, "completion": 0}})
            self.assertEqual(admitted["usage"], {"include": True})
            with self.assertRaises(driver.BudgetRejected):
                policy.admit(json.dumps(self.body(chosen["model"])).encode())

    def test_nous_keeps_native_body_without_openrouter_fields(self):
        chosen = route(prompt=0.20, completion=0.50)
        policy, admitted = self.admit(chosen)
        self.assertEqual(admitted, self.body(chosen["model"]))
        self.assertAlmostEqual(driver.estimated_cost(chosen, 4096), 0.006048)
        with self.assertRaises(driver.BudgetRejected):
            policy.admit(json.dumps(admitted).encode())

    def test_invalid_endpoint_route_model_and_price_evidence_rejected(self):
        for field, value in (("schema_version", True), ("max_estimated_cost_usd", False)):
            chosen = driver.default_route()
            chosen[field] = value
            with self.subTest(legacy_field=field), self.assertRaises(driver.BudgetRejected):
                driver.validate_route(chosen)
        mutations = [("endpoint", "https://example.test/v1/chat/completions"),
                     ("provider", "unexpected"), ("model", "openrouter/auto"), ("model", "a?fallback=b"),
                     ("schema_version", True), ("max_estimated_cost_usd", 0.03),
                     ("max_estimated_cost_usd", float("nan"))]
        for field, value in mutations:
            chosen = route()
            chosen[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(driver.BudgetRejected):
                driver.validate_route(chosen, 4096)
        for field, value in (("prompt_usd_per_million", 0.21), ("completion_usd_per_million", 0.51),
                             ("prompt_usd_per_million", -1), ("completion_usd_per_million", float("inf")),
                             ("prompt_usd_per_million", True), ("catalog_sha256", "invalid"),
                             ("source_url", "https://example.test/models"), ("retrieved_at", "2026-10-02")):
            chosen = route()
            chosen["pricing"][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(driver.BudgetRejected):
                driver.validate_route(chosen, 4096)
        with self.assertRaises(driver.BudgetRejected):
            driver.validate_route(route("openrouter", prompt=0.01))
        chosen = route(prompt=0.20, completion=0.50)
        chosen["max_estimated_cost_usd"] = 0.001
        with self.assertRaises(driver.BudgetRejected):
            driver.validate_route(chosen, 4096)

    def test_changed_model_fallback_tools_stream_multiple_outputs_and_or_fields_blocked(self):
        chosen = route()
        for field, value in (("model", "unapproved/model"), ("models", ["fallback"]), ("route", "fallback"),
                             ("provider", {"allow_fallbacks": True}), ("usage", {"include": True}),
                             ("tools", [{"type": "function"}]), ("stream", True), ("n", 2), ("n", True),
                             ("max_tokens", 4097)):
            policy = driver.OneRequestPolicy(expected_context="fixed public evidence", max_output_tokens=4096, route=chosen)
            with self.subTest(field=field), self.assertRaises(driver.BudgetRejected):
                policy.admit(json.dumps({**self.body(chosen["model"]), field: value}).encode())
            self.assertFalse(policy.used)

    def test_context_and_serialized_request_caps_still_bind_nous(self):
        for content in ("different evidence", "fixed public evidence" * 2, "fixed public evidence" + "x" * 20000):
            chosen = route()
            body = self.body(chosen["model"])
            body["messages"][0]["content"] = content
            with self.subTest(length=len(content)), self.assertRaises(driver.BudgetRejected):
                self.admit(chosen, body)

    def test_json_object_admission_requires_exact_frozen_format(self):
        chosen = route()
        body = self.body(chosen["model"])
        valid = {**body, "response_format": {"type": "json_object"}}
        policy = driver.OneRequestPolicy(expected_context="fixed public evidence", max_output_tokens=4096,
                                          route=chosen, require_json_object=True)
        self.assertEqual(policy.admit(json.dumps(valid).encode()), valid)
        for changed in (body, {**body, "response_format": None},
                        {**body, "response_format": {"type": "text"}},
                        {**body, "response_format": {"type": "json_object", "extra": True}}):
            with self.subTest(changed=changed), self.assertRaises(driver.BudgetRejected):
                driver.OneRequestPolicy(expected_context="fixed public evidence", max_output_tokens=4096,
                                        route=chosen, require_json_object=True).admit(json.dumps(changed).encode())
        for value in (None, {"type": "json_object"}):
            with self.subTest(default=value), self.assertRaises(driver.BudgetRejected):
                self.admit(chosen, {**body, "response_format": value})

    def test_worker_passes_only_opted_in_native_json_override_and_dummy_key(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prompt = root / "prompt.txt"
            prompt.write_text("same frozen question")
            for enabled in (False, True):
                with self.subTest(enabled=enabled):
                    agent = Mock(tools=[])
                    agent.run_conversation.return_value = {"final_response": "{}"}
                    constructor = Mock(return_value=agent)
                    db = Mock()
                    modules = {"hermes_state": SimpleNamespace(SessionDB=Mock(return_value=db)),
                               "hermes_cli.plugins": SimpleNamespace(discover_plugins=Mock()),
                               "run_agent": SimpleNamespace(AIAgent=constructor)}
                    args = SimpleNamespace(out=root, hermes_repo=root, profile=root / "profile", prompt=prompt,
                                           proxy_url="http://127.0.0.1:12345/v1", require_json_object=enabled)
                    with patch.dict(sys.modules, modules), patch.object(sys, "path", sys.path.copy()):
                        driver.worker(args)
                    self.assertEqual(constructor.call_args.kwargs["request_overrides"],
                                     {"response_format": {"type": "json_object"}} if enabled else None)
                    self.assertEqual(constructor.call_args.kwargs["api_key"], driver.LOCAL_WORKER_KEY)
                    self.assertEqual(agent.run_conversation.call_args.args[0], "same frozen question")
                    self.assertEqual(agent.run_conversation.call_args.kwargs["system_message"], driver.SYSTEM)
                    db.close.assert_called_once()

    def test_prepare_is_network_and_credential_free_and_freezes_separate_route(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native = root / "hermes/evals/factory_state/driver.py"
            native.parent.mkdir(parents=True)
            native.write_text("# fixture recorder\n")
            checker = root / "checker.py"
            checker.write_text("# fixture checker\n")
            chosen = route(prompt=0.05, completion=0.20)
            route_file = root / "route-input.json"
            route_file.write_text(json.dumps(chosen))
            context = "fixed public evidence"
            prepared = ({"context": context, "context_sha256": hashlib.sha256(context.encode()).hexdigest()},
                        "frozen question", lambda answer: {}, checker, None)
            args = SimpleNamespace(out=root / "run", hermes_repo=root / "hermes", route_file=route_file,
                                   max_output_tokens=4096, execute=False)
            with patch.object(driver, "prepare_inputs", return_value=prepared), \
                    patch.object(driver, "resolve_secret", side_effect=AssertionError("credentials accessed")), \
                    patch.object(driver, "post_once", side_effect=AssertionError("network accessed")):
                self.assertEqual(driver.run(args)["provider_calls"], 0)
            freeze = json.loads((args.out / "freeze.json").read_text())
            self.assertEqual(freeze["route_spec"], chosen)
            self.assertEqual(freeze["route_sha256"], driver.route_digest(chosen))
            self.assertIsNone(freeze["max_paid_cost_usd"])
            self.assertFalse(freeze["catalog_estimate_is_billing_cap"])
            self.assertEqual(freeze["max_physical_inference_attempts"], 1)
            self.assertIsNone(freeze["response_format_requested"])

    def test_repository_credentials_rejected_without_reading_them(self):
        with self.assertRaises(driver.BudgetRejected):
            driver.external_secret_path(driver.HERE / "must-not-be-read.key")

    def test_expired_deadline_records_no_upstream_attempt(self):
        handlers = []
        class FakeServer:
            server_port = 12345
            def __init__(self, address, handler):
                handlers.append(handler)
            def serve_forever(self, **kwargs):
                pass
            def shutdown(self):
                pass
            def server_close(self):
                pass
        def fake_worker(command, **kwargs):
            raw = json.dumps(self.body(driver.MODEL)).encode()
            handler = handlers[0].__new__(handlers[0])
            handler.path = "/v1/chat/completions"
            handler.headers = {"Content-Length": str(len(raw))}
            handler.rfile = io.BytesIO(raw)
            handler.connection = SimpleNamespace(settimeout=lambda seconds: None)
            replies = []
            handler.reply = lambda code, data: replies.append((code, data))
            handler.do_POST()
            self.assertEqual(replies[0][0], 400)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            native = root / "hermes/evals/factory_state/driver.py"
            native.parent.mkdir(parents=True)
            native.write_text('def prepare_profile(profile, recorder, *, context):\n'
                              '    profile.mkdir()\n'
                              '    (profile / "config.yaml").write_text(\'{"model": {}}\')\n')
            checker = root / "checker.py"
            checker.write_text("# fixture checker\n")
            context = "fixed public evidence"
            prepared = ({"context": context, "context_sha256": hashlib.sha256(context.encode()).hexdigest()},
                        "frozen question", lambda answer: {"verified_success": False}, checker, None)
            args = SimpleNamespace(out=root / "run", hermes_repo=root / "hermes",
                                   max_output_tokens=4096, execute=True)
            with patch.object(driver, "prepare_inputs", return_value=prepared), \
                    patch.object(driver, "resolve_secret", return_value="synthetic-test-credential"), \
                    patch.object(driver, "MAX_WALL_SECONDS", 0), \
                    patch.object(driver, "ThreadingHTTPServer", FakeServer), \
                    patch.object(driver.subprocess, "run", side_effect=fake_worker), \
                    patch.object(driver, "post_once") as post:
                receipt = driver.run(args)
            post.assert_not_called()
            self.assertEqual(receipt["provider_calls"], 0)
            calls = json.loads((args.out / "physical-calls.json").read_text())
            self.assertEqual(calls, [{"kind": "blocked_inference", "upstream_sent": False,
                                      "error_type": "BudgetRejected"}])

    def test_error_summary_retains_bounded_reason_and_redacts_secret(self):
        secret = "credential-sentinel"
        error = {"error": {"code": 429, "message": "quota exhausted " + secret + "x" * 1300,
                           "metadata": {"provider_name": "upstream", "raw": secret}, "untrusted": secret}}
        summary = driver.error_summary(error, secret)
        self.assertEqual(set(summary), {"code", "message", "provider_name"})
        self.assertNotIn(secret, json.dumps(summary))
        self.assertIn("quota exhausted", summary["message"])
        self.assertLessEqual(len(summary["message"]), 1200)


if __name__ == "__main__":
    unittest.main()
