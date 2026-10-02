"""Frozen control qualification; no provider calls or semantic-use claims."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from discriminator import build_cases, deterministic_bundle, prepare, render_case
from frozen_check import grade

HERE = Path(__file__).resolve().parent
PHASE_C = Path(os.environ.get('HERMES_PHASE_C_ROOT', str(HERE.parent)))
HERMES = Path(os.environ['HERMES_SOURCE_TREE']) if os.environ.get('HERMES_SOURCE_TREE') else None
NATIVE_AVAILABLE = HERMES is not None and HERMES.is_dir()


class DiscriminatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pool = json.loads((HERE / 'fixtures/development_pool.json').read_text())
        cls.cases = build_cases(cls.pool)

    def test_missing_case_removes_only_decisive_ref_without_hint(self):
        full, missing = self.cases['complete'], self.cases['missing_source_identity']
        before = [r for r in full['evidence'] if r['source_id'] != 'source-identity']
        self.assertEqual(before, missing['evidence'])
        self.assertEqual(full['unresolved_gaps'], missing['unresolved_gaps'])
        self.assertNotIn('a0bca744067d04f05904319d3d919be30c336556', json.dumps(missing))
        self.assertEqual(full['needs'], missing['needs'])

    def test_bundle_uses_exact_same_refs_without_gold_or_semantic_rewrite(self):
        original = copy.deepcopy(self.pool)
        rendered = deterministic_bundle(self.pool)
        self.assertEqual(json.loads(rendered)['evidence'], self.pool['evidence'])
        self.assertEqual(original, self.pool)
        self.assertNotIn('expected_answer', rendered)

    @unittest.skipUnless(NATIVE_AVAILABLE, 'Set HERMES_SOURCE_TREE to a pinned Hermes checkout')
    def test_native_context_identical_to_deterministic_comparator(self):
        for case_id in ('complete', 'missing_source_identity', 'contradictory_source_identity'):
            result = render_case(self.cases[case_id], PHASE_C, HERMES)
            self.assertTrue(result['same_evidence_refs'])
            self.assertTrue(result['same_context_bytes'])
            self.assertTrue(result['witness']['request_contains_exact_context'])
            self.assertEqual(result['witness']['occurrences'], 1)
            self.assertIsNone(result['witness']['model_consumed_context'])
            self.assertFalse(result['witness']['transmitted'])
            self.assertFalse(result['admission']['default_profile']['exact_context_preserved'])
            self.assertTrue(result['admission']['study_profile']['exact_context_preserved'])
            self.assertGreaterEqual(result['admission']['study_profile']['max_chars'], len(result['context']))

    def test_frozen_checker_requires_abstention_when_decisive_evidence_absent(self):
        hallucination = {'status': 'SUPPORTED', 'tested_source': 'a0bca744067d04f05904319d3d919be30c336556',
                         'hermes_speedup_established': False, 'citations': ['limits'], 'reason': 'supported'}
        self.assertFalse(grade('missing_source_identity', hallucination)['verified_success'])
        abstention = dict(hallucination, status='ABSTAIN', tested_source=None, reason='missing_evidence')
        self.assertTrue(grade('missing_source_identity', abstention)['verified_success'])
        self.assertFalse(grade('complete', abstention)['verified_success'])

    def test_frozen_checker_requires_unresolved_contradiction(self):
        answer = {'status': 'ABSTAIN', 'tested_source': None, 'hermes_speedup_established': False,
                  'citations': ['limits', 'source-identity', 'source-identity-conflict'],
                  'reason': 'conflicting_evidence'}
        self.assertTrue(grade('contradictory_source_identity', answer)['verified_success'])
        answer['tested_source'] = 'f' * 40
        self.assertFalse(grade('contradictory_source_identity', answer)['verified_success'])

    def test_checker_rejects_unknown_citation_type_and_extra_authority(self):
        answer = {'status': 'SUPPORTED', 'tested_source': 'a0bca744067d04f05904319d3d919be30c336556',
                  'hermes_speedup_established': False, 'citations': ['limits', 'source-identity'],
                  'reason': 'supported'}
        self.assertTrue(grade('complete', answer)['verified_success'])
        for mutated in (dict(answer, hermes_speedup_established=0),
                        dict(answer, citations=['limits', 'source-identity', 'not-in-pool']),
                        dict(answer, execution_authorized=True)):
            self.assertFalse(grade('complete', mutated)['verified_success'])

    @unittest.skipUnless(NATIVE_AVAILABLE, 'Set HERMES_SOURCE_TREE to a pinned Hermes checkout')
    def test_preparation_is_create_only_replayable_and_controls_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / 'one', Path(tmp) / 'two'
            a = prepare(PHASE_C, HERMES, first)
            b = prepare(PHASE_C, HERMES, second)
            self.assertEqual(a, b)
            self.assertEqual(a['provider_calls'], 0)
            self.assertEqual(a['classification'], 'OFFLINE_CONFORMANCE_ONLY')
            self.assertTrue(all(a['controls'].values()))
            self.assertEqual((first / 'freeze.json').read_bytes(), (second / 'freeze.json').read_bytes())
            self.assertEqual((first / 'complete/context.txt').read_bytes(),
                             (second / 'complete/context.txt').read_bytes())
            with self.assertRaises(FileExistsError):
                prepare(PHASE_C, HERMES, first)


if __name__ == '__main__':
    unittest.main()
