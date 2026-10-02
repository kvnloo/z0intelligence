"""Constraint-controller checks; these are not sampler-quality experiments."""

from types import SimpleNamespace

import numpy as np
import pytest

from benchmarks.thermocontext.constrained_budget import project_arrays, project_budget, propose


def test_feasible_proposals_are_unchanged_and_input_is_not_mutated():
    proposals = np.array([[[True, False], [False, True]]])
    before = proposals.copy()
    result, stats = project_arrays([4.0, 5.0], np.zeros((2, 2)), [2, 3], 3, proposals)
    np.testing.assert_array_equal(result, before)
    np.testing.assert_array_equal(proposals, before)
    assert stats["deleted_bits"] == 0
    assert stats["raw_budget_valid_count"] == 2


def test_projection_preserves_complementarity_when_removing_noise():
    interactions = np.array([[0.0, -1.0, 0.0], [-1.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
    result, stats = project_arrays([0.1] * 3, interactions, [4, 4, 4], 8, [[1, 1, 1]])
    np.testing.assert_array_equal(result, [[1, 1, 0]])
    assert stats["deleted_bits"] == 1
    assert stats["projected_budget_valid_count"] == 1


def test_uses_energy_damage_per_released_token():
    result, _ = project_arrays([-0.3, -0.2], np.zeros((2, 2)), [10, 1], 1, [[1, 1]])
    np.testing.assert_array_equal(result, [[0, 1]])


def test_impossible_single_items_are_removed_before_other_deletions():
    result, stats = project_arrays([-100.0, 1.0], np.zeros((2, 2)), [200, 10], 100, [[1, 1]])
    np.testing.assert_array_equal(result, [[0, 1]])
    assert stats["individually_infeasible_deleted_bits"] == 1
    assert stats["empty_projection_from_nonempty_legal_support_count"] == 0


def test_zero_budget_and_all_impossible_inputs_return_empty_honestly():
    result, stats = project_arrays([-1.0] * 2, np.zeros((2, 2)), [1, 2], 0, [[1, 1], [0, 0]])
    assert not result.any()
    assert stats["projected_empty_count"] == 2
    assert stats["raw_empty_count"] == 1


def test_deletions_use_updated_original_energy_marginals():
    # Removing bit 0 changes bit 1's removal damage. Frozen marginals would
    # remove 2 next, while the actual current objective removes 1.
    interactions = np.array([[0.0, -4.0, 0.0], [-4.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
    result, _ = project_arrays([10.0, 1.0, -1.0], interactions, [1, 1, 1], 1, [[1, 1, 1]])
    np.testing.assert_array_equal(result, [[0, 0, 1]])


def test_vectorized_projection_matches_simple_reference_and_is_delete_only():
    rng = np.random.default_rng(29)
    n = 15
    unary = rng.normal(size=n)
    interactions = np.zeros((n, n))
    for i in range(n - 1):
        interactions[i, i + 1] = interactions[i + 1, i] = rng.normal()
    tokens = rng.integers(1, 12, size=n)
    proposals = rng.random((3, 7, n)) < 0.5
    budget = 17
    expected = proposals.reshape(-1, n).copy()
    for proposal in expected:
        proposal[tokens > budget] = False
        while tokens @ proposal > budget:
            damage_per_token = -(unary + interactions @ proposal) / tokens
            proposal[int(np.argmin(np.where(proposal, damage_per_token, np.inf)))] = False
    result, stats = project_arrays(unary, interactions, tokens, budget, proposals)
    np.testing.assert_array_equal(result, expected.reshape(proposals.shape))
    assert np.all(result <= proposals)
    assert np.all(result @ tokens <= budget)
    assert stats["max_deletions_per_proposal"] <= n
    assert stats["empty_projection_from_nonempty_legal_support_count"] == 0


@pytest.mark.parametrize(
    "unary,interactions,tokens,budget,proposals",
    [
        ([1.0], [[1.0]], [1], 1, [[1]]),
        ([float("nan")], [[0.0]], [1], 1, [[1]]),
        ([1.0], [[0.0]], [0], 1, [[1]]),
        ([1.0], [[0.0]], [1.5], 1, [[1]]),
        ([1.0], [[0.0]], [1], -1, [[1]]),
        ([1.0], [[0.0]], [1], 1, [[0.4]]),
        ([1.0], [[0.0]], [1], 1, [[1, 0]]),
    ],
)
def test_invalid_public_inputs_are_rejected(unary, interactions, tokens, budget, proposals):
    with pytest.raises(ValueError):
        project_arrays(unary, interactions, tokens, budget, proposals)


def test_task_wrapper_uses_only_public_objective_and_token_costs():
    task = SimpleNamespace(
        unary=lambda: np.array([0.1, 0.3]),
        J=np.zeros((2, 2)),
        items=[SimpleNamespace(tokens=2), SimpleNamespace(tokens=2)],
        budget=2,
    )
    result, _ = project_budget(task, np.array([[True, True]]))
    np.testing.assert_array_equal(result, [[True, False]])


def test_propose_uses_one_backend_call_and_preserves_raw_proposals(monkeypatch):
    pytest.importorskip("jax", exc_type=ModuleNotFoundError)
    pytest.importorskip("thrml", exc_type=ModuleNotFoundError)
    from benchmarks.thermocontext import thrml_backend

    calls = []
    raw = np.array([[[True, True], [False, True]]])
    task = SimpleNamespace(
        unary=lambda: np.array([0.1, 0.3]), J=np.zeros((2, 2)),
        items=[SimpleNamespace(tokens=2), SimpleNamespace(tokens=2)], budget=2,
    )

    def fake_sampler(unary, J, seed, **kwargs):
        calls.append((unary, J, seed, kwargs))
        return SimpleNamespace(
            trajectories=raw, timings={"compile_ms": 4.0, "warm_sampling_ms": 2.0},
            dependency={"test_double": True}, metadata={"sample_count": 2, "graph_degree": 0},
        )

    monkeypatch.setattr(thrml_backend, "sample_qubo", fake_sampler)
    result = propose(task, 92, chains=2, sweeps=2)
    assert len(calls) == 1
    assert calls[0][2:] == (92, {"chains": 2, "sweeps": 2})
    np.testing.assert_array_equal(result["raw_trajectories"], [[[1, 1], [0, 1]]])
    np.testing.assert_array_equal(result["trajectories"], [[[1, 0], [0, 1]]])
    assert result["metadata"]["graph_degree"] == 0
    assert result["metadata"]["budget_coupling_count"] == 0
    assert result["metadata"]["controller"]["resample_count"] == 0
    assert result["timings"]["compile_ms"] == 4.0


def test_genuine_thrml_integration_preserves_fixed_sample_count_and_sparse_graph():
    pytest.importorskip("jax", exc_type=ModuleNotFoundError)
    pytest.importorskip("thrml", exc_type=ModuleNotFoundError)
    task = SimpleNamespace(
        unary=lambda: np.array([0.1, 0.2, 0.5]),
        J=np.array([[0.0, -0.5, 0.0], [-0.5, 0.0, 0.0], [0.0, 0.0, 0.0]]),
        items=[SimpleNamespace(tokens=2) for _ in range(3)], budget=4,
    )
    result = propose(task, 112, chains=4, sweeps=8)
    assert result["dependency"]["package"] == "thrml"
    assert result["dependency"]["revision_verified"]
    assert result["metadata"]["backend"] == "thrml.IsingSamplingProgram/sample_states"
    assert result["metadata"]["sample_count"] == 16
    assert result["trajectories"].shape == (4, 4, 3)
    assert result["raw_trajectories"].shape == (4, 4, 3)
    assert np.all(result["trajectories"].sum(axis=-1) <= 2)
    assert np.all(result["trajectories"] <= result["raw_trajectories"])
    assert result["metadata"]["graph_degree"] == 1
    assert result["metadata"]["controller"]["verifier_calls"] == 0
