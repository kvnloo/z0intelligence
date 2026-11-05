# Owner canon: pure decision kernel over typed host facts.
# Spec: unified-memory/OWNER-CANON.md (locked 2026-11-05). Text classification (is this a GPT model? does this
# command touch the GUI? is this a public post?) lives at the host boundary (plugins/owner-canon-gate); the
# kernel only decides from the facts the host establishes. A refusal here is a pure decision, not an approval.
import Base
import ../model_routing/routing.bend as R

# `trigger` happened only if `cleared` holds
def guard(trigger: Bool, cleared: Bool) -> Bool:
  match trigger:
    case False{}:
      True{}
    case True{}:
      cleared

# owner rule 2026-11-04 (canon 8/39): GPT models run only through fx or pi, never inside Hermes
def gpt_ok(gpt: Bool, via_fx_pi: Bool) -> Bool:
  guard(gpt, via_fx_pi)

# canon 3: GUI work only on a headless/virtual display, never Kevin's focus or cursor
def gui_ok(touches_gui: Bool, isolated: Bool) -> Bool:
  guard(touches_gui, isolated)

# canon 6: a done/fixed claim needs a verification receipt
def done_ok(claims_done: Bool, receipt: Bool) -> Bool:
  guard(claims_done, receipt)

# canon 15: after a quota/rate-limit hit, retry only once the reset or Retry-After has passed
def quota_ok(limited: Bool, backed_off: Bool) -> Bool:
  guard(limited, backed_off)

# canon 40/25: delete only what a finished creator receipt says is expendable
def delete_ok(deletes: Bool, creator_done: Bool) -> Bool:
  guard(deletes, creator_done)

# canon 31/16: public posts, PRs to upstream and publishing need Kevin's explicit OK
def public_ok(public: Bool, owner_ok: Bool) -> Bool:
  guard(public, owner_ok)

# one tool call: every rule must clear
def admits(g: Bool, gv: Bool, u: Bool, ui: Bool, d: Bool, dr: Bool, l: Bool, lb: Bool, x: Bool, xc: Bool, p: Bool, po: Bool) -> Bool:
  Bool.and(gpt_ok(g, gv), Bool.and(gui_ok(u, ui), Bool.and(done_ok(d, dr), Bool.and(quota_ok(l, lb), Bool.and(delete_ok(x, xc), public_ok(p, po))))))

# model dispatch reuses O14 admission (bend/model_routing) and adds the GPT transport rule
def route_ok(c: Bool, q: Bool, s: Bool, o: Bool, r: Bool, g: Bool, gv: Bool) -> Bool:
  Bool.and(gpt_ok(g, gv), R.admits(c, q, s, o, r))
