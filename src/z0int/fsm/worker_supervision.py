"""Worker supervision statechart (loop 2), the Python twin of
bend/worker_supervision_fsm/fsm.bend.

    Dispatched -> Running (heartbeats) -> {Completed | Interrupted | Stalled}
               -> Resumed | Abandoned | Integrated      (+ Overdue past the limit)

plus the two shared resources the laws need: a `Lease` (one owner per
worktree/branch; also the phone install lock) and the owner's S25 as a
`Device` (input only into the app last seen in the foreground).

The Bend file is the reference: tests/test_fsm_worker_supervision.py runs
seeded tapes through both and requires identical final states. This module
only computes; it never touches a worker, process, worktree or phone.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any


class Host(str, Enum):
    GROOT = "groot"   # the memory-starved desktop
    MBP = "mbp"
    PHONE = "phone"   # S25 / S22+ polite lanes


class Load(str, Enum):
    LIGHT = "light"
    HEAVY = "heavy"


class Exc(str, Enum):
    NONE = "none"
    GPU = "gpu"
    TOOLCHAIN = "toolchain"


@dataclass(frozen=True)
class W:
    id: int
    wt: int = 0
    load: Load = Load.LIGHT
    exc: Exc = Exc.NONE


class Phase(str, Enum):
    DISPATCHED = "Dispatched"
    RUNNING = "Running"
    STALLED = "Stalled"
    INTERRUPTED = "Interrupted"
    OVERDUE = "Overdue"
    COMPLETED = "Completed"
    RESUMED = "Resumed"
    ABANDONED = "Abandoned"
    INTEGRATED = "Integrated"


END = (Phase.ABANDONED, Phase.INTEGRATED)


@dataclass(frozen=True)
class St:
    phase: Phase
    w: W
    h: Host | None = None   # Running, Stalled
    hb: int = 0             # last heartbeat (Running, Stalled)
    at: int = 0             # interruption time (Interrupted, Overdue)
    bg: int = 0             # background processes left running
    by: int = 0             # successor (Resumed)


@dataclass(frozen=True)
class Ev:
    kind: str  # Start Beat Spawn Reap Tick Finish Interrupt Resume Abandon Integrate
    h: Host | None = None
    t: int = 0              # Start/Beat/Interrupt time, Tick: now
    by: int = 0
    log: bool = False
    evidence: tuple = ()


def placed(w: W, h: Host) -> bool:
    return h is not Host.GROOT or w.load is Load.LIGHT or w.exc is not Exc.NONE


def late(now: int, base: int, lim: int) -> bool:
    return now > base + lim


def step(s: St, e: Ev, lim: int) -> St:
    p, k = s.phase, e.kind
    if p is Phase.DISPATCHED:
        if k == "Start":
            return St(Phase.RUNNING, s.w, h=e.h, hb=e.t, bg=0) if e.h is not None and placed(s.w, e.h) else s
        if k == "Interrupt":
            return St(Phase.INTERRUPTED, s.w, at=e.t, bg=0)
        if k == "Abandon":
            return St(Phase.ABANDONED, s.w)
        return s
    if p is Phase.RUNNING:
        if k == "Beat":
            return replace(s, hb=e.t)
        if k == "Spawn":
            return replace(s, bg=s.bg + 1)
        if k == "Reap":
            return replace(s, bg=max(0, s.bg - 1))
        if k == "Tick":
            return replace(s, phase=Phase.STALLED) if late(e.t, s.hb, lim) else s
        if k == "Finish":
            return St(Phase.COMPLETED, s.w, bg=s.bg)
        if k == "Interrupt":
            return St(Phase.INTERRUPTED, s.w, at=e.t, bg=s.bg)
        return s
    if p is Phase.STALLED:
        if k == "Beat":
            return replace(s, phase=Phase.RUNNING, hb=e.t)
        if k == "Reap":
            return replace(s, bg=max(0, s.bg - 1))
        if k == "Tick":
            return St(Phase.INTERRUPTED, s.w, at=e.t, bg=s.bg) if late(e.t, s.hb, lim + lim) else s
        if k == "Finish":
            return St(Phase.COMPLETED, s.w, bg=s.bg)
        if k == "Interrupt":
            return St(Phase.INTERRUPTED, s.w, at=e.t, bg=s.bg)
        return s
    if p in (Phase.INTERRUPTED, Phase.OVERDUE):
        if k == "Reap":
            return replace(s, bg=max(0, s.bg - 1))
        if k == "Tick" and p is Phase.INTERRUPTED:
            return replace(s, phase=Phase.OVERDUE) if late(e.t, s.at, lim) else s
        if k == "Resume":
            return St(Phase.RESUMED, s.w, by=e.by, bg=s.bg) if e.log else s
        if k == "Abandon":
            return St(Phase.ABANDONED, s.w) if s.bg == 0 else s
        return s
    if p is Phase.COMPLETED:
        if k == "Reap":
            return replace(s, bg=max(0, s.bg - 1))
        if k == "Integrate":
            return St(Phase.INTEGRATED, s.w) if s.bg == 0 else s
        if k == "Abandon":
            return St(Phase.ABANDONED, s.w) if s.bg == 0 else s
        return s
    return s  # Resumed, Abandoned, Integrated absorb every event


def run(es: list[Ev], lim: int, s: St) -> St:
    for e in es:
        s = step(s, e, lim)
    return s


def refused(s: St, e: Ev, lim: int) -> bool:
    """An event the statechart does not take in s (state unchanged although
    the event names a move). Tick/Beat/Reap that change nothing are not refusals."""
    if e.kind in ("Tick", "Beat", "Spawn", "Reap"):
        return False
    return step(s, e, lim) == s


def needs_kevin(s: St) -> bool:
    return s.phase is Phase.OVERDUE


# ---------------------------------------------------------------- lease

@dataclass(frozen=True)
class Lease:
    holder: int | None = None   # None = Free


def lstep(l: Lease, kind: str, a: int, dead: bool = False) -> Lease:
    if l.holder is None:
        return Lease(a) if kind == "Acquire" else l
    if kind == "Release":
        return Lease(None) if a == l.holder else l
    if kind == "Reclaim":
        return Lease(a) if dead else l
    return l  # Acquire on a held lease: refused


def holds(l: Lease, a: int) -> bool:
    return l.holder is not None and l.holder == a


# ---------------------------------------------------------------- device (S25)

@dataclass(frozen=True)
class Device:
    lk: Lease = Lease()
    fg: int | None = None   # app last seen in the foreground; None = Unknown


def peff(p: Device, kind: str, a: int = 0, app: int = 0) -> tuple:
    if kind == "Install":
        return ("Installed", a) if holds(p.lk, a) else ("NoEff",)
    if kind == "Input":
        return ("Typed", a, app) if p.fg is not None and p.fg == app else ("NoEff",)
    return ("NoEff",)


def pstep(p: Device, kind: str, a: int = 0, app: int = 0) -> Device:
    if kind == "Lock":
        return Device(lstep(p.lk, "Acquire", a), p.fg)
    if kind == "Unlock":
        return Device(lstep(p.lk, "Release", a), p.fg)
    if kind == "Peek":
        return Device(p.lk, app)
    if kind == "Input":
        return Device(p.lk, None)
    return p


# ---------------------------------------------------------------- documentation tables

TRANSITIONS: list[dict[str, Any]] = [
    {"from": "Dispatched", "to": "Running", "event": "Start(host)", "guard": "placed: heavy work off groot unless a GPU/toolchain exception is declared", "law": 4},
    {"from": "Dispatched", "to": "Interrupted", "event": "Interrupt", "guard": "", "law": 2},
    {"from": "Dispatched", "to": "Abandoned", "event": "Abandon", "guard": "", "law": 2},
    {"from": "Running", "to": "Running", "event": "Beat / Spawn / Reap", "guard": "", "law": None},
    {"from": "Running", "to": "Stalled", "event": "Tick(now)", "guard": "now > last heartbeat + limit", "law": 2},
    {"from": "Running", "to": "Completed", "event": "Finish", "guard": "", "law": None},
    {"from": "Running", "to": "Interrupted", "event": "Interrupt", "guard": "", "law": 2},
    {"from": "Stalled", "to": "Running", "event": "Beat", "guard": "", "law": None},
    {"from": "Stalled", "to": "Interrupted", "event": "Tick(now) / Interrupt", "guard": "now > last heartbeat + 2 x limit", "law": 2},
    {"from": "Stalled", "to": "Completed", "event": "Finish", "guard": "", "law": None},
    {"from": "Interrupted", "to": "Overdue", "event": "Tick(now)", "guard": "now > interrupted + limit (needs Kevin)", "law": 2},
    {"from": "Interrupted", "to": "Resumed", "event": "Resume(by, from log)", "guard": "successor resumes from the log, adopts background processes", "law": 3},
    {"from": "Interrupted", "to": "Abandoned", "event": "Abandon", "guard": "every background process reaped", "law": 3},
    {"from": "Overdue", "to": "Resumed", "event": "Resume(by, from log)", "guard": "adopts background processes", "law": 3},
    {"from": "Overdue", "to": "Abandoned", "event": "Abandon", "guard": "every background process reaped", "law": 3},
    {"from": "Completed", "to": "Integrated", "event": "Integrate", "guard": "every background process reaped", "law": 3},
    {"from": "Completed", "to": "Abandoned", "event": "Abandon", "guard": "every background process reaped", "law": 3},
]

LAWS: list[dict[str, Any]] = [
    {"id": 1, "name": "one owner per worktree or branch (lease)",
     "bend": ["lease_no_steal", "lease_release_by_holder", "lease_free_acquires", "write_needs_lease"]},
    {"id": 2, "name": "an interrupted worker is resumed from its log or explicitly abandoned within the limit",
     "bend": ["interrupted_can_resume", "interrupted_can_abandon", "interrupted_overdue", "overdue_needs_kevin",
              "interrupted_exits", "overdue_exits", "running_stalls", "stalled_interrupts"]},
    {"id": 3, "name": "background processes of a dead worker are reaped or adopted",
     "bend": ["end_needs_reaped", "interrupted_can_resume"]},
    {"id": 4, "name": "heavy commands run on a fleet node, not groot, unless a GPU/toolchain exception is declared",
     "bend": ["running_is_placed", "stalled_is_placed"]},
    {"id": 5, "name": "installs to the owner's phone are serialized by a lock",
     "bend": ["install_needs_lock", "install_lock_no_steal"]},
    {"id": 6, "name": "no input to the S25 while another app is in the foreground",
     "bend": ["input_needs_foreground", "input_needs_fresh_look"]},
]


def mermaid() -> str:
    lines = ["stateDiagram-v2", "    [*] --> Dispatched"]
    for t in TRANSITIONS:
        if t["from"] == t["to"]:
            continue
        label = t["event"] + (f" [L{t['law']}]" if t["law"] else "")
        lines.append(f"    {t['from']} --> {t['to']}: {label}")
    lines += ["    Resumed --> [*]", "    Abandoned --> [*]", "    Integrated --> [*]",
              "    note right of Overdue: needs-Kevin queue (L2)",
              "    note left of Running: Lease (L1) - one owner per worktree/branch<br/>"
              "phone install lock (L5) - S25 input needs a fresh foreground look (L6)"]
    return "\n".join(lines) + "\n"
