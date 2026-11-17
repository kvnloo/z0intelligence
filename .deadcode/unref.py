#!/usr/bin/env python3
"""Find unreferenced Python modules: one ripgrep pass per module, in parallel."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
OUT = sys.argv[2] if len(sys.argv) > 2 else "unreached.json"


def tracked() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True,
                         text=True, check=True).stdout
    return [p for p in out.split("\0") if p]


def module_name(path: str) -> str | None:
    if not path.endswith(".py"):
        return None
    rel = path[len("src/"):] if path.startswith("src/") else path
    rel = rel[: -len(".py")]
    parts = rel.split("/")
    if parts[-1] in ("__init__", "__main__"):
        parts = parts[:-1]
    if not parts:
        return None
    return ".".join(parts)


def rg(patterns: list[str], exclude: str) -> list[str]:
    cmd = ["rg", "-l", "--no-ignore", "--hidden"]
    for p in patterns:
        cmd += ["-e", p]
    cmd += ["-g", f"!{exclude}", "-g", "!.git/", "-g", "!.deadcode/", "."]
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    if r.returncode not in (0, 1):
        raise RuntimeError(r.stderr)
    out = r.stdout.split("\n")
    files = [x for x in out if x]
    return [os.path.relpath(x, REPO) for x in files]


def probe(mod: str, path: str) -> dict:
    base = os.path.basename(path)
    stem = os.path.splitext(base)[0]
    pats = [
        rf"\bimport\s+{re_esc(mod)}\b",
        rf"\bfrom\s+{re_esc(mod)}\b",
        rf"\b{re_esc(mod)}\.[A-Za-z_]",
        rf"['\"]{re_esc(mod)}['\"]",
        rf"/{re_esc(path)}\b",
    ]
    try:
        hits = rg(pats, path)
    except RuntimeError:
        hits = []
    # filename-only mentions elsewhere (weak)
    weak = []
    try:
        weak = rg([rf"\b{re_esc(stem)}\b"], path)
        weak = [w for w in weak if w not in hits and w != path]
    except RuntimeError:
        pass
    return {"module": mod, "file": path, "refs": hits, "weak": weak}


def re_esc(s: str) -> str:
    return s.replace(".", r"\.")


def main() -> None:
    files = tracked()
    mods = {}
    for f in files:
        m = module_name(f)
        if m and f.startswith("src/"):
            mods[m] = f
    items = sorted(mods.items())
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(lambda kv: probe(*kv), items))
    unreached = [r for r in results if not r["refs"]]
    payload = {
        "total": len(results),
        "unreached": unreached,
        "weak_only": [r for r in unreached if r["weak"]],
        "all": results,
    }
    with open(os.path.join(REPO, OUT), "w") as fh:
        json.dump(payload, fh, indent=1)
    print(f"modules={len(results)} unreached={len(unreached)} weak_only={len(payload['weak_only'])}")


if __name__ == "__main__":
    main()
