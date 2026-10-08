"""Calibrate the post-run MILP oracle against independent exhaustive search."""
import pytest

pytest.importorskip("jax", exc_type=ModuleNotFoundError)
pytest.importorskip("scipy", exc_type=ModuleNotFoundError)

from benchmarks.thermocontext import audit_next_wave as audit
from benchmarks.thermocontext.audit_reference_milp import solve_reference


@pytest.mark.parametrize("n,seed", [(8, 0), (12, 5), (16, 11)])
def test_independent_milp_matches_exhaustive_frozen_cohort(n, seed):
    task = audit.load_frozen().make_task(n, seed)
    _, optimum = audit.independent_exact(task)
    result = solve_reference(task)
    assert result["certified_to_reported_numerical_tolerance"], result
    assert abs(result["original_float32_energy"] - optimum) <= audit.TOL
    assert result["tokens"] <= task.budget


def test_rejects_unbounded_postrun_oracle_budget():
    with pytest.raises(ValueError, match="at most five seconds"):
        solve_reference(audit.load_frozen().make_task(8, 0), time_limit=10)
