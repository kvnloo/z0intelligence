# AODL structural gate: the laws. A human states them; PROOF.bend proves
# them; `bend PROOF.bend` (and `--verdict`) is the check.
#
# Scope, stated plainly. These laws are theorems about the Bend kernel's
# own decision functions over the Core IR. They are NOT claims that the
# kernel agrees with the canonical Python validator (that is measured, by
# benchmarks/bend_gate_parity.py), that the host encoder is faithful (also
# measured), or that an admitted contract or spawn succeeds in the world.

import Base
import ./core.bend as C
import ./rules.bend as R
import ./transition.bend as X
import ./gate.bend as G

# Boolean reflection: T(b) is Unit when b holds, Empty when it does not
def T(b: Bool) -> Data:
  match b:
    case True{}:
      Unit
    case False{}:
      Empty

def is_allow(v: G.Verdict) -> Bool:
  match v:
    case G.Allow{}:
      True{}
    case G.Deny{cs}:
      False{}

# x occurs in xs (by Nat equality, not by a Bool check)
def Elem(-x: Nat, xs: List<&2, Nat>) -> Type:
  match xs:
    case Nil{}:
      Empty
    case h <> t:
      Or({x == h : Nat}, Elem(x, t))

# Transition gate (z0intelligence#13 spawn check)
# -----------------------------------------------
# A spawn is written out field by field: contract revision cr, request
# revision rr, dynamic flag dy, bounds mc/md, live children ks, parent
# depth dp, budget dimensions ds, parent ceiling cl, requested caps rq.

# LAW: an admitted spawn used exactly the frozen contract revision
law spawn_revision_pinned:
  for +cr: Nat
  for +rr: Nat
  for +dy: Bool
  for +mc: Nat
  for +md: Nat
  for +ks: Nat
  for +dp: Nat
  for +ds: List<&2, X.Dim>
  for +cl: List<&2, Nat>
  for +rq: List<&2, Nat>
  for w: T(X.allows(X.Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}))
  {cr == rr : Nat}

# LAW: an admitted spawn needs dynamic spawning declared allowed
law spawn_needs_dynamic:
  for +cr: Nat
  for +rr: Nat
  for +dy: Bool
  for +mc: Nat
  for +md: Nat
  for +ks: Nat
  for +dp: Nat
  for +ds: List<&2, X.Dim>
  for +cl: List<&2, Nat>
  for +rq: List<&2, Nat>
  for w: T(X.allows(X.Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}))
  {dy == True{} : Bool}

# LAW: an admitted spawn keeps the parent within maxChildren and maxDepth
law spawn_within_bounds:
  for +cr: Nat
  for +rr: Nat
  for +dy: Bool
  for +mc: Nat
  for +md: Nat
  for +ks: Nat
  for +dp: Nat
  for +ds: List<&2, X.Dim>
  for +cl: List<&2, Nat>
  for +rq: List<&2, Nat>
  for w: T(X.allows(X.Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}))
  T(Nat.is_le(1n+ks, mc)) & T(Nat.is_le(1n+dp, md))

# the budget property, stated apart from the gate's own checks: every
# declared dimension's observed plus proposed spend fits its limit, in
# exact Nat arithmetic (no wrap-around, no float tolerance)
def DimFits(d: X.Dim) -> Type:
  match d:
    case X.Dim{has, limit, seen, prop}:
      match has:
        case False{}:
          Unit
        case True{}:
          T(Nat.is_le(Nat.add(seen, prop), limit))

def AllFit(ds: List<&2, X.Dim>) -> Type:
  match ds:
    case Nil{}:
      Unit
    case d <> t:
      DimFits(d) & AllFit(t)

# LAW: an admitted spawn cannot exceed any declared Gamma budget
law spawn_within_budget:
  for +cr: Nat
  for +rr: Nat
  for +dy: Bool
  for +mc: Nat
  for +md: Nat
  for +ks: Nat
  for +dp: Nat
  for +ds: List<&2, X.Dim>
  for +cl: List<&2, Nat>
  for +rq: List<&2, Nat>
  for w: T(X.allows(X.Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}))
  AllFit(ds)

# LAW: no invented authority: every capability an admitted spawn asks for
# is literally on the parent's authority ceiling
law spawn_no_invented_authority:
  for +cr: Nat
  for +rr: Nat
  for +dy: Bool
  for +mc: Nat
  for +md: Nat
  for +ks: Nat
  for +dp: Nat
  for +ds: List<&2, X.Dim>
  for +cl: List<&2, Nat>
  for +rq: List<&2, Nat>
  for w: T(X.allows(X.Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}))
  for -x: Nat
  for m: Elem(x, rq)
  Elem(x, cl)

# LAW: spend is antitone: if a larger proposal fits a dimension, every
# smaller one fits too (a denial can never be cured by asking for more)
law spend_antitone:
  for +limit: Nat
  for +seen: Nat
  for +p: Nat
  for +q: Nat
  for le: T(Nat.is_le(p, q))
  for w: T(Nat.is_le(Nat.add(seen, q), limit))
  T(Nat.is_le(Nat.add(seen, p), limit))

# Documents
# ---------

# no edge of es grants a privileged capability (payment, wallet, sudo...,
# compared as str(v).lower(), exactly as validator._privileged)
def NoPrivGrant(+tab: List<&2, C.Val>, es: List<&2, C.Edge>) -> Type:
  match es:
    case Nil{}:
      Unit
    case C.Edge{i, r, a, b, ap, bp, k, g} <> t:
      T(Bool.not(R.any_lower(tab, g, R.PRIVILEGED()))) & NoPrivGrant(tab, t)

# LAW: forbidden effect: a document the gate admits grants no privileged
# capability on any intent edge, whatever its endpoints, ports or policies
law doc_no_privileged_grant:
  for +tab: List<&2, C.Val>
  for +ns: List<&2, C.Node>
  for +es: List<&2, C.Edge>
  for +pol: C.Pol
  for +b: Bool
  for +evs: List<&2, U32>
  for +obs: Maybe<&2, C.Graph>
  for w: T(List.is_empty(&2, U32, R.doc_issues(tab, C.Doc{C.Graph{ns, es}, pol, b, evs, obs})))
  NoPrivGrant(tab, es)

# Requests
# --------

def is_some.doc(m: Maybe<&2, C.Req>) -> Bool:
  match m:
    case None{}:
      False{}
    case Some{q}:
      True{}

def is_some.tr(m: Maybe<&2, X.Tr>) -> Bool:
  match m:
    case None{}:
      False{}
    case Some{t}:
      True{}

# the request is well formed: a known mode whose payload parses in full
def parses.mode(n: Nat, t: List<&2, U32>) -> Bool:
  match n:
    case 1n:
      is_some.doc(C.parse(t))
    case 2n:
      is_some.tr(X.parse(t))
    case _:
      False{}

def parses(ts: List<&2, U32>) -> Bool:
  match ts:
    case Nil{}:
      False{}
    case h <> t:
      parses.mode(U32.to_nat(h), t)

# LAW: fail closed: the gate never admits a request it could not parse
law allow_needs_parse:
  for ts: List<&2, U32>
  for w: T(is_allow(G.decide(ts)))
  T(parses(ts))
