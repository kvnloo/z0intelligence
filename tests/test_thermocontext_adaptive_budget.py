import types

import numpy as np
import pytest

from benchmarks.thermocontext import adaptive_budget


class Task:
    def __init__(self):
        self.items = [types.SimpleNamespace(tokens=3), types.SimpleNamespace(tokens=2)]
        self.budget = 3
        self.J = np.zeros((2, 2), dtype=np.float32)

    def unary(self):
        return np.array([-2.0, -1.0], dtype=np.float32)

    def energy(self, x):
        return float(self.unary() @ np.asarray(x, dtype=np.float32))

    def verify(self, x):
        raise AssertionError("proposal may not inspect frozen verifier")


def test_fixed_budget_preserves_feasible_incumbent_across_collapse(monkeypatch):
    calls = []

    def fake_sample(unary, J, seed, *, chains, sweeps, burn_in, beta, initial):
        calls.append({"unary": unary.copy(), "sweeps": sweeps, "burn_in": burn_in})
        # First stage has the optimum; later stages collapse to empty.
        state = [1, 0] if len(calls) == 1 else [0, 0]
        trajectory = np.broadcast_to(state, (sweeps - burn_in, chains, 2)).astype(bool).copy()
        return types.SimpleNamespace(trajectories=trajectory,
            timings={"build_ms": 1, "compile_ms": 2, "warm_sampling_ms": 3, "total_ms": 6},
            dependency={"revision": "test"}, metadata={"max_degree": 0, "color_blocks": 1})

    monkeypatch.setattr(adaptive_budget, "sample_qubo", fake_sample)
    result = adaptive_budget.propose(Task(), 1, chains=4, sweeps=16)
    assert len(calls) == 4
    assert sum(c["sweeps"] for c in calls) == 16
    assert result["trajectories"].shape == (8, 4, 2)
    assert result["metadata"]["sample_count"] == 32
    assert result["metadata"]["incumbent_energy"] == -2.0
    assert result["metadata"]["incumbent_mask"] == [True, False]
    assert result["metadata"]["empty_samples"] == 24
    assert result["metadata"]["selection_objective"] == "frozen_original_task_energy"
    assert result["metadata"]["stage_history"][-1]["empty_fraction"] == 1.0


def test_price_initialization_scales_with_distractors():
    small = adaptive_budget.initial_price(np.zeros(8), np.full(8, 20), 150, beta=3.5)
    large = adaptive_budget.initial_price(np.zeros(128), np.full(128, 20), 150, beta=3.5)
    assert 0 <= small < large <= 64


def test_empty_safeguard_lowers_price_without_negative_subsidy():
    price, low, high, reason = adaptive_budget.update_price(
        price=8, lower=2, upper=None, mean_tokens=0, empty_fraction=1, budget=150)
    assert (price, low, high) == (5, 2, 8)
    assert reason == "empty_context_safeguard"
    price, *_ = adaptive_budget.update_price(
        price=0, lower=0, upper=None, mean_tokens=0, empty_fraction=1, budget=150)
    assert price == 0


def test_schedule_rejects_unaccounted_unequal_work():
    with pytest.raises(ValueError, match="multiple of 8"):
        adaptive_budget.propose(Task(), 1, chains=4, sweeps=15)
