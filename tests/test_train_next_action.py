"""Hermetic tests for the next-action ridge baseline and optional MB provider."""

import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest

from z0int import train
from z0int.families import FAMILIES


def _write_episodes(tmp_path: Path, rows: list[dict]) -> Path:
    path = tmp_path / "episodes.jsonl"
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return path


def _alternating_rows(count: int) -> list[dict]:
    return [
        {
            "family": "EDIT" if i % 2 else "VERIFY",
            "user": "user text 1 edit" if i % 2 else "run verify tests 0",
            "prev": ["a", "b"],
        }
        for i in range(count)
    ]


def test_hash_is_unit_norm_and_deterministic():
    first = train._hash("hello world")
    assert first.shape == (96,)
    assert np.linalg.norm(first) == pytest.approx(1.0)
    assert np.array_equal(first, train._hash("hello world"))
    assert np.array_equal(train._hash("ABC"), train._hash("abc"))

    small = train._hash("hello world", dim=8)
    assert small.shape == (8,)
    assert np.linalg.norm(small) == pytest.approx(1.0)


def test_hash_short_and_empty_text():
    for text in ("", None, "ab"):
        features = train._hash(text)
        assert features.shape == (96,)
        assert np.linalg.norm(features) == pytest.approx(1.0)
        assert np.count_nonzero(features) == 1


def test_ridge_fit_recovers_identity():
    matrix = np.eye(3)
    labels = np.array([0, 1, 2])
    weights = train.ridge_fit(matrix, labels, 3, l2=0.0)
    assert weights.shape == (3, 3)
    assert np.allclose(weights, np.eye(3))


def test_load_xy_skips_unknown_family_and_joins_prev(tmp_path):
    path = _write_episodes(
        tmp_path,
        [
            {"family": "EDIT", "user": "change this", "prev": ["a", "b"]},
            {"family": "NOPE", "user": "ignored", "prev": []},
            {"family": "VERIFY", "prev": ["check"]},
            {"family": "ABSTAIN", "user": "skip"},
            {"user": "missing family", "prev": []},
        ],
    )
    features, labels = train.load_xy(path)

    assert features.shape == (3, 192)
    assert labels.dtype == np.int64
    assert labels.tolist() == [
        FAMILIES.index("EDIT"),
        FAMILIES.index("VERIFY"),
        FAMILIES.index("ABSTAIN"),
    ]
    assert np.array_equal(features[0, :96], train._hash("change this"))
    assert np.array_equal(features[0, 96:], train._hash("a b"))
    assert np.array_equal(features[1, :96], train._hash(""))
    assert np.array_equal(features[1, 96:], train._hash("check"))
    assert np.array_equal(features[2, 96:], train._hash(""))


@pytest.mark.parametrize(
    "rows",
    [[], [{"family": "NOPE"}, {"user": "missing family"}]],
    ids=["empty-file", "unknown-families"],
)
def test_load_xy_no_valid_rows_exits(tmp_path, rows):
    path = _write_episodes(tmp_path, rows)
    with pytest.raises(SystemExit) as exc:
        train.load_xy(path)
    assert str(exc.value) == "no episodes"


def test_load_xy_ignores_blank_lines(tmp_path):
    path = tmp_path / "episodes.jsonl"
    row = {"family": "EDIT", "user": "edit", "prev": ["a", "b"]}
    path.write_text("\n  \t\n" + json.dumps(row) + "\n\n", encoding="utf-8")

    features, labels = train.load_xy(path)
    assert features.shape == (1, 192)
    assert labels.tolist() == [FAMILIES.index("EDIT")]
    assert np.array_equal(features[0, 96:], train._hash("a b"))


def test_mb_provider_unset(monkeypatch):
    monkeypatch.delenv("Z0INT_MB_PROVIDER", raising=False)
    fit, predict, reason = train._mb_provider()
    assert fit is None
    assert predict is None
    assert "Z0INT_MB_PROVIDER" in reason


def test_mb_provider_unimportable_and_incomplete(monkeypatch):
    monkeypatch.setenv("Z0INT_MB_PROVIDER", "no_such_module_xyz")
    fit, predict, reason = train._mb_provider()
    assert fit is None
    assert predict is None
    assert "not importable" in reason

    name = "z0int_test_train_mb_provider"
    module = types.ModuleType(name)
    monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("Z0INT_MB_PROVIDER", name)
    fit, predict, reason = train._mb_provider()
    assert fit is None
    assert predict is None
    assert "does not export fit_fly/predict_fly" in reason

    def fit_fly(*args, **kwargs):
        return {"n_params": 7}

    def predict_fly(*args, **kwargs):
        return np.empty(0, dtype=np.int64)

    module.fit_fly = fit_fly
    module.predict_fly = predict_fly
    fit, predict, reason = train._mb_provider()
    assert fit is module.fit_fly
    assert predict is module.predict_fly
    assert reason is None


def test_main_writes_report_with_time_split(tmp_path, capsys):
    path = _write_episodes(tmp_path, _alternating_rows(120))
    assert train.main(["--episodes", str(path)]) == 0

    printed = json.loads(capsys.readouterr().out)
    report_path = tmp_path / "next_action_report.json"
    assert report_path.is_file()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert printed == report
    assert report["n"] == 120
    assert report["n_confirm"] == 100
    assert report["n_train"] == 20
    assert report["ridge_n_params"] == 192 * len(FAMILIES)
    assert report["label_counts"]["EDIT"] == 60
    assert report["label_counts"]["VERIFY"] == 60
    assert all(
        value == 0
        for family, value in report["label_counts"].items()
        if family not in {"EDIT", "VERIFY"}
    )
    assert report["majority_confirm"] == pytest.approx(0.5)
    assert report["ridge_confirm"] >= 0.9
    assert not any(key.startswith("mb_") for key in report)


def test_main_mb_unavailable_and_available(tmp_path, monkeypatch):
    path = _write_episodes(tmp_path, _alternating_rows(120))
    argv = ["--episodes", str(path), "--mb"]
    monkeypatch.delenv("Z0INT_MB_PROVIDER", raising=False)

    assert train.main(argv) == 0
    report_path = tmp_path / "next_action_report.json"
    unavailable = json.loads(report_path.read_text(encoding="utf-8"))
    assert unavailable["mb_confirm"] is None
    assert "Z0INT_MB_PROVIDER" in unavailable["mb_unavailable"]
    assert "mb_n_params" not in unavailable

    calls = {}
    name = "z0int_test_train_mb_provider"
    module = types.ModuleType(name)

    def fit_fly(X, y, n, n_kc, seed):
        calls.update(
            x_shape=X.shape, y_len=len(y), n=n, n_kc=n_kc, seed=seed
        )
        return {"n_params": 7}

    def predict_fly(X, fly):
        calls["confirm_shape"] = X.shape
        assert fly == {"n_params": 7}
        return np.full(len(X), FAMILIES.index("VERIFY"), dtype=np.int64)

    module.fit_fly = fit_fly
    module.predict_fly = predict_fly
    monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setenv("Z0INT_MB_PROVIDER", name)

    assert train.main(argv + ["--n-kc", "12"]) == 0
    available = json.loads(report_path.read_text(encoding="utf-8"))
    assert available["mb_confirm"] == pytest.approx(0.5)
    assert available["mb_n_params"] == 7
    assert available["mb_n_kc"] == 12
    assert "mb_unavailable" not in available
    assert calls == {
        "x_shape": (20, 192),
        "y_len": 20,
        "n": len(FAMILIES),
        "n_kc": 12,
        "seed": 1,
        "confirm_shape": (100, 192),
    }


@pytest.mark.parametrize("count", [1, 50, 100])
def test_main_too_few_episodes(tmp_path, count):
    path = _write_episodes(tmp_path, _alternating_rows(count))
    with pytest.raises(SystemExit) as exc:
        train.main(["--episodes", str(path)])
    assert str(exc.value) == "need more than 100 episodes"
    assert not (tmp_path / "next_action_report.json").exists()
