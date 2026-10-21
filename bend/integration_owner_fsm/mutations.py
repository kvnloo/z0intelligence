#!/usr/bin/env python3
"""Semantic mutations in disposable copies. Reference never changes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
BEND = str(Path.home() / ".bend/bin/bend")
MUTATIONS = [
    ("I1", "Bool.and(Bool.not(Bool.or(contained, parked)), Nat.is_ge(age, 86400n))", "False{}"),
    ("I2", "pick(equal, Deployed{}, s)", "Deployed{}"),
    ("I2 drift", "pick(equal, Deployed{}, OnIntegrationBranch{})", "Deployed{}"),
    ("I3 guard", "Bool.and(lease, Bool.and(stack, independent))", "Bool.and(stack, independent)"),
    ("I3 lease", "Bool.and(Nat.is_ge(now, expiry), Nat.is_gt(until, now))", "Nat.is_gt(until, now)"),
    ("I4", "Bool.and(lease, Bool.and(stack, independent))", "Bool.and(lease, independent)"),
    ("independent", "Bool.and(lease, Bool.and(stack, independent))", "Bool.and(lease, stack)"),
    ("I5 accept", "pick(owner, OwnerAccepted{}, s)", "OwnerAccepted{}"),
    ("I5 merge", "case OwnerAccepted{} Merge{}:", "case Deployed{} Merge{}:"),
    ("parking reason", "Bool.and(reason, future)", "future"),
    ("parking expiry", "Bool.and(reason, future)", "reason"),
]


def check(directory):
    r = subprocess.run([BEND, "PROOF.bend", "--verdict"], cwd=directory, text=True,
                       capture_output=True, timeout=180)
    return r.returncode, r.stdout + r.stderr


def main():
    rows = []
    source = (HERE / "fsm.bend").read_text()
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
        td = Path(td)
        for name in ["fsm.bend", "LAWS.bend", "PROOF.bend"]:
            shutil.copyfile(HERE / name, td / name)
        rc, out = check(td)
        assert rc == 0 and "ALL PROOFS CHECK" in out, out
        for law, old, new in MUTATIONS:
            assert source.count(old) == 1, old
            (td / "fsm.bend").write_text(source.replace(old, new))
            # First compile the broken implementation alone: syntax/type errors
            # are not semantic law failures and do not count as killed mutants.
            syntax = subprocess.run([BEND, "fsm.bend"], cwd=td, text=True, capture_output=True)
            rc, out = check(td)
            rejected = syntax.returncode == 0 and rc != 0 and "SOME PROOFS FAIL" in out
            rows.append({"law": law, "implementation_compiles": syntax.returncode == 0,
                         "rejected": rejected, "proof_output": out.strip()})
    result = {"reference_passes": True, "verdict_mode": True, "mutations": rows,
              "rejected": sum(x["rejected"] for x in rows), "total": len(rows)}
    (HERE / "mutations.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k,v in result.items() if k != "mutations"}))
    return 0 if all(x["rejected"] for x in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
