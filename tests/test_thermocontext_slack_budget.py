"""Structural and exact small-state checks for the sparse budget encoding."""

import itertools
import unittest

import numpy as np

from benchmarks.thermocontext.slack_budget import build_slack_qubo, initial_states


class SparseSlackTests(unittest.TestCase):
    def test_every_feasible_mask_has_zero_penalty_encoding(self):
        costs = np.array([1, 2, 3])
        unary = np.array([-0.4, 0.2, -0.8])
        coupling = np.zeros((3, 3))
        coupling[0, 2] = coupling[2, 0] = -0.7
        model = build_slack_qubo(unary, coupling, costs, 4)
        for bits in itertools.product((0, 1), repeat=3):
            mask = np.asarray(bits, dtype=bool)
            if costs @ mask <= 4:
                state = model.encode(mask)
                self.assertEqual(model.constraint_violations(state), 0)
                self.assertAlmostEqual(model.energy(state), model.base_energy(mask))
            else:
                with self.assertRaises(ValueError):
                    model.encode(mask)

    def test_exhaustive_ground_state_equals_constrained_base_optimum(self):
        unary = np.array([-0.4, -0.8])
        coupling = np.array([[0.0, -0.9], [-0.9, 0.0]])
        costs = np.array([1, 1])
        model = build_slack_qubo(unary, coupling, costs, 1)
        energy_by_mask = {}
        for bits in itertools.product((0, 1), repeat=len(model.unary)):
            state = np.asarray(bits, dtype=bool)
            key = tuple(bits[:2])
            energy = model.energy(state)
            self.assertAlmostEqual(energy, model.base_energy(state[:2])
                                   + model.penalty * model.constraint_violations(state))
            energy_by_mask[key] = min(energy, energy_by_mask.get(key, np.inf))
            if model.constraint_violations(state) == 0:
                self.assertLessEqual(costs @ state[:2], 1)
                self.assertAlmostEqual(energy, model.base_energy(state[:2]))
        winner = min(energy_by_mask, key=energy_by_mask.get)
        self.assertEqual(winner, (0, 1))
        self.assertAlmostEqual(energy_by_mask[winner], -0.8)
        self.assertGreater(energy_by_mask[(1, 1)], 0.0)

    def test_zero_budget_still_encodes_empty_pack(self):
        model = build_slack_qubo([-0.2], [[0]], [1], 0)
        state = model.encode([False])
        self.assertEqual(model.constraint_violations(state), 0)
        self.assertAlmostEqual(model.energy(state), 0)
        with self.assertRaises(ValueError):
            model.encode([True])

    def test_over_budget_item_cannot_have_zero_penalty(self):
        model = build_slack_qubo(np.array([-1.0]), np.zeros((1, 1)), [3], 1)
        for bits in itertools.product((0, 1), repeat=len(model.unary)):
            state = np.asarray(bits, dtype=bool)
            if state[0]:
                self.assertGreater(model.constraint_violations(state), 0)

    def test_sparse_degree_does_not_grow_with_item_count(self):
        for n in (8, 32, 128):
            model = build_slack_qubo(np.full(n, -0.2), np.zeros((n, n)),
                                     [31 + i % 8 for i in range(n)], 160)
            graph = model.metadata()
            self.assertLessEqual(graph["max_degree"], 8)
            self.assertLessEqual(graph["node_count"], 3 * n * model.width + 2 * model.width)
            self.assertEqual(graph["auxiliary_node_count"], graph["node_count"] - n)
            self.assertEqual(len(graph["nodes"]), graph["node_count"])
            self.assertEqual(len(graph["edges"]), graph["edge_count"])
            self.assertGreater(graph["coefficient_dynamic_range"], 1)

    def test_warm_start_is_reproducible_budget_feasible_without_labels(self):
        model = build_slack_qubo(np.full(8, -0.2), np.zeros((8, 8)),
                                 [31 + i for i in range(8)], 120)
        one = initial_states(model, seed=19, chains=64)
        two = initial_states(model, seed=19, chains=64)
        np.testing.assert_array_equal(one, two)
        self.assertEqual(one.dtype, np.bool_)
        self.assertGreater(len(np.unique(one[:, :8], axis=0)), 1)
        for state in one:
            self.assertEqual(model.constraint_violations(state), 0)
            self.assertLessEqual(model.costs @ state[:8], 120)

    def test_rejects_inputs_that_break_binary_energy_convention(self):
        with self.assertRaises(ValueError):
            build_slack_qubo([1], [[1]], [1], 1)
        with self.assertRaises(ValueError):
            build_slack_qubo([1, 2], [[0, 1], [0, 0]], [1, 1], 1)
        with self.assertRaises(ValueError):
            build_slack_qubo([1], [[0]], [1.5], 1)

    def test_real_thrml_smoke_has_only_public_construction_inputs(self):
        from types import SimpleNamespace
        from benchmarks.thermocontext.slack_budget import propose

        class PublicOnly:
            items = [SimpleNamespace(tokens=1), SimpleNamespace(tokens=1)]
            J = np.array([[0.0, -0.9], [-0.9, 0.0]])
            budget = 1

            def unary(self):
                return np.array([-0.4, -0.8])

            def __getattr__(self, name):
                raise AssertionError("non-public task field read: " + name)

        result = propose(PublicOnly(), 19, chains=2, sweeps=4)
        self.assertEqual(result["trajectories"].shape, (2, 2, 2))
        graph = result["auxiliary_graph"]
        self.assertEqual(graph["n_items"], 2)
        np.testing.assert_array_equal(result["auxiliary_trajectories"][..., :2],
                                      result["trajectories"])
        np.testing.assert_array_equal(result["initial_full_states"][:, :2],
                                      result["initial_item_states"])
        self.assertEqual(result["initial_full_states"].shape, (2, graph["n_nodes"]))
        import hashlib
        import json
        digest = hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(",", ":"),
                                           allow_nan=False).encode("utf-8")).hexdigest()
        self.assertEqual(result["auxiliary_graph_sha256"], digest)
        self.assertEqual(result["metadata"]["auxiliary_graph_sha256"], digest)
        states = result["auxiliary_trajectories"].reshape(-1, graph["n_nodes"]).astype(int)
        residuals = np.array([sum(coefficient * states[:, index]
                                 for index, coefficient in cell["terms"]) + cell["constant"]
                              for cell in graph["cells"]])
        valid = np.all(residuals == 0, axis=0)
        self.assertEqual(float(valid.mean()), result["metadata"]["constraint_valid_sample_fraction"])
        expanded = states @ np.asarray(graph["expanded_unary"]) + graph["constant"]
        for edge in graph["expanded_edges"]:
            expanded += edge["coefficient"] * states[:, edge["i"]] * states[:, edge["j"]]
        base = states[:, :2] @ np.asarray(graph["base_unary"])
        for edge in graph["base_edges"]:
            base += edge["coefficient"] * states[:, edge["i"]] * states[:, edge["j"]]
        np.testing.assert_allclose(expanded, base + graph["penalty"] * (residuals ** 2).sum(axis=0),
                                   rtol=0, atol=1e-10)
        motion = np.any(result["auxiliary_trajectories"][..., :2]
                        != result["initial_full_states"][None, :, :2], axis=-1).mean()
        self.assertEqual(float(motion), result["metadata"]["retained_item_change_fraction"])
        self.assertEqual(result["metadata"]["initial_baseline"]["sampler_transitions"], 0)
        self.assertEqual(result["metadata"]["backend"]["backend"],
                         "thrml.IsingSamplingProgram/sample_states")
        self.assertTrue(result["metadata"]["dependency"]["revision_verified"])
        with self.assertRaises(ValueError):
            propose(PublicOnly(), 19, chains=65, sweeps=160)


if __name__ == "__main__":
    unittest.main()
