"""A–E pure decisions and read-only state guard reporting contracts."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from z0int.fsm.state_laws import evaluate

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/state-laws.json"
REQUIRED = {
    "A": ("current", "scope_matches", "agrees"),
    "B": ("disk_matches", "process_matches", "canonical"),
    "C": ("epoch_current", "no_active_hits", "coverage_complete"),
    "D": ("typed_receipt", "output_verified", "work_positive", "transport_ok", "target_current"),
    "E": ("measured", "scope_matches", "agrees"),
}
BITS = [(law, bit) for law, bits in REQUIRED.items() for bit in bits]
INVALID = [None, 0, 1, "true", "false", [], {}]
INCIDENTS = json.loads(FIXTURE.read_text())["checks"]


def complete(law):
    return dict.fromkeys(("available", *REQUIRED[law]), True)


@pytest.mark.parametrize("law", REQUIRED)
def test_all_required_bits_true_pass(law):
    assert evaluate(law, complete(law)) == "pass"


@pytest.mark.parametrize("law,bit", BITS)
def test_each_false_required_bit_is_violation(law, bit):
    facts = complete(law)
    facts[bit] = False
    assert evaluate(law, facts) == "violation"


@pytest.mark.parametrize("law,bit", BITS)
def test_each_missing_required_bit_is_unknown(law, bit):
    facts = complete(law)
    del facts[bit]
    assert evaluate(law, facts) == "unknown"


@pytest.mark.parametrize("value", INVALID)
@pytest.mark.parametrize("law,bit", BITS)
def test_each_nonboolean_required_bit_is_unknown(law, bit, value):
    facts = complete(law)
    facts[bit] = value
    assert evaluate(law, facts) == "unknown"


@pytest.mark.parametrize("law", REQUIRED)
@pytest.mark.parametrize("value", [False, *INVALID])
def test_unavailable_or_nonboolean_available_is_unknown(law, value):
    facts = complete(law)
    facts["available"] = value
    assert evaluate(law, facts) == "unknown"


@pytest.mark.parametrize("law", REQUIRED)
def test_missing_available_is_unknown(law):
    facts = complete(law)
    del facts["available"]
    assert evaluate(law, facts) == "unknown"


@pytest.mark.parametrize("law", REQUIRED)
def test_unavailable_takes_precedence_over_false_bits(law):
    facts = dict.fromkeys(("available", *REQUIRED[law]), False)
    assert evaluate(law, facts) == "unknown"
    assert evaluate(law, {"available": False}) == "unknown"


@pytest.mark.parametrize("law", REQUIRED)
def test_unknown_takes_precedence_over_known_violation(law):
    facts = complete(law)
    first, second, *_ = REQUIRED[law]
    facts[first] = False
    del facts[second]
    assert evaluate(law, facts) == "unknown"
    facts[second] = 1
    assert evaluate(law, facts) == "unknown"


@pytest.mark.parametrize("case", INCIDENTS, ids=lambda case: case["id"])
def test_incident_law_blocked_table(case):
    before = dict(case["facts"])
    status = evaluate(case["law"], case["facts"])
    assert status == case["expected_status"]
    assert (status != "pass") is case["expected_blocked"]
    assert case["facts"] == before


def test_fixture_covers_incidents_and_laws():
    expected = {1: {"C", "A"}, 2: {"D"}, 3: {"B"}, 4: {"B"},
                5: {"E", "A"}, 6: {"A"}, 7: {"C"}, 8: {"A"}, 9: {"D", "B"}}
    assert len({case["id"] for case in INCIDENTS}) == len(INCIDENTS)
    for incident, laws in expected.items():
        assert {case["law"] for case in INCIDENTS if case["incident"] == incident} >= laws


def run_guard(path, tmp_path):
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "Z0_GUARD_STATE": str(tmp_path / "state"),
        "Z0_GUARD_CACHE": str(tmp_path / "cache"),
        "Z0_GUARD_CONFIG": str(tmp_path / "config"),
    }
    return subprocess.run(
        [sys.executable, "-m", "z0int.fsm.guard", "state", "--input", str(path)],
        capture_output=True, text=True, env=env, check=False,
    )


def assert_report(result, cases, exit_code):
    assert result.returncode == exit_code, result.stderr
    report = json.loads(result.stdout)
    assert report["schema"] == "z0.state_guard.v1"
    assert report["mode"] == "report-only"
    assert isinstance(report["checks"], list)
    assert len(report["checks"]) == len(cases)
    actual = {row["id"]: row for row in report["checks"]}
    assert set(actual) == {case["id"] for case in cases}
    for case in cases:
        row = actual[case["id"]]
        assert row["law"] == case["law"]
        assert row["status"] == case["expected_status"]
        assert type(row["blocked"]) is bool
        assert row["blocked"] is case["expected_blocked"]
        assert isinstance(row["reason"], str) and row["reason"].strip()
    return report


def test_cli_seed_incident_report_is_read_only(tmp_path):
    source = FIXTURE.read_bytes()
    path = tmp_path / "input.json"
    path.write_bytes(source)
    assert_report(run_guard(path, tmp_path), INCIDENTS, 3)
    assert path.read_bytes() == source
    assert not (tmp_path / "state").exists()
    assert not (tmp_path / "cache").exists()
    assert not (tmp_path / "config").exists()


@pytest.mark.parametrize("statuses,exit_code", [
    (["pass"], 0),
    (["unknown"], 4),
    (["pass", "unknown"], 4),
    (["violation"], 3),
    (["unknown", "violation"], 3),
    (["pass", "unknown", "violation"], 3),
])
def test_cli_exit_precedence_and_report_only(tmp_path, statuses, exit_code):
    cases = []
    for index, status in enumerate(statuses):
        facts = complete("A")
        if status == "unknown":
            facts["available"] = False
        elif status == "violation":
            facts["agrees"] = False
        cases.append({"id": f"check-{index}", "law": "A", "facts": facts,
                      "expected_status": status, "expected_blocked": status != "pass"})
    path = tmp_path / "input.json"
    path.write_text(json.dumps({"checks": cases}))
    assert_report(run_guard(path, tmp_path), cases, exit_code)
    assert not (tmp_path / "state").exists()
