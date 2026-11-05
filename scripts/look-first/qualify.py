#!/usr/bin/env python3
"""Produce bounded, read-only inventory evidence for an existing registry.

No network, credentials, model inference, installation, or routing promotion.
Executable discovery and package metadata do not establish integration quality.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def registry_receipt(registry: Path) -> dict:
    data = json.loads(registry.read_text(encoding="utf-8"))
    if data.get("schema") != "z0int.capability_registry.v1":
        raise ValueError("Expected the existing z0int.capability_registry.v1 registry")
    root = registry.resolve().parent.parent
    entries = []
    for number, entry in enumerate(data["entries"]):
        source = entry.get("eval_source")
        integrity = "no_evidence"
        observed = None
        if source:
            evidence = (root / source["path"]).resolve()
            if not evidence.is_relative_to(root):
                raise ValueError("Evidence path escapes the registry repository")
            if evidence.is_file():
                observed = sha256(evidence)
                integrity = "matching" if observed == source["sha256"] else "mismatch"
            else:
                integrity = "missing"
        entries.append({
            "registry_index": number,
            "provider": entry["provider"], "model": entry["model"],
            "function": entry["function"], "route": entry["route"],
            "registry_eligible": entry["eligible"],
            "declared_availability": entry["availability"],
            "eval_source": source, "observed_sha256": observed,
            "evidence_integrity": integrity,
            "live_integration": "not_tested",
            "decision": "retain_existing_admission_and_quality_gates",
        })
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "--", str(registry.resolve())], capture_output=True, text=True, check=True, timeout=10).stdout
    return {"path": str(registry.resolve()), "sha256": sha256(registry), "head": head,
            "registry_file_dirty": bool(status), "entries": entries,
            "integrity_counts": dict(Counter(e["evidence_integrity"] for e in entries))}


def host_receipt() -> dict:
    tools = {name: shutil.which(name) for name in (
        "git", "python3", "uv", "uvx", "ctx", "fleet-build", "bun", "ollama", "z0int", "serena", "serena-mcp-server")}
    libraries = {}
    for name in ("serena", "serena-agent", "mcp", "z0int", "torch", "transformers", "pytest"):
        try:
            libraries[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            libraries[name] = None
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE VIRTUAL TABLE smoke USING fts5(text)")
        db.execute("INSERT INTO smoke VALUES ('capability qualification')")
        fts5 = db.execute("SELECT text FROM smoke WHERE smoke MATCH 'qualification'").fetchone()[0]
    return {"executables": tools, "python_distributions": libraries, "sqlite_fts5_smoke": fts5,
            "scope": "Current PATH and current Python environment only; no exhaustive absence claim",
            "serena": "not_qualified; no download, MCP handshake, or task comparison attempted",
            "apis": "not_live_tested; no credentials read and no requests sent",
            "models": "registry_metadata_only; no inference or weights inspection"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    args = parser.parse_args()
    preflight = json.loads(args.preflight.read_text(encoding="utf-8"))
    result = {"schema": "look-first.qualification-receipt.v1", "complete": False,
              "claim": "Bounded evidence receipt, not a replacement catalog or live qualification",
              "preflight": {"path": str(args.preflight.resolve()), "sha256": sha256(args.preflight),
                            "source": preflight["source"], "errors": preflight["errors"], "skills": preflight["skills"]},
              "host": host_receipt(), "registry": registry_receipt(args.registry),
              "installation_applied": False, "routing_promoted": False}
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
