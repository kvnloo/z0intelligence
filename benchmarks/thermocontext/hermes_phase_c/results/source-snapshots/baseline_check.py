"""Independent factual checker for a NEW development task, not Phase A's judge.

This module must not be mounted into model-visible context or candidate tools.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def grade(answer: object) -> dict:
    expected = {
        "tested_source": "a0bca744067d04f05904319d3d919be30c336556",
        "baseline_verified": 40, "baseline_attempted": 40,
        "guarded_verified": 40, "guarded_attempted": 40,
        "baseline_provider_requests_per_task": 2,
        "guarded_provider_requests_per_task": 1,
        "hermes_speedup_established": False,
        "scope": "single_fixture_python_typesafe",
    }
    issues = []
    if not isinstance(answer, dict):
        return {"verified_success": False, "problems": ["answer_not_object"]}
    if set(answer) != set(expected) | {"citations"}:
        issues.append("unexpected_or_missing_fields")
    for key, value in expected.items():
        if type(answer.get(key)) is not type(value) or answer.get(key) != value:
            issues.append("incorrect:" + key)
    citations = answer.get("citations")
    if (not isinstance(citations, list) or any(not isinstance(c, str) for c in citations)
            or not {"limits", "source-identity", "baseline", "guarded"} <= set(citations)
            or not set(citations) <= {"limits", "source-identity", "baseline", "guarded", "gates",
                                     "task_verified_ms", "input_tokens", "output_tokens"}):
        issues.append("missing_or_unknown_citation")
    return {"verified_success": not issues, "problems": issues,
            "scope": "one_inspected_development_claim_not_population_noninferiority"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("answer", type=Path)
    args = parser.parse_args()
    print(json.dumps(grade(json.loads(args.answer.read_text())), sort_keys=True))
