"""Offline contract checks. Byte-count fixture is not a model tokenizer."""
import copy
import os
import sys

if os.environ.get("HERMES_SOURCE_TREE"):
    sys.path.insert(0, os.environ["HERMES_SOURCE_TREE"])
import unittest

from z0int.context_resolve import ContextPacket, EvidenceRef, InformationNeed
from selection_boundary import SelectionRejected, freeze_digest, materialize, witness


class SelectionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.packet = ContextPacket(
            task_id="real-claim-development",
            needs=[InformationNeed("scope", "Preserve the exact tested scope")],
            evidence=[EvidenceRef(k, "sha256:" + k, "/" + k, "code", "2026-10-02", k)
                      for k in ("pinned", "optional", "ineligible")],
            unresolved_gaps=["No independent real Hermes efficacy evidence"],
        )
        self.kw = dict(pinned_ids=("pinned",), legal_optional_ids=("optional",),
                       expected_pool_digest=freeze_digest(self.packet),
                       budget=10000, count_tokens=lambda s: len(s.encode()),
                       tokenizer_identity="fixture_utf8_bytes_not_tokens")

    def test_pinned_evidence_retained_and_original_unchanged(self):
        before = copy.deepcopy(self.packet.to_dict())
        result = materialize(self.packet, ("optional",), **self.kw)
        self.assertEqual(result["selected_ids"], ["pinned", "optional"])
        self.assertEqual(before, self.packet.to_dict())
        self.assertEqual(result["packet"]["unresolved_gaps"], self.packet.unresolved_gaps)
        self.assertNotIn("ineligible", result["context"])
        self.assertNotIn("verified_success", result)

    def test_unselected_optional_pack_can_still_retain_pinned_evidence(self):
        self.assertEqual(materialize(self.packet, (), **self.kw)["selected_ids"], ["pinned"])

    def test_sampler_cannot_choose_unknown_ineligible_or_pinned_id(self):
        for ids in (("new",), ("ineligible",), ("pinned",)):
            with self.subTest(ids=ids), self.assertRaises(SelectionRejected):
                materialize(self.packet, ids, **self.kw)

    def test_duplicate_selection_rejected(self):
        with self.assertRaises(SelectionRejected):
            materialize(self.packet, ("optional", "optional"), **self.kw)

    def test_pool_measurements_are_not_carried_to_different_context(self):
        self.packet.measurements = {"input_tokens": 5000, "verified_success": True}
        result = materialize(self.packet, (), **{**self.kw, "expected_pool_digest": freeze_digest(self.packet)})
        self.assertEqual(result["packet"]["measurements"], {})
        self.assertEqual(result["packet"]["aodl_projection"]["observation"]["measurements"], {})

    def test_changed_pool_rejected(self):
        self.packet.evidence[1] = EvidenceRef("optional", "changed", "/optional", "code", "today", "changed")
        with self.assertRaises(SelectionRejected):
            materialize(self.packet, ("optional",), **self.kw)

    def test_duplicate_alias_and_missing_pinned_evidence_rejected(self):
        for packet in (ContextPacket(evidence=[self.packet.evidence[0]] * 2), ContextPacket()):
            with self.subTest(packet=packet), self.assertRaises(SelectionRejected):
                materialize(packet, (), **{**self.kw, "expected_pool_digest": freeze_digest(packet)})

    def test_budget_counts_rendered_wrappers_and_pinned_evidence(self):
        with self.assertRaises(SelectionRejected):
            materialize(self.packet, (), **{**self.kw, "budget": len("pinned")})

    def test_invalid_counter_values_rejected(self):
        for value in (None, -1, True, 2.5):
            with self.subTest(value=value), self.assertRaises(SelectionRejected):
                materialize(self.packet, (), **{**self.kw, "count_tokens": lambda _: value})

    @unittest.skipUnless(os.environ.get("HERMES_SOURCE_TREE"), "Set HERMES_SOURCE_TREE for native Hermes composition")
    def test_real_native_sidecar_composition_and_serialization(self):
        from agent.turn_context import compose_user_api_content, substitute_api_content
        result = materialize(self.packet, ("optional",), **self.kw)
        original_intent = "What is actually supported?"
        row = {"role": "user", "content": original_intent,
               "api_content": compose_user_api_content(original_intent, "", result["context"])}
        substitute_api_content(row)
        request = {"model": "never-called", "messages": [{"role": "system", "content": "fixed authority"}, row]}
        evidence = witness(result, request)
        self.assertTrue(evidence["request_contains_exact_context"])
        self.assertIsNone(evidence["model_consumed_context"])
        self.assertIsNone(evidence["verified_success"])
        self.assertEqual(request["messages"][0]["content"], "fixed authority")
        self.assertTrue(row["content"].startswith(original_intent + "\n\n"))
        self.assertNotIn("api_content", row)
        self.assertEqual(evidence, witness(result, copy.deepcopy(request)))

    def test_prepared_but_absent_or_duplicated_context_is_not_exact_injection(self):
        result = materialize(self.packet, (), **self.kw)
        for content in ("No context", result["context"] * 2):
            with self.subTest(content=content):
                self.assertFalse(witness(result, {"messages": [{"role": "user", "content": content}]})["request_contains_exact_context"])


if __name__ == "__main__":
    unittest.main()
