#!/usr/bin/env python3
"""For every ruff F401 finding, check for re-export usage: some other file
importing the importing-module's own name (`from mod import name` / `mod.name`)."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

REPO = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
RLINE = re.compile(r"^(?P<file>[^:]+):(?P<line>\d+):(?P<col>\d+): F401 \[\*\] `(?P<mod>[^`]+)` imported but unused")

r = subprocess.run(["uvx", "ruff", "check", "--select", "F401", "--output-format", "concise",
                    "src", "tests", "scripts", "adapters", "benchmarks", "harness-adapters",
                    "omp-extensions", "deploy", "examples", "operations", "webgpu-demo"],
                   cwd=REPO, capture_output=True, text=True)
findings = [RLINE.match(l).groupdict() for l in r.stdout.splitlines() if RLINE.match(l)]

files = [f for f in subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True,
                                   text=True, check=True).stdout.split("\n") if f]
contents = {}
for f in files:
    if f.startswith((".test-state/", ".deadcode/")) or f.endswith(".pyc"):
        continue
    try:
        contents[f] = open(os.path.join(REPO, f), encoding="utf-8", errors="replace").read()
    except OSError:
        pass


def module_name(path: str) -> str:
    rel = path[len("src/"):] if path.startswith("src/") else path
    rel = rel[: -len(".py")]
    parts = rel.split("/")
    if parts[-1] in ("__init__", "__main__"):
        parts = parts[:-1]
    return ".".join(parts)


out = []
for f in findings:
    path, name = f["file"], f["mod"]
    # the *name* imported (last dotted segment of the imported module/symbol)
    sym = name.rsplit(".", 1)[-1].split(" as ")[-1].strip()
    mod = module_name(path)
    importers = []
    if sym and sym != "*":
        rx_from = re.compile(r"\bfrom\s+" + re.escape(mod) + r"\s+import\s+[^#\n]*\b" + re.escape(sym) + r"\b")
        rx_attr = re.compile(r"\b" + re.escape(mod) + r"\.\s*" + re.escape(sym) + r"\b")
        for cf, t in contents.items():
            if cf == path:
                continue
            if rx_from.search(t) or rx_attr.search(t):
                importers.append(cf)
    out.append({**f, "sym": sym, "reexport_importers": importers})

risky = [f for f in out if f["reexport_importers"]]
json.dump(out, open(os.path.join(REPO, ".deadcode", "f401.json"), "w"), indent=1)
print(f"F401 findings={len(out)} with re-export users={len(risky)}")
for f in risky:
    print(f"  {f['file']}:{f['line']} `{f['mod']}` -> used by {f['reexport_importers']}")
