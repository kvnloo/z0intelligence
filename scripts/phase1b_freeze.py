#!/usr/bin/env python3
"""Phase 1B step A -- freeze the Phase 1 baseline before changing behaviour.

Phase 1B is a measurement campaign.  Its conclusions are only meaningful if the
starting state is pinned: the exact weights, the exact fixtures, the exact gate,
and the exact raw per-call receipts that produced the Phase 1 numbers.  This
script writes that pin.

It deliberately does **not** copy fixtures out of their owning repositories.
Fixtures are referenced and hashed (owner repo, path, sha256, rows); copying them
would create a second source of truth, which the ownership boundaries forbid.
Raw *run receipts* are different: they live under the ephemeral ``.work`` scratch
tree and are overwritten by the next run, so those are copied into the immutable
run directory and hashed there.

    python scripts/phase1b_freeze.py --run-id p1b-20260921T000000Z
    python scripts/phase1b_freeze.py --run-id p1b-x --skip-gguf-hash   # fast, hashes reused
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

SCHEMA = "z0int.phase1b.freeze.v1"
RESULTS_ROOT = REPO / "results" / "phase1b"

WORK = Path("/mnt/zer0models/workspace/zer0/oss/.work")
GGUF_ROOT = Path("/mnt/zer0models/zer0-models/gguf")
DEFAULT_LLAMA_SERVER = WORK / "llama.cpp" / "build" / "bin" / "llama-server"

#: Every repository that participates in Phase 1B measurements.  ``root`` is the
#: checkout whose git metadata owns the worktree (a linked worktree's ``.git`` is
#: a file, so we resolve the real repo through ``worktree list``).
PARTICIPATING_REPOS: tuple[dict[str, str], ...] = (
    {"label": "z0intelligence", "path": "/mnt/zer0models/workspace/zer0/oss/.work/z0intelligence"},
    {"label": "evolution-lab", "path": "/home/kvn/tmp/evolution-lab"},
    {"label": "aodl", "path": "/home/kvn/tmp/aodl"},
    {"label": "tokenomics", "path": "/mnt/zer0models/workspace/tokenomics"},
    {"label": "kerdoios", "path": "/home/kvn/tmp/kerdoios"},
    {"label": "openjev", "path": "/home/kvn/tmp/openjev"},
)

#: Fixtures, pinned by owning repo -- never copied into this repo.
FIXTURE_REFS: tuple[dict[str, str], ...] = (
    {"owner": "z0intelligence", "kind": "bounded-choice", "path": "benchmarks/fixtures/local-cognition-v1/examples.jsonl", "root": "z0intelligence"},
    {"owner": "z0intelligence", "kind": "orchestration", "path": "benchmarks/fixtures/orchestration-v1/scenarios.jsonl", "root": "z0intelligence"},
    {"owner": "z0intelligence", "kind": "capability", "path": "benchmarks/fixtures/decision-capability-v1/examples.jsonl", "root": "z0intelligence"},
    {"owner": "z0intelligence", "kind": "serving", "path": "manifests/local_cognition.v1.json", "root": "z0intelligence"},
    {"owner": "evolution-lab", "kind": "tournament", "path": "data/tool_tournament/v1/fixtures.jsonl", "root": "evolution-lab"},
    {"owner": "evolution-lab", "kind": "tournament-manifest", "path": "data/tool_tournament/v1/manifest.json", "root": "evolution-lab"},
    {"owner": "aodl", "kind": "authority-schema", "path": "schema/hotl-0.2.schema.json", "root": "aodl"},
    {"owner": "aodl", "kind": "authority-language", "path": "spec/hotl-0.2.ebnf", "root": "aodl"},
    {"owner": "tokenomics", "kind": "accounting-conformance", "path": "fixtures/conformance.json", "root": "tokenomics"},
    {"owner": "tokenomics", "kind": "accounting-receipt", "path": "fixtures/z0int_receipt.json", "root": "tokenomics"},
    {"owner": "tokenomics", "kind": "accounting-placement", "path": "fixtures/kerdoios_observation.json", "root": "tokenomics"},
)

#: Raw per-call receipts from the Phase 1 run.  Copied into the frozen run dir.
RAW_SOURCES: tuple[dict[str, str], ...] = (
    {"kind": "composition", "path": "compositions/raw.jsonl"},
    {"kind": "composition-escalating", "path": "compositions-escalating/raw.jsonl"},
    {"kind": "bounded", "path": "eval-llamacpp/raw.jsonl"},
    {"kind": "bounded", "path": "eval-thinking/raw.jsonl"},
    {"kind": "bounded", "path": "eval-budget1024/raw.jsonl"},
    {"kind": "orchestration", "path": "orch-v1/raw.jsonl"},
)


def _sha256_file(path: Path, chunk: int = 1 << 22) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _count_jsonl(path: Path) -> int:
    n = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.strip():
                n += 1
    return n


def _git(args: list[str], cwd: Path) -> str:
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def repo_state(entry: dict[str, str]) -> dict[str, Any]:
    path = Path(entry["path"])
    out: dict[str, Any] = {"label": entry["label"], "path": str(path), "exists": path.is_dir()}
    if not out["exists"]:
        return out
    out["sha"] = _git(["rev-parse", "HEAD"], path)
    out["branch"] = _git(["branch", "--show-current"], path)
    dirty = _git(["status", "--porcelain"], path)
    out["dirty_files"] = [ln for ln in dirty.splitlines() if ln.strip()] if dirty else []
    out["dirty_count"] = len(out["dirty_files"])
    # A linked worktree's common dir is the owning checkout; record both.
    common = _git(["rev-parse", "--path-format=absolute", "--git-common-dir"], path)
    out["git_common_dir"] = common
    out["worktree_root"] = _git(["rev-parse", "--show-toplevel"], path)
    return out


def _load_manifest() -> dict[str, Any]:
    return json.loads((REPO / "manifests" / "local_cognition.v1.json").read_text(encoding="utf-8"))


def model_records(*, gguf_hashes: dict[str, str], gguf_dir: Path) -> list[dict[str, Any]]:
    from z0int.cognition.serving import GGUF_LAYOUT

    manifest = _load_manifest()
    rows: list[dict[str, Any]] = []
    for model_id, cap in manifest["models"].items():
        rel = GGUF_LAYOUT.get(model_id)
        gguf = (gguf_dir / rel) if rel else None
        record: dict[str, Any] = {
            "model_id": model_id,
            "hf": cap.get("hf"),
            "hf_revision": cap.get("revision"),
            "quant": cap.get("tested_quantization") or (cap.get("available_quantizations") or [None])[0],
            "available_quantizations": list(cap.get("available_quantizations") or []),
            "license": cap.get("license"),
            "commercial_use": cap.get("commercial_use"),
            "gated_access": cap.get("gated_access"),
            "parameter_count": cap.get("parameter_count"),
            "role_tags": list(cap.get("role_tags") or []),
            "runtime": (cap.get("supported_runtimes") or [None])[0],
            "gguf": None,
        }
        if gguf is not None and gguf.is_file():
            record["gguf"] = {
                "path": str(gguf),
                "bytes": gguf.stat().st_size,
                "sha256": gguf_hashes.get(str(gguf)) or _sha256_file(gguf),
            }
        elif gguf is not None:
            record["gguf"] = {"path": str(gguf), "missing": True}
        rows.append(record)
    return rows


def fixture_records() -> list[dict[str, Any]]:
    roots = {entry["label"]: Path(entry["path"]) for entry in PARTICIPATING_REPOS}
    out: list[dict[str, Any]] = []
    for ref in FIXTURE_REFS:
        root = roots.get(ref["root"])
        record: dict[str, Any] = {
            "owner": ref["owner"],
            "kind": ref["kind"],
            "path": ref["path"],
            "root": ref["root"],
        }
        if root is None:
            record["status"] = "unknown-owner"
            out.append(record)
            continue
        target = root / ref["path"]
        if not target.is_file():
            record["status"] = "missing"
            record["resolved"] = str(target)
            out.append(record)
            continue
        record["status"] = "ok"
        record["resolved"] = str(target)
        record["bytes"] = target.stat().st_size
        record["sha256"] = _sha256_file(target)
        if target.suffix in (".jsonl",):
            record["rows"] = _count_jsonl(target)
        out.append(record)
    return out


def _extract_frozen_config() -> dict[str, Any]:
    """Read the frozen gate/utility/escalation config from its owning repos."""
    cfg: dict[str, Any] = {}
    # evolution-lab owns the gate + utility + ladder.
    try:
        el = next(Path(e["path"]) for e in PARTICIPATING_REPOS if e["label"] == "evolution-lab")
        sys.path.insert(0, str(el))
        from evolution_lab.q_route.gate import GateConfig, LADDER, ARM_LADDER
        from evolution_lab.q_route.utility import UtilityConfig

        cfg["gate"] = GateConfig().to_dict()
        cfg["utility"] = UtilityConfig().to_dict()
        cfg["ladder"] = list(LADDER)
        cfg["arm_ladder"] = dict(sorted(ARM_LADDER.items()))
    except Exception as exc:  # noqa: BLE001 - config capture must not abort the freeze
        cfg["gate_error"] = f"{type(exc).__name__}: {exc}"
    # z0intelligence owns escalation thresholds.
    try:
        from z0int.cognition.escalation import EscalationThresholds

        th = EscalationThresholds()
        if hasattr(th, "to_dict"):
            cfg["escalation"] = th.to_dict()
        else:  # pragma: no cover - defensive
            cfg["escalation"] = {
                key: getattr(th, key)
                for key in sorted(vars(th)) if not key.startswith("_")
            }
    except Exception as exc:  # noqa: BLE001
        cfg["escalation_error"] = f"{type(exc).__name__}: {exc}"
    return cfg


def _serving_snapshot() -> dict[str, Any]:
    path = Path.home() / ".z0int" / "config" / "serving.json"
    if not path.is_file():
        return {"status": "missing", "path": str(path)}
    raw = path.read_text(encoding="utf-8")
    return {
        "status": "ok",
        "path": str(path),
        "sha256": hashlib.sha256(raw.encode()).hexdigest(),
        "document": json.loads(raw),
    }


def _runtime_snapshot() -> dict[str, Any]:
    out: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
    }
    if DEFAULT_LLAMA_SERVER.is_file():
        try:
            proc = subprocess.run(
                [str(DEFAULT_LLAMA_SERVER), "--version"], capture_output=True, text=True, timeout=20
            )
            for line in (proc.stdout + proc.stderr).splitlines():
                if line.strip().startswith("version:"):
                    out["llama_cpp"] = line.split(":", 1)[1].strip().split()[0]
                    break
        except (OSError, subprocess.SubprocessError):
            pass
        out["llama_server_sha256"] = _sha256_file(DEFAULT_LLAMA_SERVER)
    return out


def copy_raw_sources(dest: Path) -> list[dict[str, Any]]:
    raw_dir = dest / "sources"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    for src in RAW_SOURCES:
        origin = WORK / src["path"]
        record: dict[str, Any] = {"kind": src["kind"], "origin": str(origin)}
        if not origin.is_file():
            record["status"] = "missing"
            out.append(record)
            continue
        target = raw_dir / src["path"].replace("/", "__")
        shutil.copy2(origin, target)
        record.update(
            {
                "status": "copied",
                "frozen_path": str(target.relative_to(dest)),
                "bytes": target.stat().st_size,
                "sha256": _sha256_file(target),
                "rows": _count_jsonl(target),
            }
        )
        out.append(record)
    return out


def discover_runner_files() -> list[dict[str, Any]]:
    """Hash the harnesses whose behaviour the numbers depend on."""
    out: list[dict[str, Any]] = []
    for pattern, label in (
        ("scripts/*.py", "script"),
        ("scripts/*.sh", "script"),
    ):
        for path in sorted(REPO.glob(pattern)):
            out.append(
                {
                    "label": label,
                    "path": str(path.relative_to(REPO)),
                    "sha256": _sha256_file(path),
                    "bytes": path.stat().st_size,
                }
            )
    for path in sorted((REPO / "src" / "z0int" / "cognition").rglob("*.py")):
        out.append(
            {
                "label": "cognition-module",
                "path": str(path.relative_to(REPO)),
                "sha256": _sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return out


def build_snapshot(*, run_id: str, gguf_cache: dict[str, str] | None = None) -> dict[str, Any]:
    gguf_cache = dict(gguf_cache or {})
    return {
        "schema": SCHEMA,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "created_at_unix": time.time(),
        "machine": {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "cwd": os.getcwd(),
            "gpu": "NVIDIA GeForce RTX 3080 Ti 12GB",
        },
        "runtime": _runtime_snapshot(),
        "repos": [repo_state(entry) for entry in PARTICIPATING_REPOS],
        "models": model_records(gguf_hashes=gguf_cache, gguf_dir=GGUF_ROOT),
        "fixtures": fixture_records(),
        "frozen_config": _extract_frozen_config(),
        "serving": _serving_snapshot(),
        "harness_files": discover_runner_files(),
    }


def write_sha256sums(root: Path) -> Path:
    lines: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "SHA256SUMS":
            continue
        lines.append(f"{_sha256_file(path)}  {path.relative_to(root)}")
    target = root / "SHA256SUMS"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-id", default=None, help="immutable run id (default: p1b-<utc stamp>)")
    ap.add_argument("--out", default=None, help="run directory (default: results/phase1b/<run-id>)")
    ap.add_argument(
        "--skip-gguf-hash",
        action="store_true",
        help="reuse hashes from an existing freeze.json instead of re-reading ~18 GB",
    )
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(list(argv) if argv is not None else None)

    run_id = args.run_id or ("p1b-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    dest = Path(args.out) if args.out else (RESULTS_ROOT / run_id)
    if dest.exists() and (dest / "baseline" / "freeze.json").exists() and not args.skip_gguf_hash:
        print(f"refusing to overwrite an existing frozen run: {dest}", file=sys.stderr)
        return 2

    gguf_cache: dict[str, str] = {}
    if args.skip_gguf_hash:
        for candidate in sorted(RESULTS_ROOT.glob("*/baseline/freeze.json")):
            try:
                prior = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for model in prior.get("models", []):
                gguf = model.get("gguf") or {}
                if gguf.get("path") and gguf.get("sha256"):
                    gguf_cache[str(gguf["path"])] = str(gguf["sha256"])
        print(f"# reused {len(gguf_cache)} gguf hashes", file=sys.stderr)

    baseline = dest / "baseline"
    baseline.mkdir(parents=True, exist_ok=True)

    print("# hashing models, fixtures and harness files ...", file=sys.stderr)
    snapshot = build_snapshot(run_id=run_id, gguf_cache=gguf_cache)
    snapshot["raw_sources"] = copy_raw_sources(dest)
    (baseline / "freeze.json").write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    sums = write_sha256sums(dest)
    print(f"# wrote {baseline / 'freeze.json'}", file=sys.stderr)
    print(f"# wrote {sums}", file=sys.stderr)

    if args.json:
        print(json.dumps({"run_id": run_id, "out": str(dest),
                          "models": len(snapshot["models"]),
                          "fixtures": len(snapshot["fixtures"]),
                          "raw_sources": len(snapshot["raw_sources"])}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
