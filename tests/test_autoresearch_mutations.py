"""Contract tests for autoresearch context-policy mutations and gap delegation."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re

import pytest

from z0int.autoresearch import mutations as m, measurement_gaps as mg


DEFAULT_OPS = ["exact_path", "lexical_local", "qmd_lexical"]


def test_treatment_hash_stable_and_order_independent() -> None:
    first = m.treatment_hash({"a": 1, "b": 2})

    assert first == m.treatment_hash({"b": 2, "a": 1})
    assert re.fullmatch(r"[0-9a-f]{16}", first) is not None
    assert m.treatment_hash({"a": 1}) != m.treatment_hash({"a": 2})
    assert re.fullmatch(r"[0-9a-f]{16}", m.treatment_hash({"path": Path("x")})) is not None
    assert re.fullmatch(r"[0-9a-f]{16}", m.treatment_hash({"items": {"a", "b"}})) is not None


def test_champion_policy_default() -> None:
    expected = {
        "ops": DEFAULT_OPS,
        "use_cache": True,
        "allow_qmd_semantic": False,
        "ablate": [],
        "role": "champion",
    }

    assert m.champion_policy() == expected
    assert m.champion_policy(None) == expected
    assert m.champion_policy({}) == expected


def test_champion_policy_uses_snapshot() -> None:
    snapshot = {
        "context_policy": {
            "ops": ["exact_path"],
            "role": "x",
            "use_cache": False,
        }
    }
    original = deepcopy(snapshot)

    champion = m.champion_policy(snapshot)

    assert champion["ops"] == ["exact_path"]
    assert champion["use_cache"] is False
    assert champion["role"] == "champion"
    assert snapshot == original
    assert snapshot["context_policy"]["role"] == "x"
    assert champion is not snapshot["context_policy"]


def test_champion_policy_ignores_non_dict_context_policy() -> None:
    assert m.champion_policy({"context_policy": "oops"}) == m.champion_policy()


def test_challenger_policies_default_set() -> None:
    challengers = m.challenger_policies(m.champion_policy())

    assert len(challengers) == 5
    assert [c["hypothesis"] for c in challengers] == [
        "drop_exact_path_sufficient",
        "drop_lexical_local_sufficient",
        "drop_qmd_lexical_sufficient",
        "cached_evidence_packet_sufficient",
        "exact_path_only",
    ]
    for index, dropped_op in enumerate(DEFAULT_OPS):
        assert challengers[index]["ops"] == [
            op for op in DEFAULT_OPS if op != dropped_op
        ]
    assert challengers[3]["ops"] == ["cached_packet"]
    assert challengers[4]["ops"] == ["exact_path"]
    for challenger in challengers:
        assert challenger["role"] == "challenger"
        assert challenger["allow_qmd_semantic"] is False
        assert re.fullmatch(r"[0-9a-f]{16}", challenger["treatment_hash"]) is not None


def test_challenger_ablate_lists() -> None:
    challengers = m.challenger_policies(m.champion_policy())

    for index, dropped_op in enumerate(DEFAULT_OPS):
        assert challengers[index]["ablate"] == [dropped_op]
    assert challengers[3]["ablate"] == DEFAULT_OPS
    assert challengers[4]["ablate"] == ["lexical_local", "qmd_lexical"]


def test_challenger_treatment_hash_matches_policy() -> None:
    challengers = m.challenger_policies(m.champion_policy())

    for challenger in challengers:
        unhashed_policy = {
            key: value for key, value in challenger.items() if key != "treatment_hash"
        }
        assert m.treatment_hash(unhashed_policy) == challenger["treatment_hash"]
    assert len({c["treatment_hash"] for c in challengers}) == len(challengers)


def test_challenger_use_cache_propagation() -> None:
    challengers = m.challenger_policies({"ops": DEFAULT_OPS, "use_cache": False})

    assert [c["use_cache"] for c in challengers] == [
        False,
        False,
        False,
        True,
        True,
    ]


def test_challenger_single_op_champion_skips_empty_drop() -> None:
    single = m.challenger_policies({"ops": ["exact_path"]})
    assert [c["hypothesis"] for c in single] == [
        "cached_evidence_packet_sufficient",
        "exact_path_only",
    ]

    default = m.challenger_policies({"ops": DEFAULT_OPS})
    assert len(default) == 5
    assert m.challenger_policies({"ops": []}) == default
    assert m.challenger_policies({}) == default


def test_challenger_dedupes_identical_policies() -> None:
    champion = {"ops": ["exact_path", "exact_path"]}
    original = deepcopy(champion)

    challengers = m.challenger_policies(champion)

    assert len(challengers) == 3
    assert [c["hypothesis"] for c in challengers] == [
        "drop_exact_path_sufficient",
        "cached_evidence_packet_sufficient",
        "exact_path_only",
    ]
    assert champion == original
    assert champion["ops"] == ["exact_path", "exact_path"]


def test_exact_path_only_always_emitted() -> None:
    challengers = m.challenger_policies({"ops": ["lexical_local"]})

    # Current implementation includes `or True`: exact_path_only is unconditional.
    exact_only = [c for c in challengers if c["hypothesis"] == "exact_path_only"]
    assert len(exact_only) == 1
    assert exact_only[0]["ops"] == ["exact_path"]
    assert exact_only[0]["ablate"] == ["lexical_local"]


def test_top_measurement_gaps_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int]] = []
    expected = [{"gap": 1}]

    def fake(*, range_spec: str, limit: int) -> list[dict[str, int]]:
        calls.append((range_spec, limit))
        return expected

    monkeypatch.setattr(mg, "_gaps", fake)

    assert mg.top_measurement_gaps() is expected
    assert mg.top_measurement_gaps(range_spec="1d", limit=3) is expected
    assert calls == [("7d", 12), ("1d", 3)]
