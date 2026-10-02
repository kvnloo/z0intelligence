"""Negative controls for the independent ThermoContext result auditor."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from benchmarks.thermocontext import audit_next_wave as audit


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen = audit.load_frozen()

    def setUp(self):
        self.task = self.frozen.make_task(8, 0)
        self.mask, self.energy = audit.independent_exact(self.task)

    def row(self):
        return {
            "N": 8, "task_id": self.task.task_id, "task_seed": 0,
            "arm": "audit_fixture", "seed": 0,
            "mask": self.mask.astype(int).tolist(),
            "ids": sorted(self.task.selected_ids(self.mask)),
            "tokens": self.task.tokens(self.mask), "budget": self.task.budget,
            "energy": self.task.energy(self.mask), "verified": self.task.verify(self.mask),
            "feasible": True, "empty": not self.mask.any(),
            "exact_energy": self.energy, "energy_gap": 0.0,
            "no_feasible": False, "sample_count": 2, "first_valid_sample": 1,
        }

    def trace(self, directory, row):
        states = np.stack([self.mask, np.zeros(8, dtype=bool)]).reshape(1, 2, 8)
        path = Path(directory) / "trace.npz"
        np.savez_compressed(path, packed=np.packbits(states.ravel(), bitorder="little"),
                            shape=np.array(states.shape), finalmask=self.mask)
        row.update(trace_path=path.name, trace_sha256=audit.sha256(path))

    def test_independent_oracle_matches_frozen_exhaustive_across_sizes(self):
        for n in (8, 12, 16):
            task = self.frozen.make_task(n, 3)
            _, actual = audit.independent_exact(task)
            _, expected = self.frozen.exact(task)
            self.assertAlmostEqual(actual, expected, delta=audit.TOL)

    def test_honest_row_and_trace_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            row = self.row()
            self.trace(directory, row)
            self.assertEqual(audit.audit_row(row, self.task, exact_energy=self.energy, root=directory), [])

    def test_energy_and_oracle_claim_tampering_rejected(self):
        row = self.row()
        row.update(seed=None, energy=row["energy"] - 0.25, verified=False)
        errors = audit.audit_row(row, self.task, exact_energy=self.energy)
        self.assertTrue(any("frozen energy" in err for err in errors))
        self.assertTrue(any("verified" in err for err in errors))
        self.assertTrue(any("beats exhaustive" in err for err in errors))

    def test_first_valid_claim_checked_against_retained_draws(self):
        with tempfile.TemporaryDirectory() as directory:
            row = self.row()
            self.trace(directory, row)
            row["first_valid_sample"] = 2
            errors = audit.audit_row(row, self.task, exact_energy=self.energy, root=directory)
            self.assertTrue(any("First-valid sample" in err for err in errors))

    def test_verifier_passing_but_not_minimum_draw_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            alternative = np.array([it.id in self.task.required_ids for it in self.task.items])
            self.assertTrue(self.task.verify(alternative))
            self.assertGreater(self.task.energy(alternative), self.energy + audit.TOL)
            row = self.row()
            row.update(mask=alternative.astype(int).tolist(),
                       ids=sorted(self.task.selected_ids(alternative)),
                       tokens=self.task.tokens(alternative), energy=self.task.energy(alternative),
                       energy_gap=self.task.energy(alternative) - self.energy)
            states = np.stack([self.mask, alternative]).reshape(1, 2, 8)
            path = Path(directory) / "trace.npz"
            np.savez_compressed(path, packed=np.packbits(states.ravel(), bitorder="little"),
                                shape=np.array(states.shape), finalmask=alternative)
            row.update(trace_path=path.name, trace_sha256=audit.sha256(path))
            errors = audit.audit_row(row, self.task, exact_energy=self.energy, root=directory)
            self.assertTrue(any("not minimum" in err for err in errors))

    def test_trace_hash_and_missing_trace_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            row = self.row()
            self.assertTrue(any("missing auditable" in err for err in audit.audit_row(row, self.task)))
            self.trace(directory, row)
            row["trace_sha256"] = "0" * 64
            errors = audit.audit_row(row, self.task, root=directory)
            self.assertTrue(any("hash mismatch" in err for err in errors))

    def test_partial_or_duplicate_grid_is_not_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            row = self.row()
            self.trace(directory, row)
            report = audit.audit_results({"rows": [row, copy.deepcopy(row)]}, directory, self.frozen)
            self.assertFalse(report["complete_present_arm_grids"])
            self.assertFalse(report["all_row_checks_pass"])
            self.assertTrue(any("Duplicate result row" in f["errors"] for f in report["failures"]))

    def test_auxiliary_graph_and_chain_motion_are_independently_checked(self):
        from benchmarks.thermocontext.slack_budget import build_slack_qubo

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = self.row()
            self.trace(root, row)
            model = build_slack_qubo(self.task.unary(), self.task.J,
                                    [it.tokens for it in self.task.items], self.task.budget)
            graph = model.graph_receipt()
            graph_path = root / "graph.json"
            graph_path.write_text(json.dumps(graph))
            canonical = json.dumps(graph, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            initial = np.stack([model.encode(self.mask), model.encode(np.zeros(8, dtype=bool))])
            states = initial[None, :, :]
            trace_path = root / "auxiliary.npz"
            np.savez_compressed(trace_path, packed=np.packbits(states.ravel(), bitorder="little"),
                                shape=np.array(states.shape),
                                initial_packed=np.packbits(initial.ravel(), bitorder="little"),
                                initial_shape=np.array(initial.shape))
            row.update(auxiliary_graph_path=graph_path.name,
                       auxiliary_graph_file_sha256=audit.sha256(graph_path),
                       auxiliary_graph_sha256=hashlib.sha256(canonical).hexdigest(),
                       auxiliary_trace_path=trace_path.name,
                       auxiliary_trace_sha256=audit.sha256(trace_path),
                       metadata={**model.metadata(), "constraint_valid_sample_fraction": 1.0,
                                 "retained_item_change_fraction": 0.0})
            self.assertEqual(audit.audit_row(row, self.task, exact_energy=self.energy, root=root), [])
            row["metadata"]["retained_item_change_fraction"] = 1.0
            row["metadata"]["max_degree"] = 0
            errors = audit.audit_row(row, self.task, exact_energy=self.energy, root=root)
            self.assertTrue(any("change_fraction metadata mismatch" in err for err in errors))
            self.assertTrue(any("max_degree metadata mismatch" in err for err in errors))

    def test_frozen_source_edits_are_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "edited.py"
            path.write_text("raise AssertionError('must not execute')\n")
            with self.assertRaisesRegex(ValueError, "hash changed"):
                audit.load_frozen(path)


if __name__ == "__main__":
    unittest.main()
