#!/usr/bin/env python3
"""Require the reference proof and Lean rejection of each broken owner-standing gate (are the laws load-bearing?)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
BEND = os.environ.get("BEND", str(Path.home() / ".bend/bin/bend"))
MUTANTS = [
    ("gpt_any_route", "  guard(gpt, via_fx_pi)", "  True{}"),
    ("delete_without_receipt", "  guard(deletes, creator_finished_receipt)", "  True{}"),
    ("public_without_ok", "  guard(public, owner_ok)", "  True{}"),
    ("delete_never_allowed", "  guard(deletes, creator_finished_receipt)", "  guard(deletes, False{})"),
    ("admits_drops_gpt", "Bool.and(gpt_ok(g, gv), Bool.and(delete_ok(x, xc), public_ok(p, po)))", "Bool.and(delete_ok(x, xc), public_ok(p, po))"),
    ("admits_drops_delete", "Bool.and(delete_ok(x, xc), public_ok(p, po)))", "public_ok(p, po))"),
    ("admits_drops_public", "Bool.and(delete_ok(x, xc), public_ok(p, po)))", "delete_ok(x, xc))"),
    ("guard_inverted", "    case False{}:\n      True{}\n    case True{}:\n      cleared", "    case False{}:\n      cleared\n    case True{}:\n      True{}"),
]


def check(directory):
    p = subprocess.run([BEND, "PROOF.bend", "--verdict"], cwd=directory, capture_output=True, text=True, timeout=600,
                       env={**os.environ, "PATH": str(Path.home() / ".elan/bin") + ":" + os.environ["PATH"]})
    out = p.stdout + p.stderr
    return "ALL PROOFS CHECK" in out and "SOME PROOFS FAIL" not in out, out


def main():
    results = []
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp) / "owner_standing"
        work.mkdir()
        for name in ("standing.bend", "LAWS.bend", "PROOF.bend"):
            shutil.copyfile(HERE / name, work / name)
        ref = (HERE / "standing.bend").read_text()
        ok, detail = check(work)
        results.append({"mutation": "reference", "proofs_check": ok, "detail": detail[-300:]})
        for name, old, new in MUTANTS:
            if ref.count(old) != 1:
                raise ValueError("mutation anchor changed: " + name)
            (work / "standing.bend").write_text(ref.replace(old, new))
            passed, detail = check(work)
            results.append({"mutation": name, "proofs_check": passed, "rejected_by_proof": "SOME PROOFS FAIL" in detail,
                            "detail": detail[-300:]})
        (work / "standing.bend").write_text(ref)
    print(json.dumps({"rule": "owner-standing", "results": results}, indent=2))
    return 0 if results[0]["proofs_check"] and all(r["rejected_by_proof"] for r in results[1:]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
