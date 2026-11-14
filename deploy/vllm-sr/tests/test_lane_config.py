"""Offline checks for the owner's free/local-only router contract."""
import json
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class LaneConfigTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = yaml.safe_load((ROOT / "config.yaml").read_text())
        cls.models = {row["name"]: row for row in cls.config["providers"]["models"]}

    def test_only_free_and_local_entrypoints(self):
        names = [name for entry in self.config["entrypoints"] for name in entry["model_names"]]
        self.assertCountEqual(names, ["vllm-sr/free", "vllm-sr/local"])

    def test_no_paid_backend_or_subscription_secret(self):
        config = (ROOT / "config.yaml").read_text()
        self.assertNotIn("paid", config.lower())
        self.assertNotIn("SUBPROXY", config)
        self.assertNotIn("VLLM_SR_SUBPROXY_TOKEN", (ROOT / "deploy.sh").read_text())

    def test_local_recipe_is_local_only(self):
        entry = next(e for e in self.config["entrypoints"] if "vllm-sr/local" in e["model_names"])
        recipe = next(r for r in self.config["recipes"] if r["name"] == entry["recipe"])
        decisions = recipe["routing"]["decisions"]
        self.assertEqual(len(decisions), 1)
        for ref in decisions[0]["modelRefs"]:
            model = self.models[ref["model"]]
            self.assertEqual(model["provider_model_id"], "qwen3-0.6b-q8")
            for backend in model["backend_refs"]:
                self.assertEqual(backend["base_url"], "http://100.113.138.100:11530/v1")
                self.assertNotIn("api_key_env", backend)
        self.assertFalse(decisions[0].get("fallback", {}).get("enabled", False))

    def test_every_model_is_reachable_and_has_a_card(self):
        routings = [self.config["routing"]] + [r["routing"] for r in self.config.get("recipes", [])]
        refs = {r["model"] for routing in routings for d in routing["decisions"] for r in d["modelRefs"]}
        self.assertEqual(refs, set(self.models))
        self.assertEqual({m["name"] for m in self.config["routing"]["modelCards"]}, set(self.models))

    def test_free_pool_uses_only_qualified_non_subscription_backends(self):
        for decision in self.config["routing"]["decisions"]:
            self.assertEqual(decision["algorithm"]["type"], "random")
            for ref in decision["modelRefs"]:
                backend = self.models[ref["model"]]["backend_refs"][0]
                self.assertEqual(backend["api_key_env"], "GROQ_API_KEY")
                self.assertTrue(backend["openai_compat_gateway"])
            self.assertTrue({413, 429}.issubset(decision["fallback"]["retryable_status_codes"]))
            self.assertGreaterEqual(decision["fallback"]["max_attempts"], len(decision["modelRefs"]))

    def test_per_model_limits_match_inventory(self):
        limits = json.loads((ROOT / "limits.json").read_text())
        limiter = self.config["global"]["services"]["ratelimit"]
        self.assertFalse(limiter["fail_open"])
        self.assertEqual(len(limiter["providers"]), 1)
        self.assertEqual(limiter["providers"][0]["type"], "local")
        rules = limiter["providers"][0]["rules"]
        names = [r["name"] for r in rules]
        self.assertEqual(len(names), len(set(names)))
        free_refs = self.config["routing"]["decisions"][0]["modelRefs"]
        self.assertEqual(len(rules), 2 * len(free_refs))
        for ref in free_refs:
            model = self.models[ref["model"]]
            observed = next(r for r in limits["models"] if r["provider"] == "groq" and r["model"] == model["provider_model_id"])
            for unit, request_key, token_key in [("minute", "rpm", "tpm"), ("day", "rpd", "tpd")]:
                rule = next(r for r in rules if r["match"]["model"] == ref["model"] and r["unit"] == unit)
                self.assertEqual(rule["requests_per_unit"], observed[request_key])
                self.assertEqual(rule["tokens_per_unit"], observed[token_key])


if __name__ == "__main__":
    unittest.main()
