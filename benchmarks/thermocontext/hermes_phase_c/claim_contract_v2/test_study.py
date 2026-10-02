"""No-network prompt-contract and first-forward admission tests."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_study as study

PRIOR = os.environ.get('HERMES_PRIOR_COHORT')


class PromptTemplateTests(unittest.TestCase):
    def test_only_the_user_prompt_changes(self):
        old = {'model': 'upstage/solar-mini4', 'max_tokens': 4096,
               'response_format': {'type': 'json_object'}, 'messages': [
                   {'role': 'system', 'content': 'Complete original system /fixed/profile'},
                   {'role': 'user', 'content': 'Old prompt\n\n' + study.base.SENTINEL}]}
        transformed = study.prompt_template(old, 'Old prompt')
        transformed['messages'][1]['content'] = old['messages'][1]['content']
        self.assertEqual(transformed, old)
        self.assertEqual(old['messages'][1]['content'], 'Old prompt\n\n' + study.base.SENTINEL)

    def test_non_exact_predecessor_template_rejected(self):
        for user in ({'role': 'user', 'content': 'Unmatched prompt'},
                     {'role': 'user', 'content': 'Old\n\n' + study.base.SENTINEL, 'extra': True}):
            with self.assertRaises(ValueError):
                study.prompt_template({'messages': [{'role': 'system', 'content': 'system'}, user]}, 'Old')


@unittest.skipUnless(PRIOR, 'set HERMES_PRIOR_COHORT to the preserved five-failure cohort')
class FrozenPromptStudyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / 'v2'
        self.prior = Path(PRIOR)
        with patch.object(study.base.driver, 'resolve_secret') as credentials:
            self.frozen = study.prepare(self.out, self.prior)
            credentials.assert_not_called()

    def test_all_contexts_and_previous_failures_stay_unchanged(self):
        self.assertEqual(study.validate(self.out), self.frozen)
        for case in study.base.CASES:
            for name in ('selection.json', 'context.txt'):
                self.assertEqual((self.out / 'prepared' / case / name).read_bytes(),
                                 (self.prior / 'prepared' / case / name).read_bytes())
        for path, expected in self.frozen['prior_artifacts'].items():
            self.assertEqual(study.base.digest(Path(path).read_bytes()), expected)
        result = study.replay(self.out)
        self.assertEqual(result['status'], 'NOT_RUN')
        self.assertEqual(result['physical_posts_including_failures'], 0)
        self.assertTrue(result['predecessor_cohort_not_pooled'])
        self.assertEqual(self.frozen['prior_semantic_failures'], 5)
        self.assertLessEqual(max(self.frozen['expected_wire_bytes_from_prior_native_template'].values()), 20000)

    def test_first_forward_already_requires_old_complete_system(self):
        case = 'full-complete'
        context = (self.out / 'prepared' / case / 'context.txt').read_text()
        request = study.base.read(self.prior / 'runs' / case / 'native-request.bin')
        request['messages'][1]['content'] = study.PROMPT + '\n\n' + context
        route = study.base.read(self.out / 'route.json')
        policy = study.base.invariant_policy(study.base.driver.OneRequestPolicy,
            template_path=self.out / 'wire-template.json', prompt=study.PROMPT)
        def instance():
            return policy(expected_context=context, max_output_tokens=4096, route=route, require_json_object=True)
        changed = copy.deepcopy(request)
        changed['messages'][0]['content'] += '\nNew profile path'
        with self.assertRaises(study.base.driver.BudgetRejected):
            instance().admit(study.base.driver.serialize_request(changed))
        instance().admit(study.base.driver.serialize_request(request))
        self.assertEqual(study.validate(self.out), self.frozen)

    def test_missing_template_has_no_fallback(self):
        (self.out / 'wire-template.json').unlink()
        with self.assertRaises(FileNotFoundError):
            study.validate(self.out)

    def test_prompt_artifact_tampering_is_rejected(self):
        path = self.out / 'prepared/full-complete/prompt.txt'
        path.write_text(path.read_text() + 'Extra instruction')
        with self.assertRaisesRegex(ValueError, 'preparation changed'):
            study.validate(self.out)


if __name__ == '__main__':
    unittest.main()
