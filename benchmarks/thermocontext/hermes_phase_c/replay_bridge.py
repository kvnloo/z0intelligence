"""Offline Phase-C bridge conformance. Never calls a provider or activates a plugin."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from z0int.context_resolve import ContextPacket, EvidenceRef, InformationNeed
from selection_boundary import freeze_digest, materialize, witness

HERE = Path(__file__).resolve().parent


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_native_sources(hermes_repo: Path, manifest: dict) -> str:
    """Shared source admission for baseline and separately frozen host-owned cases."""
    hermes_source = hermes_repo / manifest["hermes_source_file"]
    if sha(hermes_source) != manifest["hermes_composition_source_sha256"]:
        raise ValueError("Hermes composition source changed; requalify explicitly")
    revision = subprocess.check_output(["git", "-C", str(hermes_repo), "rev-parse", "HEAD"], text=True).strip()
    if revision != manifest["hermes_revision"]:
        raise ValueError("Hermes revision changed; requalify explicitly")
    # Bind the actual imported source, not an assumed checkout/PYTHONPATH identity.
    import z0int.context_resolve as resolver
    if sha(Path(resolver.__file__)) != manifest["context_packet_source_sha256"]:
        raise ValueError("ContextPacket source changed; requalify explicitly")
    return revision


def replay(pool_path: Path, manifest_path: Path, out: Path, hermes_repo: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    if sha(pool_path) != manifest["pool_file_sha256"]:
        raise ValueError("frozen pool hash mismatch")
    revision = verify_native_sources(hermes_repo, manifest)
    raw = json.loads(pool_path.read_text())
    packet = ContextPacket(task_id=raw["task_id"], needs=[InformationNeed(**n) for n in raw["needs"]],
                           evidence=[EvidenceRef(**e) for e in raw["evidence"]],
                           contradictions=raw["contradictions"], unresolved_gaps=raw["unresolved_gaps"])
    selection = materialize(packet, manifest["optional_ids"], pinned_ids=manifest["pinned_ids"],
                            legal_optional_ids=manifest["optional_ids"], expected_pool_digest=freeze_digest(packet),
                            budget=1_000_000, count_tokens=lambda text: len(text.encode()),
                            tokenizer_identity="fixture_utf8_bytes_not_provider_tokens")
    sys.path.insert(0, str(hermes_repo))
    from agent.turn_context import compose_user_api_content, substitute_api_content
    task = packet.needs[0].description
    row = {"role": "user", "content": task,
           "api_content": compose_user_api_content(task, "", selection["context"])}
    substitute_api_content(row)
    observed = witness(selection, {"model": "NOT_RUN", "messages": [row]})
    observed.update(request_origin="offline_native_serialization_not_transport", transmitted=False)
    receipt = {"classification": "OFFLINE_CONFORMANCE_ONLY", "hermes_revision": revision,
               "source_manifest_sha256": sha(manifest_path), "pool_file_sha256": sha(pool_path),
               "study_files": {p.name: sha(p) for p in sorted(HERE.glob("*.py"))},
               "sampler_calls": 0, "provider_calls": 0, "runtime_activation": False,
               "verified_task_success": None, "actual_provider_tokens": None,
               "constructed_request_contains_context": observed["request_contains_exact_context"],
               "speedup_or_savings_claim": False, "frozen_phase_a_modified": False}
    out.mkdir(parents=True, exist_ok=False)
    for name, value in (("selection.json", selection), ("wire-witness.json", observed), ("receipt.json", receipt)):
        (out / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", type=Path, default=HERE / "development_pool.json")
    parser.add_argument("--manifest", type=Path, default=HERE / "sources.json")
    parser.add_argument("--hermes-repo", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(replay(args.pool, args.manifest, args.out, args.hermes_repo), sort_keys=True))
