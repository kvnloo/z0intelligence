"""Frozen host-only wrapper; the earlier independent checker is unchanged."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'causal_context'))
from frozen_check import grade as original_grade


def grade(case_id: str, answer: object) -> dict:
    size, control = case_id.split('-', 1)
    if size not in {'full', 'minimal'}:
        raise ValueError('unknown frozen size arm')
    result = original_grade(control, answer)
    # The original judge permits all full-pool citations. This additional fixed
    # visibility rule prevents a minimal case from citing an omitted source.
    if size == 'minimal' and isinstance(answer, dict):
        allowed = {'limits'}
        if control != 'missing_source_identity':
            allowed.add('source-identity')
        if control == 'contradictory_source_identity':
            allowed.add('source-identity-conflict')
        citations = answer.get('citations')
        if isinstance(citations, list) and any(isinstance(c, str) and c not in allowed for c in citations):
            result['problems'].append('citation_not_in_visible_subset')
            result['verified_success'] = False
    return result
