#!/usr/bin/env python3
"""Require the reference proof and rejection of each O14 broken admission gate."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
BEND = os.environ.get("BEND", str(Path.home() / ".bend/bin/bend"))
MUTANTS = [
    ("capability", "Bool.and(capability, Bool.and(quota, choice(scored, optimal, reason)))",
     "Bool.and(quota, choice(scored, optimal, reason))"),
    ("quota", "Bool.and(capability, Bool.and(quota, choice(scored, optimal, reason)))",
     "Bool.and(capability, choice(scored, optimal, reason))"),
    ("argmin", "Bool.or(optimal, reason)", "True{}"),
    ("unknown_credit", "Bool.and(scored, optimal)", "optimal"),
]


def check(directory):
    process = subprocess.run([BEND, "PROOF.bend", "--verdict"], cwd=directory,
                             capture_output=True, text=True, timeout=600)
    output = process.stdout + process.stderr
    return "ALL PROOFS CHECK" in output and "SOME PROOFS FAIL" not in output, output


def main():
    results = []
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        for name in ("routing.bend", "LAWS.bend", "PROOF.bend"):
            shutil.copyfile(HERE / name, work / name)
        reference = (HERE / "routing.bend").read_text()
        ok, detail = check(work)
        results.append({"mutation": "reference", "proofs_check": ok, "detail": detail})
        for name, old, new in MUTANTS:
            if reference.count(old) != 1:
                raise ValueError("mutation anchor changed: " + name)
            (work / "routing.bend").write_text(reference.replace(old, new))
            passed, detail = check(work)
            results.append({"mutation": name, "proofs_check": passed,
                            "rejected_by_proof": "SOME PROOFS FAIL" in detail, "detail": detail})
    print(json.dumps({"rule": "O14", "results": results}, indent=2))
    return 0 if results[0]["proofs_check"] and all(r["rejected_by_proof"] for r in results[1:]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
