#!/usr/bin/env python3
"""Executable differential parity: Python evaluate versus the Bend state model.

Covers the complete {missing, False, True} product for every A–E signature,
plus non-bool values in each position with positive and false sibling facts.
A non-bool normalizes to SAbsent; Python itself still receives the raw value.
This measures abstract-input agreement, not sensor/parser correctness or live
system state. The runner writes only temporary Bend copies and an optional
JSON receipt; it never alters the reference model or invokes operational guards.

    python3 bend/state_fsm/parity.py --out state-parity.json
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BEND = os.environ.get("BEND", str(Path.home() / ".bend/bin/bend"))
FIELDS = {
    "A": ("available", "current", "scope_matches", "agrees"),
    "B": ("available", "disk_matches", "process_matches", "canonical"),
    "C": ("available", "epoch_current", "no_active_hits", "coverage_complete"),
    "D": ("available", "typed_receipt", "output_verified", "work_positive", "transport_ok", "target_current"),
    "E": ("available", "measured", "scope_matches", "agrees"),
    "I": ("available", "tracked_source", "scope_matches", "agrees"),
}
MISSING = object()
INVALID = (None, 0, 1, "true", "false", [], {}, 0.0)
CODES = {"unknown": "0n", "pass": "1n", "violation": "2n"}
HEAD = """import Base
import ./state.bend as S

def code(v: S.Verdict) -> Nat:
  match v:
    case S.SUnknown{}:
      0n
    case S.SPass{}:
      1n
    case S.SViolation{}:
      2n

"""


def evidence(value: object) -> str:
    if type(value) is not bool:
        return "S.SAbsent{}"
    return "S.SBool{True{}}" if value else "S.SBool{False{}}"


def cases(law: str) -> list[dict]:
    names = FIELDS[law]
    rows = [{name: value for name, value in zip(names, values) if value is not MISSING}
            for values in itertools.product((MISSING, False, True), repeat=len(names))]
    for name in names:
        sibling = next(other for other in names[1:] if other != name)
        for invalid, other_false in itertools.product(INVALID, (False, True)):
            facts = dict.fromkeys(names, True)
            facts[name] = invalid
            if other_false:
                facts[sibling] = False
            rows.append(facts)
    return rows


def run_parity(batch_size: int = 96) -> dict:
    # Keep this runner in bend/state_fsm; the Python model belongs to the parent.
    sys.path.insert(0, str(ROOT / "src"))
    from z0int.fsm.state_laws import evaluate

    env = dict(os.environ)
    env["PATH"] = "/usr/bin:" + env.get("PATH", "") + ":" + str(Path.home() / ".elan/bin")
    groups = {}
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
        work = Path(td)
        shutil.copy(HERE / "state.bend", work / "state.bend")
        for law, names in FIELDS.items():
            rows = cases(law)
            failures = []
            compared = 0
            for offset in range(0, len(rows), batch_size):
                batch = rows[offset:offset + batch_size]
                expressions = []
                expected = []
                for facts in batch:
                    expressions.append("code(S." + law.lower() + "(" +
                                       ", ".join(evidence(facts.get(name, MISSING)) for name in names) + "))")
                    result = evaluate(law, facts)
                    if result not in CODES:
                        raise ValueError(f"evaluate({law!r}, {facts!r}) returned {result!r}")
                    expected.append(CODES[result])
                source = HEAD + "def main() -> List<&2, Nat>:\n  [" + ",\n   ".join(expressions) + "]\n"
                target = work / "parity.bend"
                target.write_text(source)
                try:
                    p = subprocess.run([BEND, target.name], cwd=work, capture_output=True,
                                       text=True, timeout=600, env=env)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    failures.append({"offset": offset, "error": str(exc)})
                    break
                got = next((line for line in p.stdout.splitlines() if line.startswith("[")), "")
                want = "[" + ", ".join(expected) + "]"
                if p.returncode != 0 or got != want:
                    failure = {"offset": offset, "returncode": p.returncode,
                               "output": p.stdout + p.stderr, "expected": want}
                    if got.startswith("[") and got.endswith("]"):
                        values = got[1:-1].split(", ")
                        failure["first_mismatches"] = [
                            {"case": offset + i, "facts": batch[i], "bend": actual, "python": wanted}
                            for i, (actual, wanted) in enumerate(itertools.zip_longest(values, expected))
                            if actual != wanted and i < len(batch)][:3]
                    failures.append(failure)
                compared += len(batch)
            groups[law] = {"cases": len(rows), "compared": compared,
                           "equal": not failures and compared == len(rows), "failures": failures}
    return {"equal": all(group["equal"] for group in groups.values()),
            "total_cases": sum(group["cases"] for group in groups.values()),
            "groups": groups,
            "contract": "Exact bool only; missing/nonbool or unavailable yields unknown; otherwise all true passes, any false violates."}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--batch-size", type=int, default=96)
    args = ap.parse_args()
    if args.batch_size < 1:
        ap.error("--batch-size must be positive")
    receipt = run_parity(args.batch_size)
    rendered = json.dumps(receipt, indent=2) + "\n"
    print(rendered, end="")
    if args.out:
        args.out.write_text(rendered)
    return 0 if receipt["equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
