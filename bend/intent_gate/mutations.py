#!/usr/bin/env python3
"""Check the reference and independent type-correct I0-I4 mutants in scratch copies.

Use on the MBP with the approved Bend tool archive. This command never changes
tracked sources. A parse/type/tool error does NOT count as a rejected mutant.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
BEND = os.environ.get("BEND", str(Path.home() / ".bend/bin/bend"))
# (law, defect, unique original expression, replacement of the same result type)
MUTATIONS = [
    ("I0", "mutation ignores Kevin approval", "    case True{}:\n      approved", "    case True{}:\n      True{}"),
    ("I0", "all mutation is refused even after approval", "    case True{}:\n      approved", "    case True{}:\n      False{}"),
    ("I0", "Q&A requires approval", "    case False{}:\n      True{}\n\ntype Status", "    case False{}:\n      approved\n\ntype Status"),
    ("I1", "open task closes the turn", "    case IOpen{}:\n      False{}", "    case IOpen{}:\n      True{}"),
    ("I1", "drop omits its reason", "    case IDropped{}:\n      reason", "    case IDropped{}:\n      True{}"),
    ("I1", "dead delegation closes the turn", "    case IDelegated{}:\n      live", "    case IDelegated{}:\n      True{}"),
    ("I1", "verified status substitutes for a receipt", "    case IVerified{}:\n      verified", "    case IVerified{}:\n      True{}"),
    ("I2", "claim has no verified update", "  Bool.and(updated, Bool.and(marked, Bool.and(passed, tool_ok)))", "  Bool.and(True{}, Bool.and(marked, Bool.and(passed, tool_ok)))"),
    ("I2", "failed verifier is accepted", "  Bool.and(updated, Bool.and(marked, Bool.and(passed, tool_ok)))", "  Bool.and(updated, Bool.and(marked, Bool.and(True{}, tool_ok)))"),
    ("I2", "tool failure is accepted", "  Bool.and(updated, Bool.and(marked, Bool.and(passed, tool_ok)))", "  Bool.and(updated, Bool.and(marked, Bool.and(passed, True{})))"),
    ("I2", "verification predates latest update", "Bool.not(Nat.is_gt(changed, start))", "True{}"),
    ("I2", "verification at the ask timestamp is accepted", "Nat.is_gt(start, asked)", "Bool.not(Nat.is_gt(asked, start))"),
    ("I2", "delegate result receipt omitted", "      Bool.and(received, Bool.not(Nat.is_gt(result, start)))", "      Bool.and(True{}, Bool.not(Nat.is_gt(result, start)))"),
    ("I2", "delegate result arrives after verification", "      Bool.and(received, Bool.not(Nat.is_gt(result, start)))", "      received"),
    ("I2", "claim dispatcher bypasses freshness", "  Bool.and(proof, Bool.and(fresh, Bool.and(delegate, identity)))", "  Bool.and(proof, Bool.and(True{}, Bool.and(delegate, identity)))"),
    ("I3", "verifier runs against another target", "  Bool.and(Nat.is_eq(task, verifier), Bool.and(Nat.is_eq(task, result), Nat.is_eq(acceptance, used)))", "  Bool.and(True{}, Bool.and(Nat.is_eq(task, result), Nat.is_eq(acceptance, used)))"),
    ("I3", "result comes from another target", "  Bool.and(Nat.is_eq(task, verifier), Bool.and(Nat.is_eq(task, result), Nat.is_eq(acceptance, used)))", "  Bool.and(Nat.is_eq(task, verifier), Bool.and(True{}, Nat.is_eq(acceptance, used)))"),
    ("I3", "another acceptance verifier is substituted", "  Bool.and(Nat.is_eq(task, verifier), Bool.and(Nat.is_eq(task, result), Nat.is_eq(acceptance, used)))", "  Bool.and(Nat.is_eq(task, verifier), Bool.and(Nat.is_eq(task, result), True{}))"),
    ("I4", "forwarded head ID is not carried", "      Bool.and(mem(h, carried), relay_allows(t, carried))", "      Bool.and(True{}, relay_allows(t, carried))"),
    ("I4", "forwarded tail IDs are discarded", "      Bool.and(mem(h, carried), relay_allows(t, carried))", "      mem(h, carried)"),
]


def run(work: Path, filename: str, verdict: bool) -> dict:
    env = dict(os.environ)
    env["PATH"] = "/usr/bin:" + env.get("PATH", "") + ":" + str(Path.home() / ".elan/bin")
    command = [BEND, filename] + (["--verdict"] if verdict else [])
    try:
        process = subprocess.run(command, cwd=work, env=env, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"passes": False, "proof_failure": False, "output": str(error), "returncode": None}
    text = (process.stdout + process.stderr).strip()
    return {"passes": process.returncode == 0 and "ALL PROOFS CHECK" in text and "SOME PROOFS FAIL" not in text,
            "proof_failure": "SOME PROOFS FAIL" in text,
            "output": text, "returncode": process.returncode}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verdict", action="store_true")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    reference = (HERE / "intent.bend").read_text()
    rows = []
    with tempfile.TemporaryDirectory(prefix="intent-mutants-", dir=os.environ.get("TMPDIR")) as temporary:
        work = Path(temporary)
        for filename in ("intent.bend", "LAWS.bend", "PROOF.bend"):
            shutil.copy(HERE / filename, work / filename)
        baseline = run(work, "PROOF.bend", args.verdict)
        for law, defect, old, new in MUTATIONS:
            matches = reference.count(old)
            row = {"law": law, "mutation": defect, "matches": matches,
                   "type_correct": False, "rejected": False}
            if matches != 1:
                row["error"] = "Mutation anchor must match exactly once"
            else:
                (work / "intent.bend").write_text(reference.replace(old, new))
                types = run(work, "intent.bend", args.verdict)
                row["type_correct"] = types["passes"]
                row["typecheck"] = types
                if types["passes"]:
                    proof = run(work, "PROOF.bend", args.verdict)
                    row["proof"] = proof
                    row["rejected"] = proof["proof_failure"] and not proof["passes"]
            rows.append(row)
            print(f"{law}: {defect}: type_correct={row['type_correct']} rejected={row['rejected']}")
    rejected = sum(row["rejected"] for row in rows)
    report = {"verdict_mode": args.verdict, "reference": baseline, "rows": rows,
              "rejected": rejected, "total": len(rows), "reference_untouched": True}
    print(f"reference passes: {baseline['passes']}; typed mutants rejected: {rejected}/{len(rows)}")
    if args.out:
        args.out.write_text(json.dumps(report, indent=2) + "\n")
    return 0 if baseline["passes"] and rejected == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
