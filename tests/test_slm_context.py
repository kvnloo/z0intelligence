from __future__ import annotations

import unittest

from z0int.slm_context import Variant, decision_input, inject, packet_evidence, split_rows


def row(index: int) -> dict:
    return {"id": str(index), "family": "route", "group": f"task:{index}",
            "state": {"request": f"independent decision {index}"}, "question": "Which route?",
            "options": [{"id": "local", "description": "small local"},
                        {"id": "teacher", "description": "strong teacher"}],
            "teacher_label": "local", "human_label": None,
            "summary": {"branch": "experiment", "failure_count": 0},
            "skill_relevance": {}, "source": {"private": "never inject"}}


class SlmContextTests(unittest.TestCase):
    def test_baseline_is_identical_and_labels_never_enter(self):
        value = row(1)
        baseline = inject(value, Variant("none", (), 0), [])
        self.assertEqual(decision_input(baseline), decision_input(value))
        self.assertNotIn("teacher_label", baseline)
        self.assertNotIn("source", baseline)

    def test_budget_is_whole_json_and_does_not_change_original(self):
        value = row(1)
        small = inject(value, Variant("summary", ("summary",), 1), [])
        self.assertEqual(small["state"], value["state"])
        large = inject(value, Variant("summary", ("summary",), 200), [])
        self.assertEqual(large["state"]["decision_state"], value["state"])
        self.assertLessEqual(large["context_bytes"], 200)
        exact = inject(value, Variant("summary", ("summary",), 100), [], count_tokens=lambda s: len(s))
        self.assertEqual(exact["budget_unit"], "tokens")
        self.assertNotIn("untrusted_context_evidence", value["state"])

    def test_fewshot_excludes_own_group(self):
        value = row(1)
        result = inject(value, Variant("few_shot", ("few_shot",), 2000), [value, row(2)])
        examples = result["state"]["untrusted_context_evidence"]["few_shot"]
        self.assertEqual(len(examples), 1)
        self.assertEqual(examples[0]["input"]["state"], row(2)["summary"])

    def test_memory_requires_provenance_and_allowlisted_sources(self):
        packet = {"schema": "z0int.context_resolve.v1", "evidence": [
            {"source_id": "a", "source_version": "sha256:x", "locator": "memory://a",
             "trust_class": "conversation", "excerpt": "retrieved evidence"}]}
        self.assertEqual(packet_evidence(packet, set()), [])
        self.assertEqual(len(packet_evidence(packet, {"a"})), 1)
        packet["evidence"][0].pop("source_version")
        with self.assertRaises(ValueError):
            packet_evidence(packet, {"a"})

    def test_group_split_is_disjoint_and_colocates_duplicate_inputs(self):
        values = [row(i) for i in range(20)]
        split, manifest = split_rows(values)
        self.assertEqual(manifest, split_rows(values)[1])
        groups = [{r["group"] for r in split[name]} for name in ("pool", "dev", "test")]
        self.assertFalse(groups[0] & groups[1] or groups[0] & groups[2] or groups[1] & groups[2])
        duplicate = row(50)
        duplicate["state"] = values[0]["state"]
        _, merged = split_rows([*values, duplicate])
        self.assertEqual(merged["assignments"]["task:50"], merged["assignments"]["task:0"])
        self.assertEqual(merged["group_clusters"]["task:50"], merged["group_clusters"]["task:0"])


if __name__ == "__main__":
    unittest.main()
