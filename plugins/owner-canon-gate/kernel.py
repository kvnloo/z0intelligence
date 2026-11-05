"""Python twin of bend/owner_canon/canon.bend (same predicates, same argument order).

Scope: LOCKED owner-canon rules 1-15 only (Kevin's OK 2026-11-05). tests/test_owner_canon_gate.py checks the twin
exhaustively against the truth table the Bend laws pin down (bend/owner_canon/LAWS.bend, Lean-checked)."""


def guard(trigger: bool, cleared: bool) -> bool:
    return True if not trigger else cleared


def gui_ok(touches_gui, isolated):
    return guard(touches_gui, isolated)


def done_ok(claims_done, receipt):
    return guard(claims_done, receipt)


def quota_ok(limited, backed_off):
    return guard(limited, backed_off)


def admits(u, ui, d, dr, l, lb):
    return gui_ok(u, ui) and done_ok(d, dr) and quota_ok(l, lb)


RULES = {  # law name -> (locked canon rule, twin predicate)
    "gui_never_steals_focus": ("3", gui_ok),
    "done_needs_receipt": ("6", done_ok),
    "quota_backs_off": ("15", quota_ok),
}
