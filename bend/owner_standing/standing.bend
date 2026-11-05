# Owner standing rules: pure decision kernel over typed host facts. Separate from bend/owner_canon (canon 1-15).
# These are Kevin's explicit standing rules, not canon adoptions (ayo 2026-11-05: "my top-15 instruction was about
# canon adoption, not about dropping Kevin's explicit standing rules"). Sources are quoted in LAWS.bend.
# Text classification (is this a GPT dispatch, a delete, a public post? is there a finished-creator receipt?) lives
# at the host boundary (plugins/owner-canon-gate). A refusal here is a pure decision, not an approval.
import Base

def guard(trigger: Bool, cleared: Bool) -> Bool:
  match trigger:
    case False{}:
      True{}
    case True{}:
      cleared

# S1: GPT models run only through fx or pi
def gpt_ok(gpt: Bool, via_fx_pi: Bool) -> Bool:
  guard(gpt, via_fx_pi)

# S2: deletion only with Kevin's rule satisfied: the creating session/job finished, with a one-line receipt
def delete_ok(deletes: Bool, creator_finished_receipt: Bool) -> Bool:
  guard(deletes, creator_finished_receipt)

# S3: public posts / upstream PRs / publishing only with Kevin's OK
def public_ok(public: Bool, owner_ok: Bool) -> Bool:
  guard(public, owner_ok)

def admits(g: Bool, gv: Bool, x: Bool, xc: Bool, p: Bool, po: Bool) -> Bool:
  Bool.and(gpt_ok(g, gv), Bool.and(delete_ok(x, xc), public_ok(p, po)))
