#!/usr/bin/env python3
"""Executable differential parity: Python root_cause predicates versus the Bend root model.

Covers the complete {missing, False, True} product for R1-R4 signatures,
plus invalid values; R7 covers Boolean age-boundary combinations.
A non-bool normalizes to SAbsent; Python itself still receives the raw value.
This measures abstract-input agreement, not sensor/parser correctness or live
system state. The runner writes only temporary Bend copies and an optional
JSON receipt; it never alters the reference model or invokes operational guards.

    python3 bend/root_cause_fsm/parity.py --out state-parity.json
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
FIELDS = {'R1': ('target', 'before_metric', 'functional_probe', 'expiry_valid'), 'R2': ('same_metric', 'in_bounds_twice', 'separated_24h'), 'R3': ('probe_failed', 'owned', 'leased'), 'R4': ('same_window', 'share_at_least_30'), 'R7': ('report_only', 'same_finding', 'age_expired')}
MISSING = object()
INVALID = (None, 0, 1, "true", "false", [], {}, 0.0)
CODES = {"unknown": "0n", "pass": "1n", "violation": "2n"}
HEAD = """import Base
import ./root.bend as S

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
        sibling = next(other for other in names if other != name)
        for invalid, other_false in itertools.product(INVALID, (False, True)):
            facts = dict.fromkeys(names, True)
            facts[name] = invalid
            if other_false:
                facts[sibling] = False
            rows.append(facts)
    return rows


def run_parity(batch_size: int = 96) -> dict:
    # Keep this runner in bend/root_cause_fsm; the Python model belongs to the parent.
    sys.path.insert(0, str(ROOT / "src"))
    from z0int.fsm.root_cause import r1, r2, r3, r4
    from z0int.fsm.root_canaries import report_only_expired
    predicates = {"R1": r1, "R2": r2, "R3": r3, "R4": r4}

    env = dict(os.environ)
    env["PATH"] = "/usr/bin:" + env.get("PATH", "") + ":" + str(Path.home() / ".elan/bin")
    groups = {}
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
        work = Path(td)
        shutil.copy(HERE / "root.bend", work / "root.bend")
        for law, names in FIELDS.items():
            rows = ([dict(zip(names, values)) for values in itertools.product((False, True), repeat=len(names))] if law == "R7" else cases(law))
            failures = []
            compared = 0
            for offset in range(0, len(rows), batch_size):
                batch = rows[offset:offset + batch_size]
                expressions = []
                expected = []
                for facts in batch:
                    arguments = ", ".join(evidence(facts.get(name, MISSING)) for name in names)
                    expressions.append("S.r3_result(" + arguments + ")" if law == "R3"
                                       else "code(S." + law.lower() + "(" + arguments + "))")
                    if law == "R7":
                        # Compare concrete seconds immediately below and at expiry.
                        result = "pass" if report_only_expired(facts["report_only"], facts["same_finding"], 259200 if facts["age_expired"] else 259199) else "violation"
                    elif law == "R3":
                        raw = dict(facts)
                        value = raw.pop("probe_failed", MISSING)
                        if type(value) is bool:
                            raw["probe_passed"] = not value
                        actual = predicates[law](raw)
                        result = actual
                    else:
                        result = {"allow": "pass", "refuse": "violation", "escalate": "unknown"}[predicates[law](facts)]
                    codes = {"allow": "1n", "escalate": "0n", "rollback": "3n"} if law == "R3" else CODES
                    if result not in codes:
                        raise ValueError(f"predicate({law!r}, {facts!r}) returned {result!r}")
                    expected.append(codes[result])
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
            "contract": "Report-only eligibility; R3 pass means owned leased rollback recommendation. R7 age flag maps to the 72-hour boundary; raw metric derivation is not proved."}


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
