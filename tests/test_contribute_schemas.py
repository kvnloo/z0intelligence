"""CW0: contributor-layer JSON Schemas and the stdlib validator.

Each law-like rule has a positive and a negative example. When the reference
``jsonschema`` package is installed, every example must get the same verdict from
it (twin check), so the stdlib subset cannot drift from the standard.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from z0int.contribute import schemas as S

ROOT = Path(__file__).resolve().parents[1]
NAMES = ("priorities", "vocab", "contribute.state", "suggestion", "contrib.receipt", "profile")
SHA = "a" * 40


def _task(**over):
    t = {
        "id": "t-1-x", "issue": 1, "title": "x", "kind": "issue", "tier": "P1", "rank": 1,
        "status": "open", "gate": None, "newcomer": True, "needs_owner_approval": False,
        "claim": "exclusive", "difficulty": "S", "expected_hours": 2, "skills": ["python"],
        "touches": ["src/"],
        "hardware": {"gpu_vram_gib": 0, "ram_gib": 4, "disk_gib": 1, "os": ["linux"], "arch": ["x86_64"]},
        "runtime": "repo_only", "network": "none", "risk_class": "low", "smoke": None,
        "verifier": "python -m pytest -q tests/test_core.py", "exit_criterion": "green",
        "impact_note": "short",
    }
    t.update(over)
    return t


def _priorities(*tasks):
    return {
        "schema": "z0.priorities.v1", "updated": "2026-10-26", "north_star": "n",
        "alignment": {
            "weights": {"impact": 0.3, "fit": 0.3, "interest": 0.15, "ramp": 0.15, "uniq_hw": 0.1},
            "setup_penalty_per_missing": 0.08, "tie_epsilon": 0.05,
        },
        "tasks": list(tasks) or [_task()],
    }


def _receipt(**over):
    r = {
        "schema": "z0.contrib.receipt.v1", "state": "S2_smoke_passed", "task": None, "sha": SHA,
        "z0_sha": None, "host_class": "cpu", "harness": "claude-code", "command": "z0int contribute smoke",
        "exit": 0, "output_sha256": "b" * 64, "ts": "2026-10-26T00:00:00Z", "result": "pass",
    }
    r.update(over)
    return r


def _card(**over):
    c = {"task": "t-1-x", "issue": 1, "score": 0.5, "reasons": ["tier:P1"], "next": []}
    c.update(over)
    return c


def _suggestion(**over):
    s = {
        "schema": "z0.contribute.suggestion.v1", "sha": SHA, "priorities_sha256": "c" * 64,
        "profile_tier": "T0", "engine": {"version": "1", "llm": "none"}, "pick": _card(),
        "alternatives": [], "excluded_counts": {"hardware": 1},
    }
    s.update(over)
    return s


def _state(**over):
    s = {
        "schema": "z0.contribute.state.v1", "state": "S3_suggested", "sha": SHA, "receipts": [],
        "next_action": {"kind": "ask_human", "command": None, "why": "claim?"},
        "needs_human": ["claim?"], "warnings": [],
    }
    s.update(over)
    return s


def _profile(**over):
    p = {
        "schema": "z0.contribute.profile.v1", "tier": "T0",
        "hw": {"os": "linux", "arch": "x86_64", "ram_gib": 16, "gpu": {"class": "none", "vram_gib": 0}},
        "toolchains": ["python"], "harnesses": ["claude"],
    }
    p.update(over)
    return p


VOCAB = {"schema": "z0.vocab.v1", "languages": ["python"], "domains": ["docs"], "toolchains": ["python"]}

# (schema name, document, expected valid)
CASES = [
    ("priorities", _priorities(), True),
    ("priorities", _priorities(_task(newcomer=True, needs_owner_approval=True)), False),
    ("priorities", _priorities(_task(newcomer=True, risk_class="high")), False),
    ("priorities", _priorities(_task(newcomer=False, risk_class="high")), True),
    ("priorities", _priorities(_task(newcomer=True, verifier=None)), False),
    ("priorities", _priorities(_task(newcomer=False, verifier=None)), True),
    ("priorities", _priorities(_task(claim="parallel(4)")), True),
    ("priorities", _priorities(_task(claim="parallel(0)")), False),
    ("priorities", _priorities(_task(claim="shared")), False),
    ("priorities", _priorities(_task(kind="z0evals")), True),
    ("priorities", _priorities(_task(kind="chore")), False),
    ("priorities", _priorities(_task(id="Bad_ID")), False),
    ("priorities", _priorities(_task(gate={"issue": 12})), True),
    ("priorities", _priorities(_task(gate={"task": "t-2"})), True),
    ("priorities", _priorities(_task(gate={"issue": 12, "task": "t-2"})), False),
    ("priorities", _priorities(_task(impact_note="x" * 121)), False),
    ("priorities", _priorities(_task(extra=1)), False),
    ("vocab", VOCAB, True),
    ("vocab", dict(VOCAB, languages=[]), False),
    ("vocab", dict(VOCAB, affinity={"rust": ["systems"]}), True),
    ("vocab", dict(VOCAB, languages=["Python"]), False),
    ("contrib.receipt", _receipt(), True),
    ("contrib.receipt", _receipt(sha="abc"), False),
    ("contrib.receipt", _receipt(host_class="nvidia-12g"), True),
    ("contrib.receipt", _receipt(host_class="my-laptop.local"), False),
    ("contrib.receipt", _receipt(result="maybe"), False),
    ("contrib.receipt", _receipt(ts="yesterday"), False),
    ("suggestion", _suggestion(), True),
    ("suggestion", _suggestion(pick=_card(reasons=["because I said so"])), False),
    ("suggestion", _suggestion(pick=_card(reasons=["skill_match:typescript", "hw_unique:nvidia-12g"])), True),
    ("suggestion", _suggestion(alternatives=[_card(), _card(), _card()]), False),
    ("suggestion", _suggestion(engine={"version": "1", "llm": "grok:4"}), False),
    ("suggestion", _suggestion(engine={"version": "1", "llm": "cursor:x"}), False),
    ("contribute.state", _state(), True),
    ("contribute.state", _state(state="S9_magic"), False),
    ("contribute.state", _state(next_action={"kind": "post", "command": None, "why": "x"}), False),
    ("contribute.state", _state(sha=None, state="none"), True),
    ("profile", _profile(), True),
    ("profile", _profile(hostname="box"), False),
    ("profile", _profile(hw={"os": "linux", "arch": "x86_64", "ram_gib": 1, "gpu": {"class": "none"}}), False),
]


@pytest.mark.contrib_smoke
@pytest.mark.parametrize("name", NAMES)
def test_schema_files_are_draft_2020_12_with_matching_id(name):
    path = ROOT / "schemas" / f"{name}.schema.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert doc["$id"].endswith(f"/schemas/{name}.schema.json")
    assert S.load_schema(name) == doc


@pytest.mark.contrib_smoke
@pytest.mark.parametrize("name,doc,ok", CASES)
def test_stdlib_validator_verdicts(name, doc, ok):
    errors = S.validate(copy.deepcopy(doc), name)
    assert (not errors) is ok, errors


@pytest.mark.parametrize("name,doc,ok", CASES)
def test_twin_reference_jsonschema_agrees(name, doc, ok):
    jsonschema = pytest.importorskip("jsonschema")
    validator = jsonschema.Draft202012Validator(S.load_schema(name))
    assert (not list(validator.iter_errors(doc))) is ok


def test_unknown_keyword_is_refused_not_ignored():
    # A schema keyword the subset does not implement must fail loudly, never pass silently.
    with pytest.raises(S.SchemaError):
        S.validate_instance({"a": 1}, {"type": "object", "propertyNames": {"pattern": "^a$"}})
