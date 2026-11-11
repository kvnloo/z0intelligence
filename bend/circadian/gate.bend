# Circadian notice gate: the finite core of z0int.human_posture.route and of
# dash shared/interaction-policy.ts route. Pure. LAWS.bend states what must
# hold; PROOF.bend proves it; main.bend prints the full truth table so the
# Python and TypeScript twins can be checked against it on every input.

import Base

type Phase is Data:
  Sleep{}
  Inertia{}
  Peak{}
  Dip{}
  Second{}
  WindDown{}
  PreSleep{}
  Disengaged{}

type Cls is Data:
  Critical{}
  Decision{}
  Report{}
  Digest{}

type Act is Data:
  Deliver{}
  Defer{}
  ToDigest{}

type Line is Data:
  Focus{}
  Updates{}
  Quiet{}

# WIND_DOWN, PRE_SLEEP, SLEEP and DISENGAGED: the owner is not asked
def quiet(p: Phase) -> Bool:
  match p:
    case Sleep{}:
      True{}
    case Inertia{}:
      False{}
    case Peak{}:
      False{}
    case Dip{}:
      False{}
    case Second{}:
      False{}
    case WindDown{}:
      True{}
    case PreSleep{}:
      True{}
    case Disengaged{}:
      True{}

def is_deliver(a: Act) -> Bool:
  match a:
    case Deliver{}:
      True{}
    case Defer{}:
      False{}
    case ToDigest{}:
      False{}

# the Android channel each phase uses (C14)
def channel(p: Phase) -> Line:
  match p:
    case Sleep{}:
      Quiet{}
    case Inertia{}:
      Updates{}
    case Peak{}:
      Focus{}
    case Dip{}:
      Updates{}
    case Second{}:
      Updates{}
    case WindDown{}:
      Quiet{}
    case PreSleep{}:
      Quiet{}
    case Disengaged{}:
      Quiet{}

def sounds(c: Line) -> Bool:
  match c:
    case Focus{}:
      True{}
    case Updates{}:
      False{}
    case Quiet{}:
      False{}

def when_cap(k: Bool) -> Act:
  match k:
    case True{}:
      Deliver{}
    case False{}:
      Defer{}

# a report (also a non-allowlisted critical, C5)
def report(p: Phase, k: Bool) -> Act:
  match p:
    case Sleep{}:
      ToDigest{}
    case Inertia{}:
      ToDigest{}
    case Peak{}:
      when_cap(k)
    case Dip{}:
      Defer{}
    case Second{}:
      Defer{}
    case WindDown{}:
      ToDigest{}
    case PreSleep{}:
      ToDigest{}
    case Disengaged{}:
      ToDigest{}

def second_decision(t: Bool, k: Bool) -> Act:
  match t:
    case True{}:
      when_cap(k)
    case False{}:
      Defer{}

def decision(p: Phase, t: Bool, k: Bool) -> Act:
  match p:
    case Sleep{}:
      ToDigest{}
    case Inertia{}:
      Defer{}
    case Peak{}:
      when_cap(k)
    case Dip{}:
      Defer{}
    case Second{}:
      second_decision(t, k)
    case WindDown{}:
      ToDigest{}
    case PreSleep{}:
      ToDigest{}
    case Disengaged{}:
      ToDigest{}

def digest(p: Phase, k: Bool) -> Act:
  match p:
    case Sleep{}:
      ToDigest{}
    case Inertia{}:
      Deliver{}
    case Peak{}:
      when_cap(k)
    case Dip{}:
      when_cap(k)
    case Second{}:
      when_cap(k)
    case WindDown{}:
      ToDigest{}
    case PreSleep{}:
      ToDigest{}
    case Disengaged{}:
      ToDigest{}

def crit_quiet(o: Bool) -> Act:
  match o:
    case True{}:
      Deliver{}
    case False{}:
      ToDigest{}

# an allowlisted, irreversible critical: delivered by day; at night only with opt-in (R2)
def crit_ok(p: Phase, o: Bool) -> Act:
  match p:
    case Sleep{}:
      crit_quiet(o)
    case Inertia{}:
      Deliver{}
    case Peak{}:
      Deliver{}
    case Dip{}:
      Deliver{}
    case Second{}:
      Deliver{}
    case WindDown{}:
      crit_quiet(o)
    case PreSleep{}:
      crit_quiet(o)
    case Disengaged{}:
      crit_quiet(o)

def crit_irr(i: Bool, p: Phase, o: Bool, k: Bool) -> Act:
  match i:
    case True{}:
      crit_ok(p, o)
    case False{}:
      report(p, k)

def critical(a: Bool, i: Bool, p: Phase, o: Bool, k: Bool) -> Act:
  match a:
    case True{}:
      crit_irr(i, p, o, k)
    case False{}:
      report(p, k)

# phase, class, allowlisted producer, irreversible, critical opt-in,
# tonight's plan, capacity left -> action
def gate(p: Phase, c: Cls, a: Bool, i: Bool, o: Bool, t: Bool, k: Bool) -> Act:
  match c:
    case Critical{}:
      critical(a, i, p, o, k)
    case Decision{}:
      decision(p, t, k)
    case Report{}:
      report(p, k)
    case Digest{}:
      digest(p, k)

def is_peak(p: Phase) -> Bool:
  match p:
    case Peak{}:
      True{}
    case _:
      False{}

def is_second(p: Phase) -> Bool:
  match p:
    case Second{}:
      True{}
    case _:
      False{}
