# Constructive proofs of the intent decision obligations.
import Base
import ./intent.bend as I
import ./LAWS.bend as Laws

def and_l(+a: Bool, -b: Bool, w: Laws.T(Bool.and(a, b))) -> Laws.T(a):
  match a:
    case True{}:
      Unit{}
    case False{}:
      Empty.absurd(Laws.T(False{}), w)

def and_r(a: Bool, -b: Bool, w: Laws.T(Bool.and(a, b))) -> Laws.T(b):
  match a:
    case True{}:
      w
    case False{}:
      Empty.absurd(Laws.T(b), w)

def eq_sound(a: Nat, b: Nat, w: Laws.T(Nat.is_eq(a, b))) -> {a == b : Nat}:
  match a b:
    case 0n 0n:
      {==}
    case 0n 1n+q:
      Empty.absurd({0n == 1n+q : Nat}, w)
    case 1n+p 0n:
      Empty.absurd({1n+p == 0n : Nat}, w)
    case 1n+p 1n+q:
      %eq_sound(p, q, w) : {1n+p == 1n+_ : Nat}
      {==}

# Four-way conjunction projections keep each receipt condition independent.
def first(a: Bool, b: Bool, c: Bool, d: Bool, w: Laws.T(Bool.and(a, Bool.and(b, Bool.and(c, d))))) -> Laws.T(a):
  and_l(a, Bool.and(b, Bool.and(c, d)), w)

def second(a: Bool, +b: Bool, +c: Bool, +d: Bool, w: Laws.T(Bool.and(a, Bool.and(b, Bool.and(c, d))))) -> Laws.T(b):
  and_l(b, Bool.and(c, d), and_r(a, Bool.and(b, Bool.and(c, d)), w))

def third(a: Bool, +b: Bool, +c: Bool, +d: Bool, w: Laws.T(Bool.and(a, Bool.and(b, Bool.and(c, d))))) -> Laws.T(c):
  and_l(c, d, and_r(b, Bool.and(c, d), and_r(a, Bool.and(b, Bool.and(c, d)), w)))

def fourth(a: Bool, +b: Bool, +c: Bool, +d: Bool, w: Laws.T(Bool.and(a, Bool.and(b, Bool.and(c, d))))) -> Laws.T(d):
  and_r(c, d, and_r(b, Bool.and(c, d), and_r(a, Bool.and(b, Bool.and(c, d)), w)))

def Laws.i0_mutation_requires_approval(approved, w):
  w

def Laws.i0_qa_without_approval():
  {==}

def Laws.i0_approved_mutation():
  {==}

def Laws.i1_open_blocks_end(reason, live, verified):
  {==}

def Laws.i1_drop_needs_reason(reason, live, verified, w):
  w

def Laws.i1_delegation_must_be_live(reason, live, verified, w):
  w

def Laws.i1_verified_needs_receipt(reason, live, verified, w):
  w

def Laws.i1_work_may_continue(status, reason, live, verified):
  {==}

def Laws.i2_needs_verified_update(updated, marked, passed, tool, w):
  first(updated, marked, passed, tool, w)

def Laws.i2_needs_verified_mark(updated, marked, passed, tool, w):
  second(updated, marked, passed, tool, w)

def Laws.i2_verifier_actually_passed(updated, marked, passed, tool, w):
  third(updated, marked, passed, tool, w)

def Laws.i2_tool_run_succeeded(updated, marked, passed, tool, w):
  fourth(updated, marked, passed, tool, w)

def Laws.i2_verifier_after_ask(asked, changed, start, finish, recorded, w):
  +start = start
  +finish = finish
  first(Nat.is_gt(start, asked), Bool.not(Nat.is_gt(changed, start)), Bool.not(Nat.is_gt(start, finish)), Bool.not(Nat.is_gt(finish, recorded)), w)

def Laws.i2_verifier_after_update(asked, changed, start, finish, recorded, w):
  +start = start
  +finish = finish
  second(Nat.is_gt(start, asked), Bool.not(Nat.is_gt(changed, start)), Bool.not(Nat.is_gt(start, finish)), Bool.not(Nat.is_gt(finish, recorded)), w)

def Laws.i2_verifier_finishes_after_start(asked, changed, start, finish, recorded, w):
  +start = start
  +finish = finish
  third(Nat.is_gt(start, asked), Bool.not(Nat.is_gt(changed, start)), Bool.not(Nat.is_gt(start, finish)), Bool.not(Nat.is_gt(finish, recorded)), w)

def Laws.i2_update_after_verifier(asked, changed, start, finish, recorded, w):
  +start = start
  +finish = finish
  fourth(Nat.is_gt(start, asked), Bool.not(Nat.is_gt(changed, start)), Bool.not(Nat.is_gt(start, finish)), Bool.not(Nat.is_gt(finish, recorded)), w)

def Laws.i2_delegate_result_received(received, result, start, w):
  and_l(received, Bool.not(Nat.is_gt(result, start)), w)

def Laws.i2_delegate_result_before_verifier(received, result, start, w):
  and_r(received, Bool.not(Nat.is_gt(result, start)), w)

def Laws.i2_claim_requires_proof(proof, fresh, delegate, identity, w):
  first(proof, fresh, delegate, identity, w)

def Laws.i2_claim_requires_freshness(proof, fresh, delegate, identity, w):
  second(proof, fresh, delegate, identity, w)

def Laws.i2_claim_requires_delegate_result(proof, fresh, delegate, identity, w):
  third(proof, fresh, delegate, identity, w)

def Laws.i3_verifier_target_exact(task, verifier, result, acceptance, used, w):
  +task = task
  +verifier = verifier
  eq_sound(task, verifier, and_l(Nat.is_eq(task, verifier), Bool.and(Nat.is_eq(task, result), Nat.is_eq(acceptance, used)), w))

def Laws.i3_result_target_exact(task, verifier, result, acceptance, used, w):
  +task = task
  +result = result
  +acceptance = acceptance
  +used = used
  eq_sound(task, result, and_l(Nat.is_eq(task, result), Nat.is_eq(acceptance, used), and_r(Nat.is_eq(task, verifier), Bool.and(Nat.is_eq(task, result), Nat.is_eq(acceptance, used)), w)))

def Laws.i3_acceptance_verifier_exact(task, verifier, result, acceptance, used, w):
  +task = task
  +result = result
  +acceptance = acceptance
  +used = used
  eq_sound(acceptance, used, and_r(Nat.is_eq(task, result), Nat.is_eq(acceptance, used), and_r(Nat.is_eq(task, verifier), Bool.and(Nat.is_eq(task, result), Nat.is_eq(acceptance, used)), w)))

def Laws.i3_claim_requires_identity(proof, fresh, delegate, identity, w):
  fourth(proof, fresh, delegate, identity, w)

def carry_x(c: Bool, +h: Nat, -t: List<&2, Nat>, +carried: List<&2, Nat>, +x: Nat, e: Laws.T(c) -> {h == x : Nat}, k: Laws.T(I.mem(h, carried)), rest: Laws.T(I.mem(x, t)) -> Laws.T(I.mem(x, carried)), m: Laws.T(Bool.or(c, I.mem(x, t)))) -> Laws.T(I.mem(x, carried)):
  match c:
    case True{}:
      %e(Unit{}) : Laws.T(I.mem(_, carried))
      k
    case False{}:
      rest(m)

def carry_all(forwarded: List<&2, Nat>, +carried: List<&2, Nat>, +x: Nat, w: Laws.T(I.relay_allows(forwarded, carried)), m: Laws.T(I.mem(x, forwarded))) -> Laws.T(I.mem(x, carried)):
  match forwarded:
    case Nil{}:
      Empty.absurd(Laws.T(I.mem(x, carried)), m)
    case +h <> +t:
      +w2 = w
      +head = and_l(I.mem(h, carried), I.relay_allows(t, carried), w2)
      +tail = and_r(I.mem(h, carried), I.relay_allows(t, carried), w2)
      carry_x(Nat.is_eq(h, x), h, t, carried, x, z => eq_sound(h, x, z), head, z => carry_all(t, carried, x, tail, z), m)

def Laws.i4_forwarded_ids_are_carried(forwarded, carried, x, w, m):
  carry_all(forwarded, carried, x, w, m)

def Laws.i4_empty_forward_is_allowed(carried):
  {==}

def Laws.i4_missing_id_is_rejected():
  {==}

def Laws.i1_reasoned_drop_allowed():
  {==}

def Laws.i1_live_delegation_allowed():
  {==}

def Laws.i1_verified_end_allowed():
  {==}

def Laws.i2_successful_proof_allowed():
  {==}

def Laws.i2_equal_execution_boundary_allowed():
  {==}

def Laws.i2_valid_claim_allowed():
  {==}

def Laws.i3_identical_target_allowed():
  {==}

def Laws.i4_carried_superset_allowed():
  {==}

def Laws.i2_execution_at_ask_rejected():
  {==}

def Laws.i1_r6_unpublished_drop_open():
  {==}

def Laws.i1_r6_visible_drop_resolves():
  {==}

def Laws.i2_r6_child_completion_not_resolution():
  {==}

def Laws.i2_r6_parent_requires_return():
  {==}

def Laws.i2_r6_verified_return_resolves():
  {==}

def Laws.i1_r6_exact_day_not_overdue():
  {==}

def Laws.i1_r6_old_open_reported():
  {==}

def Laws.i1_r6_closed_not_overdue():
  {==}

def Laws.i1_r6_unapproved_not_overdue():
  {==}
