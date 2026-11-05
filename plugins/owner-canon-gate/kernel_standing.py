"""Python twin of bend/owner_standing/standing.bend (Kevin's explicit standing rules; same predicates, same order).
tests/test_owner_canon_gate.py checks it exhaustively against the truth table the Lean-checked laws pin down."""


def guard(trigger: bool, cleared: bool) -> bool:
    return True if not trigger else cleared


def gpt_ok(gpt, via_fx_pi):
    return guard(gpt, via_fx_pi)


def delete_ok(deletes, creator_finished_receipt):
    return guard(deletes, creator_finished_receipt)


def public_ok(public, owner_ok):
    return guard(public, owner_ok)


def admits(g, gv, x, xc, p, po):
    return gpt_ok(g, gv) and delete_ok(x, xc) and public_ok(p, po)


RULES = {  # law name -> (standing rule, twin predicate)
    "gpt_only_via_fx_pi": ("S1", gpt_ok),
    "delete_needs_creator_receipt": ("S2", delete_ok),
    "public_needs_owner_ok": ("S3", public_ok),
}
