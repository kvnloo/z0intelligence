"""Hermetic unit tests for ordinary and contrastive data-recipe racing."""

from __future__ import annotations

import pytest

from z0int import data_recipe_race as drr


class F:
    def __init__(self, i: str) -> None:
        self.i = i


EVALS = {
    "f1": {
        "family_id": "f1",
        "conditions": {"original": {"ok": True}},
        "full_pass": True,
        "metrics": {"original_accuracy": 1.0, "full_family_pass_rate": 1.0},
    },
    "f2": {
        "family_id": "f2",
        "conditions": {"original": {"ok": True}},
        "full_pass": False,
        "metrics": {"original_accuracy": 0.5, "full_family_pass_rate": 0.0},
    },
    "f3": {
        "family_id": "f3",
        "conditions": {"original": {"ok": False}},
        "full_pass": True,
        "metrics": {},
    },
}


def _patch_probe_path(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_evaluate_family(fam: F, *, probe: object = None) -> dict:
        return EVALS[fam.i]

    monkeypatch.setattr(drr, "evaluate_family", fake_evaluate_family)
    monkeypatch.setattr(drr, "resolve_probe", lambda fam: "P" + fam.i)


def test_ordinary_gate() -> None:
    assert drr.ordinary_gate({}) is False
    assert drr.ordinary_gate({"conditions": None}) is False
    assert drr.ordinary_gate({"conditions": {"original": {"ok": False}}}) is False
    assert drr.ordinary_gate({"conditions": {"original": {"ok": 1}}}) is True


def test_contrastive_gate() -> None:
    assert drr.contrastive_gate({}) is False
    assert drr.contrastive_gate({"full_pass": True}) is True
    assert drr.contrastive_gate({"full_pass": 0}) is False


def test_race_counts_and_rates(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe_path(monkeypatch)
    race = drr.race_data_recipes([F("f1"), F("f2"), F("f3")])

    assert race["schema"] == "z0int.data_recipe_race.v1"
    assert race["n_families"] == 3
    assert race["ordinary"]["accepted"] == 2
    assert race["contrastive"]["accepted"] == 2
    assert race["ordinary"]["acceptance_rate"] == pytest.approx(2 / 3)
    assert race["contrastive"]["acceptance_rate"] == pytest.approx(2 / 3)
    assert race["families"] == ["f1", "f2", "f3"]
    assert race["incremental_rejection"] == 0


def test_race_metrics_averaged_over_accepted_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe_path(monkeypatch)
    race = drr.race_data_recipes([F("f1"), F("f2"), F("f3")])

    ordinary_metrics = race["ordinary"]["metrics"]
    contrastive_metrics = race["contrastive"]["metrics"]
    assert ordinary_metrics["n"] == 2
    assert ordinary_metrics["original_accuracy"] == pytest.approx(0.75)
    assert ordinary_metrics["full_family_pass_rate"] == pytest.approx(0.5)
    assert contrastive_metrics["n"] == 2
    # f3 passes contrastive but omits metrics: aggregation counts it as 0.0.
    assert contrastive_metrics["original_accuracy"] == pytest.approx(0.5)


def test_incremental_rejection_positive_and_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_probe_path(monkeypatch)
    positive = drr.race_data_recipes([F("f2")])
    negative = drr.race_data_recipes([F("f3")])

    assert positive["ordinary"]["accepted"] == 1
    assert positive["contrastive"]["accepted"] == 0
    assert positive["incremental_rejection"] == 1
    assert negative["ordinary"]["accepted"] == 0
    assert negative["contrastive"]["accepted"] == 1
    assert negative["incremental_rejection"] == -1


def test_empty_family_list(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("empty family list must not evaluate or resolve a probe")

    for name in ("evaluate_family", "evaluate_recipe_on_family", "resolve_probe"):
        monkeypatch.setattr(drr, name, unexpected)

    race = drr.race_data_recipes([])
    assert race["n_families"] == 0
    assert race["ordinary"]["acceptance_rate"] == 0.0
    assert race["contrastive"]["acceptance_rate"] == 0.0
    assert race["ordinary"]["accepted"] == 0
    assert race["contrastive"]["accepted"] == 0
    empty_metrics = {"n": 0, "full_family_pass_rate": 0.0}
    assert race["ordinary"]["metrics"] == empty_metrics
    assert race["contrastive"]["metrics"] == empty_metrics
    assert race["families"] == []
    assert race["incremental_rejection"] == 0


def test_recipe_dict_routes_to_evaluate_recipe_on_family(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str, object]] = []

    def fake_evaluate_family(fam: F, *, probe: object = None) -> dict:
        calls.append(("family", fam.i, probe))
        return EVALS[fam.i]

    def fake_evaluate_recipe(fam: F, recipe: dict) -> dict:
        calls.append(("recipe", fam.i, recipe))
        return EVALS[fam.i]

    monkeypatch.setattr(drr, "evaluate_family", fake_evaluate_family)
    monkeypatch.setattr(drr, "evaluate_recipe_on_family", fake_evaluate_recipe)
    monkeypatch.setattr(drr, "resolve_probe", lambda fam: "P" + fam.i)

    race = drr.race_data_recipes([F("f1")], recipe={"a": 1})
    assert calls == [("recipe", "f1", {"a": 1})]
    assert race["families"] == ["f1"]
    assert race["ordinary"]["accepted"] == 1


def test_empty_recipe_falls_back_to_probe_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []
    resolved: list[str] = []

    def fake_evaluate_family(fam: F, *, probe: object = None) -> dict:
        calls.append((fam.i, probe))
        return EVALS[fam.i]

    def unexpected_recipe(*args: object, **kwargs: object) -> None:
        pytest.fail("empty recipe must use evaluate_family")

    def fake_resolve_probe(fam: F) -> str:
        resolved.append(fam.i)
        return "P" + fam.i

    monkeypatch.setattr(drr, "evaluate_family", fake_evaluate_family)
    monkeypatch.setattr(drr, "evaluate_recipe_on_family", unexpected_recipe)
    monkeypatch.setattr(drr, "resolve_probe", fake_resolve_probe)

    derived = drr.race_data_recipes([F("f1")], recipe={})
    assert calls == [("f1", "Pf1")]
    assert resolved == ["f1"]
    assert derived["families"] == ["f1"]

    calls.clear()
    resolved.clear()
    explicit = drr.race_data_recipes([F("f2")], recipe={}, probe="PX")
    assert calls == [("f2", "PX")]
    assert resolved == []
    assert explicit["families"] == ["f2"]


def test_recipe_path_ignores_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    recipe_calls: list[tuple[str, dict]] = []

    def unexpected(*args: object, **kwargs: object) -> None:
        pytest.fail("recipe evaluation must not use probe evaluation or resolution")

    def fake_evaluate_recipe(fam: F, recipe: dict) -> dict:
        recipe_calls.append((fam.i, recipe))
        return EVALS[fam.i]

    monkeypatch.setattr(drr, "evaluate_family", unexpected)
    monkeypatch.setattr(drr, "resolve_probe", unexpected)
    monkeypatch.setattr(drr, "evaluate_recipe_on_family", fake_evaluate_recipe)

    race = drr.race_data_recipes([F("f1")], recipe={"a": 1}, probe="PX")
    assert recipe_calls == [("f1", {"a": 1})]
    assert race["families"] == ["f1"]
    assert race["ordinary"]["accepted"] == 1
