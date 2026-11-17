#!/usr/bin/env python3
"""For every vulture finding in src/, count references to the name elsewhere in the repo."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
VULTURE = sys.argv[2]

VLINE = re.compile(r"^(?P<file>[^:]+):(?P<line>\d+): (?P<kind>unused (?:function|method|class|variable|attribute|property|import)) '(?P<name>[^']+)' \((?P<conf>\d+)% confidence\)")

files = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True,
                       check=True).stdout.split("\n")
contents = {}
for f in files:
    if not f or f.startswith((".test-state/", ".deadcode/")) or f.endswith(".pyc"):
        continue
    try:
        contents[f] = open(os.path.join(REPO, f), encoding="utf-8", errors="replace").read()
    except OSError:
        pass

findings = []
for line in open(os.path.join(REPO, VULTURE)):
    m = VLINE.match(line.strip())
    if not m:
        continue
    d = m.groupdict()
    if not d["file"].startswith("src/"):
        continue
    findings.append(d)


def probe(f: dict) -> dict:
    name = f["name"]
    path = f["file"]
    src = contents.get(path, "")
    lines = src.splitlines()
    li = int(f["line"]) - 1
    # skip decorated fixture / property / placeholder markers
    context = "\n".join(lines[max(0, li - 6):li + 2])
    guarded = any(k in context for k in ("@pytest.fixture", "@property", "@fixture",
                                         "@staticmethod", "@classmethod", "@dataclass",
                                         "__all__", "classmethod", "@cached_property"))
    pats = {
        "attr": re.compile(r"\.\s*" + re.escape(name) + r"\b"),
        "dotted": re.compile(r"\b" + re.escape(path.replace("/", ".").replace(".py", "").replace("src.", "").replace("src/", "")) + r"\.\s*" + re.escape(name) + r"\b"),
        "str": re.compile(r"['\"]" + re.escape(name) + r"['\"]"),
        "call": re.compile(r"\b" + re.escape(name) + r"\s*\("),
    }
    hits = {}
    for k, p in pats.items():
        found = [cf for cf, t in contents.items() if cf != path and p.search(t)]
        hits[k] = found
    total = len(set(hits["attr"]) | set(hits["dotted"]) | set(hits["str"]) | set(hits["call"]))
    return {**f, "guarded": guarded, "hits": {k: v[:12] for k, v in hits.items()}, "total": total}


with ThreadPoolExecutor(max_workers=8) as ex:
    out = list(ex.map(probe, findings))

json.dump(out, open(os.path.join(REPO, ".deadcode", "vulture-probe.json"), "w"), indent=1)
dead = [f for f in out if f["total"] == 0]
print(f"findings={len(out)} zero-ref={len(dead)} guarded={sum(1 for f in out if f['guarded'])}")
for f in sorted(dead, key=lambda x: x["file"]):
    print(f"  {f['conf']}% {f['kind']:22s} {f['file']}:{f['line']} {f['name']}"
          + ("  [guarded]" if f["guarded"] else ""))
