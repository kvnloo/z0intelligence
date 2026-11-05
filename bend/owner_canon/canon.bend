# Owner canon: pure decision kernel over typed host facts.
# Spec: unified-memory/OWNER-CANON.md, LOCKED rules 1-15 only (Kevin's OK 2026-11-05); rules 16-60 are candidates
# and get no law. Text classification (does this command touch the GUI? is this a done claim? was this a 429?)
# lives at the host boundary (plugins/owner-canon-gate); the kernel only decides from host-established facts.
# A refusal here is a pure decision, not an approval. Rule 14 (enforce rules with deterministic checks) is this file.
import Base
import ../model_routing/routing.bend as R

# `trigger` happened only if `cleared` holds
def guard(trigger: Bool, cleared: Bool) -> Bool:
  match trigger:
    case False{}:
      True{}
    case True{}:
      cleared

# rule 3: GUI work only on a headless/virtual display, never Kevin's focus or cursor
def gui_ok(touches_gui: Bool, isolated: Bool) -> Bool:
  guard(touches_gui, isolated)

# rule 6: a done/fixed claim needs a verification receipt
def done_ok(claims_done: Bool, receipt: Bool) -> Bool:
  guard(claims_done, receipt)

# rule 15: after a quota/rate-limit hit, retry only once the reset or Retry-After has passed
def quota_ok(limited: Bool, backed_off: Bool) -> Bool:
  guard(limited, backed_off)

# one tool call: every locked rule must clear
def admits(u: Bool, ui: Bool, d: Bool, dr: Bool, l: Bool, lb: Bool) -> Bool:
  Bool.and(gui_ok(u, ui), Bool.and(done_ok(d, dr), quota_ok(l, lb)))

# rules 8 + 15: a model dispatch is admitted only with measured capability and quota (reuses bend/model_routing)
def route_ok(c: Bool, q: Bool, s: Bool, o: Bool, r: Bool) -> Bool:
  Bool.and(True{}, R.admits(c, q, s, o, r))
