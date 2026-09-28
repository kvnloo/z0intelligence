from __future__ import annotations

import math
import unittest

from z0int.backends.image_decision import (
    ImageDecisionOption,
    ImageDecisionResult,
    build_prompt,
    canonical_labels,
    normalize_logits,
)


class ImageDecisionHelpersTests(unittest.TestCase):
    def test_canonical_labels(self):
        self.assertEqual(canonical_labels(2), ("A", "B"))
        self.assertEqual(canonical_labels(5), ("A", "B", "C", "D", "E"))
        with self.assertRaises(ValueError):
            canonical_labels(1)

    def test_prompt_maps_dynamic_options_to_short_labels(self):
        options = (
            ImageDecisionOption("red", "red"),
            ImageDecisionOption("blue", "blue"),
        )
        prompt = build_prompt("What color is the square?", options)
        self.assertIn("A. red", prompt)
        self.assertIn("B. blue", prompt)
        self.assertIn("Return exactly one label from: A, B", prompt)
        self.assertNotIn("red:", prompt)

    def test_normalize_logits_is_stable_and_normalized(self):
        probs = normalize_logits([1000.0, 999.0, 998.0])
        self.assertTrue(all(math.isfinite(p) for p in probs))
        self.assertAlmostEqual(sum(probs), 1.0, places=12)
        self.assertGreater(probs[0], probs[1])
        self.assertGreater(probs[1], probs[2])

    def test_result_schema_keeps_distribution(self):
        result = ImageDecisionResult(
            backend="test",
            model="m",
            revision="r",
            choice="red",
            probabilities={"red": 0.75, "blue": 0.25},
            latency_ms=12.5,
            diagnostics={"readout": "restricted_next_token_logits"},
        )
        raw = result.to_dict()
        self.assertEqual(raw["schema"], "z0int.image_decision.v1")
        self.assertEqual(raw["choice"], "red")
        self.assertAlmostEqual(sum(raw["probabilities"].values()), 1.0)


if __name__ == "__main__":
    unittest.main()
