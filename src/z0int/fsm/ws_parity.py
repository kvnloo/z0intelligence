"""Differential parity between worker_supervision.py and
bend/worker_supervision_fsm/fsm.bend.

Seeded random tapes run through the Python twin and, as a generated Bend
`main`, through Bend's normalizer; the printed final states (worker), final
leases and the device's final state plus every effect it emitted must be
equal. Bend is the reference. Agreement is measured evidence, not proof that
the two are equivalent.
"""
from __future__ import annotations

import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

from .worker_supervision import (
    Device, END, Ev, Exc, Host, Lease, Load, Phase, St, W, lstep, peff, pstep, step,
)

FSM_DIR = Path(__file__).resolve().parents[3] / "bend" / "worker_supervision_fsm"
BEND = os.environ.get("BEND", str(Path.home() / ".bend/bin/bend"))

HOST = {Host.GROOT: "Groot", Host.MBP: "Mbp", Host.PHONE: "Phone"}
LOAD = {Load.LIGHT: "Light", Load.HEAVY: "Heavy"}
EXC = {Exc.NONE: "NoExc", Exc.GPU: "Gpu", Exc.TOOLCHAIN: "Toolchain"}


def n(x: int) -> str:
    return f"{x}n"


def b(x: bool) -> str:
    return "True{}" if x else "False{}"


def bw(w: W, ns: str) -> str:
    return f"{ns}W{{{n(w.id)}, {n(w.wt)}, {ns}{LOAD[w.load]}{{}}, {ns}{EXC[w.exc]}{{}}}}"


def bend_ev(e: Ev, ns: str = "F.") -> str:
    k = e.kind
    if k == "Start":
        assert e.h is not None
        return f"{ns}Start{{{ns}{HOST[e.h]}{{}}, {n(e.t)}}}"
    if k in ("Beat", "Interrupt"):
        return f"{ns}{k}{{{n(e.t)}}}"
    if k == "Tick":
        return f"{ns}Tick{{{n(e.t)}}}"
    if k == "Resume":
        return f"{ns}Resume{{{n(e.by)}, {b(e.log)}}}"
    return f"{ns}{k}{{}}"


def bend_st(s: St, ns: str = "fsm.") -> str:
    w, p = bw(s.w, ns), s.phase
    c = ns + p.value
    if p is Phase.DISPATCHED or p in END:
        return f"{c}{{{w}}}"
    if p in (Phase.RUNNING, Phase.STALLED):
        assert s.h is not None
        return f"{c}{{{w}, {ns}{HOST[s.h]}{{}}, {n(s.hb)}, {n(s.bg)}}}"
    if p in (Phase.INTERRUPTED, Phase.OVERDUE):
        return f"{c}{{{w}, {n(s.at)}, {n(s.bg)}}}"
    if p is Phase.COMPLETED:
        return f"{c}{{{w}, {n(s.bg)}}}"
    return f"{c}{{{w}, {n(s.by)}, {n(s.bg)}}}"  # Resumed


def bend_lease(l: Lease, ns: str = "fsm.") -> str:
    return f"{ns}Free{{}}" if l.holder is None else f"{ns}Held{{{n(l.holder)}}}"


def bend_dev(p: Device, ns: str = "fsm.") -> str:
    fg = f"{ns}Unknown{{}}" if p.fg is None else f"{ns}Seen{{{n(p.fg)}}}"
    return f"{ns}Dev{{{bend_lease(p.lk, ns)}, {fg}}}"


def bend_eff(x: tuple, ns: str = "fsm.") -> str:
    if x[0] == "NoEff":
        return f"{ns}NoEff{{}}"
    return f"{ns}{x[0]}{{" + ", ".join(n(v) for v in x[1:]) + "}"


# ---------------------------------------------------------------- tapes

LIKELY = {
    Phase.DISPATCHED: ["Start"] * 6 + ["Interrupt", "Abandon"],
    Phase.RUNNING: ["Beat"] * 3 + ["Spawn"] * 2 + ["Reap", "Tick", "Tick", "Finish", "Interrupt"],
    Phase.STALLED: ["Beat", "Tick", "Tick", "Reap", "Interrupt", "Finish"],
    Phase.INTERRUPTED: ["Tick", "Reap", "Resume", "Abandon", "Abandon"],
    Phase.OVERDUE: ["Reap", "Reap", "Resume", "Abandon"],
    Phase.COMPLETED: ["Reap", "Integrate", "Integrate", "Abandon"],
}
ALL = ["Start", "Beat", "Spawn", "Reap", "Tick", "Finish", "Interrupt", "Resume", "Abandon", "Integrate"]


def random_worker(rng: random.Random) -> W:
    return W(rng.randint(1, 4), rng.randint(0, 3), rng.choice(list(Load)), rng.choice(list(Exc)))


def random_tape(rng: random.Random, lim: int, max_len: int = 16) -> tuple[W, list[Ev]]:
    w = random_worker(rng)
    s, tape, clock = St(Phase.DISPATCHED, w), [], 0
    for _ in range(rng.randint(1, max_len)):
        k = rng.choice(LIKELY.get(s.phase, ALL) if rng.random() < 0.85 else ALL)
        clock += rng.choice([0, 1, 2, lim, lim + 1, 2 * lim + 1])
        e = Ev(k, h=rng.choice(list(Host)), t=clock, by=rng.randint(1, 4), log=rng.random() < 0.7)
        tape.append(e)
        s = step(s, e, lim)
    return w, tape


def random_lease_tape(rng: random.Random, max_len: int = 10) -> list[tuple]:
    return [(rng.choice(["Acquire", "Release", "Reclaim"]), rng.randint(1, 3), rng.random() < 0.5)
            for _ in range(rng.randint(1, max_len))]


def random_dev_tape(rng: random.Random, max_len: int = 12) -> list[tuple]:
    return [(rng.choice(["Lock", "Unlock", "Peek", "Install", "Input", "Input"]), rng.randint(1, 3), rng.randint(1, 3))
            for _ in range(rng.randint(1, max_len))]


def _lease_ev(e: tuple) -> str:
    k, a, dead = e
    return f"F.Reclaim{{{n(a)}, {b(dead)}}}" if k == "Reclaim" else f"F.{k}{{{n(a)}}}"


def _dev_ev(e: tuple) -> str:
    k, a, app = e
    if k == "Peek":
        return f"F.Peek{{{n(app)}}}"
    if k == "Input":
        return f"F.Input{{{n(a)}, {n(app)}}}"
    return f"F.{k}{{{n(a)}}}"


MAIN_HEAD = '''import Base
import ./fsm.bend as F

def lrun(es: List<&2, F.LEv>, l: F.Lease) -> F.Lease:
  match es:
    case Nil{}:
      l
    case e <> t:
      lrun(t, F.lstep(l, e))

def prun(es: List<&2, F.PEv>, p: F.Device) -> List<&2, F.Eff>:
  match es:
    case Nil{}:
      []
    case +e <> t:
      +pp = p
      F.peff(pp, e) <> prun(t, F.pstep(pp, e))

def pend(es: List<&2, F.PEv>, p: F.Device) -> F.Device:
  match es:
    case Nil{}:
      p
    case +e <> t:
      pend(t, F.pstep(p, e))

'''


def run_parity(seed: int, n_tapes: int, lim: int = 3, max_len: int = 16, stepper=step) -> dict:
    rng = random.Random(seed)
    wt = [random_tape(rng, lim, max_len) for _ in range(n_tapes)]
    lt = [random_lease_tape(rng) for _ in range(n_tapes // 4)]
    dt = [random_dev_tape(rng) for _ in range(n_tapes // 4)]

    def py_run(w, tape):
        s = St(Phase.DISPATCHED, w)
        for e in tape:
            s = stepper(s, e, lim)
        return s

    finals = [py_run(w, t) for w, t in wt]
    want_w = "[" + ", ".join(bend_st(s) for s in finals) + "]"
    want_l, want_d = [], []
    for t in lt:
        l = Lease()
        for k, a, dead in t:
            l = lstep(l, k, a, dead)
        want_l.append(bend_lease(l))
    for t in dt:
        p, effs = Device(), []
        for k, a, app in t:
            effs.append(bend_eff(peff(p, k, a, app)))
            p = pstep(p, k, a, app)
        want_d.append("[" + ", ".join(effs) + "]")
        want_d.append(bend_dev(p))
    body_w = ",\n   ".join(f"F.run([{', '.join(bend_ev(e) for e in t)}], {n(lim)}, F.Dispatched{{{bw(w, 'F.')}}})"
                           for w, t in wt)
    body_l = ",\n   ".join(f"lrun([{', '.join(_lease_ev(e) for e in t)}], F.Free{{}})" for t in lt)
    dev0 = "F.Dev{F.Free{}, F.Unknown{}}"
    src = MAIN_HEAD + "def main() -> List<&2, F.St>:\n  [" + body_w + "]\n"
    src_l = MAIN_HEAD + "def main() -> List<&2, F.Lease>:\n  [" + body_l + "]\n"
    out_d = []
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
        shutil.copy(FSM_DIR / "fsm.bend", Path(td) / "fsm.bend")
        (Path(td) / "w.bend").write_text(src)
        (Path(td) / "l.bend").write_text(src_l)
        got = []
        for f in ("w.bend", "l.bend"):
            p = subprocess.run([BEND, f], cwd=td, capture_output=True, text=True, timeout=600)
            got.append(next((ln for ln in p.stdout.splitlines() if ln.startswith("[")), p.stdout + p.stderr))
        for i, t in enumerate(dt):
            tape = "[" + ", ".join(_dev_ev(e) for e in t) + "]"
            (Path(td) / f"d{i}.bend").write_text(
                MAIN_HEAD + "def main() -> List<&2, F.Eff>:\n  prun(" + tape + ", " + dev0 + ")\n")
            # Bend runs `main` only: the final device comes from a second file
            (Path(td) / f"e{i}.bend").write_text(
                MAIN_HEAD + "def main() -> F.Device:\n  pend(" + tape + ", " + dev0 + ")\n")
            for f in (f"d{i}.bend", f"e{i}.bend"):
                p = subprocess.run([BEND, f], cwd=td, capture_output=True, text=True, timeout=120)
                out_d.append(p.stdout.strip().splitlines()[0] if p.stdout.strip() else p.stderr[:300])
    phases: dict[str, int] = {}
    for s in finals:
        phases[s.phase.value] = phases.get(s.phase.value, 0) + 1
    got_l = got[1]
    eq_w, eq_l, eq_d = got[0] == want_w, got_l == "[" + ", ".join(want_l) + "]", out_d == want_d
    mism = []
    if not eq_w:
        g, w_ = got[0].strip("[]").split(", fsm."), want_w.strip("[]").split(", fsm.")
        mism = [i for i, (x, y) in enumerate(zip(g, w_)) if x != y][:5]
    return {"seed": seed, "worker_tapes": n_tapes, "worker_events": sum(len(t) for _, t in wt),
            "lease_tapes": len(lt), "device_tapes": len(dt), "lim": lim,
            "equal": eq_w and eq_l and eq_d, "worker_equal": eq_w, "lease_equal": eq_l, "device_equal": eq_d,
            "final_phases": phases, "first_mismatches": mism}
