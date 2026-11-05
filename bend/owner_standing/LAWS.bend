# Owner standing laws (Kevin's explicit rules; not canon). PROOF.bend proves them; `bend PROOF.bend --verdict` in Lean.
#
# Sources (verbatim):
#  S1 ayo memory, owner rule 2026-11-04: "GPT models (Sol, Astra, Terra, Luna) run only through fx (groot; T3 instance
#     fx-acp-local) or pi+SoL-Pi (MBP) with a short contract brief, never inside Hermes chat/delegate_task or T3 Codex"
#  S2,S3 goal constraint: "GPT only via fx; no money, public posts, credential handling or deletion ... without Kevin"
#  S2 Kevin 2026-11-04 23:25 CT (fleet-stage-lifecycle-20261104/AYO-APPROVAL.md): "just check which session created
#     the worktree and see if it finished thr work, then u can delete if necessary" => a delete is cleared by a
#     one-line receipt (path, size, creating session, why finished). That receipt is Kevin's standing approval.
import Base
import ./standing.bend as S

def T(b: Bool) -> Data:
  match b:
    case True{}:
      Unit
    case False{}:
      Empty

# LAW (S1): an admitted GPT dispatch went through fx/pi
law gpt_only_via_fx_pi:
  for v: Bool
  for w: T(S.gpt_ok(True{}, v))
  T(v)

# LAW (S2): an admitted delete has a finished-creator receipt
law delete_needs_creator_receipt:
  for c: Bool
  for w: T(S.delete_ok(True{}, c))
  T(c)

# LAW (S3): an admitted public action has Kevin's OK
law public_needs_owner_ok:
  for o: Bool
  for w: T(S.public_ok(True{}, o))
  T(o)

law admits_keeps_gpt:
  for gv: Bool
  for x: Bool
  for xc: Bool
  for p: Bool
  for po: Bool
  for w: T(S.admits(True{}, gv, x, xc, p, po))
  T(gv)

law admits_keeps_delete:
  for g: Bool
  for gv: Bool
  for xc: Bool
  for p: Bool
  for po: Bool
  for w: T(S.admits(g, gv, True{}, xc, p, po))
  T(xc)

law admits_keeps_public:
  for g: Bool
  for gv: Bool
  for x: Bool
  for xc: Bool
  for po: Bool
  for w: T(S.admits(g, gv, x, xc, True{}, po))
  T(po)

# LAW (S2, Kevin 23:25): the rule is satisfiable; a delete with a finished-creator receipt is admitted
law receipted_delete_admitted:
  {S.admits(False{}, False{}, True{}, True{}, False{}, False{}) == True{} : Bool}

# LAW: not vacuous; an ordinary call is admitted
law ordinary_call_admitted:
  {S.admits(False{}, False{}, False{}, False{}, False{}, False{}) == True{} : Bool}
