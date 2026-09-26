from __future__ import annotations

import unittest

from z0int.alignbench import auroc, ece, fit_threshold, metrics


class AlignBenchTests(unittest.TestCase):
    def test_auroc_perfect_and_reversed(self):
        self.assertEqual(auroc([0.1, 0.9], [0, 1]), 1.0)
        self.assertEqual(auroc([0.9, 0.1], [0, 1]), 0.0)

    def test_auroc_ties_use_average_rank(self):
        self.assertEqual(auroc([0.5, 0.5], [0, 1]), 0.5)

    def test_metrics_perfect_separation(self):
        report = metrics(
            [
                {"label": 0, "score": 0.1},
                {"label": 0, "score": 0.2},
                {"label": 1, "score": 0.8},
                {"label": 1, "score": 0.9},
            ]
        )
        self.assertEqual(report["auroc"], 1.0)
        self.assertEqual(report["f1"], 1.0)
        self.assertEqual(report["tp"], 2)
        self.assertEqual(report["tn"], 2)
        self.assertAlmostEqual(report["brier"], 0.025)

    def test_calibration_rows_choose_threshold_without_eval_leakage(self):
        calibration = [
            {"label": 0, "score": 0.10},
            {"label": 0, "score": 0.20},
            {"label": 1, "score": 0.35},
            {"label": 1, "score": 0.40},
        ]
        threshold = fit_threshold(
            [r["score"] for r in calibration],
            [r["label"] for r in calibration],
        )
        self.assertLess(threshold, 0.5)

        report = metrics(
            [
                {"label": 0, "score": 0.15},
                {"label": 1, "score": 0.38},
            ],
            calibration_rows=calibration,
        )
        self.assertEqual(report["threshold"], threshold)
        self.assertEqual(report["calibration_n"], 4)
        self.assertEqual(report["f1"], 1.0)

    def test_ece_bounds(self):
        value = ece([0.1, 0.9], [0, 1])
        self.assertIsNotNone(value)
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 1.0)


if __name__ == "__main__":
    unittest.main()
