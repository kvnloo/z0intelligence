"""Unit tests for sealed battery manifests and scoring gates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError

import pytest

from z0int import battery
import z0int.cascade
import z0int.routines


ROWS = [
    {"split": "sealed", "session_id": "s2"},
    {"split": "sealed", "session_id": "s1"},
    {"split": "sealed", "session_id": "s1"},
    {"split": "sealed"},
    {"split": "sealed", "session_id": ""},
    {"split": "dev", "session_id": "d"},
    {"session_id": "x"},
]


def test_manifest_counts_only_requested_split():
    sealed = battery._manifest(ROWS)
    assert sealed.n_rows == 5
    assert sealed.n_sessions == 2
    assert sealed.split == "sealed"
    assert sealed.schema == "z0int.battery_manifest.v1"

    dev = battery._manifest(ROWS, split="dev")
    assert dev.n_rows == 1
    assert dev.n_sessions == 1
    assert dev.split == "dev"
    assert battery._manifest([{"session_id": "x"}]).n_rows == 0


def test_manifest_battery_id_is_stable_sha256_prefix():
    material = {"split": "sealed", "n": 5, "sessions": ["s1", "s2"]}
    expected = hashlib.sha256(
        json.dumps(material, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]

    manifest = battery._manifest(ROWS)
    assert manifest.battery_id == expected
    assert len(manifest.battery_id) == 24
    assert battery._manifest(list(reversed(ROWS))).battery_id == expected
    assert battery._manifest(ROWS, split="dev").battery_id != expected
    assert battery._manifest(
        ROWS + [{"split": "sealed", "session_id": "s1"}]
    ).battery_id != expected


def test_manifest_of_empty_input():
    manifest = battery._manifest([])
    expected = hashlib.sha256(
        json.dumps(
            {"split": "sealed", "n": 0, "sessions": []}, sort_keys=True
        ).encode("utf-8")
    ).hexdigest()[:24]
    assert manifest.n_rows == 0
    assert manifest.n_sessions == 0
    assert len(manifest.battery_id) == 24
    assert manifest.battery_id == expected


def test_manifest_to_dict_and_frozen():
    rows = [dict(row) for row in ROWS]
    rows[0]["features"] = {"private_marker": "sealed_secret_value"}
    manifest = battery._manifest(rows)
    data = manifest.to_dict()

    assert set(data) == {
        "battery_id", "n_rows", "n_sessions", "split", "schema"
    }
    assert data == {
        "battery_id": manifest.battery_id,
        "n_rows": 5,
        "n_sessions": 2,
        "split": "sealed",
        "schema": "z0int.battery_manifest.v1",
    }
    with pytest.raises(FrozenInstanceError):
        manifest.n_rows = 1

    rendered = str(data)
    for private_value in (
        "s1", "s2", "session_id", "features", "sealed_secret_value"
    ):
        assert private_value not in rendered


@pytest.mark.parametrize(
    "gate_type", (battery.RoutineSealedGate, battery.CascadeSealedGate)
)
def test_gates_keep_only_sealed_rows_private(gate_type):
    gate = gate_type(ROWS)
    assert gate.manifest == battery._manifest(ROWS)
    assert not hasattr(gate, "rows")

    private_rows = getattr(gate, f"_{gate_type.__name__}__rows")
    assert isinstance(private_rows, tuple)
    assert len(private_rows) == 5
    assert all(row["split"] == "sealed" for row in private_rows)


@pytest.mark.parametrize(
    "gate_type", (battery.RoutineSealedGate, battery.CascadeSealedGate)
)
def test_gate_copies_rows(gate_type):
    rows = [dict(row) for row in ROWS]
    gate = gate_type(rows)
    rows[0]["session_id"] = "mut"

    private_rows = getattr(gate, f"_{gate_type.__name__}__rows")
    assert private_rows[0] is not rows[0]
    assert private_rows[0]["session_id"] == "s2"
    assert gate.manifest.n_sessions == 2


def test_routine_gate_credit_delegates(monkeypatch):
    calls = []

    def fake(rows, candidates, config=None):
        calls.append((rows, candidates, config))
        return "R"

    gate = battery.RoutineSealedGate(ROWS)
    monkeypatch.setattr(z0int.routines, "credit_sealed", fake)

    assert gate.credit(["c"], config=5) == "R"
    assert len(calls) == 1
    scored_rows, candidates, config = calls[0]
    assert isinstance(scored_rows, tuple)
    assert len(scored_rows) == 5
    assert all(row["split"] == "sealed" for row in scored_rows)
    assert candidates == ["c"]
    assert config == 5

    assert gate.credit(["c"]) == "R"
    assert len(calls) == 2
    assert calls[1][0] is scored_rows
    assert calls[1][1] == ["c"]
    assert calls[1][2] is None


def test_cascade_gate_credit_delegates(monkeypatch):
    calls = []

    def fake(rows, policy, config=None):
        calls.append((rows, policy, config))
        return "C"

    gate = battery.CascadeSealedGate(ROWS)
    monkeypatch.setattr(z0int.cascade, "credit_sealed", fake)

    assert gate.credit("policy") == "C"
    assert len(calls) == 1
    scored_rows, policy, config = calls[0]
    assert isinstance(scored_rows, tuple)
    assert len(scored_rows) == 5
    assert all(row["split"] == "sealed" for row in scored_rows)
    assert (policy, config) == ("policy", None)


def test_gate_with_no_sealed_rows(monkeypatch):
    calls = []

    def fake(rows, candidates, config=None):
        calls.append((rows, candidates, config))
        return "empty"

    gate = battery.RoutineSealedGate([{"split": "train"}])
    assert gate.manifest.n_rows == 0
    assert gate.manifest.n_sessions == 0
    monkeypatch.setattr(z0int.routines, "credit_sealed", fake)

    assert gate.credit(["candidate"]) == "empty"
    assert len(calls) == 1
    assert calls[0] == ((), ["candidate"], None)
    assert isinstance(calls[0][0], tuple)
