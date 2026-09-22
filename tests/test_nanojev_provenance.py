"""Regression guard for the NanoJev / "JEV" provenance error.

For an unknown period the Phase 1B arm `compiler+jev` was narrated, in this
repo's docs and in the published z0evals article, as the hosted TypeSafe Jev
scorer's result. It never was. The immutable corpus records:

    model_id       = nanojev_06b
    model_revision = 4a19595eada0857133c0d2be024f879a4077054b
    quant          = bfloat16

for every `compiler+jev` row. The name came from the arm's legacy display value
`model="JEV"`, which every emission silently rewrote to `nanojev_06b`.

These tests fail if that substitution is reintroduced, or if the corpus is ever
relabelled.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "results" / "phase1b" / "p1b-20260921T1430Z" / "observations.jsonl"

NANOJEV_REVISION = "4a19595eada0857133c0d2be024f879a4077054b"


def _load_script(module_name: str, rel_path: str):
    """Import a script that is not part of the package."""
    path = ROOT / rel_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # dataclasses resolves the module through sys.modules; without this the
    # decorator fails with AttributeError on __dict__.
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def densify():
    return _load_script("_z0int_densify_for_test", "scripts/densify_measurements.py")


def test_compiler_jev_arm_names_nanojev(densify):
    arm = densify._arms()["compiler+jev"]
    assert arm.model == "nanojev_06b"
    assert arm.scorer_type == "nanojev"
    assert arm.jev is True


def test_nanojev_scorer_is_not_treated_as_a_supervisor_resident(densify):
    """The scorer is non-generative, so it contributes no residency class.

    This behaviour is unchanged; the test exists because the honest `model` value
    makes it easy to accidentally start counting NanoJev as a served llama.cpp
    model.
    """
    arm = densify._arms()["compiler+jev"]
    assert arm.models() == ()


def test_every_jev_flagged_arm_declares_its_scorer_type(densify):
    for name, arm in densify._arms().items():
        if arm.jev:
            assert arm.scorer_type == "nanojev", f"{name} routes the nanojev scorer but does not say so"


def test_no_legacy_jev_sentinel_remains_in_the_generator():
    src = (ROOT / "scripts" / "densify_measurements.py").read_text(encoding="utf-8")
    assert 'model="JEV"' not in src, "the arm model must name the real scorer"
    assert 'arm.model != "JEV"' not in src, "the silent JEV -> nanojev_06b substitution is back"


def test_composition_eval_sentinel_names_the_loaded_scorer():
    mod = _load_script("_z0int_composition_eval_for_test", "scripts/composition_eval.py")
    assert mod.SCORER_SENTINEL == "nanojev_06b"
    for comp in mod.COMPOSITIONS:
        if comp.jev is not None:
            assert comp.jev == mod.SCORER_SENTINEL


@pytest.mark.skipif(not CORPUS.is_file(), reason="phase 1B corpus not present")
def test_corpus_records_nanojev_for_every_compiler_jev_row():
    rows = []
    with CORPUS.open(encoding="utf-8") as fh:
        for line in fh:
            d = json.loads(line)
            if d.get("arm") == "compiler+jev":
                rows.append(d)

    assert rows, "no compiler+jev rows found; the corpus layout changed"
    model_ids = {r.get("model_id") for r in rows}
    revisions = {r.get("model_revision") for r in rows}

    assert model_ids == {"nanojev_06b"}, f"compiler+jev is not a single scorer: {model_ids}"
    assert revisions == {NANOJEV_REVISION}, f"unexpected revision(s): {revisions}"


@pytest.mark.skipif(not CORPUS.is_file(), reason="phase 1B corpus not present")
def test_no_arm_in_the_corpus_claims_to_be_the_hosted_jev():
    """No phase 1B arm is TypeSafe Jev; the hosted comparison is the J1 pilot."""
    with CORPUS.open(encoding="utf-8") as fh:
        offenders = set()
        for line in fh:
            d = json.loads(line)
            mid = str(d.get("model_id") or "")
            if mid.lower() in ("jev", "typesafe-jev"):
                offenders.add((d.get("arm"), mid))
    assert not offenders, f"phase 1B corpus contains a hosted-Jev arm: {sorted(offenders)}"
