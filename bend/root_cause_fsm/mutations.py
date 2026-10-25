#!/usr/bin/env python3
"""Scratch-only mutants; parser/setup errors never count as proof rejection."""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
HERE = Path(__file__).resolve().parent
BEND = os.environ.get("BEND", str(Path.home() / ".bend/bin/bend"))
def first_location(out: str) -> str:
    for line in out.splitlines():
        if line.startswith("Location:"):
            return line.split(":", 1)[1].strip()
    lines = out.splitlines()
    return lines[1] if len(lines) > 1 else out


def check(proj: Path, verdict: bool) -> dict:
    env = dict(os.environ)
    # Match ops_fsm: host clang precedes Lean's bundled clang.
    env["PATH"] = "/usr/bin:" + env.get("PATH", "") + ":" + str(Path.home() / ".elan/bin")
    cmd = [BEND, "PROOF.bend"] + (["--verdict"] if verdict else [])
    try:
        p = subprocess.run(cmd, cwd=proj, capture_output=True, text=True, env=env, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"proofs_check": None, "rejected": False, "where": str(exc), "output": str(exc)}
    out = (p.stdout + p.stderr).strip()
    proof_names = {
        line.removeprefix("def ").split("(", 1)[0]
        for line in (proj / "PROOF.bend").read_text().splitlines()
        if line.startswith("def ")
    }
    lines = [line.strip() for line in out.splitlines() if line.strip()]
    target = ""
    for line in lines:
        if line.startswith("Location:"):
            candidate = line.partition(":")[2].strip()
        elif line.startswith("In ") and line.endswith(":"):
            candidate = line[3:-1]
        else:
            continue
        if candidate in proof_names:
            target = candidate
            break
    # The frontend emits a same-line Location identifier for proof failures.
    # Parser failures have only "Location:" followed by a source excerpt.
    # The dependent kernel uses "In <definition>:" instead. Accept neither
    # arbitrary source locations nor a bare failure marker as proof rejection.
    lower = out.lower()
    setup_error = any(text in lower for text in (
        "out of fuel", "parse error", "syntax error", "unknown reference",
        "unbound variable", "no such file", "command not found", "traceback",
        "panic:", "fatal error:", "consumed more than once", "affine live code",
    ))
    passed = ("ALL PROOFS CHECK" in lines and "SOME PROOFS FAIL" not in lines
              and p.returncode == 0 and not setup_error)
    rejected = ("SOME PROOFS FAIL" in lines and "ALL PROOFS CHECK" not in lines
                and p.returncode == 1 and bool(target) and not setup_error)
    return {"proofs_check": True if passed else False if rejected else None,
            "rejected": rejected, "where": "" if passed else target if rejected else first_location(out),
            "failure_kind": "none" if passed else "proof" if rejected else "tool_or_setup",
            "returncode": p.returncode, "output": out}



FIELDS = {'R1': ('target', 'before_metric', 'functional_probe', 'expiry_valid'), 'R2': ('same_metric', 'in_bounds_twice', 'separated_24h'), 'R3': ('probe_failed', 'owned', 'leased'), 'R4': ('same_window', 'share_at_least_30'), 'R7': ('report_only', 'same_finding', 'age_expired')}

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verdict", action="store_true")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    source = (HERE / "root.bend").read_text()
    rows = []
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
        work = Path(td)
        for name in ("root.bend", "LAWS.bend", "PROOF.bend"):
            shutil.copy(HERE / name, work / name)
        baseline = check(work, args.verdict)
        rows.append({"law": "reference", **baseline})
        if baseline["proofs_check"] is True:
            for law, names in FIELDS.items():
                for name in names:
                    for kind in ("affirmed", "known"):
                        start = source.index("def " + law.lower() + "(")
                        end = source.find("\ndef ", start + 1)
                        end = len(source) if end < 0 else end
                        body = source[start:end]
                        old = kind + "(" + name + ")"
                        if body.count(old) != 1:
                            rows.append({"law": law, "mutation": name, "rejected": False, "failure_kind": "mutation_did_not_apply"})
                            continue
                        changed = body.replace(old, "True{}")
                        (work / "root.bend").write_text(source[:start] + changed + source[end:])
                        rows.append({"law": law, "mutation": kind + " bypass " + name, **check(work, args.verdict)})
                start = source.index("def " + law.lower() + "(")
                end = source.find("\ndef ", start + 1)
                end = len(source) if end < 0 else end
                body = source[start:end]
                signature = body.splitlines()[0]
                for verdict in ("SUnknown{}", "SViolation{}"):
                    changed = signature + "\n  " + verdict + "\n"
                    (work / "root.bend").write_text(source[:start] + changed + source[end:])
                    rows.append({"law": law, "mutation": "always " + verdict, **check(work, args.verdict)})
    expected = sum(len(names) * 2 + 2 for names in FIELDS.values())
    receipt = {"reference_passes": baseline["proofs_check"] is True,
               "rejected": sum(row.get("rejected", False) for row in rows[1:]),
               "total": expected, "rows": rows, "reference_restored": True}
    rendered = json.dumps(receipt, indent=2) + "\n"
    print(rendered, end="")
    if args.out:
        args.out.write_text(rendered)
    return 0 if receipt["reference_passes"] and receipt["rejected"] == expected else 1

if __name__ == "__main__":
    raise SystemExit(main())
