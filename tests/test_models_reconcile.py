"""Source-of-truth reconciliation (manifest vs disk vs generated projection).

On 2026-09-22 three artifacts disagreed about the same models:

* the manifest pinned `openjev_06b` at `c1899de289a04d1…` (what is on disk);
* `~/.z0int/config/z0int.json`, a generated projection, pinned it at
  `c1899deebe5c9af…` (not on disk at all);
* the projection claimed `resident: [openjev_06b, local_mb]` while the manifest
  policy said `[nanojev_06b, local_mb]`, and the live generative supervisor
  reported `hammer2.1_3b` — three answers, three id spaces, nothing reading all
  three.
"""

from __future__ import annotations

import json

import pytest

from z0int.models_mgmt import (
    RECONCILE_SCHEMA,
    load_manifest,
    manifest_digest,
    reconcile,
    write_projection,
)


def test_reconcile_reports_both_planes_separately():
    r = reconcile()
    assert r["schema"] == RECONCILE_SCHEMA
    assert set(r["residency"]) == {"generative", "decision"}
    assert r["canonical_source"].endswith("models.z0int.json")


def test_generative_residency_is_only_ever_the_live_runtime():
    gen = reconcile()["residency"]["generative"]
    # A config file must never be the source of a residency claim.
    assert gen["config_file_declares_resident"] is None
    if gen.get("reachable"):
        assert gen["reported_by_live_runtime"]


def test_decision_residency_is_labelled_as_a_plan_not_a_fact():
    dec = reconcile()["residency"]["decision"]
    assert "planned_resident" in dec
    assert "resident" not in dec or dec.get("resident") is None
    # Nothing has loaded a decision backend, so there is nothing to report.
    assert dec["reported_by_live_runtime"] is None


def test_projection_never_stores_a_bare_resident_key(tmp_path):
    """A generated file must not be readable as 'this model is loaded now'."""
    target = tmp_path / "z0int.json"
    write_projection(path=target)

    payload = json.loads(target.read_text(encoding="utf-8"))
    plan = payload["models_plan"]

    assert "resident" not in plan, "an unqualified `resident` is the ambiguity being removed"
    assert "planned_resident" in plan
    assert "residency_note" in plan
    assert "intent only" in plan["residency_note"]


def test_projection_is_stamped_with_the_manifest_digest(tmp_path):
    target = tmp_path / "z0int.json"
    write_projection(path=target)
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["generated_manifest_sha256"] == manifest_digest()
    assert payload["generated_from"] == "manifests/models.z0int.json"


def test_a_regenerated_projection_reconciles_clean_on_the_axes_it_controls(tmp_path):
    """The projection-specific drift classes must not fire after regeneration."""
    target = tmp_path / "z0int.json"
    write_projection(path=target)
    r = reconcile()
    projection_kinds = {d["kind"] for d in r["drift"] if d["model_id"] is None}
    assert not projection_kinds, f"projection drift still present: {projection_kinds}"


def test_a_revision_absent_from_disk_is_reported():
    man = load_manifest()
    man = json.loads(json.dumps(man))
    man["models"]["openjev_06b"]["revision"] = "0" * 40
    r = reconcile(manifest=man)
    kinds = {d["kind"] for d in r["drift"] if d["model_id"] == "openjev_06b"}
    assert "manifest_revision_not_on_disk" in kinds


def test_a_bundle_model_is_not_reported_missing_because_it_is_absent_from_the_hf_cache():
    """NanoJev lives in the managed bundle dir, not the HF hub cache."""
    r = reconcile()
    kinds = {d["kind"] for d in r["drift"] if d["model_id"] == "nanojev_06b"}
    assert "manifest_revision_not_on_disk" not in kinds

    row = next(m for m in r["models"] if m["model_id"] == "nanojev_06b")
    assert row["on_disk"] is True
    assert row["type"] == "hf_bundle"


def test_manifest_digest_is_stable_and_order_independent():
    man = load_manifest()
    assert manifest_digest(man) == manifest_digest(man)
    reordered = dict(reversed(list(man.items())))
    assert manifest_digest(reordered) == manifest_digest(man)


@pytest.mark.skipif(
    not load_manifest().get("models"),
    reason="manifest has no models",
)
def test_every_manifest_model_appears_in_the_report():
    r = reconcile()
    ids = {m["model_id"] for m in r["models"]}
    assert ids == set((load_manifest().get("models") or {}).keys())
