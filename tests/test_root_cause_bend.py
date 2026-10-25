"""Behavioral root-cause contracts and optional executable Bend parity.

Set BEND to the MBP Bend executable to include differential execution.
"""
from __future__ import annotations

import importlib.util
import itertools
import os
from pathlib import Path

import pytest

from z0int.fsm.root_cause import r1, r2, r3, r4
from z0int.fsm.root_canaries import report_only_expired

ROOT = Path(__file__).resolve().parents[1]
FIELDS = {
    r1: ("target", "before_metric", "functional_probe", "expiry_valid"),
    r2: ("same_metric", "in_bounds_twice", "separated_24h"),
    r4: ("same_window", "share_at_least_30"),
}


@pytest.mark.parametrize("predicate,names", list(FIELDS.items()))
def test_mandatory_root_cause_evidence(predicate, names):
    for values in itertools.product((False, True), repeat=len(names)):
        assert predicate(dict(zip(names, values))) == ("allow" if all(values) else "refuse")
    for name in names:
        for invalid in (None, 0, 1, "true", [], {}):
            facts = dict.fromkeys(names, True)
            facts[name] = invalid
            assert predicate(facts) == "escalate"
        facts = dict.fromkeys(names, True)
        del facts[name]
        assert predicate(facts) == "escalate"


def test_rollback_recommendation_requires_owned_lease_and_failed_probe():
    for passed, owned, leased in itertools.product((False, True), repeat=3):
        expected = "allow" if passed else "rollback" if owned and leased else "escalate"
        assert r3({"probe_passed": passed, "owned": owned, "leased": leased}) == expected
    for name in ("probe_passed", "owned", "leased"):
        facts = {"probe_passed": True, "owned": True, "leased": True}
        del facts[name]
        assert r3(facts) == "escalate"


@pytest.mark.parametrize("seconds,expired", [(0, False), (259199, False), (259200, True), (259201, True)])
def test_report_only_expiry_boundary(seconds, expired):
    assert report_only_expired(True, True, seconds) is expired
    assert report_only_expired(False, True, seconds) is False
    assert report_only_expired(True, False, seconds) is False


@pytest.mark.skipif(not os.environ.get("BEND"), reason="BEND is required for executable parity")
def test_executable_bend_root_cause_parity():
    path = ROOT / "bend/root_cause_fsm/parity.py"
    spec = importlib.util.spec_from_file_location("root_cause_bend_parity", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    receipt = module.run_parity()
    assert receipt["equal"], receipt
    assert set(receipt["groups"]) == {"R1", "R2", "R3", "R4", "R7"}
    assert all(group["compared"] == group["cases"] for group in receipt["groups"].values())
