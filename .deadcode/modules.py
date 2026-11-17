#!/usr/bin/env python3
"""Resolve every import in the repo to an absolute module path, then report
src/ modules nothing imports (plus any string/path mention)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import defaultdict

REPO = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
PKGS = ("z0int", "openjev_phase1")

FROM_RE = re.compile(r"^\s*from\s+([.\w]+)\s+import\s+(.+)$")
IMPORT_RE = re.compile(r"^\s*import\s+([.\w]+(?:\s*,\s*[.\w]+)*)")
STR_RE = re.compile(r"""['"]([^'"\n]*\b(?:z0int|openjev_phase1)[.\w/][^'"\n]*)['"]""")


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True,
                         text=True, check=True).stdout
    return [p for p in out.split("\0") if p]


def mod(path: str) -> str | None:
    if not path.endswith(".py"):
        return None
    rel = path[len("src/"):] if path.startswith("src/") else path
    rel = rel[: -len(".py")]
    parts = rel.split("/")
    if parts[-1] in ("__init__", "__main__"):
        parts = parts[:-1]
    return ".".join(parts) or None


def package_of(path: str) -> str:
    """The package a file lives in (parent of the file's own module)."""
    rel = path[len("src/"):] if path.startswith("src/") else path
    rel = rel[: -len(".py")]
    parts = rel.split("/")
    if parts[-1] != "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def resolve(imp: str, importer: str) -> str:
    if not imp.startswith("."):
        return imp
    base = package_of(importer)
    depth = len(imp) - len(imp.lstrip("."))
    rest = imp[depth:]
    parts = base.split(".") if base else []
    drop = depth - 1
    anchor = ".".join(parts[: len(parts) - drop]) if drop else base
    return f"{anchor}.{rest}" if anchor else rest


def imported_names(text: str, importer: str) -> set[str]:
    out: set[str] = set()
    for line in text.splitlines():
        m = FROM_RE.match(line)
        if m:
            src, names = m.group(1), m.group(2)
            abs_src = resolve(src, importer)
            out.add(abs_src)
            for n in re.split(r"[,\s]+", names.strip().strip("()")):
                n = n.strip().split(" as ")[0].split(" as")[0]
                if n and n != "*":
                    out.add(f"{abs_src}.{n}" if abs_src else n)
            continue
        m = IMPORT_RE.match(line)
        if m:
            for part in m.group(1).split(","):
                name = part.strip().split(" as ")[0]
                if name:
                    out.add(resolve(name, importer))
    return out


def main() -> None:
    files = tracked()
    contents: dict[str, str] = {}
    for f in files:
        if f.startswith((".test-state/", ".deadcode/")) or f.startswith(".git"):
            continue
        try:
            contents[f] = open(os.path.join(REPO, f), encoding="utf-8", errors="replace").read()
        except OSError:
            contents[f] = ""
    pyfiles = [f for f in files if f.endswith(".py") and not f.startswith((".test-state/", ".deadcode/"))]

    pyfiles = [f for f in pyfiles if f in contents]

    imported: dict[str, set[str]] = defaultdict(set)
    for f in pyfiles:
        for name in imported_names(contents[f], f):
            imported[name].add(f)

    modules = {m: f for f in pyfiles if (m := mod(f)) and f.startswith("src/")}
    # also count non-py references (mjs/ts/json/yaml/sh/md/toml)
    nonpy = [f for f in files if not f.endswith(".py") and not f.startswith((".test-state/", ".deadcode/"))]
    # normalize pyfile set to those we could read
    pyfiles = [f for f in pyfiles if f in contents]

    def mentions(m: str, path: str, stem: str) -> list[str]:
        hits = set()
        for cf, ctext in contents.items():
            if cf == path:
                continue
            if (m and (re.search(r"\b" + re.escape(m) + r"\b", ctext)
                       or re.search(r"\b" + re.escape(m.replace(".", "/")) + r"\.py", ctext))):
                hits.add(cf)
        return sorted(hits)

    results = []
    for m, path in sorted(modules.items()):
        stem = path.rsplit("/", 1)[-1][: -len(".py")]
        # direct importers resolved to this exact module
        importers = set(imported.get(m, set()))
        importers = {i for i in importers if i != path}
        # a module is live if any *ancestor* is imported into it by name, or it is
        # named by a `from z0int import x` / `from . import x` form (covered above)
        nonpy_hits = []
        for cf in nonpy:
            t = contents.get(cf, "")
            if (m in t) or (path in t) or re.search(r"\b" + re.escape(stem) + r"\.py\b", t):
                nonpy_hits.append(cf)
        results.append({"module": m, "file": path, "importers": sorted(importers),
                        "nonpy": nonpy_hits, "dead": not importers and not nonpy_hits})

    dead = [r for r in results if r["dead"]]
    payload = {"total": len(results), "dead": dead, "all": results}
    with open(os.path.join(REPO, ".deadcode", "modules.json"), "w") as fh:
        json.dump(payload, fh, indent=1)
    print(f"modules={len(results)} dead={len(dead)}")
    for r in dead:
        print("  DEAD", r["file"])


if __name__ == "__main__":
    main()
