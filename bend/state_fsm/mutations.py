#!/usr/bin/env python3
"""Mutate state.bend only in scratch copies; run the same dependent PROOF.bend.

Every required predicate (including available) has a bypass mutant. Every
non-availability predicate also has a missing-evidence-check mutant. Unknown
escalation and positive-completeness mutations exercise the tri-state boundary.
Only Bend's explicit SOME PROOFS FAIL verdict counts as rejection. A tool crash,
timeout, failed patch or absent verdict is an error, never a killed mutant.

    python3 bend/state_fsm/mutations.py --verdict --out mutations.json
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
FIELDS = {
    "A": ("available", "current", "scope_matches", "agrees"),
    "B": ("available", "disk_matches", "process_matches", "canonical"),
    "C": ("available", "epoch_current", "no_active_hits", "coverage_complete"),
    "D": ("available", "typed_receipt", "output_verified", "work_positive", "transport_ok", "target_current"),
    "E": ("available", "measured", "scope_matches", "agrees"),
}


def conjunction(xs: list[str]) -> str:
    return xs[0] if len(xs) == 1 else f"Bool.and({xs[0]}, {conjunction(xs[1:])})"


def definition(law: str, names: tuple[str, ...]) -> str:
    params = ", ".join(f"+{name}: Evidence" for name in names)
    known = conjunction([f"known({name})" for name in names[1:]])
    holds = conjunction([f"affirmed({name})" for name in names[1:]])
    return f"def {law.lower()}({params}) -> Verdict:\n  decide(available, {known}, {holds})"


def mutations() -> list[tuple[str, str, str, str]]:
    rows = []
    for law, names in FIELDS.items():
        old = definition(law, names)
        for name in names:
            if name == "available":
                new = old.replace("  decide(available,", "  decide(SBool{True{}},")
            else:
                new = old.replace(f"affirmed({name})", "True{}")
            rows.append((f"{law} {law.lower()}_pass_needs_{name}",
                         f"bypass mandatory {name}", old, new))
        for name in names[1:]:
            rows.append((f"{law} {law.lower()}_missing_{name}_unknown",
                         f"treat missing {name} as observed", old,
                         old.replace(f"known({name})", "True{}")))
    old = ("def decide_known(complete: Bool, holds: Bool) -> Verdict:\n"
           "  match complete:\n    case True{}:\n      finish(holds)\n"
           "    case False{}:\n      SUnknown{}")
    rows.append(("A-E missing evidence", "escalate Unknown to Pass", old,
                 old.replace("SUnknown{}", "SPass{}")))
    old = ("def finish(holds: Bool) -> Verdict:\n  match holds:\n"
           "    case True{}:\n      SPass{}\n    case False{}:\n      SViolation{}")
    rows.append(("A-E positive completeness", "suppress fully evidenced positives", old,
                 old.replace("SPass{}", "SUnknown{}")))
    return rows


MUTATIONS = mutations()


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
    passed = "ALL PROOFS CHECK" in out and "SOME PROOFS FAIL" not in out and p.returncode == 0
    rejected = "SOME PROOFS FAIL" in out and "ALL PROOFS CHECK" not in out
    return {"proofs_check": True if passed else False if rejected else None,
            "rejected": rejected, "where": "" if passed else first_location(out),
            "returncode": p.returncode, "output": out}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verdict", action="store_true")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    rows = []
    reference = (HERE / "state.bend").read_text()
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
        work = Path(td)
        for name in ("state.bend", "LAWS.bend", "PROOF.bend"):
            shutil.copy(HERE / name, work / name)
        baseline = check(work, args.verdict)
        rows.append({"law": "-", "mutation": "reference (unmodified)", **baseline})
        # A broken baseline cannot establish mutation rejection.
        if baseline["proofs_check"] is True:
            for law, desc, old, new in MUTATIONS:
                matches = reference.count(old)
                if matches != 1:
                    rows.append({"law": law, "mutation": desc, "proofs_check": None,
                                 "rejected": False, "where": f"MUTATION DID NOT APPLY (matches: {matches})"})
                    continue
                (work / "state.bend").write_text(reference.replace(old, new))
                rows.append({"law": law, "mutation": desc, **check(work, args.verdict)})
    reference_ok = rows[0]["proofs_check"] is True
    caught = sum(row["rejected"] for row in rows[1:])
    for row in rows:
        status = {True: "PASS", False: "FAIL", None: "N/A"}[row["proofs_check"]]
        print(f"{status:4}  {row['law']:40} {row['mutation']}  {row['where']}")
    print(f"reference passes: {reference_ok}; mutations rejected: {caught}/{len(MUTATIONS)}; laws covered: A B C D E")
    if args.out:
        args.out.write_text(json.dumps({"verdict_mode": args.verdict, "rows": rows,
                                        "reference_passes": reference_ok, "rejected": caught,
                                        "total": len(MUTATIONS), "reference_restored": True}, indent=2) + "\n")
    return 0 if reference_ok and caught == len(MUTATIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
