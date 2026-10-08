"""Public-objective deterministic controls, checked against independent search."""

from types import SimpleNamespace

import numpy as np
import pytest

from benchmarks.thermocontext.pair_greedy import component_dp, pair_greedy


def _task(unary, J, tokens, budget):
    return SimpleNamespace(
        unary=lambda: np.asarray(unary, dtype=np.float32),
        J=np.asarray(J, dtype=np.float32),
        items=[SimpleNamespace(tokens=t) for t in tokens], budget=budget,
    )


def _energy(task, mask):
    x = np.asarray(mask, dtype=np.float32)
    return float(task.unary() @ x + 0.5 * x @ task.J @ x)


def _brute(task):
    tokens = np.array([it.tokens for it in task.items])
    best = np.zeros(len(tokens), dtype=bool)
    best_energy = _energy(task, best)
    for bits in range(1 << len(tokens)):
        mask = np.array([(bits >> i) & 1 for i in range(len(tokens))], dtype=bool)
        if tokens @ mask > task.budget:
            continue
        energy = _energy(task, mask)
        if energy < best_energy:
            best, best_energy = mask, energy
    return best, best_energy


def test_pair_lookahead_crosses_single_addition_barrier():
    task = _task([0.3, 0.3, 0.2], [[0, -0.9, 0], [-0.9, 0, 0], [0, 0, 0]], [3, 3, 3], 6)
    np.testing.assert_array_equal(pair_greedy(task), [1, 1, 0])


def test_pair_greedy_respects_budget_and_is_not_claimed_exact():
    # Greedy takes the best immediate singleton, blocking the superior pair.
    task = _task([-1.1, 0.5, 0.5], [[0, 0, 0], [0, 0, -2], [0, -2, 0]], [2, 1, 1], 2)
    np.testing.assert_array_equal(pair_greedy(task), [1, 0, 0])
    # This case demonstrates feasibility; a separate trap makes the gap explicit.
    trap = _task([-1.1, -0.5, -0.5, -0.5], np.zeros((4, 4)), [3, 1, 1, 1], 3)
    mask = pair_greedy(trap)
    assert _energy(trap, mask) > _brute(trap)[1] + 0.1
    assert sum(it.tokens for it, take in zip(trap.items, mask) if take) <= trap.budget


def test_component_dp_empty_optimum_and_zero_budget():
    task = _task([0.5, 1.0], np.zeros((2, 2)), [1, 1], 2)
    mask, metadata = component_dp(task)
    assert not mask.any()
    assert metadata["proof_status"] == "additive_components_integer_budget"
    assert metadata["energy_float_tolerance"] == 1e-5
    task.budget = 0
    assert not component_dp(task)[0].any()
    assert not pair_greedy(task).any()


def test_component_dp_disconnected_edges_and_budget_tradeoff():
    J = np.zeros((6, 6))
    J[0, 1] = J[1, 0] = -1.3
    J[2, 3] = J[3, 2] = -1.7
    task = _task([0.2, 0.2, 0.4, 0.4, -0.1, 0.5], J, [2, 2, 3, 3, 1, 1], 7)
    mask, metadata = component_dp(task)
    assert abs(_energy(task, mask) - _brute(task)[1]) <= 1e-5
    assert metadata["component_sizes"] == [2, 2, 1, 1]
    assert metadata["enumerated_local_states"] == 12
    assert metadata["dp_states"] <= task.budget + 1


def test_component_cap_rejects_before_exponential_enumeration():
    J = np.zeros((5, 5))
    for i in range(4):
        J[i, i + 1] = J[i + 1, i] = -0.1
    task = _task([0] * 5, J, [1] * 5, 3)
    with pytest.raises(ValueError, match="component.*5.*cap.*4"):
        component_dp(task, max_component_size=4)


@pytest.mark.parametrize("seed", range(12))
def test_component_dp_matches_independent_exhaustive_search_on_sparse_graphs(seed):
    rng = np.random.default_rng(seed)
    n = 8
    J = np.zeros((n, n))
    for a, b in [(0, 1), (1, 2), (3, 4), (4, 5), (6, 7)]:
        if rng.random() < 0.8:
            J[a, b] = J[b, a] = rng.uniform(-1.5, 1)
    tokens = rng.integers(1, 6, size=n)
    task = _task(rng.uniform(-0.5, 0.8, size=n), J, tokens, int(rng.integers(1, 16)))
    mask, metadata = component_dp(task)
    assert tokens @ mask <= task.budget
    assert abs(_energy(task, mask) - _brute(task)[1]) <= metadata["energy_float_tolerance"]
    np.testing.assert_array_equal(component_dp(task)[0], mask)
    greedy_mask = pair_greedy(task)
    assert tokens @ greedy_mask <= task.budget
    assert _energy(task, greedy_mask) >= _energy(task, mask) - 1e-5


def test_neither_algorithm_reads_oracle_ids_facts_or_verifier():
    class PublicOnlyTask:
        J = np.array([[0, -1], [-1, 0]], dtype=np.float32)
        budget = 2
        items = [SimpleNamespace(tokens=1), SimpleNamespace(tokens=1)]

        def unary(self):
            return np.array([0.2, 0.2], dtype=np.float32)

        def __getattr__(self, name):
            raise AssertionError(f"Nonpublic task attribute accessed: {name}")

    task = PublicOnlyTask()
    np.testing.assert_array_equal(pair_greedy(task), [1, 1])
    np.testing.assert_array_equal(component_dp(task)[0], [1, 1])


def test_exact_tie_breaking_is_deterministic_and_prefers_empty_at_zero_energy():
    task = _task([0, 0], np.zeros((2, 2)), [1, 1], 1)
    assert not component_dp(task)[0].any()
    assert not pair_greedy(task).any()


def test_tiny_nonzero_edges_are_not_dropped_from_components():
    J = np.array([[0, -1e-12], [-1e-12, 0]], dtype=np.float32)
    task = _task([0, 0], J, [1, 1], 2)
    _, metadata = component_dp(task)
    assert metadata["component_sizes"] == [2]


@pytest.mark.parametrize("bad_costs,budget", [([0, 1], 1), ([1.2, 1], 1), ([1, 1], -1)])
def test_invalid_integer_budget_problem_is_rejected(bad_costs, budget):
    task = _task([0, 0], np.zeros((2, 2)), bad_costs, budget)
    with pytest.raises(ValueError):
        component_dp(task)
    with pytest.raises(ValueError):
        pair_greedy(task)
