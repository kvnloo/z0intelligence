import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from run_pair import assess_pair, execute_pair, prepare_pair, replay_pair
from frozen_check import grade

HERE = Path(__file__).resolve().parent
PHASE_C = Path(os.environ.get('HERMES_PHASE_C_ROOT', str(HERE.parent)))
HERMES = Path(os.environ['HERMES_SOURCE_TREE']) if os.environ.get('HERMES_SOURCE_TREE') else None
NATIVE_AVAILABLE = HERMES is not None and HERMES.is_dir()


class PairAssessmentTests(unittest.TestCase):
    def setUp(self):
        self.arms = {case: {'provider_calls': 1, 'returncode': 0, 'error_type': None,
                      'served_model': 'fixed-free-model', 'served_provider': 'fixed-provider',
                      'resource_comparison_eligible': True,
                      'exact_selected_context_in_forwarded_request': True,
                      'outcome': {'verified_success': True},
                      'usage': {'prompt_tokens': 50, 'completion_tokens': 12, 'cost': 0}}
                     for case in ('complete', 'missing_source_identity')}

    def test_same_model_evidence_pair_can_pass_without_speedup_claim(self):
        result = assess_pair(self.arms)
        self.assertEqual(result['decision'], 'CAUSAL_DISCRIMINATOR_PASSED')
        self.assertEqual(result['actual_consumed_prompt_tokens'], 100)
        self.assertEqual(result['actual_consumed_completion_tokens'], 24)
        self.assertFalse(result['speedup_or_noninferiority_claim'])
        self.assertFalse(result['thermal_selection_claim'])

    def test_model_or_provider_change_invalidates_comparison(self):
        for key in ('served_model', 'served_provider'):
            for value in ('different', None):
                changed = copy.deepcopy(self.arms)
                changed['missing_source_identity'][key] = value
                self.assertEqual(assess_pair(changed)['decision'], 'UNIDENTIFIABLE')

    def test_valid_pair_with_wrong_semantics_is_failure_not_rerun(self):
        self.arms['missing_source_identity']['outcome']['verified_success'] = False
        result = assess_pair(self.arms)
        self.assertEqual(result['decision'], 'CAUSAL_DISCRIMINATOR_FAILED')
        self.assertFalse(result['retry_authorized'])

    def test_spill_duplicate_or_missing_physical_call_blocks_attribution(self):
        for field, value in (('exact_selected_context_in_forwarded_request', False),
                             ('provider_calls', 2), ('provider_calls', 0),
                             ('resource_comparison_eligible', False), ('returncode', 1)):
            changed = copy.deepcopy(self.arms)
            changed['complete'][field] = value
            self.assertEqual(assess_pair(changed)['decision'], 'UNIDENTIFIABLE')

    def test_missing_usage_is_unknown_not_zero(self):
        self.arms['complete']['usage'] = None
        self.arms['complete']['resource_comparison_eligible'] = False
        result = assess_pair(self.arms)
        self.assertIsNone(result['actual_consumed_prompt_tokens'])
        self.assertIsNone(result['actual_consumed_completion_tokens'])

    def test_incomplete_pair_is_not_a_win(self):
        del self.arms['missing_source_identity']
        self.assertEqual(assess_pair(self.arms)['decision'], 'UNIDENTIFIABLE')


@unittest.skipUnless(NATIVE_AVAILABLE, 'Set HERMES_SOURCE_TREE to a pinned Hermes checkout')
class BoundedDriverTests(unittest.TestCase):
    def setUp(self):
        self.phase_c = PHASE_C
        self.hermes = HERMES

    def test_prepare_spends_nothing_execute_runs_two_and_replay_spends_nothing(self):
        calls = []

        def fake_run(args, *, prepared_case):
            calls.append(prepared_case['metadata']['case_id'])
            self.assertEqual(args.max_output_tokens, 1024)
            self.assertEqual(set(prepared_case), {'selection', 'prompt', 'checker', 'checker_source', 'metadata'})
            self.assertNotIn('a0bca744067d04f05904319d3d919be30c336556', prepared_case['prompt'])
            case = prepared_case['metadata']['case_id']
            answer = {'status': 'SUPPORTED' if case == 'complete' else 'ABSTAIN',
                      'tested_source': 'a0bca744067d04f05904319d3d919be30c336556' if case == 'complete' else None,
                      'hermes_speedup_established': False,
                      'citations': ['limits', 'source-identity'] if case == 'complete' else ['limits'],
                      'reason': 'supported' if case == 'complete' else 'missing_evidence'}
            usage = {'prompt_tokens': 50, 'completion_tokens': 12, 'cost': 0}
            receipt = {'provider_calls': 1, 'returncode': 0, 'error_type': None,
                       'served_model': 'synthetic-unit-test-model', 'served_provider': 'synthetic-unit-test-provider',
                       'resource_comparison_eligible': True,
                       'exact_selected_context_in_forwarded_request': True,
                       'outcome': prepared_case['checker'](answer), 'usage': usage}
            physical = [{'kind': 'inference', 'upstream_sent': True, 'status_code': 200,
                         'served_model': receipt['served_model'], 'served_provider': receipt['served_provider'],
                         'usage': usage,
                         'forwarded_request': {'model': 'openrouter/free', 'max_tokens': 1024,
                            'provider': {'allow_fallbacks': False, 'max_price': {'prompt': 0, 'completion': 0}},
                            'messages': [{'role': 'user', 'content': prepared_case['selection']['context']}]}}]
            args.out.mkdir()
            for name, value in [('answer.json', answer), ('receipt.json', receipt),
                                ('physical-calls.json', physical), ('freeze.json', {'synthetic_test_only': True})]:
                (args.out / name).write_text(json.dumps(value))
            return receipt

        fake_driver = SimpleNamespace(MODEL='openrouter/free',
                        ENDPOINT='https://openrouter.ai/api/v1/chat/completions', run=fake_run)
        with tempfile.TemporaryDirectory() as tmp, patch('run_pair.shared_driver', return_value=fake_driver):
            credential = Path(tmp) / 'synthetic-test-credential'
            credential.write_text('test-only-no-network')
            args = SimpleNamespace(out=Path(tmp) / 'pair', phase_c=self.phase_c,
                                   hermes_repo=self.hermes, credential_file=credential)
            prepare_pair(args)
            self.assertEqual(calls, [])
            result = execute_pair(args)
            self.assertEqual(calls, ['complete', 'missing_source_identity'])
            self.assertEqual(result['decision'], 'CAUSAL_DISCRIMINATOR_PASSED')
            self.assertEqual(result, replay_pair(args.out))
            self.assertEqual(len(calls), 2)
            with self.assertRaises(FileExistsError):
                execute_pair(args)
            self.assertEqual(len(calls), 2)

    def test_edited_context_is_rejected_before_driver_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(out=Path(tmp) / 'pair', phase_c=self.phase_c,
                                   hermes_repo=self.hermes, credential_file=None)
            prepare_pair(args)
            with (args.out / 'study/complete/context.txt').open('a') as stream:
                stream.write('post-freeze edit')
            with patch('run_pair.shared_driver') as driver, self.assertRaises(ValueError):
                execute_pair(args)
            driver.assert_not_called()

    def test_rate_limit_block_prevents_execution_without_semantic_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = SimpleNamespace(out=Path(tmp) / 'pair', phase_c=self.phase_c,
                                   hermes_repo=self.hermes, credential_file=None)
            prepare_pair(args)
            (args.out / 'provider-block.json').write_text(json.dumps({'status_code': 429}))
            with patch('run_pair.shared_driver') as driver, self.assertRaises(ValueError):
                execute_pair(args)
            driver.assert_not_called()
            self.assertFalse((args.out / 'execution-started.json').exists())
            replay = replay_pair(args.out)
            self.assertEqual(replay['status'], 'BLOCKED_PROVIDER_RATE_LIMIT')
            self.assertEqual(replay['decision'], 'NOT_RUN')
            self.assertEqual(replay['provider_calls'], 0)


if __name__ == '__main__':
    unittest.main()
