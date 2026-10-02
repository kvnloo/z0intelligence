#!/usr/bin/env python3
"""Run the first full AODL governed golden canary and collect its proof.

This wrapper does not create or print credentials. It orchestrates the existing
live public-text proof and the frozen z0evals root-trace collector.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def git_head(path: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()


def git_clean(path: Path) -> bool:
    return subprocess.check_output(["git", "status", "--porcelain"], cwd=path, text=True) == ""


def pinned_z0int_revision(manifest: Path) -> str:
    text = manifest.read_text(encoding="utf-8")
    match = re.search(
        r"- repo: kvnloo/z0intelligence\s+commit: ([0-9a-f]{40})",
        text,
    )
    if not match:
        raise ValueError("golden-trace manifest does not pin kvnloo/z0intelligence")
    return match.group(1)


def run(cmd: list[str], *, cwd: Path, env: dict[str, str]) -> None:
    subprocess.run(cmd, cwd=cwd, env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--omp-root", type=Path, required=True)
    parser.add_argument("--z0evals-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not os.environ.get("OPENROUTER_API_KEY"):
        parser.error("OPENROUTER_API_KEY must already be configured in the environment")
    if not args.omp_root.is_dir():
        parser.error("--omp-root must be an existing OMP checkout")
    if shutil.which("bun") is None:
        parser.error("bun must be installed to execute the real OMP registered tool")
    omp_required = (
        "src/extensibility/extensions/loader.ts",
        "src/extensibility/extensions/runner.ts",
        "src/session/session-manager.ts",
        "src/session/auth-storage.ts",
        "src/config/model-registry.ts",
    )
    missing_omp = [rel for rel in omp_required if not (args.omp_root / rel).is_file()]
    if missing_omp:
        parser.error("OMP checkout is missing required runtime files: " + ", ".join(missing_omp))
    collector = args.z0evals_root / "studies" / "aodl-admission-v1" / "collect_golden.py"
    manifest_path = args.z0evals_root / "studies" / "aodl-admission-v1" / "golden-trace-manifest.yaml"
    bundle_schema = args.z0evals_root / "studies" / "aodl-admission-v1" / "golden-canary-bundle.schema.json"
    if not collector.is_file() or not manifest_path.is_file() or not bundle_schema.is_file():
        parser.error("--z0evals-root does not contain the golden collector, manifest, and bundle schema")
    if not git_clean(ROOT):
        parser.error("z0intelligence worktree must be clean for a golden artifact")
    if not git_clean(args.z0evals_root):
        parser.error("z0evals worktree must be clean for a golden artifact")
    z0int_head = git_head(ROOT)
    try:
        pinned = pinned_z0int_revision(manifest_path)
    except ValueError as exc:
        parser.error(str(exc))
    if pinned != z0int_head:
        parser.error(
            f"z0evals golden manifest pins {pinned}, but z0intelligence HEAD is {z0int_head}"
        )
    try:
        import aodl_contract
    except Exception as exc:
        parser.error(f"aodl_contract is not importable: {type(exc).__name__}")
    if getattr(aodl_contract, "CANON_VERSION", None) != "aodl-canon-1":
        parser.error("aodl_contract must expose aodl-canon-1")
    if args.output.exists():
        parser.error("--output must not already exist")

    args.output.mkdir(parents=True)
    raw = args.output / "raw"
    env = dict(os.environ)

    # The live proof owns a fresh isolated Z0INT_HOME under raw/state and sends
    # only the synthetic public fixture to the configured free provider.
    run(
        [
            sys.executable,
            str(ROOT / "scripts" / "prove-intelligence.py"),
            "--output",
            str(raw),
            "--omp-root",
            str(args.omp_root),
        ],
        cwd=ROOT,
        env=env,
    )

    state = raw / "state"
    golden = args.output / "golden-trace.json"
    run(
        [
            sys.executable,
            str(collector),
            "--receipts",
            str(state / "receipts" / "decisions.jsonl"),
            "--outcomes",
            str(state / "receipts" / "outcomes.jsonl"),
            "--tokenomics",
            str(state / "tokenomics" / "events.jsonl"),
            "--trace-id",
            "remote-public-proof",
            "--require-verified",
            "--out",
            str(golden),
        ],
        cwd=args.z0evals_root,
        env=env,
    )

    proof = json.loads(golden.read_text(encoding="utf-8"))
    # A complete gold outcome can be a verified failure. Only this fixture's
    # positive verifier outcome permits the success claim in bundle.json.
    stages = proof.get("stages") or {}
    verified = stages.get("verified_outcome") or {}
    outcome = verified.get("outcome") or {}
    physical_id = (stages.get("physical_execution") or {}).get("receipt_id")
    if not (
        proof.get("structural_execution_complete") is True
        and proof.get("verified_outcome_complete") is True
        and proof.get("root_trace_id") == "remote-public-proof"
        and verified.get("present") is True
        and verified.get("outcome_tier") == "gold"
        and isinstance(physical_id, str) and bool(physical_id)
        and verified.get("trace_id") == physical_id
        and outcome.get("verified_success") is True
        and outcome.get("verified") is True
        and outcome.get("verification_source") == "exact_string_CANONICAL_OK"
    ):
        raise RuntimeError("golden trace did not verify the exact CANONICAL_OK canary")

    # Copy only small, human-reviewable summaries to the bundle root. Raw
    # receipts remain under raw/ and are never implicitly committed anywhere.
    for name in ("summary.json", "health.json", "omp-governed.json"):
        source = raw / name
        if source.is_file():
            shutil.copy2(source, args.output / name)

    manifest = {
        "schema": "z0int.aodl_golden_canary_bundle.v1",
        "z0int_revision": z0int_head,
        "z0evals_revision": git_head(args.z0evals_root),
        "study_pinned_z0int_revision": pinned,
        "root_trace_id": "remote-public-proof",
        "synthetic_fixture": "CANONICAL_OK",
        "provider_execution": True,
        "verified_outcome": True,
        "verification_scope": "exact public synthetic fixture only",
        "structural_execution_complete": proof["structural_execution_complete"],
        "verified_outcome_complete": proof["verified_outcome_complete"],
        "golden_trace": "golden-trace.json",
        "raw_state": "raw/state",
    }
    (args.output / "bundle.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps({
        "ok": True,
        "output": str(args.output),
        "z0int_revision": manifest["z0int_revision"],
        "z0evals_revision": manifest["z0evals_revision"],
        "root_trace_id": manifest["root_trace_id"],
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
