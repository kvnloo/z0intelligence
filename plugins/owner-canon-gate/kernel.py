"""Python twin of bend/owner_canon/canon.bend (same predicates, same argument order).

tests/test_owner_canon_gate.py checks it exhaustively against the truth table the Bend laws pin down
(bend/owner_canon/LAWS.bend; proofs Lean-checked by `bend PROOF.bend --verdict`)."""


def guard(trigger: bool, cleared: bool) -> bool:
    return True if not trigger else cleared


def gpt_ok(gpt, via_fx_pi):
    return guard(gpt, via_fx_pi)


def gui_ok(touches_gui, isolated):
    return guard(touches_gui, isolated)


def done_ok(claims_done, receipt):
    return guard(claims_done, receipt)


def quota_ok(limited, backed_off):
    return guard(limited, backed_off)


def delete_ok(deletes, creator_done):
    return guard(deletes, creator_done)


def public_ok(public, owner_ok):
    return guard(public, owner_ok)


def admits(g, gv, u, ui, d, dr, l, lb, x, xc, p, po):
    return gpt_ok(g, gv) and gui_ok(u, ui) and done_ok(d, dr) and quota_ok(l, lb) and delete_ok(x, xc) and public_ok(p, po)


RULES = {  # law name -> (canon ids, twin predicate)
    "gpt_only_via_fx_pi": ("C08,C39 + owner rule 2026-11-04", gpt_ok),
    "gui_never_steals_focus": ("C03", gui_ok),
    "done_needs_receipt": ("C06,C29", done_ok),
    "quota_backs_off": ("C15", quota_ok),
    "delete_needs_creator_receipt": ("C40,C25,C37", delete_ok),
    "public_needs_owner_ok": ("C31,C16", public_ok),
}
