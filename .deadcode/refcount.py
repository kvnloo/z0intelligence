#!/usr/bin/env python3
"""Count external references for every Python module in the repo.

A module is "unreferenced" when nothing outside its own file names it:
imports, dotted-string dispatch, CLI paths, docs-free grep over all tracked files.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import defaultdict

REPO = sys.argv[1] if len(sys.argv) > 1 else "."
ROOTS = ["src", "tests", "benchmarks", "scripts", "adapters", "harness-adapters",
         "omp-extensions", "deploy", "examples", "operations", "demo", "results",
         "webgpu-demo", "manifests", "skills", "bin", "contracts", "third_party"]


def tracked(root: str) -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z", root], cwd=REPO,
                         capture_output=True, text=True, check=True).stdout
    return [p for p in out.split("\0") if p]


def module_name(path: str) -> str | None:
    if not path.endswith(".py"):
        return None
    rel = path
    if rel.startswith("src/"):
        rel = rel[len("src/"):]
    rel = rel[: -len(".py")]
    parts = rel.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    if not parts or parts[-1] == "__main__":
        return None
    return ".".join(parts)


def main() -> None:
    files: list[str] = []
    for r in ROOTS:
        files += tracked(r)
    files += tracked(".")
    files = sorted(set(files))
    pyfiles = [f for f in files if f.endswith(".py")]
    mods: dict[str, str] = {}
    for f in pyfiles:
        m = module_name(f)
        if m:
            mods[m] = f

    # Build a big haystack per file, keyed by module, once.
    contents: dict[str, str] = {}
    for f in files:
        if os.path.islink(os.path.join(REPO, f)):
            continue
        try:
            contents[f] = open(os.path.join(REPO, f), encoding="utf-8", errors="replace").read()
        except OSError:
            contents[f] = ""
    byfile_owner: dict[str, str] = {}
    for m, f in mods.items():
        byfile_owner[f] = m

    # referenced_from[m] = set of module names whose file mentions m
    refs: dict[str, set[str]] = defaultdict(set)
    for m, f in mods.items():
        # patterns that mention module m from another file
        dotted = re.escape(m)
        last = m.rsplit(".", 1)[-1]
        pats = [
            re.compile(r"\bimport\s+" + dotted + r"\b"),
            re.compile(r"\bfrom\s+" + dotted + r"\b"),
            re.compile(r"\b" + dotted + r"\.[A-Za-z_]"),
            re.compile(r"[\"']" + dotted + r"[\"']"),
            re.compile(r"/" + re.escape(f) + r"\b"),
            re.compile(r"\b" + re.escape(os.path.basename(f)) + r"\b"),
        ]
        for cf, text in contents.items():
            if cf == f:
                continue
            for p in pats:
                if p.search(text):
                    refs[m].add(cf)
                    break

    # every tracked file (incl. non-py) mentioning the module
    def all_mentions(m: str, f: str) -> list[str]:
        dotted = re.escape(m)
        last = m.rsplit(".", 1)[-1]
        pats = [
            re.compile(r"\bimport\s+" + dotted + r"\b"),
            re.compile(r"\bfrom\s+" + dotted + r"\b"),
            re.compile(r"\b" + dotted + r"\.[A-Za-z_]"),
            re.compile(r"[\"']" + dotted + r"[\"']"),
            re.compile(r"/" + re.escape(f) + r"\b"),
        ]
        hits = []
        for cf, text in contents.items():
            if cf == f:
                continue
            for p in pats:
                if p.search(text):
                    hits.append(cf)
                    break
        return hits

    unreached = []
    for m, f in sorted(mods.items()):
        hits = all_mentions(m, f)
        base = re.escape(os.path.basename(f))
        weak = [h for h in hits if re.search(base, open(os.path.join(REPO, h), encoding="utf-8", errors="replace").read())]
        if not hits:
            unreached.append({"module": m, "file": f, "refs": []})

    print(json.dumps({"unreached": unreached,
                      "weak_only": [u for u in unreached]}, indent=2))


if __name__ == "__main__":
    main()
