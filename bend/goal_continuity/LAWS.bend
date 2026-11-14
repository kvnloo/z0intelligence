# Claims about the pure model, conditional on host attestations in contract.bend.
# Check from the repository root with Bend 2.0.34 (BendTT, not Bend 1):
# PATH="$HOME/.elan/bin:$PATH" BEND_NO_TELEMETRY=1 "${BEND_BIN:-$HOME/.bend/bin/bend}" bend/goal_continuity/PROOF.bend --verdict
# The existing aodl_gate experiment records Bend 2.0.34 and Lean 4.34.0;
# the command's actual compiler and Lean versions must be recorded separately.
import Base
import ./contract.bend as C

def T(b: Bool) -> Data:
  match b:
    case True{}:
      Unit
    case False{}:
      Empty

def owned(d: C.Decision) -> Bool:
  match d:
    case C.Idle{}:
      False{}
    case _:
      True{}

# Active means unfinished and not explicitly paused or stopped. This law
# excludes only the unowned Idle result; it does not establish real liveness.
law unfinished_not_idle:
  for +r: C.Receipts
  T(owned(C.decide(C.Active{r})))

# Closed behavior checks also make the individual branches load-bearing.
law pause_authoritative:
  {C.decide(C.Paused{}) == C.RespectPause{} : C.Decision}

law stop_authoritative:
  {C.decide(C.Stopped{}) == C.RespectStop{} : C.Decision}

law all_checks_complete:
  {C.decide(C.Complete{}) == C.Finish{} : C.Decision}

law missing_receipts_load:
  {C.decide(C.Active{C.Missing{}}) == C.LoadWorkflow{} : C.Decision}

law authorized_work_executes:
  {C.decide(C.Active{C.Loaded{C.AuthorizedWork{}}}) == C.Execute{} : C.Decision}

law exhausted_plan_replans:
  {C.decide(C.Active{C.Loaded{C.ExhaustedPlan{}}}) == C.Replan{} : C.Decision}

law registered_dependency_waits:
  for +id: Nat
  {C.decide(C.Active{C.Loaded{C.ExternalDependency{C.Registered{id}}}}) == C.Wait{id} : C.Decision}

law unregistered_dependency_replans:
  {C.decide(C.Active{C.Loaded{C.ExternalDependency{C.Unregistered{}}}}) == C.Replan{} : C.Decision}
