#!/usr/bin/env python3
"""Are the Bend gate laws load-bearing?  Break the kernel, expect PROOF.bend to fail.

Each mutation copies bend/aodl_gate to a temp dir, applies one plausible
"AI edit" that weakens a rule, and runs ``bend PROOF.bend``.  A law that still
checks after its rule was weakened would be vacuous.  The unmodified kernel
must check.

    python benchmarks/bend_gate_law_mutations.py [--verdict]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

KERNEL = Path(__file__).resolve().parents[1] / "bend" / "aodl_gate"
BEND = os.environ.get("BEND_BIN", str(Path.home() / ".bend" / "bin" / "bend"))

# (name, file, old, new, law expected to break)
MUTATIONS = [
    ("drop payment rule from edge_body", "rules.bend",
     "emit(auth, 9, emit(pay, 10, emit(vgate, 11,", "emit(auth, 9, emit(False{}, 10, emit(vgate, 11,",
     "doc_no_privileged_grant"),
    ("payment check only on delegation edges", "rules.bend",
     "  pay = any_lower(tab, g, PRIVILEGED())", "  pay = Bool.and(deleg, any_lower(tab, g, PRIVILEGED()))",
     "doc_no_privileged_grant"),
    ("drop authority conjunct from allows", "transition.bend",
     "Bool.and(spend_ok(t), auth_ok(t))",
     "Bool.and(spend_ok(t), True{})",
     "spawn_no_invented_authority"),
    ("ceiling membership always true", "transition.bend",
     "      Bool.or(Nat.is_eq(x, h), nat_mem(x, t))", "      True{}",
     "spawn_no_invented_authority"),
    ("budget ignores the proposed spend", "transition.bend",
     "          Nat.is_le(Nat.add(seen, prop), limit)", "          Nat.is_le(seen, limit)",
     "spawn_within_budget"),
    ("off-by-one on maxChildren", "transition.bend",
     "      Nat.is_le(1n+ks, mc)", "      Nat.is_le(ks, mc)",
     "spawn_within_bounds"),
    ("revision check dropped", "transition.bend",
     "      Nat.is_eq(cr, rr)", "      True{}",
     "spawn_revision_pinned"),
    ("unknown mode treated as a document", "gate.bend",
     "    case _:\n      Deny{[0]}", "    case _:\n      doc(t)",
     "allow_needs_parse"),
    ("unparseable document admitted", "gate.bend",
     "    case None{}:\n      Deny{[0]}\n    case Some{q}:", "    case None{}:\n      Allow{}\n    case Some{q}:",
     "allow_needs_parse"),
]


# Weakenings no law covers: after the same mechanical edit to the proof's
# restated terms (a re-proof, not a new argument), PROOF.bend is EXPECTED
# to still check. These rules are guarded only by the measured parity
# corpus, not by proof. Without the re-proof edit they fail -- that is
# proof brittleness (the proof restates the kernel's term shape), not law
# coverage.
UNCOVERED = [
    ("dependency cycles never reported", [
        ("rules.bend", "      app(emit(cycle(nd, des), 13, Nil{}),", "      app(emit(False{}, 13, Nil{}),"),
        ("PROOF.bend", "  R.app(R.emit(R.cycle(nd, des), 13, Nil{}),", "  R.app(R.emit(False{}, 13, Nil{}),"),
    ]),
    ("self-edges allowed", [
        ("rules.bend", "      emit(U32.is_eq(a, b), 6, edge_ends(", "      emit(False{}, 6, edge_ends("),
        ("PROOF.bend", "    emit_rest(U32.is_eq(a, b), 6,", "    emit_rest(False{}, 6,"),
    ]),
    ("verifier may receive merge grants", [
        ("rules.bend", "emit(vgate, 11, emit(hgate, 11, Nil{}))", "emit(False{}, 11, emit(hgate, 11, Nil{}))"),
        ("PROOF.bend", "  +rest = R.emit(vgate, 11,", "  +rest = R.emit(False{}, 11,"),
    ]),
    ("unbounded recursion allowed", [
        ("rules.bend", "        emit(Bool.and(recursive, not(finite)), 15,", "        emit(False{}, 15,"),
    ]),
]


def run_proof(root: Path, verdict: bool, entry: str = "PROOF.bend") -> tuple[bool, str, float]:
    args = [BEND, str(root / entry)] + (["--verdict"] if verdict else ["--check-only"] if entry != "PROOF.bend" else [])
    env = dict(os.environ, BEND_NO_TELEMETRY="1")
    env["PATH"] = str(Path.home() / ".elan" / "bin") + os.pathsep + env.get("PATH", "")
    t0 = time.perf_counter()
    r = subprocess.run(args, capture_output=True, text=True, env=env, timeout=1800)
    out = (r.stdout + r.stderr).strip()
    return ("ALL PROOFS CHECK" in out and "SOME PROOFS FAIL" not in out), out, time.perf_counter() - t0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verdict", action="store_true", help="also recheck with the Lean-proven kernel")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    results = []
    ok, out, dt = run_proof(KERNEL, args.verdict)
    results.append({"mutation": "none (baseline)", "checks": ok, "seconds": round(dt, 2)})
    print(f"baseline: {'ALL PROOFS CHECK' if ok else 'FAIL'} ({dt:.1f}s)")
    failures = 0 if ok else 1
    for name, fname, old, new, law in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "k"
            shutil.copytree(KERNEL, root, ignore=shutil.ignore_patterns("build"))
            src = (root / fname).read_text()
            if old not in src:
                print(f"SKIP {name}: pattern not found")
                results.append({"mutation": name, "applied": False})
                failures += 1
                continue
            (root / fname).write_text(src.replace(old, new, 1))
            # the mutant must still be a well-typed program: only its proofs may break
            program_ok, prog_out, _ = run_proof(root, False, entry="main.bend")
            checks, out, dt = run_proof(root, args.verdict)
            caught = program_ok and not checks
            print(f"{'caught' if caught else 'MISSED'}: {name} (program well-typed={program_ok}; "
                  f"expected law {law}; {dt:.1f}s)")
            results.append({"mutation": name, "applied": True, "program_typechecks": program_ok,
                            "caught": caught, "expected_law": law, "seconds": round(dt, 2),
                            "first_error_lines": out.splitlines()[:6],
                            "program_errors": [] if program_ok else prog_out.splitlines()[:6]})
            failures += 0 if caught else 1
    for name, edits in UNCOVERED:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "k"
            shutil.copytree(KERNEL, root, ignore=shutil.ignore_patterns("build"))
            missing = False
            for fname, old, new in edits:
                src = (root / fname).read_text()
                if old not in src:
                    missing = True
                    break
                # the proof restates a term in several helpers: patch every copy
                (root / fname).write_text(src.replace(old, new, -1 if fname == "PROOF.bend" else 1))
            if missing:
                print(f"SKIP uncovered {name}: pattern not found")
                failures += 1
                continue
            checks, out, dt = run_proof(root, args.verdict)
            print(f"{'uncovered (still checks, as expected)' if checks else 'unexpectedly caught'}: {name}")
            results.append({"mutation": name, "applied": True, "uncovered_control": True, "still_checks": checks})
    if args.out:
        args.out.write_text(json.dumps(results, indent=2) + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
