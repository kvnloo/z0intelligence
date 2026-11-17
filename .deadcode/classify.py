#!/usr/bin/env python3
"""Classify zero-ref vulture findings: real dead functions vs dataclass/pydantic fields."""
from __future__ import annotations

import json
import os
import re
import sys

REPO = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
data = json.load(open(os.path.join(REPO, ".deadcode", "vulture-probe.json")))

FUNC = re.compile(r"^\s*(async\s+)?def\s+([A-Za-z_]\w*)")
VAR = re.compile(r"^\s*([A-Za-z_]\w*)\s*[:=]")
CLASS = re.compile(r"^\s*class\s+([A-Za-z_]\w*)")
DECOR = re.compile(r"^\s*@")

src_cache: dict[str, list[str]] = {}


def lines_of(path: str) -> list[str]:
    if path not in src_cache:
        src_cache[path] = open(os.path.join(REPO, path), encoding="utf-8").read().splitlines()
    return src_cache[path]


def classify(f: dict) -> str:
    path, li = f["file"], int(f["line"]) - 1
    src = lines_of(path)
    text = src[li].rstrip()
    # find the class we are inside, and whether it is a dataclass / pydantic model
    kind = None
    if f["kind"] in ("unused function", "unused method"):
        kind = "func"
    elif f["kind"] in ("unused variable", "unused attribute"):
        kind = "var"
    else:
        kind = "other"

    # walk backwards for decorators immediately above
    above = "\n".join(src[max(0, li - 8):li])

    # enclosing class header (nearest 'class X' above at lower indent)
    indent = len(text) - len(text.lstrip())
    enclosing = None
    for j in range(li - 1, -1, -1):
        l = src[j]
        if not l.strip() or l.lstrip().startswith("#"):
            continue
        ind = len(l) - len(l.lstrip())
        if ind < indent and CLASS.match(l):
            enclosing = (j, l)
            break
        if ind < indent:
            continue
    fieldish = False
    if enclosing is not None:
        cj, cl = enclosing
        # look at the decorator block just above the class
        k = cj - 1
        deco = []
        while k >= 0 and (not src[k].strip() or DECOR.match(src[k])):
            deco.append(src[k].strip())
            k -= 1
        deco_t = "\n".join(deco)
        base = cl
        if any(d.startswith("@dataclass") for d in deco) or "BaseModel" in base \
           or "TypedDict" in base or "NamedTuple" in base or "pydantic" in base:
            fieldish = True
    if "dataclass" in above or "@dataclass" in above:
        fieldish = True
    # pydantic Field(...) / dataclasses.field(...)
    if "field(" in text or "Field(" in text:
        fieldish = True
    if f["kind"] == "unused function" and (FUNC.match(text) and not enclosing):
        return "dead-func"
    if kind == "func":
        return "dead-func" if not enclosing else ("fieldish-func" if fieldish else "dead-method")
    if kind == "var":
        if enclosing is not None:
            return "field" if fieldish else "dead-attr"
        return "dead-var" if VAR.match(text) else "field"
    return "other"


for f in data:
    f["class"] = classify(f)

groups: dict[str, list] = {}
for f in data:
    if f["total"] == 0:
        groups.setdefault(f["class"], []).append(f)

print({k: len(v) for k, v in sorted(groups.items())})
for k in ("dead-func", "dead-method", "dead-var", "dead-attr"):
    if k not in groups:
        continue
    print(f"\n===== {k} ({len(groups[k])}) =====")
    for f in groups[k]:
        print(f"  {f['file']}:{f['line']}  {f['name']}")

json.dump(data, open(os.path.join(REPO, ".deadcode", "vulture-classified.json"), "w"), indent=1)
