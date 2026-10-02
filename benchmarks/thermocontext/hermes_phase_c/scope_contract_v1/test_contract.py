"""Offline semantics and failure controls; no network or model dependency."""
import copy
import json
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import qualify
from contract import ContractRejected, report, validate_report
from check_fixture import grade


class ScopeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = qualify.read(HERE / "fixtures.json")
        cls.oracle = qualify.read(HERE / "oracle.json")
        cls.cases = {case["case_id"]: case for case in cls.fixtures["cases"]}
        cls.target = cls.fixtures["target"]

    def packet(self, case="matching-positive"):
        return copy.deepcopy(self.cases[case]["packet"])

    def scope(self, packet, **updates):
        ref = packet["evidence"][1]
        value = json.loads(ref["excerpt"])
        value.update(updates)
        ref["excerpt"] = json.dumps(value)

    def test_preimplementation_and_old_artifacts_unchanged(self):
        self.assertEqual(qualify.validate_freeze(), 262)

    def test_seven_frozen_independent_oracles_and_both_arms(self):
        for name, case in self.cases.items():
            for arm in ("full", "minimal"):
                with self.subTest(name=name, arm=arm):
                    selected = qualify.render(case, arm)
                    qualify.validate_preservation(case, selected)
                    result = report(selected["packet"], self.target)
                    self.assertTrue(grade(result["answer"], self.oracle["expected"][name], selected["selected_ids"])["passed"])
                    validate_report(result["answer"], selected["packet"], self.target)
                    self.assertFalse(result["runtime_authority"])
                    self.assertFalse(result["independent_outcome_verified"])

    def test_only_frozen_ancillary_ref_removed_without_labels_or_answers(self):
        for case in self.cases.values():
            full, minimal = [qualify.render(case, arm) for arm in ("full", "minimal")]
            self.assertEqual(set(full["selected_ids"]) - set(minimal["selected_ids"]), {"telemetry"})
            self.assertLess(minimal["budget_units"], full["budget_units"])
            self.assertEqual(json.loads(minimal["context"]), {key: minimal["packet"][key]
                             for key in ("evidence", "contradictions", "unresolved_gaps")})
            self.assertNotIn(case["case_id"], minimal["context"])

    def test_certain_answers_without_matching_declarations_rejected_not_repaired(self):
        for name in ("missing-scope", "wrong-subject-positive", "historical-prose-only", "conflicting-scope"):
            for value in (True, False):
                answer = copy.deepcopy(self.oracle["expected"][name])
                answer["scope"].update(status="declared", value=value)
                before = copy.deepcopy(answer)
                with self.subTest(name=name, value=value), self.assertRaises(ContractRejected):
                    validate_report(answer, self.packet(name), self.target)
                self.assertEqual(answer, before)

    def test_boolean_like_literals_and_malformed_known_schema_rejected(self):
        for value in (0, 1, "true", "false", None, [], {}):
            packet = self.packet()
            self.scope(packet, declared_value=value)
            with self.subTest(value=value), self.assertRaises(ContractRejected):
                report(packet, self.target)
        for key in ("subject", "predicate", "declared_value"):
            packet = self.packet()
            body = json.loads(packet["evidence"][1]["excerpt"])
            del body[key]
            packet["evidence"][1]["excerpt"] = json.dumps(body)
            with self.subTest(missing=key), self.assertRaises(ContractRejected):
                report(packet, self.target)
        packet = self.packet()
        self.scope(packet, precedence="prefer me")
        with self.assertRaises(ContractRejected):
            report(packet, self.target)

    def test_duplicate_json_keys_and_nonfinite_values_rejected(self):
        for excerpt in ('{"tested_source":"' + "1" * 40 + '","tested_source":"' + "2" * 40 + '"}',
                        '{"nested":{"a":1,"a":2}}', '{"value":NaN}'):
            packet = self.packet()
            packet["evidence"][0]["excerpt"] = excerpt
            with self.subTest(excerpt=excerpt), self.assertRaises(ContractRejected):
                report(packet, self.target)

    def test_exact_subject_and_predicate_no_prefix_or_case_transfer(self):
        for updates in ({"subject": self.target["subject"] + ":other"},
                        {"subject": self.target["subject"].upper()},
                        {"predicate": "accuracy_improved"}, {"schema": "unrecognized.v1"}):
            packet = self.packet()
            self.scope(packet, **updates)
            self.assertEqual(report(packet, self.target)["answer"]["scope"],
                             {"status": "unknown", "value": None, "citations": []})

    def test_agreeing_repeated_declarations_and_source_conflict_independence(self):
        packet = self.packet()
        extra = copy.deepcopy(packet["evidence"][1])
        extra.update(source_id="another-scope", locator="fixture://another-scope")
        packet["evidence"].append(extra)
        self.assertEqual(report(packet, self.target)["answer"]["scope"]["citations"], ["record-2", "another-scope"])
        extra = copy.deepcopy(packet["evidence"][0])
        extra.update(source_id="other-revision", locator="fixture://other-revision",
                     excerpt=json.dumps({"tested_source": "f" * 40}))
        packet["evidence"].append(extra)
        answer = report(packet, self.target)["answer"]
        self.assertEqual(answer["tested_source"]["status"], "conflict")
        self.assertTrue(answer["scope"]["value"])
        self.assertEqual(answer["scope"]["status"], "declared")

    def test_no_nested_revision_or_publication_revision_substitution(self):
        for content in ({"nested": {"tested_source": "f" * 40}}, {"publication_revision": "f" * 40}):
            packet = self.packet()
            packet["evidence"][0]["excerpt"] = json.dumps(content)
            answer = report(packet, self.target)["answer"]
            self.assertEqual(answer["tested_source"]["status"], "unknown")
            self.assertTrue(answer["scope"]["value"])

    def test_malformed_revision_rejected(self):
        for value in (None, True, 123, "abc", "g" * 40):
            packet = self.packet()
            packet["evidence"][0]["excerpt"] = json.dumps({"tested_source": value})
            with self.subTest(value=value), self.assertRaises(ContractRejected):
                report(packet, self.target)

    def test_raw_historical_refs_gaps_and_provenance_exact(self):
        case = self.cases["historical-prose-only"]
        origin = self.fixtures["historical_excerpt_origin"]
        old = qualify.read(qualify.ROOT / origin["path"])["packet"]
        expected = [ref for ref in old["evidence"] if ref["source_id"] in ("limits", "source-identity")]
        minimal = qualify.render(case, "minimal")
        self.assertEqual(minimal["packet"]["evidence"], expected)
        for key in ("contradictions", "unresolved_gaps"):
            self.assertEqual(minimal["packet"][key], old[key])
        self.assertIsNone(report(minimal["packet"], self.target)["answer"]["scope"]["value"])

    def test_dropping_gap_conflict_or_mutating_provenance_rejected(self):
        case = self.cases["conflicting-scope"]
        for mutation in ("gap", "conflict", "provenance", "required_ref"):
            selection = qualify.render(case, "minimal")
            visible = json.loads(selection["context"])
            if mutation == "gap": visible["unresolved_gaps"] = []
            if mutation == "conflict": visible["contradictions"] = []
            if mutation == "provenance": visible["evidence"][0]["locator"] = "invented"
            if mutation == "required_ref": visible["evidence"].pop()
            selection["context"] = json.dumps(visible)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                qualify.validate_preservation(case, selection)

    def test_constant_and_scope_transfer_mutants_fail_frozen_controls(self):
        rejected = qualify.mutant_failures(self.oracle, self.fixtures)
        self.assertEqual(set(rejected), set(self.oracle["mutants_required_to_fail"]))
        self.assertTrue(all(rejected.values()))

    def test_independent_checker_rejects_bool_number_equivalence_and_hidden_citation(self):
        expected = self.oracle["expected"]["matching-positive"]
        candidate = copy.deepcopy(expected)
        candidate["scope"]["value"] = 1
        self.assertFalse(grade(candidate, expected, ["record-1", "record-2"])["passed"])
        self.assertFalse(grade(expected, expected, ["record-1"])["passed"])


if __name__ == "__main__":
    unittest.main()
