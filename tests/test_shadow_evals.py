import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from z0int import shadow_evals as service


class OrchestrationTests(unittest.TestCase):
    def test_atomic_private_receipt(self):
        with TemporaryDirectory() as root:
            path = Path(root) / 'result.json'
            service.save(path, {'status': 'blocked'})
            self.assertEqual(json.loads(path.read_text()), {'status': 'blocked'})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            service.save(path, {'status': 'completed'})
            self.assertEqual(json.loads(path.read_text())['status'], 'completed')
            self.assertFalse(path.with_name('result.json.new').exists())

    def test_no_calibration_never_enables_scores(self):
        with TemporaryDirectory() as root:
            self.assertFalse(service.calibration_state({'root': root})['scores_enabled'])

    def test_insufficient_offers_prevents_provider_calls(self):
        with TemporaryDirectory() as root:
            job = {'job_id': 'a' * 64, 'session_id': 'session', 'entry_id': 'entry', 'sha256': 'b' * 64, 'tier': 'CHECK'}
            with patch('z0int.shadow_evals_fanout.build_bundle', return_value={'packet': 'same'}), patch('z0int.shadow_evals_fanout.select_offers', return_value={'offers': [], 'exclusions': [{'reason': 'unknown_objective_inputs'}]}), patch('z0int.shadow_evals_fanout.run_candidates') as complete:
                result = service.process_job(job, {'root': root})
            complete.assert_not_called()
            self.assertEqual(result['status'], 'blocked')
            self.assertEqual(result['candidates'], [])
            self.assertTrue((Path(root) / 'results' / (job['job_id'] + '.json')).exists())

    def test_missing_judge_prevents_completion(self):
        with patch('z0int.shadow_evals_fanout.complete') as complete:
            result = service.judge_candidate({}, {}, {'content': 'draft'}, {})
        complete.assert_not_called()
        self.assertEqual(result['error'], 'frontier_subscription_judge_not_configured')

    def test_calibration_gate_binds_judge_and_rubric(self):
        from z0int.shadow_evals_judge import RUBRIC
        with TemporaryDirectory() as root:
            config = {'root': root, 'judge': {'provider': 'subscription', 'model': 'frontier'}}
            service.save(Path(root) / 'calibration-summary.json', {'complete': True, 'scores_enabled': True, 'evaluated': 153, 'failures': 103, 'nonfailures': 50, 'judge': config['judge'], 'rubric_sha256': service.digest(RUBRIC)})
            self.assertTrue(service.calibration_state(config)['scores_enabled'])
            self.assertFalse(service.calibration_state({**config, 'judge': {'provider': 'different', 'model': 'frontier'}})['scores_enabled'])
            service.save(Path(root) / 'calibration-summary.json', {'complete': True, 'scores_enabled': True, 'evaluated': 152, 'failures': 102, 'nonfailures': 50, 'judge': config['judge'], 'rubric_sha256': service.digest(RUBRIC)})
            self.assertFalse(service.calibration_state(config)['scores_enabled'])

    def test_malformed_judgments_do_not_shrink_authorized_calibration_cohort(self):
        cases = [{'case_id': str(i), 'gold_miss': i < 27, 'status': 'ready',
                  'job': {'text': 'ask'}, 'bundle': {}, 'candidate': {'content': 'answer'}}
                 for i in range(40)]
        with TemporaryDirectory() as root:
            config = {'root': root, 'calibration_positive': 27, 'calibration_negative': 13}
            with patch('z0int.shadow_evals_judge.calibration_cases', return_value=cases), patch.object(service, 'judge_candidate', return_value={'error': 'invalid_judge_response'}) as judge:
                state = service.calibrate(config, 'unused')
            self.assertEqual(judge.call_count, 40)
            self.assertEqual(state['selected'], 40)
            self.assertEqual(state['n'], 40)
            self.assertFalse(state['scores_enabled'])

    def test_shared_transport_failure_stops_calibration_without_retry(self):
        cases = [{'case_id': str(i), 'gold_miss': i < 27, 'status': 'ready',
                  'job': {'text': 'ask'}, 'bundle': {}, 'candidate': {'content': 'answer'}}
                 for i in range(40)]
        with TemporaryDirectory() as root:
            config = {'root': root, 'calibration_positive': 27, 'calibration_negative': 13}
            with patch('z0int.shadow_evals_judge.calibration_cases', return_value=cases), patch.object(service, 'judge_candidate', return_value={'error': {'code': 'provider_http_error', 'http_status': 403}}) as judge:
                state = service.calibrate(config, 'unused')
            self.assertEqual(judge.call_count, 1)
            self.assertFalse(state['scores_enabled'])



    def test_report_preserves_unknown_dimension_without_numeric_credit(self):
        with TemporaryDirectory() as root:
            service.save(Path(root) / 'results' / 'job.json', {
                'job_id': 'a' * 64, 'bundle_sha256': 'b' * 64,
                'selection': {'offers': []}, 'candidates': [{
                    'provider': 'local', 'model': 'fixture',
                    'judgment': {'dimensions': {'coverage': {'score': None}}},
                }],
            })
            result = service.report({'root': root})
            self.assertEqual(result['candidate_rows'], 1)
            self.assertFalse(result['scores_enabled'])
            self.assertEqual(json.loads((Path(root) / 'findings.json').read_text()), [])


if __name__ == '__main__':
    unittest.main()
