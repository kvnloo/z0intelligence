"""The evidence ingest, compilation and explanation commands."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess

from .. import paths
from .changelog import markdown_diff, policy_diff
from .compile import CompilerConfig, compile_policy, validate_policy, write_policy
from .findings import FindingsError, canonical, ledger_path, read_findings
from .ingest import discover_sources, ingest_sources


def _load(directory: Path, version: str | None = None) -> dict:
    if version is not None and (len(version) != 64 or any(c not in "0123456789abcdef" for c in version)):
        raise ValueError("Policy version must be a lowercase SHA-256")
    path = directory / "history" / (version + ".json") if version else directory / "decision-policy.json"
    policy = json.loads(path.read_text(encoding="utf-8"))
    validate_policy(policy)
    return policy


def _revision() -> str:
    release_root = Path(__file__).resolve().parents[3]
    stamp = release_root / "DEPLOYED_SHA"
    if stamp.exists():
        revision = stamp.read_text().strip()
        if len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision):
            raise ValueError("Invalid immutable release revision")
        return revision
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[3],
                                       stderr=subprocess.DEVNULL, timeout=3, text=True).strip()
    except (OSError, subprocess.SubprocessError):
        raise ValueError("Cannot determine compiler revision; set compiler_revision in --config") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="z0int policy")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("ingest", "compile", "show", "explain", "changelog"):
        command = sub.add_parser(name)
        command.add_argument("--json", action="store_true")
        if name == "ingest":
            command.add_argument("--sources", type=Path, help="JSON source specs with kind/path and optional arm/ts metadata")
            command.add_argument("--operations-root", type=Path, default=os.environ.get("Z0INT_OPERATIONS_ROOT"))
            command.add_argument("--z0evals-root", type=Path, default=os.environ.get("Z0INT_Z0EVALS_ROOT"))
        elif name == "compile":
            command.add_argument("--now", help="Explicit timezone-aware ISO timestamp")
            command.add_argument("--config", type=Path, help="CompilerConfig JSON overrides")
        elif name == "explain":
            command.add_argument("family")
        elif name == "changelog":
            command.add_argument("--from", dest="from_version")
            command.add_argument("--to", dest="to_version")
    args = parser.parse_args(argv)
    home = paths.home()
    directory = home / "policy"
    try:
        if args.command == "ingest":
            sources = json.loads(args.sources.read_text(encoding="utf-8")) if args.sources else discover_sources(
                home, Path(args.operations_root) if args.operations_root else None,
                Path(args.z0evals_root) if args.z0evals_root else None)
            result = ingest_sources(sources, home)
        elif args.command == "compile":
            findings = read_findings(ledger_path(home))
            output_home = Path(os.environ.get("Z0INT_POLICY_OUTPUT_HOME", str(home))).expanduser().resolve()
            output_directory = output_home / "policy"
            previous = _load(output_directory) if (output_directory / "decision-policy.json").exists() else None
            config = json.loads(args.config.read_text(encoding="utf-8")) if args.config else {}
            if "compiler_revision" not in config:
                config["compiler_revision"] = _revision()
            now = args.now or datetime.now(timezone.utc)
            result = compile_policy(findings, previous, now, CompilerConfig(**config))
            write_policy(result, output_home)
        elif args.command == "show":
            result = _load(directory)
        elif args.command == "explain":
            policy = _load(directory)
            findings = read_findings(ledger_path(home))
            rules = {key: row for key, row in policy["families"].items()
                     if row.get("family", key) == args.family}
            ids = {finding_id for row in rules.values() for finding_id in row["evidence"]}
            result = {"policy_version": policy["version"], "family": args.family,
                      "rules": rules, "findings": [row for row in findings if row["id"] in ids]}
        else:
            after = _load(directory, args.to_version)
            before = _load(directory, args.from_version) if args.from_version else None
            if before is None:
                candidates = []
                for path in sorted((directory / "history").glob("*.json")):
                    policy = _load(directory, path.stem)
                    if policy["compiled_at"] < after["compiled_at"]:
                        candidates.append(policy)
                if candidates:
                    before = max(candidates, key=lambda p: (p["compiled_at"], p["version"]))
            findings = read_findings(ledger_path(home))
            if not args.json:
                print(markdown_diff(before, after, findings), end="")
                return 0
            result = policy_diff(before, after, findings)
        print(canonical(result).decode("utf-8") if args.json else json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (FindingsError, OSError, ValueError, TypeError, KeyError) as exc:
        result = {"ok": False, "stage": "none", "reason": str(exc)}
        print(canonical(result).decode("utf-8") if args.json else f"Policy unavailable: {exc}")
        return 1
