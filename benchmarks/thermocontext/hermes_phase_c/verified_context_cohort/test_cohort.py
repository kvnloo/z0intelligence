"""Offline tests. Synthetic transports never call a provider or read credentials."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_cohort as cohort
from cohort_check import grade

HERMES = os.environ.get('HERMES_SOURCE_TREE')
ROUTE = cohort.PHASE_C / 'results/provider-requalification-20261002/routes/nous-cheap-solar.json'


def correct_answer(case_id):
    control = case_id.split('-', 1)[1]
    conflict, missing = control == 'contradictory_source_identity', control == 'missing_source_identity'
    return {'status': 'ABSTAIN' if conflict or missing else 'SUPPORTED',
        'tested_source': None if conflict or missing else 'a0bca744067d04f05904319d3d919be30c336556',
        'hermes_speedup_established': False,
        'reason': 'conflicting_evidence' if conflict else 'missing_evidence' if missing else 'supported',
        'citations': ['limits'] + ([] if missing else ['source-identity'])
            + (['source-identity-conflict'] if conflict else [])}


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.pools = cohort.discriminator.build_cases(cohort.read(
            cohort.PHASE_C / 'causal_context/fixtures/development_pool.json'))

    def test_exact_refs_provenance_contradictions_and_gaps_survive(self):
        for control, pool in self.pools.items():
            with self.subTest(control=control):
                full, minimal = (cohort.render_selection(pool, size) for size in ('full', 'minimal'))
                actual = json.loads(minimal['context'])
                expected = [ref for ref in pool['evidence'] if ref['source_id'] in
                            {'limits', 'source-identity', 'source-identity-conflict'}]
                self.assertEqual(actual['evidence'], expected)
                self.assertEqual(actual['contradictions'], pool['contradictions'])
                self.assertEqual(actual['unresolved_gaps'], pool['unresolved_gaps'])
                self.assertLess(len(minimal['context']), len(full['context']))
                self.assertEqual(full['context'], cohort.discriminator.deterministic_bundle(pool))

    def test_original_judge_and_missing_citation_wrapper(self):
        for case_id in cohort.CASES:
            self.assertTrue(grade(case_id, correct_answer(case_id))['verified_success'])
        answer = correct_answer('minimal-complete')
        answer['citations'].append('baseline')
        self.assertTrue(grade('full-complete', answer)['verified_success'])
        self.assertFalse(grade('minimal-complete', answer)['verified_success'])

    def test_abstention_and_contradiction_are_required(self):
        for case in ('minimal-missing_source_identity', 'full-contradictory_source_identity'):
            self.assertFalse(grade(case, correct_answer('full-complete'))['verified_success'])

    def test_non_evidence_changes_are_rejected_before_forwarding(self):
        with tempfile.TemporaryDirectory() as tmp:
            template = Path(tmp) / 'template.json'
            policy_type = cohort.invariant_policy(cohort.driver.OneRequestPolicy,
                template_path=template, prompt='Question')
            route = cohort.read(ROUTE)
            def policy(context='Context'):
                return policy_type(expected_context=context, max_output_tokens=4096,
                    route=route, require_json_object=True)
            request = {'model': route['model'], 'max_tokens': 4096,
                'response_format': {'type': 'json_object'}, 'messages': [
                    {'role': 'system', 'content': 'Full system /constant/profile'},
                    {'role': 'user', 'content': 'Question\n\nContext'}]}
            original = policy()
            original.admit(cohort.canonical(request))
            with self.assertRaises(cohort.driver.BudgetRejected):
                original.admit(cohort.canonical(request))
            changed = copy.deepcopy(request)
            changed['messages'][1]['content'] = 'Question\n\nOther evidence'
            policy('Other evidence').admit(cohort.canonical(changed))
            mutations = []
            changed = copy.deepcopy(request)
            changed['messages'][0]['content'] += '/different'
            mutations.append(changed)
            changed = copy.deepcopy(request)
            changed['max_tokens'] = 1024
            mutations.append(changed)
            changed = copy.deepcopy(request)
            changed['messages'][1]['content'] = 'Changed Question\n\nContext'
            mutations.append(changed)
            changed = copy.deepcopy(request)
            changed['messages'].append({'role': 'user', 'content': 'leaked case label'})
            mutations.append(changed)
            for changed in mutations:
                with self.assertRaises(cohort.driver.BudgetRejected):
                    policy().admit(cohort.canonical(changed))


@unittest.skipUnless(HERMES, 'set HERMES_SOURCE_TREE for pinned native offline preparation')
class NativePreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root / 'cohort'
        self.auth = self.root / 'external-auth-no-secrets'
        self.auth.mkdir()
        self.args = SimpleNamespace(out=self.out, hermes_repo=Path(HERMES),
            route_file=ROUTE, nous_auth_home=self.auth)
        cohort.prepare(self.args)

    def test_native_preparation_and_source_freeze(self):
        freeze = cohort.validate_freeze(self.out)
        self.assertEqual(freeze['provider_calls_at_freeze'], 0)
        self.assertEqual(cohort.replay(self.out)['status'], 'NOT_RUN')
        for case_id in cohort.CASES:
            case = self.out / 'prepared' / case_id
            self.assertTrue(cohort.read(case / 'native-admission.json')['exact_context_preserved'])
            self.assertEqual((case / 'prompt.txt').read_text(), cohort.discriminator.PROMPT)

    def test_selection_tampering_blocks_execution(self):
        path = self.out / 'prepared/full-complete/context.txt'
        path.write_text(path.read_text() + 'Edited')
        with patch.object(cohort.driver, 'run') as provider:
            with self.assertRaisesRegex(ValueError, 'preparation changed'):
                cohort.execute(self.args)
            provider.assert_not_called()

    def fake_driver(self, semantic_failure=None, infrastructure_failure=None):
        observed_paths = []
        def fake(args, *, prepared_case):
            # This test fixture writes synthetic records only. No driver.run,
            # worker, auth resolver, network client, or actual provider executes.
            self.assertFalse(args.out.exists())
            observed_paths.append(args.out)
            args.out.mkdir()
            (args.out / 'prepared').mkdir()
            case_id = prepared_case['metadata']['case_id']
            selection, prompt = prepared_case['selection'], prepared_case['prompt']
            route = cohort.read(args.route_file)
            request = {'model': route['model'], 'max_tokens': 4096,
                'response_format': {'type': 'json_object'}, 'messages': [
                    {'role': 'system', 'content': 'SYNTHETIC TEST ONLY ' + str(args.out / 'profile')},
                    {'role': 'user', 'content': prompt + '\n\n' + selection['context']}]}
            raw = cohort.canonical(request)
            policy = cohort.driver.OneRequestPolicy(expected_context=selection['context'],
                max_output_tokens=4096, route=route, require_json_object=True)
            forwarded = policy.admit(raw)
            answer = correct_answer(case_id)
            if case_id == semantic_failure:
                answer['hermes_speedup_established'] = True
            usage = {'prompt_tokens': len(raw) // 4, 'completion_tokens': 100, 'cost': 0.0004}
            failure = case_id == infrastructure_failure
            if failure:
                answer, usage = None, None
            response = {'model': route['model'], 'provider': None, 'usage': usage,
                'choices': [{'message': {'content': json.dumps(answer)}}]}
            call = {'kind': 'inference', 'physical_attempt': 1, 'upstream_sent': True,
                'endpoint': route['endpoint'], 'native_request': request, 'forwarded_request': forwarded,
                'native_wire_sha256': cohort.digest(raw), 'native_wire_bytes': len(raw),
                'forwarded_wire_sha256': cohort.digest(raw), 'forwarded_wire_bytes': len(raw),
                'response': response, 'status_code': 429 if failure else 200,
                'served_model': route['model'], 'served_provider': None, 'usage': usage, 'wall_ns': 100}
            frozen = {'max_output_tokens': 4096, 'max_serialized_request_bytes': 20000,
                'max_physical_inference_attempts': 1, 'response_format_requested': {'type': 'json_object'},
                'context_sha256': selection['context_sha256'], 'prompt_sha256': cohort.digest(prompt.encode()),
                'driver_sha256': cohort.digest((cohort.PHASE_C / 'live_baseline.py').read_bytes()),
                'checker_sha256': cohort.digest((HERE / 'cohort_check.py').read_bytes()),
                'route_sha256': cohort.driver.route_digest(route)}
            cohort.save(args.out / 'freeze.json', frozen)
            receipt = {'provider_calls': 1, 'freeze_sha256': cohort.digest((args.out / 'freeze.json').read_bytes()),
                'outcome': grade(case_id, answer), 'total_wall_ns': 1000,
                'preparation_wall_ns': 100, 'execution_window_wall_ns': 900,
                'served_model': route['model'], 'served_provider': None, 'usage': usage,
                'resource_comparison_eligible': not failure, 'reported_cost': None if failure else usage['cost'],
                'returncode': 0, 'error_type': None}
            for name, value in [('receipt.json', receipt), ('physical-calls.json', [call]),
                    ('answer.json', answer), ('prepared/selection.json', selection), ('route.json', route),
                    ('worker-result.json', {'result': {'final_response': json.dumps(answer)}})]:
                cohort.save(args.out / name, value)
            (args.out / 'prompt.txt').write_text(prompt)
            (args.out / 'native-request.bin').write_bytes(raw)
            (args.out / 'forwarded-request.bin').write_bytes(raw)
            return receipt
        return fake, observed_paths

    def test_five_calls_same_path_fresh_state_semantic_failure_retained(self):
        fake, paths = self.fake_driver(semantic_failure='full-complete')
        with patch.object(cohort.driver, 'run', fake):
            result = cohort.execute(self.args)
        self.assertEqual(len(paths), 5)
        self.assertEqual(len(set(paths)), 1)
        self.assertFalse(paths[0].exists())
        self.assertEqual(result['physical_posts_including_failures'], 5)
        self.assertFalse(result['all_cases_verified'])
        self.assertFalse(result['backend_provider_causal_attribution'])
        self.assertTrue(result['pairs']['missing_source_identity']['verified_prompt_token_reduction'])
        self.assertEqual(cohort.replay(self.out), result)
        with patch.object(cohort.driver, 'run') as provider:
            with self.assertRaises(FileExistsError):
                cohort.execute(self.args)
            provider.assert_not_called()

    def test_transport_failure_stops_without_retry_and_usage_stays_unknown(self):
        fake, paths = self.fake_driver(infrastructure_failure='minimal-complete')
        with patch.object(cohort.driver, 'run', fake):
            result = cohort.execute(self.args)
        self.assertEqual(len(paths), 2)
        self.assertEqual(result['physical_posts_including_failures'], 2)
        self.assertIsNone(result['known_work_reported_cost_usd'])
        self.assertFalse(result['usage_complete_for_every_physical_post'])

    def test_interrupted_attempt_does_not_claim_zero_cost_or_zero_posts(self):
        def interrupted(args, **kwargs):
            args.out.mkdir()
            raise RuntimeError('synthetic interrupted transport')
        with patch.object(cohort.driver, 'run', interrupted):
            result = cohort.execute(self.args)
        self.assertEqual(result['status'], 'INCOMPLETE')
        self.assertIsNone(result['physical_posts_including_failures'])
        self.assertIsNone(result['known_work_reported_cost_usd'])
        self.assertFalse(result['physical_attempt_accounting_complete'])

    def test_raw_wire_tampering_is_rejected(self):
        fake, _ = self.fake_driver()
        with patch.object(cohort.driver, 'run', fake):
            cohort.execute(self.args)
        path = self.out / 'runs/minimal-complete/native-request.bin'
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'raw wire'):
            cohort.replay(self.out)


if __name__ == '__main__':
    unittest.main()
