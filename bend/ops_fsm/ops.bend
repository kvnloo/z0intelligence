# Ops plane: the owner's operating rules as decision functions (the gates, as code).
#
# Today (2026-10-21) several operating rules lived only in markdown or prompts
# and were broken anyway. This file writes each rule as a small pure decision
# function or state machine; LAWS.bend states what each must guarantee and
# PROOF.bend proves it. src/z0int/fsm/ops.py is the same table in Python (the
# gates that real tools call: fleet-build, z0-guard, git hooks, the z0live
# broker, the install wrapper, the fleet health checker, supervise), kept equal
# by src/z0int/fsm/ops_parity.py and tests/test_fsm_ops.py.
#
# Every input here is already decided by the caller (a job class, whether a
# path is private-marked, a remote's visibility, ...). How a real command,
# path, config line or adb dump maps to these inputs is the Python side's
# heuristic and is not proven.
#
# Constructor names are global in Bend, so every one here carries a prefix.

import Base

# ------------------------------------------------------------------ O1
# Compute placement. Heavy classes run on a non-groot worker lane (the MBP or
# a phone); groot admits only gpu | bend | lean | service | agent-process |
# tiny (< 10 s). With no room the job waits; it never falls back to groot.

type Cls is Data:
  CTest{}
  CMutation{}
  CTypecheck{}
  CGradle{}
  CBundle{}
  CEval{}
  CShot{}
  CGpu{}
  CBend{}
  CLean{}
  CService{}
  CAgent{}
  CTiny{}

type Lane is Data:
  LGroot{}
  LMbp{}
  LPhone{}

type Place is Data:
  RunOn{l: Lane}
  Wait{}

def groot_class(c: Cls) -> Bool:
  match c:
    case CGpu{}:
      True{}
    case CBend{}:
      True{}
    case CLean{}:
      True{}
    case CService{}:
      True{}
    case CAgent{}:
      True{}
    case CTiny{}:
      True{}
    case _:
      False{}

# may lane l run a job of class c?
def admits(l: Lane, c: Cls) -> Bool:
  match l:
    case LGroot{}:
      groot_class(c)
    case _:
      True{}

def place_phone(p: Bool) -> Place:
  match p:
    case True{}:
      RunOn{LPhone{}}
    case False{}:
      Wait{}

# off groot: the MBP when it has room, else a phone with room, else wait
def place_off(m: Bool, p: Bool) -> Place:
  match m:
    case True{}:
      RunOn{LMbp{}}
    case False{}:
      place_phone(p)

def place_by(g: Bool, m: Bool, p: Bool) -> Place:
  match g:
    case True{}:
      RunOn{LGroot{}}
    case False{}:
      place_off(m, p)

# where a job of class c goes, given room on the MBP (m) and on a phone (p)
def place(c: Cls, m: Bool, p: Bool) -> Place:
  place_by(groot_class(c), m, p)

def runs_on_groot(x: Place) -> Bool:
  match x:
    case RunOn{l}:
      match l:
        case LGroot{}:
          True{}
        case _:
          False{}
    case Wait{}:
      False{}

# ------------------------------------------------------------------ O2
# Served worktree. A worktree holding a live-service lease accepts no edit,
# commit, checkout, stash or merge, except `merge --ff-only` inside the guarded
# rollout transaction (txn: the caller holds the rollout lease token), and that
# same transaction's automatic rollback to the revision it replaced (O3b).

type GitOp is Data:
  GEdit{}
  GCommit{}
  GCheckout{}
  GStash{}
  GMerge{}
  GMergeFF{}
  GRollback{}

def served_allows(op: GitOp, txn: Bool) -> Bool:
  match op:
    case GMergeFF{}:
      txn
    case GRollback{}:
      txn
    case _:
      False{}

def wt_allows(served: Bool, op: GitOp, txn: Bool) -> Bool:
  match served:
    case True{}:
      served_allows(op, txn)
    case False{}:
      True{}

# ------------------------------------------------------------------ O3 (+O2, O5)
# Device rollout. One transaction: precheck, then merge --ff-only, idle-checked
# restart, device launch check, and automatic rollback when the check fails.
#   clean    the candidate was built from a non-served worktree       (O3a)
#   ff       the merge is a fast-forward                             (O2)
#   gated    the PR is marked review-gated
#   receipt  an orchestrator review receipt exists                   (O3c)
#   call     a z0live call is live: O5 refuses device work, so the
#            launch check cannot run; the rollout is deferred        (O3 x O5)
#   idle     the service is idle (no request in flight)              (O2)
# check: the device launch check on the real runtime passed           (O3b)

type Req is Data:
  Req{clean: Bool, ff: Bool, gated: Bool, receipt: Bool, call: Bool, idle: Bool}

type Out is Data:
  ORefused{}
  ODeferred{}
  OPromoted{}
  ORolledBack{}

def review_ok(gated: Bool, receipt: Bool) -> Bool:
  Bool.or(Bool.not(gated), receipt)

def pre_ok(clean: Bool, ff: Bool, gated: Bool, receipt: Bool) -> Bool:
  Bool.and(clean, Bool.and(ff, review_ok(gated, receipt)))

def ready(call: Bool, idle: Bool) -> Bool:
  Bool.and(Bool.not(call), idle)

def finish(check: Bool) -> Out:
  match check:
    case True{}:
      OPromoted{}
    case False{}:
      ORolledBack{}

def go(r: Bool, check: Bool) -> Out:
  match r:
    case True{}:
      finish(check)
    case False{}:
      ODeferred{}

def decide(ok: Bool, r: Bool, check: Bool) -> Out:
  match ok:
    case True{}:
      go(r, check)
    case False{}:
      ORefused{}

def rollout(q: Req, check: Bool) -> Out:
  match q:
    case Req{clean, ff, gated, receipt, call, idle}:
      decide(pre_ok(clean, ff, gated, receipt), ready(call, idle), check)

def promoted(x: Out) -> Bool:
  match x:
    case OPromoted{}:
      True{}
    case _:
      False{}

# the transaction ran: it merged and restarted the service
def touched(x: Out) -> Bool:
  match x:
    case OPromoted{}:
      True{}
    case ORolledBack{}:
      True{}
    case _:
      False{}

# ------------------------------------------------------------------ O4
# Privacy boundary. A private-marked artifact is pushed only to a remote known
# to be private; an unknown visibility fails closed.

type Vis is Data:
  VPublic{}
  VPrivate{}
  VUnknown{}

def vis_private(v: Vis) -> Bool:
  match v:
    case VPrivate{}:
      True{}
    case _:
      False{}

def push_allows(private: Bool, v: Vis) -> Bool:
  match private:
    case True{}:
      vis_private(v)
    case False{}:
      True{}

# ------------------------------------------------------------------ O5
# Owner phone touch. A UI input needs an explicit owner phone-operation intent
# and the expected app in the foreground. Installing or restarting an app in
# the active call chain is refused while a z0live call is live.

type PAct is Data:
  PTap{intent: Bool, fg: Bool}
  PInstall{chain: Bool}
  PRestart{chain: Bool}

def phone_allows(a: PAct, call: Bool) -> Bool:
  match a:
    case PTap{intent, fg}:
      Bool.and(intent, fg)
    case PInstall{chain}:
      Bool.not(Bool.and(chain, call))
    case PRestart{chain}:
      Bool.not(Bool.and(chain, call))

# ------------------------------------------------------------------ O6
# Accessibility preservation. After an install, every accessibility service
# enabled before it is still enabled, unless the owner removed it.

def mem(+x: Nat, l: List<&2, Nat>) -> Bool:
  match l:
    case Nil{}:
      False{}
    case h <> t:
      Bool.or(Nat.is_eq(h, x), mem(x, t))

def a11y_ok(pre: List<&2, Nat>, +post: List<&2, Nat>, +rm: List<&2, Nat>) -> Bool:
  match pre:
    case Nil{}:
      True{}
    case +h <> t:
      Bool.and(Bool.or(mem(h, post), mem(h, rm)), a11y_ok(t, post, rm))

# ------------------------------------------------------------------ O7
# Provider routing. No role routes to a cancelled provider; visual/UX design
# roles never route to Sol.

def route_ok(cancelled: Bool, sol: Bool, design: Bool) -> Bool:
  Bool.and(Bool.not(cancelled), Bool.not(Bool.and(design, sol)))

# ------------------------------------------------------------------ O8
# Node health. A worker node unhealthy for longer than `lim` emits exactly one
# owner notification per unhealthy episode; a strike past the limit with no
# notification is the violation.

type NSt is Data:
  NHealthy{}
  NBad{since: Nat}
  NNotified{since: Nat}

type NEv is Data:
  NProbe{ok: Bool, now: Nat}

def late(now: Nat, base: Nat, lim: Nat) -> Bool:
  Nat.is_gt(now, Nat.add(base, lim))

def npick(ok: Bool, x: NSt, y: NSt) -> NSt:
  match ok:
    case True{}:
      x
    case False{}:
      y

def from_healthy(e: NEv) -> NSt:
  match e:
    case NProbe{ok, now}:
      npick(ok, NHealthy{}, NBad{now})

def from_bad(+s: Nat, e: NEv, lim: Nat) -> NSt:
  match e:
    case NProbe{ok, +now}:
      npick(ok, NHealthy{}, npick(late(now, s, lim), NNotified{s}, NBad{s}))

def from_notified(+s: Nat, e: NEv) -> NSt:
  match e:
    case NProbe{ok, now}:
      npick(ok, NHealthy{}, NNotified{s})

def nstep(s: NSt, e: NEv, lim: Nat) -> NSt:
  match s:
    case NHealthy{}:
      from_healthy(e)
    case NBad{since}:
      from_bad(since, e, lim)
    case NNotified{since}:
      from_notified(since, e)

def bad_eff(s: Nat, e: NEv, lim: Nat) -> Bool:
  match e:
    case NProbe{ok, now}:
      Bool.and(Bool.not(ok), late(now, s, lim))

# does this probe emit the owner notification?
def neff(s: NSt, e: NEv, lim: Nat) -> Bool:
  match s:
    case NBad{since}:
      bad_eff(since, e, lim)
    case _:
      False{}

def notified(s: NSt) -> Bool:
  match s:
    case NNotified{since}:
      True{}
    case _:
      False{}

# ------------------------------------------------------------------ O9
# Gateway restart. When the gateway hosting headless runs restarts, every
# dependent active run becomes Interrupted, never silently Failed, and leaves
# Interrupted only by a resume from its session or an explicit report.

type RSt is Data:
  RActive{gw: Nat}
  RInterrupted{gw: Nat}
  RFailed{}
  RDone{}
  RResumed{}
  RReported{}

type REv is Data:
  GwRestart{gw: Nat}
  RunError{}
  RunDone{}
  ResumeRun{session: Bool}
  ReportRun{}

def rpick(ok: Bool, x: RSt, y: RSt) -> RSt:
  match ok:
    case True{}:
      x
    case False{}:
      y

def ract(+g: Nat, e: REv) -> RSt:
  match e:
    case GwRestart{x}:
      rpick(Nat.is_eq(x, g), RInterrupted{g}, RActive{g})
    case RunError{}:
      RFailed{}
    case RunDone{}:
      RDone{}
    case _:
      RActive{g}

def rint(+g: Nat, e: REv) -> RSt:
  match e:
    case ResumeRun{session}:
      rpick(session, RResumed{}, RInterrupted{g})
    case ReportRun{}:
      RReported{}
    case _:
      RInterrupted{g}

def rstep(s: RSt, e: REv) -> RSt:
  match s:
    case RActive{g}:
      ract(g, e)
    case RInterrupted{g}:
      rint(g, e)
    case RFailed{}:
      RFailed{}
    case RDone{}:
      RDone{}
    case RResumed{}:
      RResumed{}
    case RReported{}:
      RReported{}

def rfailed(s: RSt) -> Bool:
  match s:
    case RFailed{}:
      True{}
    case _:
      False{}

# ------------------------------------------------------------------ O10
# Android owns phone resources. These four booleans represent arbitrary
# agent-chosen resource checks; none may affect admission or placement.
# Reachability, explicit owner pause/call and capability checks form ready.
def phone_admits(ready: Bool, -memory: Bool, -cpu: Bool, -battery: Bool, -thermal: Bool) -> Bool:
  ready

def phone_place(ready: Bool, memory: Bool, cpu: Bool, battery: Bool, thermal: Bool) -> Place:
  place_phone(phone_admits(ready, memory, cpu, battery, thermal))

# ------------------------------------------------------------------ O11/O12
# One policy cell is selected for the decision family. Natural identifiers name
# families and complete arm identities (device/model/quant/engine/backend).
type PolicyStage is Data:
  StageNone{}
  StageShadow{}
  StageCanary{}
  StageLive{}

type PolicyRoute is Data:
  FrontierRoute{}
  OffloadRoute{arm: Nat}

def policy_credited(stage: PolicyStage) -> Bool:
  match stage:
    case StageNone{}:
      False{}
    case StageShadow{}:
      False{}
    case StageCanary{}:
      True{}
    case StageLive{}:
      True{}

def policy_local(family: Nat, named_family: Nat, stage: PolicyStage, named_arm: Nat, arm: Nat) -> Bool:
  Bool.and(Nat.is_eq(family, named_family), Bool.and(policy_credited(stage), Nat.is_eq(arm, named_arm)))

def policy_frontier(stage: PolicyStage, healthy: Bool, abstain: Bool) -> Bool:
  match stage:
    case StageLive{}:
      Bool.or(Bool.not(healthy), abstain)
    case _:
      True{}

def policy_allows(+family: Nat, +named_family: Nat, stage: PolicyStage, named_arm: Nat, route: PolicyRoute, healthy: Bool, abstain: Bool) -> Bool:
  match route:
    case FrontierRoute{}:
      Bool.or(Bool.not(Nat.is_eq(family, named_family)), policy_frontier(stage, healthy, abstain))
    case OffloadRoute{arm}:
      policy_local(family, named_family, stage, named_arm, arm)

# ------------------------------------------------------------------ O13
# Scope identifiers encode the exact model/quant/engine/backend/device tuple.
# `credited` means valid, unexpired compiled ChoiceLadder evidence; never ledger
# rows or a model's self-report. Absent credit, no request is authorized.
def option_limit_by(ok: Bool, measured: Nat) -> Nat:
  match ok:
    case True{}:
      measured
    case False{}:
      0n

def option_limit(credited: Bool, actual_scope: Nat, named_scope: Nat, measured: Nat) -> Nat:
  option_limit_by(Bool.and(credited, Nat.is_eq(actual_scope, named_scope)), measured)

def option_capacity_allows(credited: Bool, actual_scope: Nat, named_scope: Nat, measured: Nat, count: Nat) -> Bool:
  Bool.not(Nat.is_gt(count, option_limit(credited, actual_scope, named_scope, measured)))

# Risk IDs: read=0, mutating_reversible=1; destructive and unknown fail closed.
def option_risk_allows(risk: Nat) -> Bool:
  Nat.is_gt(2n, risk)

def risk_option_capacity_allows(risk: Nat, credited: Bool, actual_scope: Nat, named_scope: Nat, measured: Nat, count: Nat) -> Bool:
  Bool.and(option_risk_allows(risk), option_capacity_allows(credited, actual_scope, named_scope, measured, count))


# ------------------------------------------------------------------ O16
# Guarded SQLite lifecycle. Facts come from a complete /proc fd census; a
# point-in-time census is NOT a cross-process lock or mutation authorization.
def sqlite_replace_allows(complete: Bool, empty: Bool) -> Bool:
  Bool.and(complete, empty)

def sqlite_holders_ok(complete: Bool, ghosts: Bool) -> Bool:
  Bool.and(complete, Bool.not(ghosts))

def sqlite_auth_ok(known: Bool, before: Nat, after: Nat) -> Bool:
  Bool.and(known, Bool.not(Nat.is_gt(before, after)))

def sqlite_sidecar_allows(current: Bool) -> Bool:
  current


# ------------------------------------------------------------------ O17
# GitHub access. One account's 5000/h core budget serves every agent, so a
# background poller (a timer-started unit) must read REST through ghcache; it
# never falls back to api.github.com when the cache is down. Interactive
# reads may go direct. A repo under feedback backoff (a maintainer objected to
# how we contribute) refuses our public writes until a lesson is recorded.
# Who is a poller, and whether a call is cached, are host facts (Python side).
def gh_read_allows(cached: Bool, poller: Bool) -> Bool:
  Bool.or(cached, Bool.not(poller))

def gh_write_allows(backoff: Bool) -> Bool:
  Bool.not(backoff)


# >>> O18 privacy route (generated by gen_o18.py)
# ------------------------------------------------------------------ O18
# Privacy-tier model routing. The owner's tier table (sft-svlm/data/privacy
# README, pol-1.2, approved 2026-10-20): public data may go anywhere; personal
# (T1) data to local, his paid subscriptions (Claude/ChatGPT/Codex/Grok) or the
# approved free lane (Cerebras), never to a free lane that may train on it;
# sensitive (T2), third-party (T3) and unclassified data stay local; secrets
# (T4) go to no model. Free lanes take only simple, non-agentic work. Agentic
# T0/T1 work runs on a paid lane or waits; it never falls back to the weak
# local model. Which lanes are up, and what tier a request carries, are host
# facts (Python side: router /v1/models, path and content classifiers).
type PTier is Data:
  PtPublic{}
  PtPersonal{}
  PtUnknown{}
  PtSensitive{}
  PtThird{}
  PtSecret{}

type PLane is Data:
  PlLocal{}
  PlPaid{}
  PlFreeOk{}
  PlFreePub{}

type PTask is Data:
  PkAgentic{}
  PkSimple{}

type PRoute is Data:
  PrUse{l: PLane}
  PrQueue{}
  PrRefuse{}

def tier_rank(t: PTier) -> Nat:
  match t:
    case PtPublic{}:
      0n
    case PtPersonal{}:
      1n
    case PtUnknown{}:
      2n
    case PtSensitive{}:
      3n
    case PtThird{}:
      4n
    case PtSecret{}:
      5n

def join_by(gt: Bool, a: PTier, b: PTier) -> PTier:
  match gt:
    case True{}:
      b
    case False{}:
      a

# taint: a request carries the more restrictive of two tiers
def tier_join(+a: PTier, +b: PTier) -> PTier:
  join_by(Nat.is_gt(tier_rank(b), tier_rank(a)), a, b)

def personal_allows(l: PLane) -> Bool:
  match l:
    case PlFreePub{}:
      False{}
    case _:
      True{}

def local_lane(l: PLane) -> Bool:
  match l:
    case PlLocal{}:
      True{}
    case _:
      False{}

def tier_allows(t: PTier, l: PLane) -> Bool:
  match t:
    case PtPublic{}:
      True{}
    case PtPersonal{}:
      personal_allows(l)
    case PtSecret{}:
      False{}
    case _:
      local_lane(l)

def task_fits(k: PTask, l: PLane) -> Bool:
  match k:
    case PkSimple{}:
      True{}
    case PkAgentic{}:
      match l:
        case PlPaid{}:
          True{}
        case PlLocal{}:
          True{}
        case _:
          False{}

def lane_ok(t: PTier, k: PTask, +l: PLane, up: Bool) -> Bool:
  Bool.and(up, Bool.and(tier_allows(t, l), task_fits(k, l)))

def pick(ok: Bool, l: PLane, rest: PRoute) -> PRoute:
  match ok:
    case True{}:
      PrUse{l}
    case False{}:
      rest

# agentic: the paid lane when the tier allows it, else local; never the other
def agentic_by(paid_ok: Bool, +t: PTier, loc: Bool, paid: Bool) -> PRoute:
  match paid_ok:
    case True{}:
      pick(lane_ok(t, PkAgentic{}, PlPaid{}, paid), PlPaid{}, PrQueue{})
    case False{}:
      pick(lane_ok(t, PkAgentic{}, PlLocal{}, loc), PlLocal{}, PrQueue{})

# simple: approved free, public free, local, paid; first allowed lane that is up
def route_simple(+t: PTier, loc: Bool, paid: Bool, fok: Bool, fpub: Bool) -> PRoute:
  pick(lane_ok(t, PkSimple{}, PlFreeOk{}, fok), PlFreeOk{},
    pick(lane_ok(t, PkSimple{}, PlFreePub{}, fpub), PlFreePub{},
      pick(lane_ok(t, PkSimple{}, PlLocal{}, loc), PlLocal{},
        pick(lane_ok(t, PkSimple{}, PlPaid{}, paid), PlPaid{}, PrQueue{}))))

def route_open(+t: PTier, k: PTask, loc: Bool, paid: Bool, fok: Bool, fpub: Bool) -> PRoute:
  match k:
    case PkAgentic{}:
      agentic_by(tier_allows(t, PlPaid{}), t, loc, paid)
    case PkSimple{}:
      route_simple(t, loc, paid, fok, fpub)

# where a request of tier t and task k goes, given which lanes are up
def route(+t: PTier, k: PTask, loc: Bool, paid: Bool, fok: Bool, fpub: Bool) -> PRoute:
  match t:
    case PtSecret{}:
      PrRefuse{}
    case _:
      route_open(t, k, loc, paid, fok, fpub)
# <<< O18 privacy route
