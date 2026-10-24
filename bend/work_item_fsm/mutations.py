#!/usr/bin/env python3
"""Are the laws load-bearing? Apply one broken variant of fsm.bend at a time
(in a scratch copy), run `bend PROOF.bend`, and require SOME PROOFS FAIL.

Each mutation is the bug a law exists to catch, written the way it would
show up in the code. The reference fsm.bend is never edited in place.

    python3 bend/work_item_fsm/mutations.py [--verdict] [--out mutations.json]
"""
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

# (law it guards, description, old text, new text)
MUTATIONS = [
    ("1 merged_needs_owner_acceptance", "Merge straight from Deployed, skipping owner acceptance",
     """    case Accept{+a}:
      accept_out(accept_gate(a, au), au, hd, ig, dp, a)""",
     """    case Accept{+a}:
      accept_out(accept_gate(a, au), au, hd, ig, dp, a)
    case Merge{+a}:
      Merged{au, hd, ig, a}"""),
    ("2 verify_gate_not_author", "Verifier may be the author",
     "  Bool.and(is_ci(rrole(a)), other(a, au))\n\ndef receipt_good",
     "  is_ci(rrole(a))\n\ndef receipt_good"),
    ("2 accept_gate_owner_not_author", "Any non-author may accept",
     "  Bool.and(is_owner(rrole(a)), other(a, au))",
     "  other(a, au)"),
    ("3 draft_needs_bound_receipt", "Receipt need not be for the head SHA",
     "      Bool.and(pass, Nat.is_eq(sha, hd))",
     "      pass"),
    ("4 deployed_serves_integrated_head", "Deploy whatever the service runs",
     "deploy_out(Bool.and(operator(rrole(a)), Nat.is_eq(served, ig)), au, hd, ig, served)",
     "deploy_out(operator(rrole(a)), au, hd, ig, served)"),
    ("4 drift_undeploys", "Observing drift keeps the item Deployed",
     "      observe_out(Nat.is_eq(served, ig), au, hd, ig, dp)",
     "      observe_out(True{}, au, hd, ig, dp)"),
    ("5 interrupted_exits_keep_head", "Resume restarts from an empty head",
     "      pick(coder(rrole(a)), Implementing{a, hd}, Interrupted{au, hd})",
     "      pick(coder(rrole(a)), Implementing{a, 0n}, Interrupted{au, hd})"),
    ("6 implementer_is_coder", "Free models may claim and implement",
     """    case Sol{}:
      True{}
    case _:
      False{}

def is_ci""",
     """    case Sol{}:
      True{}
    case Free{}:
      True{}
    case _:
      False{}

def is_ci"""),
    ("7 queue_never_blocks", "While Kevin is off, hold every event (owner is a blocker)",
     "  astep.go(Bool.and(is_off(av), owner_event(e)), s, e)",
     "  astep.go(is_off(av), s, e)"),
    ("7 owner_off_holds", "Owner actions fire while Kevin is off",
     "  astep.go(Bool.and(is_off(av), owner_event(e)), s, e)",
     "  astep.go(False{}, s, e)"),
]


def check(proj: Path, verdict: bool) -> tuple[bool, str]:
    env = dict(os.environ)
    # host clang before Lean's bundled clang (bend-reachable-cuda-rule-gates)
    env["PATH"] = "/usr/bin:" + env.get("PATH", "") + ":" + str(Path.home() / ".elan/bin")
    cmd = [BEND, "PROOF.bend"] + (["--verdict"] if verdict else [])
    p = subprocess.run(cmd, cwd=proj, capture_output=True, text=True, env=env, timeout=600)
    out = (p.stdout + p.stderr).strip()
    return ("ALL PROOFS CHECK" in out and "SOME PROOFS FAIL" not in out), out


def first_location(out: str) -> str:
    for line in out.splitlines():
        if line.startswith("Location:"):
            return line.split(":", 1)[1].strip()
    return out.splitlines()[1] if len(out.splitlines()) > 1 else out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verdict", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()
    files = ["fsm.bend", "LAWS.bend", "PROOF.bend"]
    rows = []
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
        work = Path(td)
        for f in files:
            shutil.copy(HERE / f, work / f)
        ok, out = check(work, args.verdict)
        rows.append({"law": "-", "mutation": "reference (unmodified)", "proofs_check": ok,
                     "where": "" if ok else first_location(out)})
        ref = (HERE / "fsm.bend").read_text()
        for law, desc, old, new in MUTATIONS:
            if ref.count(old) != 1:
                rows.append({"law": law, "mutation": desc, "proofs_check": None,
                             "where": "MUTATION DID NOT APPLY"})
                continue
            (work / "fsm.bend").write_text(ref.replace(old, new))
            ok, out = check(work, args.verdict)
            rows.append({"law": law, "mutation": desc, "proofs_check": ok,
                         "where": "" if ok else first_location(out)})
        (work / "fsm.bend").write_text(ref)
    ref_ok = rows[0]["proofs_check"] is True
    caught = sum(1 for r in rows[1:] if r["proofs_check"] is False)
    for r in rows:
        status = {True: "PASS", False: "FAIL", None: "N/A"}[r["proofs_check"]]
        print(f"{status:4}  {r['law']:34} {r['mutation']}  {r['where']}")
    print(f"reference passes: {ref_ok}; mutations rejected: {caught}/{len(MUTATIONS)}")
    if args.out:
        Path(args.out).write_text(json.dumps({"verdict_mode": args.verdict, "rows": rows,
                                              "reference_restored": True}, indent=2) + "\n")
    return 0 if ref_ok and caught == len(MUTATIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
