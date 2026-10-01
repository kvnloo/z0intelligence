from __future__ import annotations

import importlib.util
import unittest

from z0int.aodl_admission import (
    REQUIRED_CANON_VERSION,
    SpawnRequest,
    decide_spawn,
)


class FakeAodl:
    CANON_VERSION = REQUIRED_CANON_VERSION

    @staticmethod
    def validate(document):
        return []

    @staticmethod
    def semantic_fingerprint(document):
        return REQUIRED_CANON_VERSION + ":" + ("a" * 64)


def document(*, revision=3, allowed=True, max_children=4, max_depth=2, tokens=1000, ceiling=None):
    return {
        "specVersion": "0.2",
        "graphId": "gate-test",
        "revision": revision,
        "intentGraph": {
            "nodes": [{
                "id": "parent",
                "kind": "task",
                "ports": [{"id": "out", "direction": "out", "schema": "Task"}],
                "capabilities": ["execute"],
                "authorityCeiling": list(ceiling or ["execute", "verify"]),
                "lifecycle": "declared",
            }],
            "edges": [],
        },
        "policies": {
            "kinds": ["sequence"],
            "fanIn": "all",
            "dynamic": {
                "allowed": allowed,
                "maxChildren": max_children,
                "maxDepth": max_depth,
            },
        },
        "constraints": {
            "budgets": {"tokens": tokens},
            "termination": {"on": "done"},
        },
        "provenance": {"source": "test", "sourceHash": "0" * 64},
    }


def request(**kwargs):
    base = dict(
        request_revision=3,
        parent_node_id="parent",
        live_children=1,
        parent_depth=0,
        observed={"tokens": 400},
        proposed={"tokens": 100},
        requested=("execute",),
    )
    base.update(kwargs)
    return SpawnRequest(**base)


class AodlAdmissionTests(unittest.TestCase):
    def test_allow_within_contract(self):
        decision = decide_spawn(document(), request(), contract_api=FakeAodl)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.numeric_codes, ())
        self.assertTrue(decision.semantic_fingerprint.startswith(REQUIRED_CANON_VERSION + ":"))

    def test_every_bend_transition_code(self):
        cases = [
            (document(), request(request_revision=4), 101),
            (document(allowed=False), request(), 102),
            (document(max_children=1), request(live_children=1), 103),
            (document(max_depth=0), request(parent_depth=0), 104),
            (document(tokens=500), request(observed={"tokens": 450}, proposed={"tokens": 51}), 105),
            (document(ceiling=["execute"]), request(requested=("execute", "verify")), 106),
        ]
        for doc, req, code in cases:
            with self.subTest(code=code):
                decision = decide_spawn(doc, req, contract_api=FakeAodl)
                self.assertFalse(decision.allowed)
                self.assertIn(code, decision.numeric_codes)

    def test_contract_values_cannot_be_overridden_by_caller(self):
        denied = decide_spawn(
            document(max_children=1, tokens=10, ceiling=["execute"]),
            request(live_children=1, observed={"tokens": 10}, proposed={"tokens": 1}, requested=("verify",)),
            contract_api=FakeAodl,
        )
        self.assertFalse(denied.allowed)
        self.assertEqual(set(denied.numeric_codes), {103, 105, 106})

    def test_unknown_parent_fails_closed(self):
        decision = decide_spawn(document(), request(parent_node_id="not-declared"), contract_api=FakeAodl)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.codes, ("parent-not-declared",))

    def test_invalid_contract_yields_no_authority(self):
        class Invalid(FakeAodl):
            @staticmethod
            def validate(document):
                return ["bad"]

        decision = decide_spawn(document(), request(), contract_api=Invalid)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.codes, ("contract-invalid",))
        self.assertIsNone(decision.semantic_fingerprint)

    def test_unknown_canonical_version_fails_closed(self):
        class Future(FakeAodl):
            CANON_VERSION = "aodl-canon-2"

        decision = decide_spawn(document(), request(), contract_api=Future)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.codes, ("canonical-version-mismatch",))

    def test_receipt_is_gate_evidence_not_task_success(self):
        receipt = decide_spawn(document(), request(), contract_api=FakeAodl).receipt()
        self.assertEqual(receipt["schema"], "z0int.aodl_admission.v1")
        self.assertEqual(receipt["decision"], "ALLOW")
        self.assertIn("aodl_semantic_fingerprint", receipt)
        self.assertNotIn("verified_success", receipt)
        self.assertNotIn("success", receipt)

    @unittest.skipUnless(importlib.util.find_spec("aodl_contract"), "aodl-contract not installed")
    def test_real_aodl_contract_api(self):
        decision = decide_spawn(document(), request())
        self.assertTrue(decision.allowed, decision)
        self.assertTrue(decision.semantic_fingerprint.startswith("aodl-canon-1:"))


if __name__ == "__main__":
    unittest.main()
