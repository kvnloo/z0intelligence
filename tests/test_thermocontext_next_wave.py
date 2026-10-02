"""Runner integrity checks, independent of sampler-quality outcomes."""
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("jax", exc_type=ModuleNotFoundError)

from benchmarks.thermocontext.next_wave import (
    first_valid_sample,
    select_original_energy,
    summarize,
    validate_config,
    write_trace,
)


def test_selection_never_consults_verifier_or_required_ids():
    task = SimpleNamespace(
        items=[None, None], budget=1,
        tokens=lambda x: int(x.sum()),
        energy=lambda x: float(-2 * x[0] - x[1]),
        verify=lambda x: (_ for _ in ()).throw(AssertionError("oracle leak")),
    )
    result = select_original_energy(task, np.array([[[0, 1], [1, 0], [1, 1]]], bool))
    assert result["mask"].tolist() == [True, False]
    assert result["feasible_sample_count"] == 2
    assert not result["no_feasible"]


def test_minimum_can_be_empty_without_hidden_rescue():
    task = SimpleNamespace(items=[None], budget=2, tokens=lambda x: int(x.sum()),
                           energy=lambda x: float(x.sum()))
    result = select_original_energy(task, np.array([[[1], [0]]], bool))
    assert result["mask"].tolist() == [False]
    assert not result["no_feasible"]
    assert result["selected_sample"] == 2


def test_no_feasible_fallback_is_explicit_and_not_a_sample():
    task = SimpleNamespace(items=[None], budget=0, tokens=lambda x: int(x.sum()),
                           energy=lambda x: float(-x.sum()))
    result = select_original_energy(task, np.array([[[1]]], bool))
    assert result["no_feasible"]
    assert result["selected_sample"] is None
    assert result["mask"].tolist() == [False]


def test_ties_preserve_original_order_and_first_valid_is_separate():
    task = SimpleNamespace(items=[None, None], budget=1, tokens=lambda x: int(x.sum()),
                           energy=lambda x: float(-x.sum()), verify=lambda x: bool(x[1]))
    trajectory = np.array([[[1, 0], [0, 1]], [[0, 1], [1, 0]]], bool)
    result = select_original_energy(task, trajectory)
    assert result["mask"].tolist() == [True, False]
    assert first_valid_sample(task, trajectory) == 2


def test_trace_is_lossless_create_only_and_pickle_free(tmp_path):
    rng = np.random.default_rng(4)
    trajectory = rng.random((3, 7, 13)) < 0.5
    raw = ~trajectory
    path = tmp_path / "trace.npz"
    write_trace(path, trajectory, trajectory[0, 0], raw)
    with np.load(path, allow_pickle=False) as saved:
        restored = np.unpackbits(saved["packed"], bitorder="little", count=int(np.prod(saved["shape"]))).reshape(saved["shape"])
        np.testing.assert_array_equal(restored, trajectory)
        np.testing.assert_array_equal(saved["finalmask"], trajectory[0, 0])
        restored_raw = np.unpackbits(saved["raw_packed"], bitorder="little", count=int(np.prod(saved["raw_shape"]))).reshape(saved["raw_shape"])
        np.testing.assert_array_equal(restored_raw, raw)
    with pytest.raises(FileExistsError):
        write_trace(path, trajectory, trajectory[0, 0])


def test_full_mode_cannot_silently_shrink_cohort():
    with pytest.raises(ValueError, match="12 tasks and 8 seeds"):
        validate_config([8, 12, 16], 1, 1, ["prototype"], "full")
    validate_config([8], 1, 1, ["prototype"], "diagnostic")
    with pytest.raises(ValueError):
        validate_config([16, 8], 12, 8, ["prototype"], "full")


def test_summary_keeps_errors_in_denominator_and_tasks_as_units():
    rows = [
        {"N":8,"arm":"a","task_id":"t0","seed":0,"status":"ok","verified":True,
         "tokens":5,"budget":8,"energy":-1.0,"energy_gap":0.0,"empty":False,"feasible":True,
         "sample_count":5,"first_valid_sample":1,"timings":{"total_wall_ms":1.0},"graph_degree":4},
        {"N":8,"arm":"a","task_id":"t0","seed":1,"status":"error","verified":False},
        {"N":8,"arm":"a","task_id":"t1","seed":0,"status":"ok","verified":False,
         "tokens":0,"budget":8,"energy":0.0,"energy_gap":1.0,"empty":True,"feasible":True,
         "sample_count":5,"first_valid_sample":None,"timings":{"total_wall_ms":2.0},"graph_degree":4},
    ]
    report = summarize(rows)["8"]["a"]
    assert report["row_count"] == 3
    assert report["error_count"] == 1
    assert report["verified_rate_all_rows"] == 1 / 3
    assert report["task_count"] == 2
    assert report["per_task"]["t0"]["successes"] == 1
    assert report["per_task"]["t0"]["rows"] == 2
