# Work-item lifecycle: the laws. The owner states them; PROOF.bend proves
# them; `bend PROOF.bend --verdict` (or `hermes bend verify`) is the check.
#
# Scope, stated plainly. These are theorems about fsm.bend's own `step`,
# `run`, `astep` and `board_step` over abstract heads (Nat) and actors. They
# do not say that the Python replay checker equals fsm.bend (that is
# measured by tests/test_fsm_bend_parity.py), that real GitHub or service
# events were mapped faithfully, or that any work item succeeded. A PASS is
# never an authorization, an AODL admission or verified_success.
#
# "Reachable" means: produced by `run(es, Proposed{})` for some event tape
# es, i.e. by legal and refused steps from a fresh proposal.

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

# Role predicates, written apart from fsm.bend's guards
def OwnerActor(a: F.Actor) -> Data:
  match a:
    case F.Actor{id, role}:
      match role:
        case F.Owner{}:
          Unit
        case _:
          Empty

def CiActor(a: F.Actor) -> Data:
  match a:
    case F.Actor{id, role}:
      match role:
        case F.Ci{}:
          Unit
        case _:
          Empty

# Opus or Sol (docs/roles-v0.md: the only lanes that write code)
def CoderActor(a: F.Actor) -> Data:
  match a:
    case F.Actor{id, role}:
      match role:
        case F.Opus{}:
          Unit
        case F.Sol{}:
          Unit
        case _:
          Empty

# deterministic code or the Opus orchestrator
def OperatorActor(a: F.Actor) -> Data:
  match a:
    case F.Actor{id, role}:
      match role:
        case F.Ci{}:
          Unit
        case F.Opus{}:
          Unit
        case _:
          Empty

def NotAuthor(a: F.Actor, au: F.Actor) -> Data:
  match a au:
    case F.Actor{i, r} F.Actor{j, q}:
      T(distinct(i, j))

# the receipt passed and was produced for exactly the head sha h
def Binds(r: F.Receipt, h: Nat) -> Type:
  match r:
    case F.Receipt{sha, pass}:
      {pass == True{} : Bool} & {sha == h : Nat}

def is_draft(x: F.Item) -> Bool:
  match x:
    case F.DraftPR{au, hd}:
      True{}
    case _:
      False{}

def is_integrated(x: F.Item) -> Bool:
  match x:
    case F.Integrated{au, hd, ig}:
      True{}
    case _:
      False{}

def is_accepted(x: F.Item) -> Bool:
  match x:
    case F.Accepted{au, hd, ig, ap}:
      True{}
    case _:
      False{}

# Law 1. Merged only after OwnerAccepted
# --------------------------------------
# A reachable merged item carries its acceptance, and that acceptance came
# from the owner, who is not the item's author. (Merged is entered only
# from Accepted, and only Accept by an owner builds Accepted.)
law merged_needs_owner_acceptance:
  for es: List<&2, F.Ev>
  for +au: F.Actor
  for +hd: Nat
  for +ig: Nat
  for +ap: F.Actor
  for h: {F.run(es, F.Proposed{}) == F.Merged{au, hd, ig, ap} : F.Item}
  OwnerActor(ap) & NotAuthor(ap, au)

# Law 2. No actor approves its own gate
# -------------------------------------
# Each gate that moves work forward on someone's say-so is passed only by
# an actor of the right role who is not the author: verification (Ci),
# integration (Ci or Opus) and acceptance (the owner).

def VerifiedByOther(e: F.Ev, au: F.Actor) -> Type:
  match e:
    case F.Verify{a, r}:
      CiActor(a) & NotAuthor(a, au)
    case _:
      Empty

law verify_gate_not_author:
  for +au: F.Actor
  for +hd: Nat
  for e: F.Ev
  for w: T(is_draft(F.step(F.Verifying{au, hd}, e)))
  VerifiedByOther(e, au)

def IntegratedByOther(e: F.Ev, au: F.Actor) -> Type:
  match e:
    case F.Integrate{a, ish, r}:
      OperatorActor(a) & NotAuthor(a, au)
    case _:
      Empty

law integrate_gate_not_author:
  for +au: F.Actor
  for +hd: Nat
  for e: F.Ev
  for w: T(is_integrated(F.step(F.DraftPR{au, hd}, e)))
  IntegratedByOther(e, au)

def AcceptedByOwner(e: F.Ev, au: F.Actor) -> Type:
  match e:
    case F.Accept{a}:
      OwnerActor(a) & NotAuthor(a, au)
    case _:
      Empty

law accept_gate_owner_not_author:
  for +au: F.Actor
  for +hd: Nat
  for +ig: Nat
  for +dp: Nat
  for e: F.Ev
  for w: T(is_accepted(F.step(F.Deployed{au, hd, ig, dp}, e)))
  AcceptedByOwner(e, au)

# Law 3. A draft PR needs a passing test receipt bound to the head SHA
# --------------------------------------------------------------------
# and integration needs a passing receipt bound to the integration head.

def ReceiptForHead(e: F.Ev, hd: Nat) -> Type:
  match e:
    case F.Verify{a, r}:
      Binds(r, hd)
    case _:
      Empty

law draft_needs_bound_receipt:
  for +au: F.Actor
  for +hd: Nat
  for e: F.Ev
  for w: T(is_draft(F.step(F.Verifying{au, hd}, e)))
  ReceiptForHead(e, hd)

def ReceiptForIntegration(e: F.Ev) -> Type:
  match e:
    case F.Integrate{a, ish, r}:
      Binds(r, ish)
    case _:
      Empty

law integration_needs_bound_receipt:
  for +au: F.Actor
  for +hd: Nat
  for e: F.Ev
  for w: T(is_integrated(F.step(F.DraftPR{au, hd}, e)))
  ReceiptForIntegration(e)

# Law 4. Deployed head == Integrated head (the stale-service bug)
# ---------------------------------------------------------------
law deployed_serves_integrated_head:
  for es: List<&2, F.Ev>
  for +au: F.Actor
  for +hd: Nat
  for +ig: Nat
  for +dp: Nat
  for h: {F.run(es, F.Proposed{}) == F.Deployed{au, hd, ig, dp} : F.Item}
  {dp == ig : Nat}

# ...and observing a service on any other head takes "deployed" away
law drift_undeploys:
  for +au: F.Actor
  for +hd: Nat
  for +ig: Nat
  for +dp: Nat
  for +served: Nat
  for w: T(distinct(served, ig))
  {F.step(F.Deployed{au, hd, ig, dp}, F.Observe{served}) == F.Integrated{au, hd, ig} : F.Item}

# Law 5. Interrupted work is never silently dropped
# -------------------------------------------------
# Interrupted is not a dead end: a coder can always resume it, keeping the
# pushed head, and the owner can always abandon it explicitly. No other
# event moves it, and none loses the head. (That it does not sit in
# Interrupted for long is checked on real data by the replay checker; the
# model has no clock.)

law interrupted_can_resume:
  for +au: F.Actor
  for +hd: Nat
  for +i: Nat
  {F.step(F.Interrupted{au, hd}, F.Resume{F.Actor{i, F.Sol{}}}) == F.Implementing{F.Actor{i, F.Sol{}}, hd} : F.Item}

law interrupted_can_abandon:
  for +au: F.Actor
  for +hd: Nat
  for +i: Nat
  {F.step(F.Interrupted{au, hd}, F.Abandon{F.Actor{i, F.Owner{}}}) == F.Abandoned{} : F.Item}

def InterruptExit(hd: Nat, x: F.Item) -> Type:
  match x:
    case F.Interrupted{au, h}:
      {h == hd : Nat}
    case F.Implementing{au, h}:
      {h == hd : Nat}
    case F.Abandoned{}:
      Unit
    case _:
      Empty

law interrupted_exits_keep_head:
  for +au: F.Actor
  for +hd: Nat
  for e: F.Ev
  InterruptExit(hd, F.step(F.Interrupted{au, hd}, e))

# Law 6. Free models (and every other non-coding role) never implement
# --------------------------------------------------------------------
law implementer_is_coder:
  for es: List<&2, F.Ev>
  for +au: F.Actor
  for +hd: Nat
  for h: {F.run(es, F.Proposed{}) == F.Implementing{au, hd} : F.Item}
  CoderActor(au)

# Law 7. The owner is a batched queue, never a blocker
# ----------------------------------------------------
# While Kevin is off, an owner action is held: the item stays where it is
# (in the "needs Kevin" queue).
law owner_off_holds:
  for s: F.Item
  for +e: F.Ev
  for w: T(F.owner_event(e))
  {F.astep(F.Off{}, s, e) == s : F.Item}

# Any event that does not need the owner moves its item exactly as `step`
# does, whatever Kevin's availability and whatever else is waiting on him.
law queue_never_blocks:
  for xs: List<&2, F.Item>
  for +i: Nat
  for +av: F.Avail
  for +e: F.Ev
  for w: T(Bool.not(F.owner_event(e)))
  {F.nth(F.board_step(xs, i, av, e), i) == F.step(F.nth(xs, i), e) : F.Item}

# An event for one item never changes another item.
law board_frame:
  for xs: List<&2, F.Item>
  for +i: Nat
  for +j: Nat
  for +av: F.Avail
  for +e: F.Ev
  for w: T(distinct(i, j))
  {F.nth(F.board_step(xs, i, av, e), j) == F.nth(xs, j) : F.Item}
