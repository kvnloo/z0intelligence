"""Differential parity between ops.py (the gates' decision functions) and
bend/ops_fsm/ops.bend (the proven book).

Every decision function with a finite domain is compared on its WHOLE domain
(O1 13x2x2 placements and 13x3 admissions, O2 2x6x2, O3 2^6x2 rollouts, O4,
O5, O7); the list/tape ones (O6 accessibility sets, O8 health tapes, O9 run
tapes) on seeded random inputs. Bend is the reference. Exhaustive agreement is
measured evidence over that domain, not a proof that the two programs are
equivalent; the abstract inputs' classification from real data is separate.
"""
from __future__ import annotations

import itertools
import os
import random
import subprocess
import tempfile
import shutil
from pathlib import Path

from . import ops as P

FSM_DIR = Path(__file__).resolve().parents[3] / "bend" / "ops_fsm"
BEND = os.environ.get("BEND", str(Path.home() / ".bend/bin/bend"))

CLS = {P.Cls.TEST: "CTest", P.Cls.MUTATION: "CMutation", P.Cls.TYPECHECK: "CTypecheck",
       P.Cls.GRADLE: "CGradle", P.Cls.BUNDLE: "CBundle", P.Cls.EVAL: "CEval", P.Cls.SHOT: "CShot",
       P.Cls.GPU: "CGpu", P.Cls.BEND: "CBend", P.Cls.LEAN: "CLean", P.Cls.SERVICE: "CService",
       P.Cls.AGENT: "CAgent", P.Cls.TINY: "CTiny"}
LANE = {P.Lane.GROOT: "LGroot", P.Lane.MBP: "LMbp", P.Lane.PHONE: "LPhone"}
GOP = {P.GitOp.EDIT: "GEdit", P.GitOp.COMMIT: "GCommit", P.GitOp.CHECKOUT: "GCheckout",
       P.GitOp.STASH: "GStash", P.GitOp.MERGE: "GMerge", P.GitOp.MERGE_FF: "GMergeFF",
       P.GitOp.ROLLBACK: "GRollback"}
OUT = {P.Out.REFUSED: "ORefused", P.Out.DEFERRED: "ODeferred", P.Out.PROMOTED: "OPromoted",
       P.Out.ROLLED_BACK: "ORolledBack"}
VIS = {P.Vis.PUBLIC: "VPublic", P.Vis.PRIVATE: "VPrivate", P.Vis.UNKNOWN: "VUnknown"}
B2 = (False, True)


def b(x: bool) -> str:
    return "True{}" if x else "False{}"


def n(x: int) -> str:
    return f"{x}n"


def nl(xs) -> str:
    return "[" + ", ".join(n(x) for x in xs) + "]"


def place_str(lane: P.Lane | None) -> str:
    return "ops.Wait{}" if lane is None else f"ops.RunOn{{ops.{LANE[lane]}{{}}}}"


def nst_str(s: P.NSt) -> str:
    return {"healthy": "ops.NHealthy{}", "bad": f"ops.NBad{{{n(s.since)}}}",
            "notified": f"ops.NNotified{{{n(s.since)}}}"}[s.kind]


def rst_str(s: P.RSt) -> str:
    return {"active": f"ops.RActive{{{n(s.gw)}}}", "interrupted": f"ops.RInterrupted{{{n(s.gw)}}}",
            "failed": "ops.RFailed{}", "done": "ops.RDone{}", "resumed": "ops.RResumed{}",
            "reported": "ops.RReported{}"}[s.kind]


HEAD = "import Base\nimport ./ops.bend as O\n\n"
NRUN = '''def bnat(x: Bool) -> Nat:
  match x:
    case True{}:
      1n
    case False{}:
      0n

def nrun(es: List<&2, O.NEv>, +lim: Nat, s: O.NSt) -> O.NSt:
  match es:
    case Nil{}:
      s
    case e <> t:
      nrun(t, lim, O.nstep(s, e, lim))

def ncount(es: List<&2, O.NEv>, +lim: Nat, s: O.NSt) -> Nat:
  match es:
    case Nil{}:
      0n
    case +e <> t:
      +ss = s
      Nat.add(bnat(O.neff(ss, e, lim)), ncount(t, lim, O.nstep(ss, e, lim)))

def rrun(es: List<&2, O.REv>, s: O.RSt) -> O.RSt:
  match es:
    case Nil{}:
      s
    case e <> t:
      rrun(t, O.rstep(s, e))

'''


def cases(seed: int = 0, n_rand: int = 120) -> list[tuple[str, str, str, str]]:
    """(group, element type, bend expression, expected printed value)."""
    rng = random.Random(seed)
    out = []
    for c, lane in itertools.product(P.Cls, P.Lane):
        out.append(("O1 admits", "Bool", f"O.admits(O.{LANE[lane]}{{}}, O.{CLS[c]}{{}})", b(P.admits(lane, c))))
    for c, m, p in itertools.product(P.Cls, B2, B2):
        out.append(("O1 place", "O.Place", f"O.place(O.{CLS[c]}{{}}, {b(m)}, {b(p)})", place_str(P.place(c, m, p))))
    for s, op, t in itertools.product(B2, P.GitOp, B2):
        out.append(("O2 wt_allows", "Bool", f"O.wt_allows({b(s)}, O.{GOP[op]}{{}}, {b(t)})", b(P.wt_allows(s, op, t))))
    for bits in itertools.product(B2, repeat=7):
        q, chk = P.Req(*bits[:6]), bits[6]
        out.append(("O3 rollout", "O.Out", f"O.rollout(O.Req{{{', '.join(b(x) for x in bits[:6])}}}, {b(chk)})",
                    f"ops.{OUT[P.rollout(q, chk)]}{{}}"))
    for pr, v in itertools.product(B2, P.Vis):
        out.append(("O4 push_allows", "Bool", f"O.push_allows({b(pr)}, O.{VIS[v]}{{}})", b(P.push_allows(pr, v))))
    for i, f, ca in itertools.product(B2, B2, B2):
        out.append(("O5 tap", "Bool", f"O.phone_allows(O.PTap{{{b(i)}, {b(f)}}}, {b(ca)})",
                    b(P.phone_allows(P.PKind.TAP, intent=i, fg=f, call=ca))))
    for k, ch, ca in itertools.product((P.PKind.INSTALL, P.PKind.RESTART), B2, B2):
        ctor = "PInstall" if k is P.PKind.INSTALL else "PRestart"
        out.append(("O5 install/restart", "Bool", f"O.phone_allows(O.{ctor}{{{b(ch)}}}, {b(ca)})",
                    b(P.phone_allows(k, chain=ch, call=ca))))
    for _ in range(n_rand):
        pre = [rng.randint(0, 5) for _ in range(rng.randint(0, 5))]
        post = [rng.randint(0, 5) for _ in range(rng.randint(0, 6))]
        rm = [rng.randint(0, 5) for _ in range(rng.randint(0, 2))]
        out.append(("O6 a11y_ok", "Bool", f"O.a11y_ok({nl(pre)}, {nl(post)}, {nl(rm)})", b(P.a11y_ok(pre, post, rm))))
    for c, s, d in itertools.product(B2, B2, B2):
        out.append(("O7 route_ok", "Bool", f"O.route_ok({b(c)}, {b(s)}, {b(d)})", b(P.route_ok(c, s, d))))
    lim = 30
    for _ in range(n_rand):
        s, t, tape, emits = P.NSt(), 0, [], 0
        for _ in range(rng.randint(1, 14)):
            t += rng.choice([1, 5, 10, 29, 31])
            ok = rng.random() < 0.35
            tape.append((ok, t))
            emits += P.neff(s, ok, t, lim)
            s = P.nstep(s, ok, t, lim)
        ev = "[" + ", ".join(f"O.NProbe{{{b(ok)}, {n(tt)}}}" for ok, tt in tape) + "]"
        out.append(("O8 nstep", "O.NSt", f"nrun({ev}, {n(lim)}, O.NHealthy{{}})", nst_str(s)))
        out.append(("O8 emissions", "Nat", f"ncount({ev}, {n(lim)}, O.NHealthy{{}})", n(emits)))
    for _ in range(n_rand):
        g = rng.randint(0, 2)
        s, tape = P.RSt("active", g), []
        for _ in range(rng.randint(1, 6)):
            k = rng.choice(["gw-restart", "gw-restart", "error", "done", "resume", "report"])
            x, ses = rng.randint(0, 2), rng.random() < 0.5
            tape.append((k, x, ses))
            s = P.rstep(s, k, x, ses)
        ctor = {"gw-restart": lambda x, ses: f"O.GwRestart{{{n(x)}}}", "error": lambda x, ses: "O.RunError{}",
                "done": lambda x, ses: "O.RunDone{}", "resume": lambda x, ses: f"O.ResumeRun{{{b(ses)}}}",
                "report": lambda x, ses: "O.ReportRun{}"}
        ev = "[" + ", ".join(ctor[k](x, ses) for k, x, ses in tape) + "]"
        out.append(("O9 rstep", "O.RSt", f"rrun({ev}, O.RActive{{{n(g)}}})", rst_str(s)))
    for bits in itertools.product(B2, repeat=5):
        args = ", ".join(b(x) for x in bits)
        out.append(("O10 phone_admits", "Bool", f"O.phone_admits({args})", b(P.phone_admits(*bits))))
        out.append(("O10 phone_place", "O.Place", f"O.phone_place({args})", place_str(P.phone_place(*bits))))
    for credited, actual, named, measured, count in itertools.product(
            B2, (0, 1), (0, 1), (2, 3, 24), (0, 1, 2, 3, 24, 119)):
        args = f"{b(credited)}, {n(actual)}, {n(named)}, {n(measured)}, {n(count)}"
        out.append(("O13 option_capacity_allows", "Bool", f"O.option_capacity_allows({args})",
                    b(P.option_capacity_allows(credited, actual, named, measured, count))))
    for kind, i, l, k, mv, surf in itertools.product(P.DKind, B2, B2, B2, B2, P.DSurf):
        if kind is not P.DKind.SEE and mv:
            continue
        act = {P.DKind.SEE: f"O.DSee{{{b(mv)}}}", P.DKind.OPEN: "O.DOpen{}", P.DKind.INPUT: "O.DInput{}"}[kind]
        srf = "O.DLive{}" if surf is P.DSurf.LIVE else "O.DSway{}"
        out.append(("O15 desk_allows", "Bool", f"O.desk_allows({srf}, {act}, {b(i)}, {b(l)}, {b(k)})",
                    b(P.desk_allows(surf, kind, interactive=i, live_ask=l, input_ask=k, moves=mv))))
    for i, l in itertools.product(B2, B2):
        out.append(("O15 desk_surface", "O.DSurf", f"O.desk_surface({b(i)}, {b(l)})",
                    "ops.DLive{}" if P.desk_surface(i, l) is P.DSurf.LIVE else "ops.DSway{}"))
        out.append(("O15 desk_unreachable", "O.DNext", f"O.desk_unreachable({b(i)}, {b(l)})",
                    "ops.DResolve{}" if P.desk_unreachable(i, l) == "resolve" else "ops.DReport{}"))
    return out


def run_parity(seed: int = 0, n_rand: int = 120, cs: list | None = None) -> dict:
    cs = cs if cs is not None else cases(seed, n_rand)
    groups: dict[tuple[str, str], list] = {}
    for g, ty, expr, want in cs:
        groups.setdefault((g, ty), []).append((expr, want))
    res, all_eq = {}, True
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
        shutil.copy(FSM_DIR / "ops.bend", Path(td) / "ops.bend")
        for i, ((g, ty), rows) in enumerate(groups.items()):
            src = HEAD + NRUN + f"def main() -> List<&2, {ty}>:\n  [" + ",\n   ".join(e for e, _ in rows) + "]\n"
            f = Path(td) / f"g{i}.bend"
            f.write_text(src)
            p = subprocess.run([BEND, f.name], cwd=td, capture_output=True, text=True, timeout=600)
            got = next((ln for ln in p.stdout.splitlines() if ln.startswith("[")), (p.stdout + p.stderr)[:400])
            want = "[" + ", ".join(w for _, w in rows) + "]"
            eq = got == want
            all_eq &= eq
            mism = []
            if not eq and got.startswith("["):
                gl = got.strip("[]").split(", ")
                wl = want.strip("[]").split(", ")
                mism = [rows[j][0] for j, (x, y) in enumerate(zip(gl, wl)) if x != y][:3]
            res[g] = {"cases": len(rows), "equal": eq, **({"first_mismatches": mism or got[:300]} if not eq else {})}
    return {"seed": seed, "random_per_group": n_rand, "equal": all_eq, "total_cases": len(cs), "groups": res}


if __name__ == "__main__":
    import json
    import sys
    r = run_parity(int(sys.argv[1]) if len(sys.argv) > 1 else 0)
    print(json.dumps(r, indent=2))
    raise SystemExit(0 if r["equal"] else 1)
