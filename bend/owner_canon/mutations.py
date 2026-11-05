#!/usr/bin/env python3
"""Require the reference proof and Lean rejection of each broken owner-canon gate (are the laws load-bearing?)."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
BEND = os.environ.get("BEND", str(Path.home() / ".bend/bin/bend"))
MUTANTS = [
    ("gui_steals_focus", "  guard(touches_gui, isolated)", "  True{}"),
    ("done_without_receipt", "  guard(claims_done, receipt)", "  True{}"),
    ("quota_blind_retry", "  guard(limited, backed_off)", "  True{}"),
    ("admits_drops_gui", "Bool.and(gui_ok(u, ui), Bool.and(done_ok(d, dr), quota_ok(l, lb)))", "Bool.and(done_ok(d, dr), quota_ok(l, lb))"),
    ("admits_drops_done", "Bool.and(done_ok(d, dr), quota_ok(l, lb)))", "quota_ok(l, lb))"),
    ("admits_drops_quota", "Bool.and(done_ok(d, dr), quota_ok(l, lb)))", "done_ok(d, dr))"),
    ("route_ignores_o14", "Bool.and(True{}, R.admits(c, q, s, o, r))", "Bool.and(True{}, True{})"),
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
        work = Path(temp) / "owner_canon"
        work.mkdir()
        shutil.copytree(HERE.parent / "model_routing", Path(temp) / "model_routing")
        for name in ("canon.bend", "LAWS.bend", "PROOF.bend"):
            shutil.copyfile(HERE / name, work / name)
        ref = (HERE / "canon.bend").read_text()
        ok, detail = check(work)
        results.append({"mutation": "reference", "proofs_check": ok, "detail": detail[-300:]})
        for name, old, new in MUTANTS:
            if ref.count(old) != 1:
                raise ValueError("mutation anchor changed: " + name)
            (work / "canon.bend").write_text(ref.replace(old, new))
            passed, detail = check(work)
            results.append({"mutation": name, "proofs_check": passed, "rejected_by_proof": "SOME PROOFS FAIL" in detail,
                            "detail": detail[-300:]})
        (work / "canon.bend").write_text(ref)
    print(json.dumps({"rule": "owner-canon", "results": results}, indent=2))
    return 0 if results[0]["proofs_check"] and all(r["rejected_by_proof"] for r in results[1:]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
