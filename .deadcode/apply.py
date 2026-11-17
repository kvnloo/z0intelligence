#!/usr/bin/env python3
"""Delete the verified dead functions/constants, then re-verify with vulture."""
from __future__ import annotations

import re
import sys

REPO = "/mnt/zer0models/z0-wt/deadcode-z0intelligence"

# (file, name, optional [end line inclusive]) — delete the whole top-level / method block
BLOCKS = [
    ("src/z0int/ao_report.py", "_latest_ao_receipts"),
    ("src/z0int/aodl.py", "spend_from_decision"),
    ("src/z0int/autoresearch/retrieval_exec.py", "backend_health"),
    ("src/z0int/autoresearch/retrieval_exec.py", "recipe_schema_ok"),
    ("src/z0int/backends/bench/metrics.py", "_pred_str"),
    ("src/z0int/bridge/runtime.py", "clear_session_open"),
    ("src/z0int/capabilities/context_project_state.py", "evaluate_and_store"),
    ("src/z0int/capabilities/context_project_state.py", "load_families_jsonl"),
    ("src/z0int/contrastive_evidence.py", "family_fingerprint"),
    ("src/z0int/memory/sources.py", "is_pi_session"),
    ("src/z0int/cognition/candidates.py", "allowed_for"),
    ("src/z0int/claude_code_tokenomics.py", "events_for_stop"),
    # claude_code_engagement: the whole unwired prompt-gated packet block (230..324)
    ("src/z0int/claude_code_engagement.py", "__BLOCK__", 230, 324),
    # receipt.py: orphan method after an unconditional return (233..237)
    ("src/z0int/receipt.py", "__BLOCK__", 233, 237),
]

for path, name, *rest in BLOCKS:
    full = f"{REPO}/{path}"
    lines = open(full).read().splitlines(keepends=True)
    if rest:
        lo, hi = rest
        start, end = lo - 1, hi  # 0-indexed slice bounds
    else:
        found = None
        for i, l in enumerate(lines):
            m = re.match(r"^(\s*)def\s+" + re.escape(name) + r"\b", l)
            if m:
                found = (i, len(m.group(1)))
                break
        assert found, (path, name)
        i, ind = found
        j = i + 1
        while j < len(lines):
            l2 = lines[j]
            if l2.strip() == "":
                j += 1
                continue
            ind2 = len(l2) - len(l2.lstrip())
            if ind2 <= ind:
                break
            j += 1
        start, end = i, j
    removed = lines[start:end]
    assert removed, (path, name)
    assert name == "__BLOCK__" or any(re.match(r"^\s*def\s+" + re.escape(name) + r"\b", r) for r in removed), (path, name, removed[:3])
    rest_lines = lines[:start] + lines[end:]
    # collapse a run of >2 blank lines at the seam
    text = "".join(rest_lines)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    open(full, "w").write(text)
    print(f"{path}: removed {len(removed)} lines for {name}")

# redundant always-true guards (vulture 100%: redundant if-condition)
FIXES = [
    ("src/z0int/cli.py",
     "    if args.json or True:\n        print(json.dumps(payload, indent=2, ensure_ascii=False))\n",
     "    print(json.dumps(payload, indent=2, ensure_ascii=False))\n"),
    ("src/z0int/cli.py",
     "    def emit(obj: dict) -> None:\n        if getattr(args, \"json\", False) or True:\n            print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))\n",
     "    def emit(obj: dict) -> None:\n        print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))\n"),
    ("src/z0int/autoresearch/mutations.py",
     '    # exact path only\n    if "exact_path" in base_ops or True:\n        out.append(\n',
     '    # exact path only\n    out.append(\n'),
]
for path, old, new in FIXES:
    full = f"{REPO}/{path}"
    text = open(full).read()
    assert text.count(old) == 1, (path, text.count(old))
    open(full, "w").write(text.replace(old, new))
    print(f"{path}: simplified always-true guard")

# dead local: arms_spec is built and never read (src/z0int/autoresearch/replay.py)
full = f"{REPO}/src/z0int/autoresearch/replay.py"
text = open(full).read()
old = """    arms_spec = [
        ("A0", champ),
        ("B0", None),  # filled with first challenger
        ("A1", champ),
        ("B1", None),
    ]
    challengers = challenger_policies(champ)"""
new = """    challengers = challenger_policies(champ)"""
assert text.count(old) == 1
open(full, "w").write(text.replace(old, new))
print("src/z0int/autoresearch/replay.py: removed dead local arms_spec")
