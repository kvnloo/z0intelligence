"""Schedule completeness: completion must be computed, never inferred.

The 2026-09-22 rep-0 incident happened because a completion claim was taken
from prose.  These tests pin the property that makes that impossible: a cell
with fewer than the declared repetitions, or fewer than the declared states in
any one repetition, is not complete -- and an incomplete schedule may not
project a capability number.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "check_schedule_completeness", _REPO / "scripts" / "check_schedule_completeness.py"
)
assert _spec and _spec.loader
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod
_spec.loader.exec_module(mod)

STATES = 28
REPS = 3


def meta(*, matrix=None, states=STATES, reps=REPS, post=None):
    m = {
        "run_id": "synthetic",
        "matrix": matrix or {"model_a": [256, 512]},
        "fixture": {"n_states": states},
        "settings": {"repetitions": reps},
    }
    if post is not None:
        m["post_run"] = post
    return m


def records(matrix=None, *, states=STATES, reps=REPS, skip=None, kind="measured"):
    """Full synthetic coverage, minus any (cell, rep, state) triples in `skip`."""
    matrix = matrix or {"model_a": [256, 512]}
    skip = skip or set()
    out = []
    for model, caps in matrix.items():
        for cap in caps:
            cell = f"{model}@{cap}"
            for rep in range(reps):
                for i in range(states):
                    state = f"s{i:02d}"
                    if (cell, rep, state) in skip:
                        continue
                    out.append(
                        {"kind": kind, "cell": cell, "repetition": rep, "state_id": state,
                         "invalid_cell": False, "model": model, "requested_max_tokens": cap}
                    )
    return out


def test_full_coverage_is_complete_and_may_project():
    rec = mod.completeness(meta(), records())
    assert rec["complete"] is True
    assert rec["cells_completed"] == rec["cells_expected"] == 2
    assert rec["n_measured"] == rec["n_expected"] == 2 * STATES * REPS
    allowed, why = mod.may_project_capability(rec)
    assert allowed is True, why


def test_rep0_only_cell_is_not_complete():
    """The exact incident: one repetition present, two absent."""
    recs = [r for r in records() if r["repetition"] == 0]
    rec = mod.completeness(meta(), recs)
    assert rec["complete"] is False
    assert rec["cells_completed"] == 0
    assert all(c["complete"] is False for c in rec["per_cell"])
    # 28 states x 1 rep, not 84
    assert rec["n_measured"] == 2 * STATES
    allowed, why = mod.may_project_capability(rec)
    assert allowed is False
    assert "omit" in why


def test_missing_single_state_in_one_rep_blocks_the_whole_cell():
    """Partial coverage of a repetition is still an incomplete cell."""
    rec = mod.completeness(meta(), records(skip={("model_a@256", 1, "s07")}))
    assert rec["complete"] is False
    by_cell = {c["cell"]: c for c in rec["per_cell"]}
    assert by_cell["model_a@256"]["complete"] is False
    assert by_cell["model_a@256"]["reps_completed"] == 2
    assert by_cell["model_a@256"]["reps_partial"] == [1]
    # the sibling cell is untouched by the other cell's gap
    assert by_cell["model_a@512"]["complete"] is True
    assert rec["cells_completed"] == 1


def test_two_of_three_reps_is_not_complete():
    recs = [r for r in records() if r["repetition"] in (0, 1)]
    rec = mod.completeness(meta(), recs)
    assert rec["complete"] is False
    assert rec["cells_completed"] == 0


def test_warmups_do_not_count_as_campaign_coverage():
    """A warmup is a real call but not coverage; it must not close a gap."""
    recs = records(skip={("model_a@256", 2, "s00")})
    recs += [
        {"kind": "warmup", "cell": "model_a@256", "repetition": 2, "state_id": "s00",
         "invalid_cell": False}
    ]
    rec = mod.completeness(meta(), recs)
    by_cell = {c["cell"]: c for c in rec["per_cell"]}
    assert by_cell["model_a@256"]["complete"] is False
    assert rec["complete"] is False


def test_invalid_cells_do_not_count_toward_coverage():
    recs = records()
    for r in recs:
        if r["cell"] == "model_a@256" and r["repetition"] == 0 and r["state_id"] == "s03":
            r["invalid_cell"] = True
    rec = mod.completeness(meta(), recs)
    by_cell = {c["cell"]: c for c in rec["per_cell"]}
    assert by_cell["model_a@256"]["complete"] is False


def test_stale_declared_counters_are_flagged_not_trusted():
    """run_metadata's own counters are advisory; records are authoritative."""
    rec = mod.completeness(
        meta(post={"n_measured_used": 354}), records()
    )
    assert rec["declared_progress_counters"] == 354
    assert rec["n_measured"] == 2 * STATES * REPS
    assert rec["declared_counters_agree_with_records"] is False


def test_agreeing_counters_report_agreement():
    rec = mod.completeness(meta(post={"n_measured_used": 2 * STATES * REPS}), records())
    assert rec["declared_counters_agree_with_records"] is True


def test_undeclared_cells_are_reported_not_silently_absorbed():
    recs = records()
    recs += [
        {"kind": "measured", "cell": "model_z@999", "repetition": r, "state_id": f"s{i:02d}",
         "invalid_cell": False}
        for r in range(REPS) for i in range(STATES)
    ]
    rec = mod.completeness(meta(), recs)
    assert rec["undeclared_cells_observed"] == ["model_z@999"]
    # undeclared work must not inflate the declared schedule's completion
    assert rec["cells_completed"] == rec["cells_expected"] == 2
    assert rec["n_measured"] == rec["n_expected"]


def test_missing_declaration_is_an_error_not_a_pass():
    """Absent a declared schedule we cannot claim completion at all."""
    with pytest.raises(ValueError):
        mod.completeness({"run_id": "x", "fixture": {"n_states": 28}, "settings": {"repetitions": 3}}, [])
    with pytest.raises(ValueError):
        mod.completeness({"matrix": {"m": [256]}, "settings": {"repetitions": 3}}, [])
    with pytest.raises(ValueError):
        mod.completeness({"matrix": {"m": [256]}, "fixture": {"n_states": 28}}, [])


def test_default_stopping_rule_is_the_strict_one():
    rec = mod.completeness(meta(), records())
    assert rec["stopping_rule"] == mod.DEFAULT_STOPPING_RULE
    assert "no early stop declared" in rec["stopping_rule"]
