"""Evidence discipline: source claims must never drive runtime selection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from z0int.cognition.manifest import (
    ROLES,
    SCHEMA,
    LocalCognitionManifest,
    Measurement,
    ModelCapability,
    SourceClaim,
    assert_selection_is_evidence_based,
    default_manifest_path,
    load_local_cognition,
    parse_manifest,
)


def test_shipped_manifest_loads_and_validates():
    manifest = load_local_cognition()
    assert manifest.schema == SCHEMA
    assert manifest.path == default_manifest_path()
    assert "nemotron_orchestrator_8b" in manifest.models
    assert "rtx3080ti-12gb" in manifest.machines


def test_every_role_tag_in_the_shipped_manifest_is_known():
    manifest = load_local_cognition()
    for model in manifest.models.values():
        for role in model.role_tags:
            assert role in ROLES


def test_shipped_role_defaults_point_at_real_models_carrying_that_role():
    manifest = load_local_cognition()
    for role, model_id in manifest.role_defaults.items():
        assert role in ROLES
        assert model_id in manifest.models
        assert role in manifest.models[model_id].role_tags


def test_source_reported_benchmarks_are_recorded_for_the_orchestrator():
    manifest = load_local_cognition()
    model = manifest.get("nemotron_orchestrator_8b")
    names = {c.name for c in model.source_reported_benchmarks}
    assert "HLE" in names
    assert "tau2-Bench" in names
    # Every recorded claim must keep its provenance.
    assert all(c.source_url for c in model.source_reported_benchmarks)


def test_non_commercial_licences_are_not_hidden():
    manifest = load_local_cognition()
    assert manifest.get("nemotron_orchestrator_8b").commercial_use is False
    assert manifest.get("hammer2.1_3b").commercial_use is False
    assert manifest.get("hammer2.1_7b").commercial_use is False
    assert manifest.get("qwen3.5_9b").commercial_use is True
    assert manifest.get("functiongemma_270m").gated_access is True


def test_agent0_is_classified_as_a_methodology_not_a_checkpoint():
    raw = json.loads(default_manifest_path().read_text(encoding="utf-8"))
    agent0 = raw["sources"]["agent0"]
    assert agent0["kind"] == "training_methodology"
    assert agent0["not_a_checkpoint"] is True
    # and it must not leak into the serving roster
    assert "agent0" not in {k.lower() for k in raw["models"]}


# --- evidence separation ------------------------------------------------


def _model(model_id="m1", **kw):
    kw.setdefault("role_tags", ("bounded_scorer",))
    return ModelCapability(model_id=model_id, **kw)


def test_model_with_only_marketing_claims_is_not_measured():
    model = _model(
        source_reported_benchmarks=(
            SourceClaim(name="HLE", value="99.9", source_url="https://example.invalid"),
        )
    )
    assert model.measured is False


def test_selectable_requires_local_measurement_by_default():
    manifest = parse_manifest(
        {
            "schema": SCHEMA,
            "models": {
                "claimed": {
                    "role_tags": ["semantic_orchestrator"],
                    "source_reported_benchmarks": [{"name": "HLE", "value": "99.9"}],
                },
                "measured": {
                    "role_tags": ["semantic_orchestrator"],
                    "source_reported_benchmarks": [{"name": "HLE", "value": "1.0"}],
                    "measurements": [
                        {
                            "machine": "rtx3080ti-12gb",
                            "runtime": "llama.cpp",
                            "quantization": "Q4_K_M",
                            "context": 4096,
                            "decode_tok_s": 42.0,
                        }
                    ],
                },
            },
        }
    )
    # A wildly better marketing claim does not make a model selectable...
    assert [m.model_id for m in manifest.selectable("semantic_orchestrator")] == ["measured"]
    # ...but the claim is still recorded and auditable.
    assert manifest.get("claimed").source_reported_benchmarks[0].value == "99.9"


def test_selectable_excludes_rejected_and_unavailable():
    manifest = parse_manifest(
        {
            "schema": SCHEMA,
            "models": {
                "r": {
                    "role_tags": ["bounded_scorer"],
                    "promotion_state": "rejected",
                    "measurements": [
                        {"machine": "m", "runtime": "x", "quantization": "q", "context": 1}
                    ],
                },
                "u": {
                    "role_tags": ["bounded_scorer"],
                    "promotion_state": "unavailable",
                    "measurements": [
                        {"machine": "m", "runtime": "x", "quantization": "q", "context": 1}
                    ],
                },
            },
        }
    )
    assert manifest.selectable("bounded_scorer") == ()


def test_assert_selection_is_evidence_based_flags_unmeasured_defaults():
    raw = json.loads(default_manifest_path().read_text(encoding="utf-8"))
    raw["models"]["nemotron_orchestrator_8b"]["measurements"] = []
    raw["models"]["nemotron_orchestrator_8b"]["local_benchmark_receipt_ids"] = []
    manifest = parse_manifest(raw)
    offenders = assert_selection_is_evidence_based(manifest)
    assert "nemotron_orchestrator_8b" in offenders


def test_a_measured_default_passes_the_evidence_gate():
    raw = json.loads(default_manifest_path().read_text(encoding="utf-8"))
    for mid in raw["models"]:
        raw["models"][mid]["measurements"] = [
            {"machine": "rtx3080ti-12gb", "runtime": "llama.cpp", "quantization": "Q4_K_M",
             "context": 4096, "decode_tok_s": 10.0}
        ]
    manifest = parse_manifest(raw)
    assert assert_selection_is_evidence_based(manifest) == []


# --- validation ---------------------------------------------------------


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError, match="unknown role_tags"):
        _model(role_tags=("not_a_role",))
    with pytest.raises(ValueError, match="unknown role"):
        parse_manifest({"schema": SCHEMA, "models": {"a": {"role_tags": ["nope"]}}})


def test_unknown_promotion_state_is_rejected():
    with pytest.raises(ValueError, match="unknown promotion_state"):
        _model(promotion_state="winner")


def test_role_default_must_name_a_known_model_and_matching_role():
    with pytest.raises(ValueError, match="unknown model"):
        parse_manifest(
            {
                "schema": SCHEMA,
                "models": {"a": {"role_tags": ["bounded_scorer"]}},
                "role_defaults": {"bounded_scorer": "b"},
            }
        )
    with pytest.raises(ValueError, match="does not carry role"):
        parse_manifest(
            {
                "schema": SCHEMA,
                "models": {"a": {"role_tags": ["bounded_scorer"]}},
                "role_defaults": {"semantic_orchestrator": "a"},
            }
        )


def test_wrong_schema_version_is_rejected():
    with pytest.raises(ValueError, match="unsupported manifest schema"):
        parse_manifest({"schema": "z0int.models.v1", "models": {"a": {}}})


def test_non_commercial_licence_cannot_be_marked_commercial():
    with pytest.raises(ValueError, match="non-commercial"):
        _model(license="cc-by-nc-4.0", commercial_use=True)
    with pytest.raises(ValueError, match="non-commercial"):
        _model(license="qwen-research", commercial_use=True)


def test_gating_is_about_access_and_may_still_be_commercial():
    # Gemma is gated on Hugging Face but commercially usable under its terms.
    model = _model(gated_access=True, commercial_use=True, license="gemma")
    assert model.gated_access is True and model.commercial_use is True


def test_measurement_lookup_and_best_selection():
    model = _model(
        measurements=(
            Measurement(machine="m", runtime="llama.cpp", quantization="Q4_K_M", context=4096,
                        decode_tok_s=40.0),
            Measurement(machine="m", runtime="llama.cpp", quantization="Q4_K_M", context=8192,
                        decode_tok_s=12.0),
        )
    )
    exact = model.measurement_for(runtime="llama.cpp", quantization="Q4_K_M", context=8192)
    assert exact is not None and exact.decode_tok_s == 12.0
    best = model.best_measurement(metric="decode_tok_s")
    assert best is not None and best.context == 4096


def test_to_dict_round_trips_through_json():
    manifest = load_local_cognition()
    payload = manifest.to_dict()
    text = json.dumps(payload, sort_keys=True)
    reloaded = parse_manifest(json.loads(text))
    assert set(reloaded.models) == set(manifest.models)
    assert reloaded.role_defaults == manifest.role_defaults


def test_missing_manifest_file_raises_file_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_local_cognition(tmp_path / "nope.json")
