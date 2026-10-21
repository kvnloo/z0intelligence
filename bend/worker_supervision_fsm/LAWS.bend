# Worker supervision: the laws. The owner states them; PROOF.bend proves
# them; `bend PROOF.bend --verdict` (or `hermes bend verify`) is the check.
#
# Scope, stated plainly. These are theorems about fsm.bend's own `step`,
# `run`, `lstep`, `holds`, `peff` and `pstep` over abstract ids, times (Nat
# seconds) and limits. They do not say that the Python twin equals fsm.bend
# (measured by tests/test_fsm_worker_supervision.py), that delegation logs,
# worktrees, fleet runs or adb commands were mapped faithfully, or that any
# worker succeeded. A PASS is never an authorization, an AODL admission or
# verified_success.
#
# "Reachable" means: produced by `run(es, lim, Dispatched{w0})` for some
# event tape es, limit lim and dispatched worker w0.

import Base
import ./fsm.bend as F

# Boolean reflection: T(b) is Unit when b holds, Empty when it does not
def T(b: Bool) -> Data:
  match b:
    case True{}:
      Unit
    case False{}:
      Empty

def distinct(+x: Nat, +y: Nat) -> Bool:
  Bool.not(Nat.is_eq(x, y))

# Placement, written apart from fsm.bend's guard: on groot, heavy work needs
# a declared GPU or toolchain exception; the MBP and the phones take anything.
def Excepted(x: F.Exc) -> Data:
  match x:
    case F.NoExc{}:
      Empty
    case F.Gpu{}:
      Unit
    case F.Toolchain{}:
      Unit

def GrootOk(w: F.W) -> Data:
  match w:
    case F.W{id, wt, load, exc}:
      match load:
        case F.Light{}:
          Unit
        case F.Heavy{}:
          Excepted(exc)

def Placed(w: F.W, h: F.Host) -> Data:
  match h:
    case F.Groot{}:
      GrootOk(w)
    case F.Mbp{}:
      Unit
    case F.Phone{}:
      Unit

# Law 1. One owner per worktree or branch at a time (the lease)
# -------------------------------------------------------------
# A held lease passes to someone else only by Reclaim with evidence that the
# holder is dead; only the holder releases it; only the holder writes.

def stolen(+o: Nat, l: F.Lease) -> Bool:
  match l:
    case F.Free{}:
      False{}
    case F.Held{p}:
      distinct(p, o)

def Reclaimed(e: F.LEv) -> Data:
  match e:
    case F.Reclaim{a, dead}:
      T(dead)
    case _:
      Empty

law lease_no_steal:
  for +o: Nat
  for e: F.LEv
  for w: T(stolen(o, F.lstep(F.Held{o}, e)))
  Reclaimed(e)

def is_free(l: F.Lease) -> Bool:
  match l:
    case F.Free{}:
      True{}
    case F.Held{o}:
      False{}

def ReleasedBy(e: F.LEv, o: Nat) -> Type:
  match e:
    case F.Release{a}:
      {a == o : Nat}
    case _:
      Empty

law lease_release_by_holder:
  for +o: Nat
  for e: F.LEv
  for w: T(is_free(F.lstep(F.Held{o}, e)))
  ReleasedBy(e, o)

law lease_free_acquires:
  for +a: Nat
  {F.lstep(F.Free{}, F.Acquire{a}) == F.Held{a} : F.Lease}

law write_needs_lease:
  for l: F.Lease
  for +a: Nat
  for w: T(F.holds(l, a))
  {l == F.Held{a} : F.Lease}

# Law 2. An interrupted worker is resumed from its log or explicitly
# abandoned within the limit
# -------------------------------------------------------------------
# A successor can always resume it from its log, adopting its background
# processes; it can always be abandoned once they are reaped; past the limit
# it turns Overdue (the needs-Kevin queue); and no event drops it silently.
# The heartbeat clock feeds it: a silent worker stalls, then is interrupted.

law interrupted_can_resume:
  for +w: F.W
  for +at: Nat
  for +bg: Nat
  for +by: Nat
  for +lim: Nat
  {F.step(F.Interrupted{w, at, bg}, F.Resume{by, True{}}, lim) == F.Resumed{w, by, bg} : F.St}

law interrupted_can_abandon:
  for +w: F.W
  for +at: Nat
  for +lim: Nat
  {F.step(F.Interrupted{w, at, 0n}, F.Abandon{}, lim) == F.Abandoned{w} : F.St}

law interrupted_overdue:
  for +w: F.W
  for +at: Nat
  for +bg: Nat
  for +now: Nat
  for +lim: Nat
  for h: T(Nat.is_gt(now, Nat.add(at, lim)))
  {F.step(F.Interrupted{w, at, bg}, F.Tick{now}, lim) == F.Overdue{w, at, bg} : F.St}

law overdue_needs_kevin:
  for +w: F.W
  for +at: Nat
  for +bg: Nat
  {F.needs_kevin(F.Overdue{w, at, bg}) == True{} : Bool}

# where an interrupted worker may go: stay (Interrupted/Overdue), Resumed or
# Abandoned; never Completed, Integrated or back to Running
def IntExit(x: F.St) -> Data:
  match x:
    case F.Interrupted{w, at, bg}:
      Unit
    case F.Overdue{w, at, bg}:
      Unit
    case F.Resumed{w, by, bg}:
      Unit
    case F.Abandoned{w}:
      Unit
    case _:
      Empty

law interrupted_exits:
  for +w: F.W
  for +at: Nat
  for +bg: Nat
  for e: F.Ev
  for +lim: Nat
  IntExit(F.step(F.Interrupted{w, at, bg}, e, lim))

# an overdue worker cannot quietly restart its clock as merely Interrupted
def OverExit(x: F.St) -> Data:
  match x:
    case F.Overdue{w, at, bg}:
      Unit
    case F.Resumed{w, by, bg}:
      Unit
    case F.Abandoned{w}:
      Unit
    case _:
      Empty

law overdue_exits:
  for +w: F.W
  for +at: Nat
  for +bg: Nat
  for e: F.Ev
  for +lim: Nat
  OverExit(F.step(F.Overdue{w, at, bg}, e, lim))

law running_stalls:
  for +w: F.W
  for +h: F.Host
  for +hb: Nat
  for +bg: Nat
  for +now: Nat
  for +lim: Nat
  for k: T(Nat.is_gt(now, Nat.add(hb, lim)))
  {F.step(F.Running{w, h, hb, bg}, F.Tick{now}, lim) == F.Stalled{w, h, hb, bg} : F.St}

law stalled_interrupts:
  for +w: F.W
  for +h: F.Host
  for +hb: Nat
  for +bg: Nat
  for +now: Nat
  for +lim: Nat
  for k: T(Nat.is_gt(now, Nat.add(hb, Nat.add(lim, lim))))
  {F.step(F.Stalled{w, h, hb, bg}, F.Tick{now}, lim) == F.Interrupted{w, now, bg} : F.St}

# Law 3. Background processes left by a dead worker are reaped or adopted
# -----------------------------------------------------------------------
# A worker reaches Abandoned or Integrated only with no background process
# left; Resume hands them to the successor (interrupted_can_resume: the
# Resumed state carries them, owned by `by`).

def is_end(s: F.St) -> Bool:
  match s:
    case F.Abandoned{w}:
      True{}
    case F.Integrated{w}:
      True{}
    case _:
      False{}

def ends(s: F.St, t: F.St) -> Bool:
  Bool.and(Bool.not(is_end(s)), is_end(t))

def bg_of(s: F.St) -> Nat:
  match s:
    case F.Dispatched{w}:
      0n
    case F.Running{w, h, hb, bg}:
      bg
    case F.Stalled{w, h, hb, bg}:
      bg
    case F.Interrupted{w, at, bg}:
      bg
    case F.Overdue{w, at, bg}:
      bg
    case F.Completed{w, bg}:
      bg
    case F.Resumed{w, by, bg}:
      bg
    case F.Abandoned{w}:
      0n
    case F.Integrated{w}:
      0n

law end_needs_reaped:
  for +s: F.St
  for e: F.Ev
  for lim: Nat
  for w: T(ends(s, F.step(s, e, lim)))
  {bg_of(s) == 0n : Nat}

# Law 4. Heavy work runs on a fleet node, not groot, unless excepted
# ------------------------------------------------------------------
law running_is_placed:
  for es: List<&2, F.Ev>
  for +lim: Nat
  for +w0: F.W
  for +w: F.W
  for +h: F.Host
  for +hb: Nat
  for +bg: Nat
  for k: {F.run(es, lim, F.Dispatched{w0}) == F.Running{w, h, hb, bg} : F.St}
  Placed(w, h)

law stalled_is_placed:
  for es: List<&2, F.Ev>
  for +lim: Nat
  for +w0: F.W
  for +w: F.W
  for +h: F.Host
  for +hb: Nat
  for +bg: Nat
  for k: {F.run(es, lim, F.Dispatched{w0}) == F.Stalled{w, h, hb, bg} : F.St}
  Placed(w, h)

# Law 5. Installs to the owner's phone are serialized by a lock
# -------------------------------------------------------------
def installed(x: F.Eff) -> Bool:
  match x:
    case F.Installed{a}:
      True{}
    case _:
      False{}

def InstallerHolds(lk: F.Lease, e: F.PEv) -> Type:
  match e:
    case F.Install{a}:
      {lk == F.Held{a} : F.Lease}
    case _:
      Empty

law install_needs_lock:
  for +lk: F.Lease
  for fg: F.Fg
  for e: F.PEv
  for w: T(installed(F.peff(F.Dev{lk, fg}, e)))
  InstallerHolds(lk, e)

law install_lock_no_steal:
  for +o: Nat
  for +fg: F.Fg
  for +a: Nat
  {F.pstep(F.Dev{F.Held{o}, fg}, F.Lock{a}) == F.Dev{F.Held{o}, fg} : F.Device}

# Law 6. No input to the S25 while another app is in the foreground
# -----------------------------------------------------------------
# Input is typed only into the app last observed in the foreground, and an
# input spends that observation: the next input needs a fresh look.
def typed(x: F.Eff) -> Bool:
  match x:
    case F.Typed{a, app}:
      True{}
    case _:
      False{}

def TypedInto(fg: F.Fg, e: F.PEv) -> Type:
  match e:
    case F.Input{a, app}:
      {fg == F.Seen{app} : F.Fg}
    case _:
      Empty

law input_needs_foreground:
  for lk: F.Lease
  for +fg: F.Fg
  for e: F.PEv
  for w: T(typed(F.peff(F.Dev{lk, fg}, e)))
  TypedInto(fg, e)

law input_needs_fresh_look:
  for +lk: F.Lease
  for fg: F.Fg
  for +a: Nat
  for +x: Nat
  for +b: Nat
  for +y: Nat
  {F.peff(F.pstep(F.Dev{lk, fg}, F.Input{a, x}), F.Input{b, y}) == F.NoEff{} : F.Eff}
