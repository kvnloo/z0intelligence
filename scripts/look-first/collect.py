#!/usr/bin/env python3
"""Read GitHub metadata into an offline cache. Never publishes or installs.

CLI: python scripts/look-first/collect.py --repo owner/repo --cache-dir PATH
Default: read existing manifests offline. Add --refresh-remote to collect.
Python 3.10+ and authenticated gh are required for remote collection only.
Each endpoint uses explicit REST pages. An incomplete refresh replaces its
endpoint envelope, so stale data never silently counts as complete.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any

DEFAULT_CACHE = Path.home() / ".local/state/z0int/look-first-cache"
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".look-first-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def run(args: list[str], *, cwd: Path | None = None, timeout: float = 30) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        # gh errors contain no command environment or credentials.
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace").strip() or f"exit {result.returncode}")
    return result.stdout.decode("utf-8", errors="replace")


def fetch_pages(endpoint: str, *, timeout: float = 60) -> dict[str, Any]:
    """A short page proves exhaustion. Failures retain already received pages."""
    items: list[dict[str, Any]] = []
    page = 1
    result: dict[str, Any] = {"endpoint": endpoint, "complete": False, "pages": 0, "items": items, "error": None}
    try:
        while True:
            separator = "&" if "?" in endpoint else "?"
            url = f"{endpoint}{separator}per_page=100&page={page}"
            data = json.loads(run(["gh", "api", "--method", "GET", url], timeout=timeout))
            if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
                raise ValueError("Expected an array of objects")
            items.extend(data)
            result["pages"] = page
            if len(data) < 100:
                result["complete"] = True
                break
            page += 1
    except (OSError, subprocess.TimeoutExpired, RuntimeError, ValueError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def collect_repo(repo: str, cache_dir: Path = DEFAULT_CACHE, *, timeout: float = 60) -> dict[str, Any]:
    if not REPO_RE.fullmatch(repo):
        raise ValueError(f"Invalid GitHub repository: {repo}")
    root = cache_dir / "github" / repo
    endpoints = {
        "issues": f"repos/{repo}/issues?state=all&sort=created&direction=asc",
        "pulls": f"repos/{repo}/pulls?state=all&sort=created&direction=asc",
        "issue-comments": f"repos/{repo}/issues/comments?sort=created&direction=asc",
        "review-comments": f"repos/{repo}/pulls/comments?sort=created&direction=asc",
    }
    manifests = []
    pulls = []
    for name, endpoint in endpoints.items():
        envelope = fetch_pages(endpoint, timeout=timeout)
        dump_json(root / f"{name}.json", envelope)
        manifests.append({"name": name, **{key: envelope[key] for key in ("endpoint", "complete", "pages", "error")}, "count": len(envelope["items"])})
        if name == "pulls":
            pulls = envelope["items"]
    for pull in sorted(pulls, key=lambda item: item["number"]):
        number = int(pull["number"])
        endpoint = f"repos/{repo}/pulls/{number}/reviews"
        envelope = fetch_pages(endpoint, timeout=timeout)
        dump_json(root / "reviews" / f"{number}.json", envelope)
        manifests.append({"name": f"reviews/{number}", **{key: envelope[key] for key in ("endpoint", "complete", "pages", "error")}, "count": len(envelope["items"])})
    manifest = {"schema": "look-first.github.v1", "repo": repo, "complete": all(item["complete"] for item in manifests), "endpoints": manifests}
    dump_json(root / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", action="append", required=True)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--refresh-remote", action="store_true")
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    results = []
    for repo in sorted(set(args.repo)):
        if not REPO_RE.fullmatch(repo):
            parser.error(f"Invalid GitHub repository: {repo}")
        if args.refresh_remote:
            results.append(collect_repo(repo, args.cache_dir, timeout=args.timeout))
        else:
            path = args.cache_dir / "github" / repo / "manifest.json"
            try:
                results.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                results.append({"repo": repo, "complete": False, "error": str(exc)})
    print(json.dumps(results, sort_keys=True))
    return 0 if all(result["complete"] for result in results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
