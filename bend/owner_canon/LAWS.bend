# Owner canon laws (OWNER-CANON.md, locked 2026-11-05). PROOF.bend proves them; `bend PROOF.bend --verdict` checks
# them in Lean. Scope: theorems about canon.bend's decisions over host-established facts, not about whether the
# host classified a tool call correctly (that is measured by plugins/owner-canon-gate tests).
import Base
import ./canon.bend as K

def T(b: Bool) -> Data:
  match b:
    case True{}:
      Unit
    case False{}:
      Empty

# LAW: an admitted GPT call went through fx or pi
law gpt_only_via_fx_pi:
  for v: Bool
  for w: T(K.gpt_ok(True{}, v))
  T(v)

# LAW: admitted GUI work was on an isolated display
law gui_never_steals_focus:
  for i: Bool
  for w: T(K.gui_ok(True{}, i))
  T(i)

# LAW: an admitted done claim carries a receipt
law done_needs_receipt:
  for r: Bool
  for w: T(K.done_ok(True{}, r))
  T(r)

# LAW: an admitted call after a quota hit waited for the reset
law quota_backs_off:
  for b: Bool
  for w: T(K.quota_ok(True{}, b))
  T(b)

# LAW: an admitted deletion has a finished creator receipt
law delete_needs_creator_receipt:
  for c: Bool
  for w: T(K.delete_ok(True{}, c))
  T(c)

# LAW: an admitted public action has Kevin's OK
law public_needs_owner_ok:
  for o: Bool
  for w: T(K.public_ok(True{}, o))
  T(o)

# LAW: the composed gate keeps each rule (shown for the two highest-cost ones; the rest follow the same split)
law admits_keeps_public_ok:
  for g: Bool
  for gv: Bool
  for u: Bool
  for ui: Bool
  for d: Bool
  for dr: Bool
  for l: Bool
  for lb: Bool
  for x: Bool
  for xc: Bool
  for po: Bool
  for w: T(K.admits(g, gv, u, ui, d, dr, l, lb, x, xc, True{}, po))
  T(po)

law admits_keeps_gpt_route:
  for gv: Bool
  for u: Bool
  for ui: Bool
  for d: Bool
  for dr: Bool
  for l: Bool
  for lb: Bool
  for x: Bool
  for xc: Bool
  for p: Bool
  for po: Bool
  for w: T(K.admits(True{}, gv, u, ui, d, dr, l, lb, x, xc, p, po))
  T(gv)

# LAW: the gate is not vacuous; an ordinary call (no trigger) is admitted
law ordinary_call_admitted:
  {K.admits(False{}, False{}, False{}, False{}, False{}, False{}, False{}, False{}, False{}, False{}, False{}, False{}) == True{} : Bool}

# LAW: a dispatch admitted by the canon is quota-admitted by O14 (reuse of bend/model_routing) and GPT-clean
law route_needs_o14_quota:
  for c: Bool
  for q: Bool
  for s: Bool
  for o: Bool
  for r: Bool
  for g: Bool
  for gv: Bool
  for w: T(K.route_ok(c, q, s, o, r, g, gv))
  T(q)

law route_gpt_via_fx_pi:
  for c: Bool
  for q: Bool
  for s: Bool
  for o: Bool
  for r: Bool
  for gv: Bool
  for w: T(K.route_ok(c, q, s, o, r, True{}, gv))
  T(gv)
