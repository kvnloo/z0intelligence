# Ops plane: the proofs of LAWS.bend.

import Base
import ./ops.bend as O
import ./LAWS.bend as Laws

# Proof kit
# ---------

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

def and_i(a: Bool, -b: Bool, ta: Laws.T(a), tb: Laws.T(b)) -> Laws.T(Bool.and(a, b)):
  match a:
    case True{}:
      tb
    case False{}:
      Empty.absurd(Laws.T(Bool.and(False{}, b)), ta)

def t_true(b: Bool, k: Laws.T(b)) -> {b == True{} : Bool}:
  match b:
    case True{}:
      {==}
    case False{}:
      Empty.absurd({False{} == True{} : Bool}, k)

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

def eq_refl(n: Nat) -> Laws.T(Nat.is_eq(n, n)):
  match n:
    case 0n:
      Unit{}
    case 1n+p:
      eq_refl(p)

# O1. Compute placement
# ---------------------

def Laws.o1_groot_admits_only(c, w):
  match c:
    case O.CTest{}:
      w
    case O.CMutation{}:
      w
    case O.CTypecheck{}:
      w
    case O.CGradle{}:
      w
    case O.CBundle{}:
      w
    case O.CEval{}:
      w
    case O.CShot{}:
      w
    case O.CGpu{}:
      Unit{}
    case O.CBend{}:
      Unit{}
    case O.CLean{}:
      Unit{}
    case O.CService{}:
      Unit{}
    case O.CAgent{}:
      Unit{}
    case O.CTiny{}:
      Unit{}

def phone_not_groot(p: Bool, w: Laws.T(O.runs_on_groot(O.place_phone(p)))) -> Empty:
  match p:
    case True{}:
      w
    case False{}:
      w

def off_not_groot(m: Bool, p: Bool, w: Laws.T(O.runs_on_groot(O.place_off(m, p)))) -> Empty:
  match m:
    case True{}:
      w
    case False{}:
      phone_not_groot(p, w)

def Laws.o1_never_groot_fallback(c, m, p, w):
  match c:
    case O.CTest{}:
      off_not_groot(m, p, w)
    case O.CMutation{}:
      off_not_groot(m, p, w)
    case O.CTypecheck{}:
      off_not_groot(m, p, w)
    case O.CGradle{}:
      off_not_groot(m, p, w)
    case O.CBundle{}:
      off_not_groot(m, p, w)
    case O.CEval{}:
      off_not_groot(m, p, w)
    case O.CShot{}:
      off_not_groot(m, p, w)
    case O.CGpu{}:
      Unit{}
    case O.CBend{}:
      Unit{}
    case O.CLean{}:
      Unit{}
    case O.CService{}:
      Unit{}
    case O.CAgent{}:
      Unit{}
    case O.CTiny{}:
      Unit{}

def Laws.o1_full_waits(c, h):
  match c:
    case O.CTest{}:
      {==}
    case O.CMutation{}:
      {==}
    case O.CTypecheck{}:
      {==}
    case O.CGradle{}:
      {==}
    case O.CBundle{}:
      {==}
    case O.CEval{}:
      {==}
    case O.CShot{}:
      {==}
    case O.CGpu{}:
      Empty.absurd({O.RunOn{O.LGroot{}} == O.Wait{} : O.Place}, h)
    case O.CBend{}:
      Empty.absurd({O.RunOn{O.LGroot{}} == O.Wait{} : O.Place}, h)
    case O.CLean{}:
      Empty.absurd({O.RunOn{O.LGroot{}} == O.Wait{} : O.Place}, h)
    case O.CService{}:
      Empty.absurd({O.RunOn{O.LGroot{}} == O.Wait{} : O.Place}, h)
    case O.CAgent{}:
      Empty.absurd({O.RunOn{O.LGroot{}} == O.Wait{} : O.Place}, h)
    case O.CTiny{}:
      Empty.absurd({O.RunOn{O.LGroot{}} == O.Wait{} : O.Place}, h)

def Laws.o1_mbp_takes_heavy(c, p, h):
  match c:
    case O.CTest{}:
      {==}
    case O.CMutation{}:
      {==}
    case O.CTypecheck{}:
      {==}
    case O.CGradle{}:
      {==}
    case O.CBundle{}:
      {==}
    case O.CEval{}:
      {==}
    case O.CShot{}:
      {==}
    case O.CGpu{}:
      Empty.absurd({O.RunOn{O.LGroot{}} == O.RunOn{O.LMbp{}} : O.Place}, h)
    case O.CBend{}:
      Empty.absurd({O.RunOn{O.LGroot{}} == O.RunOn{O.LMbp{}} : O.Place}, h)
    case O.CLean{}:
      Empty.absurd({O.RunOn{O.LGroot{}} == O.RunOn{O.LMbp{}} : O.Place}, h)
    case O.CService{}:
      Empty.absurd({O.RunOn{O.LGroot{}} == O.RunOn{O.LMbp{}} : O.Place}, h)
    case O.CAgent{}:
      Empty.absurd({O.RunOn{O.LGroot{}} == O.RunOn{O.LMbp{}} : O.Place}, h)
    case O.CTiny{}:
      Empty.absurd({O.RunOn{O.LGroot{}} == O.RunOn{O.LMbp{}} : O.Place}, h)

# O2. Served worktree
# -------------------

def Laws.o2_txn_can_ff():
  {==}

def Laws.o2_txn_can_rollback():
  {==}

def Laws.o2_served_refuses(op, txn, w):
  match op:
    case O.GEdit{}:
      w
    case O.GCommit{}:
      w
    case O.GCheckout{}:
      w
    case O.GStash{}:
      w
    case O.GMerge{}:
      w
    case O.GMergeFF{}:
      w
    case O.GRollback{}:
      w

# the rollout transaction: what each outcome implies about its inputs

def go_touched(r: Bool, check: Bool, w: Laws.T(O.touched(O.go(r, check)))) -> Laws.T(r):
  match r:
    case True{}:
      Unit{}
    case False{}:
      w

def decide_touched(ok: Bool, r: Bool, check: Bool, w: Laws.T(O.touched(O.decide(ok, r, check)))) -> Laws.T(Bool.and(ok, r)):
  match ok:
    case True{}:
      go_touched(r, check, w)
    case False{}:
      w

def finish_promoted(check: Bool, w: Laws.T(O.promoted(O.finish(check)))) -> Laws.T(check):
  match check:
    case True{}:
      Unit{}
    case False{}:
      w

def go_promoted(r: Bool, check: Bool, w: Laws.T(O.promoted(O.go(r, check)))) -> Laws.T(Bool.and(r, check)):
  match r:
    case True{}:
      finish_promoted(check, w)
    case False{}:
      w

def decide_promoted(ok: Bool, r: Bool, check: Bool, w: Laws.T(O.promoted(O.decide(ok, r, check)))) -> Laws.T(Bool.and(ok, Bool.and(r, check))):
  match ok:
    case True{}:
      go_promoted(r, check, w)
    case False{}:
      w

def Laws.o2_rollout_ff_idle(q, check, w):
  match q:
    case O.Req{+clean, +ff, +gated, +receipt, +call, +idle}:
      +both = decide_touched(O.pre_ok(clean, ff, gated, receipt), O.ready(call, idle), check, w)
      +pre = and_l(O.pre_ok(clean, ff, gated, receipt), O.ready(call, idle), both)
      +rdy = and_r(O.pre_ok(clean, ff, gated, receipt), O.ready(call, idle), both)
      +rest = and_r(clean, Bool.and(ff, O.review_ok(gated, receipt)), pre)
      and_i(ff, idle, and_l(ff, O.review_ok(gated, receipt), rest), and_r(Bool.not(call), idle, rdy))

# O3. Device rollout gate
# -----------------------

def Laws.o3_promoted_needs_clean(q, check, w):
  match q:
    case O.Req{+clean, +ff, +gated, +receipt, +call, +idle}:
      +all = decide_promoted(O.pre_ok(clean, ff, gated, receipt), O.ready(call, idle), check, w)
      +pre = and_l(O.pre_ok(clean, ff, gated, receipt), Bool.and(O.ready(call, idle), check), all)
      and_l(clean, Bool.and(ff, O.review_ok(gated, receipt)), pre)

def Laws.o3_promoted_needs_check(q, check, w):
  match q:
    case O.Req{+clean, +ff, +gated, +receipt, +call, +idle}:
      +all = decide_promoted(O.pre_ok(clean, ff, gated, receipt), O.ready(call, idle), check, w)
      +rc = and_r(O.pre_ok(clean, ff, gated, receipt), Bool.and(O.ready(call, idle), check), all)
      and_r(O.ready(call, idle), check, rc)

def go_fail(r: Bool, w: Laws.T(O.touched(O.go(r, False{})))) -> {O.go(r, False{}) == O.ORolledBack{} : O.Out}:
  match r:
    case True{}:
      {==}
    case False{}:
      Empty.absurd({O.ODeferred{} == O.ORolledBack{} : O.Out}, w)

def decide_fail(ok: Bool, r: Bool, w: Laws.T(O.touched(O.decide(ok, r, False{})))) -> {O.decide(ok, r, False{}) == O.ORolledBack{} : O.Out}:
  match ok:
    case True{}:
      go_fail(r, w)
    case False{}:
      Empty.absurd({O.ORefused{} == O.ORolledBack{} : O.Out}, w)

def Laws.o3_failed_check_rolls_back(q, w):
  match q:
    case O.Req{+clean, +ff, +gated, +receipt, +call, +idle}:
      decide_fail(O.pre_ok(clean, ff, gated, receipt), O.ready(call, idle), w)

# admissible inputs: pre_ok and ready both hold
def adm_pre(+clean: Bool, +ff: Bool, +gated: Bool, +receipt: Bool, +call: Bool, +idle: Bool, h: Laws.T(Bool.and(clean, Bool.and(ff, Bool.and(Bool.or(Bool.not(gated), receipt), Bool.and(Bool.not(call), idle)))))) -> Laws.T(Bool.and(O.pre_ok(clean, ff, gated, receipt), O.ready(call, idle))):
  +h2 = h
  +c = and_l(clean, Bool.and(ff, Bool.and(Bool.or(Bool.not(gated), receipt), Bool.and(Bool.not(call), idle))), h2)
  +r1 = and_r(clean, Bool.and(ff, Bool.and(Bool.or(Bool.not(gated), receipt), Bool.and(Bool.not(call), idle))), h2)
  +f = and_l(ff, Bool.and(Bool.or(Bool.not(gated), receipt), Bool.and(Bool.not(call), idle)), r1)
  +r2 = and_r(ff, Bool.and(Bool.or(Bool.not(gated), receipt), Bool.and(Bool.not(call), idle)), r1)
  +rv = and_l(Bool.or(Bool.not(gated), receipt), Bool.and(Bool.not(call), idle), r2)
  +rd = and_r(Bool.or(Bool.not(gated), receipt), Bool.and(Bool.not(call), idle), r2)
  and_i(O.pre_ok(clean, ff, gated, receipt), O.ready(call, idle), and_i(clean, Bool.and(ff, O.review_ok(gated, receipt)), c, and_i(ff, O.review_ok(gated, receipt), f, rv)), rd)

def go_ok(r: Bool, check: Bool, k: Laws.T(r)) -> {O.go(r, check) == O.finish(check) : O.Out}:
  match r:
    case True{}:
      {==}
    case False{}:
      Empty.absurd({O.ODeferred{} == O.finish(check) : O.Out}, k)

def decide_ok(ok: Bool, r: Bool, check: Bool, k: Laws.T(Bool.and(ok, r))) -> {O.decide(ok, r, check) == O.finish(check) : O.Out}:
  match ok:
    case True{}:
      go_ok(r, check, k)
    case False{}:
      Empty.absurd({O.ORefused{} == O.finish(check) : O.Out}, k)

def Laws.o3_admissible_fail_rolls_back(q, h):
  match q:
    case O.Req{+clean, +ff, +gated, +receipt, +call, +idle}:
      decide_ok(O.pre_ok(clean, ff, gated, receipt), O.ready(call, idle), False{}, adm_pre(clean, ff, gated, receipt, call, idle, h))

def Laws.o3_admissible_pass_promotes(q, h):
  match q:
    case O.Req{+clean, +ff, +gated, +receipt, +call, +idle}:
      decide_ok(O.pre_ok(clean, ff, gated, receipt), O.ready(call, idle), True{}, adm_pre(clean, ff, gated, receipt, call, idle, h))

def review_to(-clean: Bool, -ff: Bool, gated: Bool, -receipt: Bool, -call: Bool, -idle: Bool, w: Laws.T(O.review_ok(gated, receipt))) -> Laws.Reviewed(O.Req{clean, ff, gated, receipt, call, idle}):
  match gated:
    case True{}:
      w
    case False{}:
      Unit{}

def Laws.o3_promoted_needs_review(q, check, w):
  match q:
    case O.Req{+clean, +ff, +gated, +receipt, +call, +idle}:
      +all = decide_promoted(O.pre_ok(clean, ff, gated, receipt), O.ready(call, idle), check, w)
      +pre = and_l(O.pre_ok(clean, ff, gated, receipt), Bool.and(O.ready(call, idle), check), all)
      +rest = and_r(clean, Bool.and(ff, O.review_ok(gated, receipt)), pre)
      review_to(clean, ff, gated, receipt, call, idle, and_r(ff, O.review_ok(gated, receipt), rest))

def nocall_to(-clean: Bool, -ff: Bool, -gated: Bool, -receipt: Bool, call: Bool, -idle: Bool, w: Laws.T(Bool.not(call))) -> Laws.NoCall(O.Req{clean, ff, gated, receipt, call, idle}):
  match call:
    case True{}:
      w
    case False{}:
      Unit{}

def Laws.o3_no_device_work_in_call(q, check, w):
  match q:
    case O.Req{+clean, +ff, +gated, +receipt, +call, +idle}:
      +both = decide_touched(O.pre_ok(clean, ff, gated, receipt), O.ready(call, idle), check, w)
      +rdy = and_r(O.pre_ok(clean, ff, gated, receipt), O.ready(call, idle), both)
      nocall_to(clean, ff, gated, receipt, call, idle, and_l(Bool.not(call), idle, rdy))

# O4. Privacy boundary
# --------------------

def Laws.o4_private_stays_private(v, w):
  match v:
    case O.VPublic{}:
      Empty.absurd({O.VPublic{} == O.VPrivate{} : O.Vis}, w)
    case O.VPrivate{}:
      {==}
    case O.VUnknown{}:
      Empty.absurd({O.VUnknown{} == O.VPrivate{} : O.Vis}, w)

def Laws.o4_nonprivate_free(v):
  {==}

# O5. Owner phone touch
# ---------------------

def Laws.o5_touch_needs_intent(intent, fg, call, w):
  and_l(intent, fg, w)

def Laws.o5_touch_needs_foreground(intent, fg, call, w):
  and_r(intent, fg, w)

def Laws.o5_no_chain_install_in_call(w):
  w

def Laws.o5_no_chain_restart_in_call(w):
  w

# O6. Accessibility preservation
# ------------------------------

def keep_x(c: Bool, +h: Nat, -t: List<&2, Nat>, +post: List<&2, Nat>, +rm: List<&2, Nat>, +x: Nat, e: Laws.T(c) -> {h == x : Nat}, k: Laws.T(Bool.or(O.mem(h, post), O.mem(h, rm))), rest: Laws.T(O.mem(x, t)) -> Laws.T(Bool.or(O.mem(x, post), O.mem(x, rm))), m: Laws.T(Bool.or(c, O.mem(x, t)))) -> Laws.T(Bool.or(O.mem(x, post), O.mem(x, rm))):
  match c:
    case True{}:
      %e(Unit{}) : Laws.T(Bool.or(O.mem(_, post), O.mem(_, rm)))
      k
    case False{}:
      rest(m)

def Laws.o6_a11y_superset(pre, post, rm, x, ok, m):
  match pre:
    case Nil{}:
      Empty.absurd(Laws.T(Bool.or(O.mem(x, post), O.mem(x, rm))), m)
    case +h <> +t:
      +ok2 = ok
      +k = and_l(Bool.or(O.mem(h, post), O.mem(h, rm)), O.a11y_ok(t, post, rm), ok2)
      +okt = and_r(Bool.or(O.mem(h, post), O.mem(h, rm)), O.a11y_ok(t, post, rm), ok2)
      keep_x(Nat.is_eq(h, x), h, t, post, rm, x, z => eq_sound(h, x, z), k, z => Laws.o6_a11y_superset(t, post, rm, x, okt, z), m)

# O7. Provider routing
# --------------------

def Laws.o7_no_cancelled_provider(sol, design, w):
  w

def Laws.o7_no_sol_design(cancelled, w):
  match cancelled:
    case True{}:
      w
    case False{}:
      w

# O8. Node health notifies exactly once
# -------------------------------------

def Laws.o8_no_silent_strike(s, now, lim, k):
  t_true(Nat.is_gt(now, Nat.add(s, lim)), k)

def Laws.o8_notify_once(s, e, lim):
  {==}

def Laws.o8_notified_sticks(s, now, lim):
  {==}

def emit_l(l: Bool, +since: Nat, w: Laws.T(Bool.and(True{}, l))) -> Laws.T(O.notified(O.npick(False{}, O.NHealthy{}, O.npick(l, O.NNotified{since}, O.NBad{since})))):
  match l:
    case True{}:
      Unit{}
    case False{}:
      w

def emit_ok(ok: Bool, l: Bool, +since: Nat, w: Laws.T(Bool.and(Bool.not(ok), l))) -> Laws.T(O.notified(O.npick(ok, O.NHealthy{}, O.npick(l, O.NNotified{since}, O.NBad{since})))):
  match ok:
    case True{}:
      w
    case False{}:
      emit_l(l, since, w)

def emit_bad(+since: Nat, e: O.NEv, +lim: Nat, w: Laws.T(O.neff(O.NBad{since}, e, lim))) -> Laws.T(O.notified(O.nstep(O.NBad{since}, e, lim))):
  match e:
    case O.NProbe{+ok, +now}:
      emit_ok(ok, O.late(now, since, lim), since, w)

def Laws.o8_emit_marks_notified(s, e, lim, w):
  match s:
    case O.NHealthy{}:
      Empty.absurd(Laws.T(O.notified(O.from_healthy(e))), w)
    case O.NBad{+since}:
      emit_bad(since, e, lim, w)
    case O.NNotified{+since}:
      Empty.absurd(Laws.T(O.notified(O.from_notified(since, e))), w)

# O9. Gateway restart
# -------------------

def rpick_true(ok: Bool, -x: O.RSt, -y: O.RSt, k: Laws.T(ok)) -> {O.rpick(ok, x, y) == x : O.RSt}:
  match ok:
    case True{}:
      {==}
    case False{}:
      Empty.absurd({y == x : O.RSt}, k)

def Laws.o9_restart_interrupts(g):
  rpick_true(Nat.is_eq(g, g), O.RInterrupted{g}, O.RActive{g}, eq_refl(g))

def restart_pick(ok: Bool, +g: Nat, w: Laws.T(O.rfailed(O.rpick(ok, O.RInterrupted{g}, O.RActive{g})))) -> Empty:
  match ok:
    case True{}:
      w
    case False{}:
      w

def Laws.o9_restart_never_fails(g, x, w):
  restart_pick(Nat.is_eq(x, g), g, w)

def resume_pick(session: Bool, +g: Nat) -> Laws.IntExit(O.rpick(session, O.RResumed{}, O.RInterrupted{g})):
  match session:
    case True{}:
      Unit{}
    case False{}:
      Unit{}

def resume_session(session: Bool, +g: Nat, w: Laws.T(Laws.Resumed(O.rpick(session, O.RResumed{}, O.RInterrupted{g})))) -> Laws.T(session):
  match session:
    case True{}:
      Unit{}
    case False{}:
      w

def Laws.o9_resume_needs_session(g, session, w):
  resume_session(session, g, w)

def Laws.o9_interrupted_exits(g, e):
  match e:
    case O.GwRestart{x}:
      Unit{}
    case O.RunError{}:
      Unit{}
    case O.RunDone{}:
      Unit{}
    case O.ResumeRun{+session}:
      resume_pick(session, g)
    case O.ReportRun{}:
      Unit{}

# O10. Resource observations never become agent admission/placement limits.
def Laws.o10_phone_no_agent_resource_caps(ready, memory, cpu, battery, thermal):
  {==}

def Laws.o10_phone_placement_no_agent_resource_caps(ready, memory, cpu, battery, thermal):
  {==}

# O11/O12. Proofs project the independent constraints from the route gate.
def Laws.o11_evidence_bound_route(family, named_family, stage, named_arm, arm, healthy, abstain, w):
  and_l(O.policy_credited(stage), Nat.is_eq(arm, named_arm),
    and_r(Nat.is_eq(family, named_family), Bool.and(O.policy_credited(stage), Nat.is_eq(arm, named_arm)), w))

def Laws.o11_named_family(family, named_family, stage, named_arm, arm, healthy, abstain, w):
  eq_sound(family, named_family,
    and_l(Nat.is_eq(family, named_family), Bool.and(O.policy_credited(stage), Nat.is_eq(arm, named_arm)), w))

def Laws.o11_named_arm(family, named_family, stage, named_arm, arm, healthy, abstain, w):
  eq_sound(arm, named_arm,
    and_r(O.policy_credited(stage), Nat.is_eq(arm, named_arm),
      and_r(Nat.is_eq(family, named_family), Bool.and(O.policy_credited(stage), Nat.is_eq(arm, named_arm)), w)))

def Laws.o11_none_not_credited():
  {==}

def Laws.o11_shadow_not_credited():
  {==}

def Laws.o11_canary_routes(family, arm):
  and_i(Nat.is_eq(family, family), Bool.and(True{}, Nat.is_eq(arm, arm)),
    eq_refl(family), eq_refl(arm))

def Laws.o11_live_routes(family, arm):
  and_i(Nat.is_eq(family, family), Bool.and(True{}, Nat.is_eq(arm, arm)),
    eq_refl(family), eq_refl(arm))

def policy_frontier_same(ok: Bool, +abstain: Bool, same: Laws.T(ok), w: Laws.T(Bool.or(Bool.not(ok), abstain))) -> Laws.T(abstain):
  match ok:
    case True{}:
      w
    case False{}:
      Empty.absurd(Laws.T(abstain), same)

def Laws.o12_offload_obligation(family, arm, abstain, w):
  policy_frontier_same(Nat.is_eq(family, family), abstain, eq_refl(family), w)

def policy_or_true(b: Bool) -> Laws.T(Bool.or(b, True{})):
  match b:
    case True{}:
      Unit{}
    case False{}:
      Unit{}

def Laws.o12_unhealthy_can_fallback(family, arm):
  policy_or_true(Bool.not(Nat.is_eq(family, family)))

def Laws.o12_abstain_can_fallback(family, arm):
  policy_or_true(Bool.not(Nat.is_eq(family, family)))

# O13. Capacity is a hard bound, with credit limited to the exact scope.
def Laws.o13_option_capacity(credited, actual, named, measured, count, w):
  w

def Laws.o13_default_zero(actual, named, measured):
  {==}

def option_mismatch(eq: Bool, measured: Nat, w: Laws.T(Bool.not(eq))) -> {O.option_limit_by(eq, measured) == 0n : Nat}:
  match eq:
    case True{}:
      Empty.absurd({measured == 0n : Nat}, w)
    case False{}:
      {==}

def Laws.o13_exact_scope(actual, named, measured, w):
  option_mismatch(Nat.is_eq(actual, named), measured, w)

def option_same(eq: Bool, +measured: Nat, w: Laws.T(eq)) -> {O.option_limit_by(eq, measured) == measured : Nat}:
  match eq:
    case True{}:
      {==}
    case False{}:
      Empty.absurd({0n == measured : Nat}, w)

def Laws.o13_credited_limit(scope, measured):
  option_same(Nat.is_eq(scope, scope), measured, eq_refl(scope))

def Laws.o13_k2_rejected():
  {==}

def Laws.o13_k24_rejected():
  {==}

def Laws.o13_k119_rejected():
  {==}

def Laws.o13_credited_excess_rejected():
  {==}

def Laws.o13_destructive_never():
  {==}

def Laws.o13_unknown_never():
  {==}

def Laws.o13_read_authority():
  {==}

def Laws.o13_reversible_authority():
  {==}

def Laws.o13_destructive_request_never(credited, actual, named, measured, count):
  {==}
