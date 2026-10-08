"""Independent energy/distribution checks for the real THRML adapter."""
import itertools

import numpy as np
import pytest

jax = pytest.importorskip("jax", exc_type=ModuleNotFoundError)
pytest.importorskip("thrml", exc_type=ModuleNotFoundError)
import jax.numpy as jnp

from benchmarks.thermocontext.thrml_backend import (
    THRML_REVISION,
    dependency_identity,
    qubo_to_ising,
    sample_qubo,
)


def test_ising_conversion_preserves_all_energy_differences():
    from thrml import Block, SpinNode
    from thrml.models import IsingEBM

    unary = np.array([0.4, -0.3, 0.8], dtype=np.float32)
    J = np.array([[0, -0.7, 0], [-0.7, 0, 0.2], [0, 0.2, 0]], dtype=np.float32)
    biases, edges, weights, offset = qubo_to_ising(unary, J)
    nodes = [SpinNode() for _ in unary]
    model = IsingEBM(nodes, [(nodes[i], nodes[j]) for i, j in edges],
                     jnp.asarray(biases), jnp.asarray(weights), jnp.asarray(3.5))
    for bits in itertools.product((False, True), repeat=3):
        x = np.asarray(bits, dtype=np.float32)
        actual = float(model.energy([jnp.asarray(bits)], [Block(nodes)]))
        expected = 3.5 * (unary @ x + 0.5 * x @ J @ x - offset)
        assert actual == pytest.approx(expected, abs=5e-7)


def test_real_thrml_distribution_matches_exact_boltzmann():
    unary = np.array([0.35, -0.18], dtype=np.float32)
    J = np.array([[0, -0.42], [-0.42, 0]], dtype=np.float32)
    beta = 1.7
    batch = sample_qubo(unary, J, 734, chains=128, sweeps=200, beta=beta)
    states = np.array(list(itertools.product((False, True), repeat=2)))
    energies = states @ unary + 0.5 * np.einsum("bi,ij,bj->b", states, J, states)
    probs = np.exp(-beta * energies)
    probs /= probs.sum()
    observed = np.array([(batch.trajectories == x).all(axis=-1).mean() for x in states])
    np.testing.assert_allclose(observed, probs, atol=0.025, rtol=0)
    assert batch.trajectories.shape == (100, 128, 2)
    assert batch.metadata["transitions_per_chain"] == 200
    assert batch.metadata["sample_count"] == 12800
    assert batch.metadata["backend"] == "thrml.IsingSamplingProgram/sample_states"
    assert batch.timings["compile_ms"] >= 0
    assert batch.timings["warm_sampling_ms"] > 0


def test_independent_spin_has_correct_conditional_and_no_edges():
    # This also exercises upstream THRML's empty pair-factor handling.
    batch = sample_qubo(np.array([0.6]), np.zeros((1, 1)), 18,
                        chains=128, sweeps=200, beta=2.0)
    assert batch.trajectories.mean() == pytest.approx(1 / (1 + np.exp(1.2)), abs=0.02)
    assert batch.metadata["max_degree"] == 0


def test_seed_replay_and_topology_cache_do_not_reuse_old_weights():
    J = np.array([[0, -0.25], [-0.25, 0]], dtype=np.float32)
    first = sample_qubo(np.array([-0.8, -0.8]), J, 27, chains=8, sweeps=12)
    replay = sample_qubo(np.array([-0.8, -0.8]), J, 27, chains=8, sweeps=12)
    changed = sample_qubo(np.array([0.8, 0.8]), J, 27, chains=8, sweeps=12)
    np.testing.assert_array_equal(first.trajectories, replay.trajectories)
    assert replay.metadata["compile_cache_hit"] is True
    assert replay.timings["compile_ms"] == 0
    assert first.trajectories.mean() > changed.trajectories.mean() + 0.5


def test_schedule_retains_correct_final_sweeps():
    batch = sample_qubo(np.array([0.0]), np.zeros((1, 1)), 2,
                        chains=3, sweeps=6, burn_in=2)
    assert batch.trajectories.shape == (4, 3, 1)
    assert batch.metadata["first_retained_transition"] == 3
    assert batch.metadata["transitions_per_chain"] == 6


def test_pin_is_actual_installed_vcs_revision():
    identity = dependency_identity()
    assert identity["revision"] == THRML_REVISION
    assert identity["revision_verified"]
    assert len(identity["installed_python_sha256"]) == 64


@pytest.mark.parametrize("unary,J", [
    ([0, 1], [[0, 1], [0, 0]]),
    ([0, 1], [[1, 0], [0, 0]]),
    ([float("nan")], [[0]]),
])
def test_reject_invalid_qubo(unary, J):
    with pytest.raises(ValueError):
        sample_qubo(np.asarray(unary), np.asarray(J), 0, chains=1, sweeps=2)
