# Ops plane: the laws. The owner states them (spec agreed 2026-10-21 from that
# day's incidents); PROOF.bend proves them; `bend PROOF.bend --verdict` (or
# `hermes bend verify`) is the check. Do not weaken a law to get a PASS.
#
# Scope, stated plainly. These are theorems about ops.bend's own decision
# functions over abstract inputs. They do not say that the Python gates equal
# ops.bend (measured by src/z0int/fsm/ops_parity.py), that a real command,
# path, config or adb dump was classified faithfully, or that any rollout
# succeeded. A PASS is never an authorization or verified_success.

import Base
import ./ops.bend as O

# Boolean reflection: T(b) is Unit when b holds, Empty when it does not
def T(b: Bool) -> Data:
  match b:
    case True{}:
      Unit
    case False{}:
      Empty

# O1. Compute placement
# ---------------------
# Written apart from ops.bend: the classes groot may run.
def GrootClass(c: O.Cls) -> Data:
  match c:
    case O.CGpu{}:
      Unit
    case O.CBend{}:
      Unit
    case O.CLean{}:
      Unit
    case O.CService{}:
      Unit
    case O.CAgent{}:
      Unit
    case O.CTiny{}:
      Unit
    case _:
      Empty

# the classes that must leave groot
def Heavy(c: O.Cls) -> Data:
  match c:
    case O.CTest{}:
      Unit
    case O.CMutation{}:
      Unit
    case O.CTypecheck{}:
      Unit
    case O.CGradle{}:
      Unit
    case O.CBundle{}:
      Unit
    case O.CEval{}:
      Unit
    case O.CShot{}:
      Unit
    case _:
      Empty

# groot admits only its own classes
law o1_groot_admits_only:
  for c: O.Cls
  for w: T(O.admits(O.LGroot{}, c))
  GrootClass(c)

# no placement ever puts another class on groot, whatever the room elsewhere
law o1_never_groot_fallback:
  for c: O.Cls
  for m: Bool
  for p: Bool
  for w: T(O.runs_on_groot(O.place(c, m, p)))
  GrootClass(c)

# a heavy job with no room on the MBP or a phone waits
law o1_full_waits:
  for c: O.Cls
  for h: Heavy(c)
  {O.place(c, False{}, False{}) == O.Wait{} : O.Place}

# a heavy job runs on the MBP whenever the MBP has room (the queue drains)
law o1_mbp_takes_heavy:
  for c: O.Cls
  for p: Bool
  for h: Heavy(c)
  {O.place(c, True{}, p) == O.RunOn{O.LMbp{}} : O.Place}

# O2. Served worktree
# -------------------
def FFInTxn(op: O.GitOp, txn: Bool) -> Data:
  match op:
    case O.GMergeFF{}:
      T(txn)
    case O.GRollback{}:
      T(txn)
    case _:
      Empty

# in a served worktree the only allowed ops are merge --ff-only and its
# automatic rollback, both inside the guarded rollout transaction
law o2_served_refuses:
  for op: O.GitOp
  for txn: Bool
  for w: T(O.wt_allows(True{}, op, txn))
  FFInTxn(op, txn)

# the guarded transaction can still fast-forward the served worktree
law o2_txn_can_ff:
  {O.wt_allows(True{}, O.GMergeFF{}, True{}) == True{} : Bool}

law o2_txn_can_rollback:
  {O.wt_allows(True{}, O.GRollback{}, True{}) == True{} : Bool}

# a rollout that merged and restarted was a fast-forward into an idle service
def FFIdle(q: O.Req) -> Data:
  match q:
    case O.Req{clean, ff, gated, receipt, call, idle}:
      T(Bool.and(ff, idle))

law o2_rollout_ff_idle:
  for q: O.Req
  for check: Bool
  for w: T(O.touched(O.rollout(q, check)))
  FFIdle(q)

# O3. Device rollout gate
# -----------------------
def Clean(q: O.Req) -> Data:
  match q:
    case O.Req{clean, ff, gated, receipt, call, idle}:
      T(clean)

def Reviewed(q: O.Req) -> Data:
  match q:
    case O.Req{clean, ff, gated, receipt, call, idle}:
      match gated:
        case True{}:
          T(receipt)
        case False{}:
          Unit

def NoCall(q: O.Req) -> Data:
  match q:
    case O.Req{clean, ff, gated, receipt, call, idle}:
      match call:
        case True{}:
          Empty
        case False{}:
          Unit

# (a) promoted only from a candidate built outside the served worktree
law o3_promoted_needs_clean:
  for q: O.Req
  for check: Bool
  for w: T(O.promoted(O.rollout(q, check)))
  Clean(q)

# (b) promoted only after the device launch check passed
law o3_promoted_needs_check:
  for q: O.Req
  for check: Bool
  for w: T(O.promoted(O.rollout(q, check)))
  T(check)

# (b) a failed launch check is rolled back automatically, never left live
law o3_failed_check_rolls_back:
  for q: O.Req
  for w: T(O.touched(O.rollout(q, False{})))
  {O.rollout(q, False{}) == O.ORolledBack{} : O.Out}

# every precondition met: the inputs the gate needs, written apart
def Admissible(q: O.Req) -> Data:
  match q:
    case O.Req{clean, ff, gated, receipt, call, idle}:
      T(Bool.and(clean, Bool.and(ff, Bool.and(Bool.or(Bool.not(gated), receipt), Bool.and(Bool.not(call), idle)))))

# (b) an admissible rollout whose launch check fails is rolled back
law o3_admissible_fail_rolls_back:
  for q: O.Req
  for h: Admissible(q)
  {O.rollout(q, False{}) == O.ORolledBack{} : O.Out}

# an admissible rollout whose launch check passes is promoted
law o3_admissible_pass_promotes:
  for q: O.Req
  for h: Admissible(q)
  {O.rollout(q, True{}) == O.OPromoted{} : O.Out}

# (c) a review-gated PR is promoted only with an orchestrator review receipt
law o3_promoted_needs_review:
  for q: O.Req
  for check: Bool
  for w: T(O.promoted(O.rollout(q, check)))
  Reviewed(q)

# O3 x O5: while the owner's z0live call is live, the transaction (restart and
# device launch check) does not run at all
law o3_no_device_work_in_call:
  for q: O.Req
  for check: Bool
  for w: T(O.touched(O.rollout(q, check)))
  NoCall(q)

# O4. Privacy boundary
# --------------------
law o4_private_stays_private:
  for v: O.Vis
  for w: T(O.push_allows(True{}, v))
  {v == O.VPrivate{} : O.Vis}

# a non-private artifact may go anywhere
law o4_nonprivate_free:
  for v: O.Vis
  {O.push_allows(False{}, v) == True{} : Bool}

# O5. Owner phone touch
# ---------------------
law o5_touch_needs_intent:
  for intent: Bool
  for fg: Bool
  for call: Bool
  for w: T(O.phone_allows(O.PTap{intent, fg}, call))
  T(intent)

law o5_touch_needs_foreground:
  for intent: Bool
  for fg: Bool
  for call: Bool
  for w: T(O.phone_allows(O.PTap{intent, fg}, call))
  T(fg)

law o5_no_chain_install_in_call:
  for w: T(O.phone_allows(O.PInstall{True{}}, True{}))
  Empty

law o5_no_chain_restart_in_call:
  for w: T(O.phone_allows(O.PRestart{True{}}, True{}))
  Empty

# O6. Accessibility preservation
# ------------------------------
# post ⊇ pre \ removed-by-owner, stated per element
law o6_a11y_superset:
  for pre: List<&2, Nat>
  for +post: List<&2, Nat>
  for +rm: List<&2, Nat>
  for +x: Nat
  for ok: T(O.a11y_ok(pre, post, rm))
  for m: T(O.mem(x, pre))
  T(Bool.or(O.mem(x, post), O.mem(x, rm)))

# O7. Provider routing
# --------------------
law o7_no_cancelled_provider:
  for sol: Bool
  for design: Bool
  for w: T(O.route_ok(True{}, sol, design))
  Empty

law o7_no_sol_design:
  for cancelled: Bool
  for w: T(O.route_ok(cancelled, True{}, True{}))
  Empty

# O8. Node health notifies exactly once
# -------------------------------------
# a node past the limit on a bad probe emits the notification (no silent strike)
law o8_no_silent_strike:
  for +s: Nat
  for +now: Nat
  for +lim: Nat
  for k: T(Nat.is_gt(now, Nat.add(s, lim)))
  {O.neff(O.NBad{s}, O.NProbe{False{}, now}, lim) == True{} : Bool}

# once notified, nothing more is emitted until the node is healthy again
law o8_notify_once:
  for +s: Nat
  for e: O.NEv
  for lim: Nat
  {O.neff(O.NNotified{s}, e, lim) == False{} : Bool}

law o8_notified_sticks:
  for +s: Nat
  for +now: Nat
  for lim: Nat
  {O.nstep(O.NNotified{s}, O.NProbe{False{}, now}, lim) == O.NNotified{s} : O.NSt}

# an emission moves the node to Notified (so the next one needs a recovery)
law o8_emit_marks_notified:
  for +s: O.NSt
  for +e: O.NEv
  for +lim: Nat
  for w: T(O.neff(s, e, lim))
  T(O.notified(O.nstep(s, e, lim)))

# O9. Gateway restart interrupts, never silently fails
# ----------------------------------------------------
law o9_restart_interrupts:
  for +g: Nat
  {O.rstep(O.RActive{g}, O.GwRestart{g}) == O.RInterrupted{g} : O.RSt}

law o9_restart_never_fails:
  for +g: Nat
  for +x: Nat
  for w: T(O.rfailed(O.rstep(O.RActive{g}, O.GwRestart{x})))
  Empty

# an interrupted run stays Interrupted until resumed from its session or
# reported; it never becomes Failed or Done
def IntExit(s: O.RSt) -> Data:
  match s:
    case O.RInterrupted{g}:
      Unit
    case O.RResumed{}:
      Unit
    case O.RReported{}:
      Unit
    case _:
      Empty

def Resumed(s: O.RSt) -> Bool:
  match s:
    case O.RResumed{}:
      True{}
    case _:
      False{}

# a resume counts only when it comes from the run's session
law o9_resume_needs_session:
  for +g: Nat
  for session: Bool
  for w: T(Resumed(O.rstep(O.RInterrupted{g}, O.ResumeRun{session})))
  T(session)

law o9_interrupted_exits:
  for +g: Nat
  for e: O.REv
  IntExit(O.rstep(O.RInterrupted{g}, e))

# O10. Phone admission/placement cannot depend on agent resource limits.
# Caller-explicit per-job memory is not a default or a phone admission gate.
law o10_phone_no_agent_resource_caps:
  for +ready: Bool
  for memory: Bool
  for cpu: Bool
  for battery: Bool
  for thermal: Bool
  {O.phone_admits(ready, memory, cpu, battery, thermal) == ready : Bool}

law o10_phone_placement_no_agent_resource_caps:
  for +ready: Bool
  for memory: Bool
  for cpu: Bool
  for battery: Bool
  for thermal: Bool
  {O.phone_place(ready, memory, cpu, battery, thermal) == O.place_phone(ready) : O.Place}

# O11. Evidence-bound routing. The policy names both the family and full arm.
law o11_evidence_bound_route:
  for +family: Nat
  for +named_family: Nat
  for +stage: O.PolicyStage
  for +named_arm: Nat
  for +arm: Nat
  for healthy: Bool
  for abstain: Bool
  for w: T(O.policy_allows(family, named_family, stage, named_arm, O.OffloadRoute{arm}, healthy, abstain))
  T(O.policy_credited(stage))

law o11_named_family:
  for +family: Nat
  for +named_family: Nat
  for +stage: O.PolicyStage
  for +named_arm: Nat
  for +arm: Nat
  for healthy: Bool
  for abstain: Bool
  for w: T(O.policy_allows(family, named_family, stage, named_arm, O.OffloadRoute{arm}, healthy, abstain))
  {family == named_family : Nat}

law o11_named_arm:
  for +family: Nat
  for +named_family: Nat
  for +stage: O.PolicyStage
  for +named_arm: Nat
  for +arm: Nat
  for healthy: Bool
  for abstain: Bool
  for w: T(O.policy_allows(family, named_family, stage, named_arm, O.OffloadRoute{arm}, healthy, abstain))
  {arm == named_arm : Nat}

# These independent stage laws prevent "credit everything" implementations.
law o11_none_not_credited:
  {O.policy_credited(O.StageNone{}) == False{} : Bool}

law o11_shadow_not_credited:
  {O.policy_credited(O.StageShadow{}) == False{} : Bool}

law o11_canary_routes:
  for +family: Nat
  for +arm: Nat
  T(O.policy_allows(family, family, O.StageCanary{}, arm, O.OffloadRoute{arm}, True{}, False{}))

law o11_live_routes:
  for +family: Nat
  for +arm: Nat
  T(O.policy_allows(family, family, O.StageLive{}, arm, O.OffloadRoute{arm}, True{}, False{}))

# O12. A healthy live family cannot silently fall back to frontier.
law o12_offload_obligation:
  for +family: Nat
  for arm: Nat
  for +abstain: Bool
  for w: T(O.policy_allows(family, family, O.StageLive{}, arm, O.FrontierRoute{}, True{}, abstain))
  T(abstain)

law o12_unhealthy_can_fallback:
  for +family: Nat
  for arm: Nat
  T(O.policy_allows(family, family, O.StageLive{}, arm, O.FrontierRoute{}, False{}, False{}))

law o12_abstain_can_fallback:
  for +family: Nat
  for arm: Nat
  T(O.policy_allows(family, family, O.StageLive{}, arm, O.FrontierRoute{}, True{}, True{}))

# O13. No shadow/canary/live request exceeds its exact-scope measured capacity.
law o13_option_capacity:
  for +credited: Bool
  for +actual: Nat
  for +named: Nat
  for +measured: Nat
  for +count: Nat
  for w: T(O.option_capacity_allows(credited, actual, named, measured, count))
  T(Bool.not(Nat.is_gt(count, O.option_limit(credited, actual, named, measured))))

law o13_default_zero:
  for actual: Nat
  for named: Nat
  for measured: Nat
  {O.option_limit(False{}, actual, named, measured) == 0n : Nat}

law o13_exact_scope:
  for +actual: Nat
  for +named: Nat
  for measured: Nat
  for w: T(Bool.not(Nat.is_eq(actual, named)))
  {O.option_limit(True{}, actual, named, measured) == 0n : Nat}

law o13_credited_limit:
  for +scope: Nat
  for +measured: Nat
  {O.option_limit(True{}, scope, scope, measured) == measured : Nat}

law o13_k2_rejected:
  {O.option_capacity_allows(False{}, 0n, 0n, 119n, 2n) == False{} : Bool}

law o13_k24_rejected:
  {O.option_capacity_allows(False{}, 0n, 0n, 24n, 24n) == False{} : Bool}

law o13_k119_rejected:
  {O.option_capacity_allows(False{}, 0n, 0n, 119n, 119n) == False{} : Bool}

law o13_credited_excess_rejected:
  {O.option_capacity_allows(True{}, 0n, 0n, 3n, 4n) == False{} : Bool}

law o13_destructive_never:
  {O.option_risk_allows(2n) == False{} : Bool}

law o13_unknown_never:
  {O.option_risk_allows(3n) == False{} : Bool}

law o13_read_authority:
  {O.option_risk_allows(0n) == True{} : Bool}

law o13_reversible_authority:
  {O.option_risk_allows(1n) == True{} : Bool}

law o13_destructive_request_never:
  for credited: Bool
  for actual: Nat
  for named: Nat
  for measured: Nat
  for count: Nat
  {O.risk_option_capacity_allows(2n, credited, actual, named, measured, count) == False{} : Bool}

# O15. Owner desktop: Sway by default, the live desktop only to look or to collaborate
# -------------------------------------------------------------------------------------
# the owned headless Sway surface is always allowed
law o15_sway_always:
  for a: O.DAct
  for i: Bool
  for l: Bool
  for k: Bool
  {O.desk_allows(O.DSway{}, a, i, l, k) == True{} : Bool}

# any agent may look at the live desktop without moving anything
law o15_look_without_moving:
  for i: Bool
  for l: Bool
  for k: Bool
  {O.desk_allows(O.DLive{}, O.DSee{False{}}, i, l, k) == True{} : Bool}

# opening a window on the live desktop needs collaboration
law o15_open_needs_collab:
  for i: Bool
  for l: Bool
  for k: Bool
  for w: T(O.desk_allows(O.DLive{}, O.DOpen{}, i, l, k))
  T(Bool.and(i, l))

# keyboard/mouse input on the live desktop needs collaboration ...
law o15_input_needs_collab:
  for i: Bool
  for l: Bool
  for k: Bool
  for w: T(O.desk_allows(O.DLive{}, O.DInput{}, i, l, k))
  T(Bool.and(i, l))

# ... and the owner asking for input
law o15_input_needs_ask:
  for i: Bool
  for l: Bool
  for k: Bool
  for w: T(O.desk_allows(O.DLive{}, O.DInput{}, i, l, k))
  T(k)

# a look that moves focus, cursor or workspace counts as input
law o15_moving_look_is_input:
  for i: Bool
  for l: Bool
  for k: Bool
  for w: T(O.desk_allows(O.DLive{}, O.DSee{True{}}, i, l, k))
  T(Bool.and(Bool.and(i, l), k))

# collaboration opens windows without a further approval prompt
law o15_collab_opens:
  for k: Bool
  {O.desk_allows(O.DLive{}, O.DOpen{}, True{}, True{}, k) == True{} : Bool}

# collaboration with an input ask drives keyboard and mouse
law o15_collab_drives:
  {O.desk_allows(O.DLive{}, O.DInput{}, True{}, True{}, True{}) == True{} : Bool}

# collaboration routes to the live desktop
law o15_collab_routes_live:
  {O.desk_surface(True{}, True{}) == O.DLive{} : O.DSurf}

# unattended work routes to Sway whatever it was asked
law o15_unattended_routes_sway:
  for l: Bool
  {O.desk_surface(False{}, l) == O.DSway{} : O.DSurf}

# a request that does not point at the owner's screen routes to Sway
law o15_no_live_ask_routes_sway:
  for i: Bool
  {O.desk_surface(i, False{}) == O.DSway{} : O.DSurf}

# collaboration never gives up on an unreachable desktop: it locates it
law o15_collab_resolves:
  {O.desk_unreachable(True{}, True{}) == O.DResolve{} : O.DNext}

# solo work reports its gap and never falls back to the live desktop
law o15_solo_reports:
  for l: Bool
  {O.desk_unreachable(False{}, l) == O.DReport{} : O.DNext}

law o15_no_ask_reports:
  for i: Bool
  {O.desk_unreachable(i, False{}) == O.DReport{} : O.DNext}
