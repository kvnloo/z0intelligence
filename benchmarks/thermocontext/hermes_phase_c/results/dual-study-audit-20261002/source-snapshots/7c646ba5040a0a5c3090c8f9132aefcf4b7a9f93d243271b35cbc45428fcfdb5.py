"""Independent development-task judge. Never expose this module to the model.

This is a new abstention-capable task; neither the Phase A verifier nor the
earlier Phase-C baseline checker is changed. Labels come from inspected public
evidence and explicit synthetic interventions, not a protected holdout.
"""
from __future__ import annotations


def grade(case_id: str, answer: object) -> dict:
    allowed = {'limits', 'source-identity', 'gates', 'baseline', 'guarded',
               'task_verified_ms', 'input_tokens', 'output_tokens'}
    if case_id == 'complete':
        expected = {'status': 'SUPPORTED',
                    'tested_source': 'a0bca744067d04f05904319d3d919be30c336556',
                    'hermes_speedup_established': False, 'reason': 'supported'}
        required = {'limits', 'source-identity'}
    elif case_id == 'missing_source_identity':
        expected = {'status': 'ABSTAIN', 'tested_source': None,
                    'hermes_speedup_established': False, 'reason': 'missing_evidence'}
        allowed.remove('source-identity')
        required = {'limits'}
    elif case_id == 'contradictory_source_identity':
        expected = {'status': 'ABSTAIN', 'tested_source': None,
                    'hermes_speedup_established': False, 'reason': 'conflicting_evidence'}
        allowed.add('source-identity-conflict')
        required = {'limits', 'source-identity', 'source-identity-conflict'}
    else:
        raise ValueError('unknown frozen case')
    problems = []
    if not isinstance(answer, dict):
        return {'verified_success': False, 'problems': ['answer_not_object']}
    if set(answer) != set(expected) | {'citations'}:
        problems.append('unexpected_or_missing_fields')
    for key, value in expected.items():
        if type(answer.get(key)) is not type(value) or answer.get(key) != value:
            problems.append('incorrect:' + key)
    citations = answer.get('citations')
    if (not isinstance(citations, list) or any(not isinstance(c, str) for c in citations)
            or len(citations) != len(set(citations)) or not required <= set(citations)
            or not set(citations) <= allowed):
        problems.append('invalid_citations')
    return {'verified_success': not problems, 'problems': problems,
            'scope': 'one_inspected_development_task_and_synthetic_controls'}
