"""Bonsai 2: additive, pinned GGUF candidates; no inferred benchmark authority."""

from __future__ import annotations

import json
import re
from pathlib import Path

from z0int import models_mgmt
from z0int.cognition.manifest import load_local_cognition
from z0int.cognition.serving import GGUF_LAYOUT, quant_from_name


SIZES = ("2b", "4b", "9b", "27b")


def test_all_four_candidates_pinned_and_unmeasured():
    manifest = load_local_cognition()
    deploy = models_mgmt.load_manifest()
    for size in SIZES:
        mid = f"bonsai2_{size}"
        capability = manifest.get(mid)
        metadata = deploy["models"][mid]
        assert capability.promotion_state == "candidate"
        assert capability.measurements == ()
        assert capability.local_benchmark_receipt_ids == ()
        assert capability.tested_context is None
        assert not capability.measured
        assert capability.license == metadata["license"] == "apache-2.0"
        assert capability.hf == metadata["hf"]
        assert capability.revision == metadata["revision"]
        assert re.fullmatch(r"[a-f0-9]{40}", capability.revision)
        assert metadata["type"] == "gguf_bundle"
        assert metadata["resident_preferred"] is False
        assert metadata["allow_patterns"] == [Path(GGUF_LAYOUT[mid]).name]
        assert GGUF_LAYOUT[mid].startswith(f"{mid}/")
        for role in capability.role_tags:
            assert mid in {m.model_id for m in manifest.by_role(role)}
            assert mid not in {m.model_id for m in manifest.selectable(role, require_measured=True)}
        assert mid not in deploy["decision_roster"]["candidates"]
        assert mid not in manifest.role_defaults.values()
        assert mid not in deploy["policies"]["twelve_gb"]["resident"]
        assert mid in deploy["policies"]["twelve_gb"]["on_demand"]


def test_shipped_yaml_and_json_model_registry_agree():
    root = Path(__file__).resolve().parents[1] / "manifests"
    try:
        import yaml
    except ImportError:
        return  # JSON is the runtime format; PyYAML is optional.
    declared = yaml.safe_load((root / "models.yaml").read_text())
    runtime = json.loads((root / "models.z0int.json").read_text())
    for size in SIZES:
        mid = f"bonsai2_{size}"
        assert declared["models"][mid] == runtime["models"][mid]
        for name in ("twelve_gb", "high_vram"):
            assert mid in declared["policies"][name]["on_demand"]
            assert mid in runtime["policies"][name]["on_demand"]


def test_gguf_quantization_reports_native_ternary_packings():
    assert quant_from_name("Ternary-Bonsai-2-2B-PQ2_0.gguf") == "PQ2_0"
    assert quant_from_name("Ternary-Bonsai-2-9B-PTQ1_0.gguf") == "PTQ1_0"
    assert quant_from_name("some-Q2_0.gguf") == "Q2_0"


def test_managed_gguf_requires_exact_file_and_rejects_wildcards(tmp_path, monkeypatch):
    monkeypatch.setattr(models_mgmt, "managed_model_dir", lambda mid: tmp_path / mid)
    metadata = models_mgmt.load_manifest()["models"]["bonsai2_4b"]
    target = tmp_path / "bonsai2_4b" / metadata["allow_patterns"][0]
    assert not models_mgmt.model_present("bonsai2_4b", metadata)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"gguf-fixture")
    assert models_mgmt.model_present("bonsai2_4b", metadata)
    assert not models_mgmt.gguf_bundle_complete(
        "bonsai2_4b", {"allow_patterns": ["../secret.gguf"]}
    )
    assert not models_mgmt.gguf_bundle_complete(
        "bonsai2_4b", {"allow_patterns": ["*.gguf"]}
    )
    target.unlink()
    assert not models_mgmt.model_present("bonsai2_4b", metadata)


def test_dry_run_sync_is_exact_revision_and_single_gguf(tmp_path, monkeypatch):
    monkeypatch.setattr(models_mgmt, "managed_model_dir", lambda mid: tmp_path / mid)
    result = models_mgmt.sync_models(
        plan={"on_demand": ["bonsai2_4b"]},
        which="on_demand",
        dry_run=True,
    )
    assert len(result["results"]) == 1
    row = result["results"][0]
    meta = models_mgmt.load_manifest()["models"]["bonsai2_4b"]
    assert row["status"] == "would_download"
    assert row["hf"] == meta["hf"]
    assert row["revision"] == meta["revision"]
    assert row["allow_patterns"] == ["Ternary-Bonsai-2-4B-PQ2_0.gguf"]
    assert row["type"] == "gguf_bundle"
