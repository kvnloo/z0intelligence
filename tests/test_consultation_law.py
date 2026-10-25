import itertools
import unittest
from z0int.fsm.consultation_law import audit, evaluate


class ConsultationLawTests(unittest.TestCase):
    def setUp(self):
        self.receipt = dict(schema='z0.consultation.v1', status='ok', query='option capacity', hits=[{'source': 'docs/METHOD.md:9'}], turn='entry1', session='s1', sourceHash='abc', ts='2026-10-24T10:00:00Z')
        self.action = dict(schema='z0.consultation_action.v1', tier='CHECK', action='edit', turn='entry1', session='s1', sourceHash='abc', ts='2026-10-24T10:00:01Z')

    def status(self, rows):
        return audit(rows)[0]['status']

    def test_finite_truth_table(self):
        for available, applicable, consulted in itertools.product((None, False, True), repeat=3):
            expected = 'unknown' if available is not True or applicable is None or consulted is None else 'violation' if applicable and not consulted else 'pass'
            self.assertEqual(evaluate(dict(available=available, applicable=applicable, consulted=consulted)), expected)

    def test_nonboolean_is_unknown(self):
        for name in ('available', 'applicable', 'consulted'):
            for invalid in (0, 1, 'true', [], {}):
                facts = dict(available=True, applicable=True, consulted=True)
                facts[name] = invalid
                self.assertEqual(evaluate(facts), 'unknown')

    def test_prior_bound_receipt_passes(self):
        self.assertEqual(self.status([self.receipt, self.action]), 'pass')

    def test_absent_receipt_violates(self):
        self.assertEqual(self.status([self.action]), 'violation')

    def test_future_or_other_turn_receipts_do_not_count(self):
        self.assertEqual(self.status([self.action, self.receipt]), 'violation')
        for key, value in (('turn', 'old'), ('session', 'other'), ('sourceHash', 'changed'), ('ts', '2026-10-24T10:01:00Z'), ('status', 'unavailable')):
            receipt = {**self.receipt, key: value}
            self.assertEqual(self.status([receipt, self.action]), 'violation', key)

    def test_empty_results_are_still_consultation(self):
        self.assertEqual(self.status([{**self.receipt, 'hits': []}, self.action]), 'pass')

    def test_malformed_hit_does_not_count(self):
        self.assertEqual(self.status([{**self.receipt, 'hits': [{}]}, self.action]), 'violation')

    def test_unknown_tier_and_empty_journal(self):
        self.assertEqual(self.status([{**self.action, 'tier': None}]), 'unknown')
        self.assertEqual(self.status([]), 'unknown')

    def test_fast_not_applicable(self):
        self.assertEqual(self.status([{**self.action, 'tier': 'FAST'}]), 'pass')


if __name__ == '__main__':
    unittest.main()
