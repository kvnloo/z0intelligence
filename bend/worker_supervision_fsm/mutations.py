#!/usr/bin/env python3
"""Worker supervision: are the laws load-bearing? Apply one broken variant of fsm.bend at a time
(in a scratch copy), run `bend PROOF.bend`, and require SOME PROOFS FAIL.

Each mutation is the bug a law exists to catch, written the way it would
show up in the code. The reference fsm.bend is never edited in place.

    python3 bend/worker_supervision_fsm/mutations.py [--verdict] [--out mutations.json]
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
    ("1 lease_no_steal", "Acquire on a held lease hands it to the newcomer",
     "    case Acquire{a}:\n      Held{o}", "    case Acquire{a}:\n      Held{a}"),
    ("1 lease_release_by_holder", "Anyone may release a lease",
     "      lpick(Nat.is_eq(a, o), Free{}, Held{o})", "      Free{}"),
    ("1 write_needs_lease", "Any worker may write to a leased worktree",
     "    case Held{o}:\n      Nat.is_eq(o, a)", "    case Held{o}:\n      True{}"),
    ("2 interrupted_overdue", "The clock never makes an interrupted worker overdue",
     "      pick(late(now, at, lim), Overdue{w, at, bg}, Interrupted{w, at, bg})",
     "      Interrupted{w, at, bg}"),
    ("2 interrupted_exits", "An interrupted worker silently counts as completed",
     "    case Abandon{}:\n      pick(is_zero(bg), Abandoned{w}, Interrupted{w, at, bg})",
     "    case Abandon{}:\n      pick(is_zero(bg), Abandoned{w}, Interrupted{w, at, bg})\n    case Finish{}:\n      Completed{w, bg}"),
    ("2 overdue_exits", "A tick restarts an overdue worker's clock",
     "    case Abandon{}:\n      pick(is_zero(bg), Abandoned{w}, Overdue{w, at, bg})",
     "    case Abandon{}:\n      pick(is_zero(bg), Abandoned{w}, Overdue{w, at, bg})\n    case Tick{now}:\n      Interrupted{w, now, bg}"),
    ("2 interrupted_can_resume", "Resume drops the dead worker's background processes",
     "      pick(log, Resumed{w, by, bg}, Interrupted{w, at, bg})",
     "      pick(log, Resumed{w, by, 0n}, Interrupted{w, at, bg})"),
    ("2 running_stalls", "A silent worker never stalls",
     "      pick(late(now, hb, lim), Stalled{w, h, hb, bg}, Running{w, h, hb, bg})",
     "      Running{w, h, hb, bg}"),
    ("2 stalled_interrupts", "A stalled worker is never declared interrupted",
     "      pick(late(now, hb, Nat.add(lim, lim)), Interrupted{w, now, bg}, Stalled{w, h, hb, bg})",
     "      Stalled{w, h, hb, bg}"),
    ("3 end_needs_reaped", "Abandon an interrupted worker with processes still running",
     "      pick(is_zero(bg), Abandoned{w}, Interrupted{w, at, bg})", "      Abandoned{w}"),
    ("3 end_needs_reaped", "Integrate a completed worker with processes still running",
     "      pick(is_zero(bg), Integrated{w}, Completed{w, bg})", "      Integrated{w}"),
    ("4 running_is_placed", "Start anywhere, ignoring placement",
     "      pick(placed(w, h), Running{w, h, t, 0n}, Dispatched{w})", "      Running{w, h, t, 0n}"),
    ("4 running_is_placed", "No exception counts as an exception",
     "    case NoExc{}:\n      False{}", "    case NoExc{}:\n      True{}"),
    ("5 install_needs_lock", "Install without holding the phone lock",
     "      epick(holds(lk, a), Installed{a}, NoEff{})", "      Installed{a}"),
    ("5 install_lock_no_steal", "Lock grabs the phone lock from its holder",
     "      Dev{lstep(lk, Acquire{a}), fg}", "      Dev{Held{a}, fg}"),
    ("6 input_needs_foreground", "Type into the phone whatever is in the foreground",
     "      epick(fg_is(fg, app), Typed{a, app}, NoEff{})", "      Typed{a, app}"),
    ("6 input_needs_fresh_look", "An input keeps the old foreground observation",
     "    case Input{a, app}:\n      Dev{lk, Unknown{}}", "    case Input{a, app}:\n      Dev{lk, fg}"),
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
