# Owner canon laws: LOCKED rules 1-15 of OWNER-CANON.md only (Kevin's OK 2026-11-05). PROOF.bend proves them;
# `bend PROOF.bend --verdict` checks them in Lean. Scope: theorems about canon.bend's decisions over host facts,
# not about whether the host classified a tool call correctly (plugins/owner-canon-gate tests measure that).
import Base
import ./canon.bend as K

def T(b: Bool) -> Data:
  match b:
    case True{}:
      Unit
    case False{}:
      Empty

# LAW (rule 3): admitted GUI work was on an isolated display
law gui_never_steals_focus:
  for i: Bool
  for w: T(K.gui_ok(True{}, i))
  T(i)

# LAW (rule 6): an admitted done claim carries a receipt
law done_needs_receipt:
  for r: Bool
  for w: T(K.done_ok(True{}, r))
  T(r)

# LAW (rule 15): an admitted call after a quota hit waited for the reset
law quota_backs_off:
  for b: Bool
  for w: T(K.quota_ok(True{}, b))
  T(b)

# LAW: the composed gate keeps each rule
law admits_keeps_gui:
  for ui: Bool
  for d: Bool
  for dr: Bool
  for l: Bool
  for lb: Bool
  for w: T(K.admits(True{}, ui, d, dr, l, lb))
  T(ui)

law admits_keeps_done:
  for u: Bool
  for ui: Bool
  for dr: Bool
  for l: Bool
  for lb: Bool
  for w: T(K.admits(u, ui, True{}, dr, l, lb))
  T(dr)

law admits_keeps_quota:
  for u: Bool
  for ui: Bool
  for d: Bool
  for dr: Bool
  for lb: Bool
  for w: T(K.admits(u, ui, d, dr, True{}, lb))
  T(lb)

# LAW: the gate is not vacuous; an ordinary call (no trigger) is admitted
law ordinary_call_admitted:
  {K.admits(False{}, False{}, False{}, False{}, False{}, False{}) == True{} : Bool}

# LAW (rules 8 + 15, reusing bend/model_routing): an admitted dispatch has measured capability and quota
law route_needs_capability:
  for c: Bool
  for q: Bool
  for s: Bool
  for o: Bool
  for r: Bool
  for w: T(K.route_ok(c, q, s, o, r))
  T(c)

law route_needs_quota:
  for c: Bool
  for q: Bool
  for s: Bool
  for o: Bool
  for r: Bool
  for w: T(K.route_ok(c, q, s, o, r))
  T(q)
