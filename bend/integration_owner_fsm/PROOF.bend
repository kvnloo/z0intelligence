import Base
import ./fsm.bend as F
import ./LAWS.bend as Laws

def Laws.park_expires():
  {==}

def Laws.I5_all_events(s, e):
  match s e:
    case F.Branch{} F.Draft{}:
      {==}
    case F.Branch{} F.Queue{}:
      {==}
    case F.Branch{} F.Integrate{True{}, True{}, True{}}:
      {==}
    case F.Branch{} F.Integrate{True{}, True{}, False{}}:
      {==}
    case F.Branch{} F.Integrate{True{}, False{}, True{}}:
      {==}
    case F.Branch{} F.Integrate{True{}, False{}, False{}}:
      {==}
    case F.Branch{} F.Integrate{False{}, True{}, True{}}:
      {==}
    case F.Branch{} F.Integrate{False{}, True{}, False{}}:
      {==}
    case F.Branch{} F.Integrate{False{}, False{}, True{}}:
      {==}
    case F.Branch{} F.Integrate{False{}, False{}, False{}}:
      {==}
    case F.Branch{} F.Deploy{True{}}:
      {==}
    case F.Branch{} F.Deploy{False{}}:
      {==}
    case F.Branch{} F.Accept{True{}}:
      {==}
    case F.Branch{} F.Accept{False{}}:
      {==}
    case F.Branch{} F.Merge{}:
      {==}
    case F.Branch{} F.Park{True{}, True{}}:
      {==}
    case F.Branch{} F.Park{True{}, False{}}:
      {==}
    case F.Branch{} F.Park{False{}, True{}}:
      {==}
    case F.Branch{} F.Park{False{}, False{}}:
      {==}
    case F.Branch{} F.Conflict{}:
      {==}
    case F.Branch{} F.Resume{}:
      {==}
    case F.Branch{} F.Observe{True{}}:
      {==}
    case F.Branch{} F.Observe{False{}}:
      {==}
    case F.Branch{} F.Expire{True{}}:
      {==}
    case F.Branch{} F.Expire{False{}}:
      {==}
    case F.DraftPR{} F.Draft{}:
      {==}
    case F.DraftPR{} F.Queue{}:
      {==}
    case F.DraftPR{} F.Integrate{True{}, True{}, True{}}:
      {==}
    case F.DraftPR{} F.Integrate{True{}, True{}, False{}}:
      {==}
    case F.DraftPR{} F.Integrate{True{}, False{}, True{}}:
      {==}
    case F.DraftPR{} F.Integrate{True{}, False{}, False{}}:
      {==}
    case F.DraftPR{} F.Integrate{False{}, True{}, True{}}:
      {==}
    case F.DraftPR{} F.Integrate{False{}, True{}, False{}}:
      {==}
    case F.DraftPR{} F.Integrate{False{}, False{}, True{}}:
      {==}
    case F.DraftPR{} F.Integrate{False{}, False{}, False{}}:
      {==}
    case F.DraftPR{} F.Deploy{True{}}:
      {==}
    case F.DraftPR{} F.Deploy{False{}}:
      {==}
    case F.DraftPR{} F.Accept{True{}}:
      {==}
    case F.DraftPR{} F.Accept{False{}}:
      {==}
    case F.DraftPR{} F.Merge{}:
      {==}
    case F.DraftPR{} F.Park{True{}, True{}}:
      {==}
    case F.DraftPR{} F.Park{True{}, False{}}:
      {==}
    case F.DraftPR{} F.Park{False{}, True{}}:
      {==}
    case F.DraftPR{} F.Park{False{}, False{}}:
      {==}
    case F.DraftPR{} F.Conflict{}:
      {==}
    case F.DraftPR{} F.Resume{}:
      {==}
    case F.DraftPR{} F.Observe{True{}}:
      {==}
    case F.DraftPR{} F.Observe{False{}}:
      {==}
    case F.DraftPR{} F.Expire{True{}}:
      {==}
    case F.DraftPR{} F.Expire{False{}}:
      {==}
    case F.Queued{} F.Draft{}:
      {==}
    case F.Queued{} F.Queue{}:
      {==}
    case F.Queued{} F.Integrate{True{}, True{}, True{}}:
      {==}
    case F.Queued{} F.Integrate{True{}, True{}, False{}}:
      {==}
    case F.Queued{} F.Integrate{True{}, False{}, True{}}:
      {==}
    case F.Queued{} F.Integrate{True{}, False{}, False{}}:
      {==}
    case F.Queued{} F.Integrate{False{}, True{}, True{}}:
      {==}
    case F.Queued{} F.Integrate{False{}, True{}, False{}}:
      {==}
    case F.Queued{} F.Integrate{False{}, False{}, True{}}:
      {==}
    case F.Queued{} F.Integrate{False{}, False{}, False{}}:
      {==}
    case F.Queued{} F.Deploy{True{}}:
      {==}
    case F.Queued{} F.Deploy{False{}}:
      {==}
    case F.Queued{} F.Accept{True{}}:
      {==}
    case F.Queued{} F.Accept{False{}}:
      {==}
    case F.Queued{} F.Merge{}:
      {==}
    case F.Queued{} F.Park{True{}, True{}}:
      {==}
    case F.Queued{} F.Park{True{}, False{}}:
      {==}
    case F.Queued{} F.Park{False{}, True{}}:
      {==}
    case F.Queued{} F.Park{False{}, False{}}:
      {==}
    case F.Queued{} F.Conflict{}:
      {==}
    case F.Queued{} F.Resume{}:
      {==}
    case F.Queued{} F.Observe{True{}}:
      {==}
    case F.Queued{} F.Observe{False{}}:
      {==}
    case F.Queued{} F.Expire{True{}}:
      {==}
    case F.Queued{} F.Expire{False{}}:
      {==}
    case F.OnIntegrationBranch{} F.Draft{}:
      {==}
    case F.OnIntegrationBranch{} F.Queue{}:
      {==}
    case F.OnIntegrationBranch{} F.Integrate{True{}, True{}, True{}}:
      {==}
    case F.OnIntegrationBranch{} F.Integrate{True{}, True{}, False{}}:
      {==}
    case F.OnIntegrationBranch{} F.Integrate{True{}, False{}, True{}}:
      {==}
    case F.OnIntegrationBranch{} F.Integrate{True{}, False{}, False{}}:
      {==}
    case F.OnIntegrationBranch{} F.Integrate{False{}, True{}, True{}}:
      {==}
    case F.OnIntegrationBranch{} F.Integrate{False{}, True{}, False{}}:
      {==}
    case F.OnIntegrationBranch{} F.Integrate{False{}, False{}, True{}}:
      {==}
    case F.OnIntegrationBranch{} F.Integrate{False{}, False{}, False{}}:
      {==}
    case F.OnIntegrationBranch{} F.Deploy{True{}}:
      {==}
    case F.OnIntegrationBranch{} F.Deploy{False{}}:
      {==}
    case F.OnIntegrationBranch{} F.Accept{True{}}:
      {==}
    case F.OnIntegrationBranch{} F.Accept{False{}}:
      {==}
    case F.OnIntegrationBranch{} F.Merge{}:
      {==}
    case F.OnIntegrationBranch{} F.Park{True{}, True{}}:
      {==}
    case F.OnIntegrationBranch{} F.Park{True{}, False{}}:
      {==}
    case F.OnIntegrationBranch{} F.Park{False{}, True{}}:
      {==}
    case F.OnIntegrationBranch{} F.Park{False{}, False{}}:
      {==}
    case F.OnIntegrationBranch{} F.Conflict{}:
      {==}
    case F.OnIntegrationBranch{} F.Resume{}:
      {==}
    case F.OnIntegrationBranch{} F.Observe{True{}}:
      {==}
    case F.OnIntegrationBranch{} F.Observe{False{}}:
      {==}
    case F.OnIntegrationBranch{} F.Expire{True{}}:
      {==}
    case F.OnIntegrationBranch{} F.Expire{False{}}:
      {==}
    case F.Deployed{} F.Draft{}:
      {==}
    case F.Deployed{} F.Queue{}:
      {==}
    case F.Deployed{} F.Integrate{True{}, True{}, True{}}:
      {==}
    case F.Deployed{} F.Integrate{True{}, True{}, False{}}:
      {==}
    case F.Deployed{} F.Integrate{True{}, False{}, True{}}:
      {==}
    case F.Deployed{} F.Integrate{True{}, False{}, False{}}:
      {==}
    case F.Deployed{} F.Integrate{False{}, True{}, True{}}:
      {==}
    case F.Deployed{} F.Integrate{False{}, True{}, False{}}:
      {==}
    case F.Deployed{} F.Integrate{False{}, False{}, True{}}:
      {==}
    case F.Deployed{} F.Integrate{False{}, False{}, False{}}:
      {==}
    case F.Deployed{} F.Deploy{True{}}:
      {==}
    case F.Deployed{} F.Deploy{False{}}:
      {==}
    case F.Deployed{} F.Accept{True{}}:
      {==}
    case F.Deployed{} F.Accept{False{}}:
      {==}
    case F.Deployed{} F.Merge{}:
      {==}
    case F.Deployed{} F.Park{True{}, True{}}:
      {==}
    case F.Deployed{} F.Park{True{}, False{}}:
      {==}
    case F.Deployed{} F.Park{False{}, True{}}:
      {==}
    case F.Deployed{} F.Park{False{}, False{}}:
      {==}
    case F.Deployed{} F.Conflict{}:
      {==}
    case F.Deployed{} F.Resume{}:
      {==}
    case F.Deployed{} F.Observe{True{}}:
      {==}
    case F.Deployed{} F.Observe{False{}}:
      {==}
    case F.Deployed{} F.Expire{True{}}:
      {==}
    case F.Deployed{} F.Expire{False{}}:
      {==}
    case F.OwnerAccepted{} F.Draft{}:
      {==}
    case F.OwnerAccepted{} F.Queue{}:
      {==}
    case F.OwnerAccepted{} F.Integrate{True{}, True{}, True{}}:
      {==}
    case F.OwnerAccepted{} F.Integrate{True{}, True{}, False{}}:
      {==}
    case F.OwnerAccepted{} F.Integrate{True{}, False{}, True{}}:
      {==}
    case F.OwnerAccepted{} F.Integrate{True{}, False{}, False{}}:
      {==}
    case F.OwnerAccepted{} F.Integrate{False{}, True{}, True{}}:
      {==}
    case F.OwnerAccepted{} F.Integrate{False{}, True{}, False{}}:
      {==}
    case F.OwnerAccepted{} F.Integrate{False{}, False{}, True{}}:
      {==}
    case F.OwnerAccepted{} F.Integrate{False{}, False{}, False{}}:
      {==}
    case F.OwnerAccepted{} F.Deploy{True{}}:
      {==}
    case F.OwnerAccepted{} F.Deploy{False{}}:
      {==}
    case F.OwnerAccepted{} F.Accept{True{}}:
      {==}
    case F.OwnerAccepted{} F.Accept{False{}}:
      {==}
    case F.OwnerAccepted{} F.Merge{}:
      {==}
    case F.OwnerAccepted{} F.Park{True{}, True{}}:
      {==}
    case F.OwnerAccepted{} F.Park{True{}, False{}}:
      {==}
    case F.OwnerAccepted{} F.Park{False{}, True{}}:
      {==}
    case F.OwnerAccepted{} F.Park{False{}, False{}}:
      {==}
    case F.OwnerAccepted{} F.Conflict{}:
      {==}
    case F.OwnerAccepted{} F.Resume{}:
      {==}
    case F.OwnerAccepted{} F.Observe{True{}}:
      {==}
    case F.OwnerAccepted{} F.Observe{False{}}:
      {==}
    case F.OwnerAccepted{} F.Expire{True{}}:
      {==}
    case F.OwnerAccepted{} F.Expire{False{}}:
      {==}
    case F.Merged{} F.Draft{}:
      {==}
    case F.Merged{} F.Queue{}:
      {==}
    case F.Merged{} F.Integrate{True{}, True{}, True{}}:
      {==}
    case F.Merged{} F.Integrate{True{}, True{}, False{}}:
      {==}
    case F.Merged{} F.Integrate{True{}, False{}, True{}}:
      {==}
    case F.Merged{} F.Integrate{True{}, False{}, False{}}:
      {==}
    case F.Merged{} F.Integrate{False{}, True{}, True{}}:
      {==}
    case F.Merged{} F.Integrate{False{}, True{}, False{}}:
      {==}
    case F.Merged{} F.Integrate{False{}, False{}, True{}}:
      {==}
    case F.Merged{} F.Integrate{False{}, False{}, False{}}:
      {==}
    case F.Merged{} F.Deploy{True{}}:
      {==}
    case F.Merged{} F.Deploy{False{}}:
      {==}
    case F.Merged{} F.Accept{True{}}:
      {==}
    case F.Merged{} F.Accept{False{}}:
      {==}
    case F.Merged{} F.Merge{}:
      {==}
    case F.Merged{} F.Park{True{}, True{}}:
      {==}
    case F.Merged{} F.Park{True{}, False{}}:
      {==}
    case F.Merged{} F.Park{False{}, True{}}:
      {==}
    case F.Merged{} F.Park{False{}, False{}}:
      {==}
    case F.Merged{} F.Conflict{}:
      {==}
    case F.Merged{} F.Resume{}:
      {==}
    case F.Merged{} F.Observe{True{}}:
      {==}
    case F.Merged{} F.Observe{False{}}:
      {==}
    case F.Merged{} F.Expire{True{}}:
      {==}
    case F.Merged{} F.Expire{False{}}:
      {==}
    case F.Parked{} F.Draft{}:
      {==}
    case F.Parked{} F.Queue{}:
      {==}
    case F.Parked{} F.Integrate{True{}, True{}, True{}}:
      {==}
    case F.Parked{} F.Integrate{True{}, True{}, False{}}:
      {==}
    case F.Parked{} F.Integrate{True{}, False{}, True{}}:
      {==}
    case F.Parked{} F.Integrate{True{}, False{}, False{}}:
      {==}
    case F.Parked{} F.Integrate{False{}, True{}, True{}}:
      {==}
    case F.Parked{} F.Integrate{False{}, True{}, False{}}:
      {==}
    case F.Parked{} F.Integrate{False{}, False{}, True{}}:
      {==}
    case F.Parked{} F.Integrate{False{}, False{}, False{}}:
      {==}
    case F.Parked{} F.Deploy{True{}}:
      {==}
    case F.Parked{} F.Deploy{False{}}:
      {==}
    case F.Parked{} F.Accept{True{}}:
      {==}
    case F.Parked{} F.Accept{False{}}:
      {==}
    case F.Parked{} F.Merge{}:
      {==}
    case F.Parked{} F.Park{True{}, True{}}:
      {==}
    case F.Parked{} F.Park{True{}, False{}}:
      {==}
    case F.Parked{} F.Park{False{}, True{}}:
      {==}
    case F.Parked{} F.Park{False{}, False{}}:
      {==}
    case F.Parked{} F.Conflict{}:
      {==}
    case F.Parked{} F.Resume{}:
      {==}
    case F.Parked{} F.Observe{True{}}:
      {==}
    case F.Parked{} F.Observe{False{}}:
      {==}
    case F.Parked{} F.Expire{True{}}:
      {==}
    case F.Parked{} F.Expire{False{}}:
      {==}
    case F.Conflicted{} F.Draft{}:
      {==}
    case F.Conflicted{} F.Queue{}:
      {==}
    case F.Conflicted{} F.Integrate{True{}, True{}, True{}}:
      {==}
    case F.Conflicted{} F.Integrate{True{}, True{}, False{}}:
      {==}
    case F.Conflicted{} F.Integrate{True{}, False{}, True{}}:
      {==}
    case F.Conflicted{} F.Integrate{True{}, False{}, False{}}:
      {==}
    case F.Conflicted{} F.Integrate{False{}, True{}, True{}}:
      {==}
    case F.Conflicted{} F.Integrate{False{}, True{}, False{}}:
      {==}
    case F.Conflicted{} F.Integrate{False{}, False{}, True{}}:
      {==}
    case F.Conflicted{} F.Integrate{False{}, False{}, False{}}:
      {==}
    case F.Conflicted{} F.Deploy{True{}}:
      {==}
    case F.Conflicted{} F.Deploy{False{}}:
      {==}
    case F.Conflicted{} F.Accept{True{}}:
      {==}
    case F.Conflicted{} F.Accept{False{}}:
      {==}
    case F.Conflicted{} F.Merge{}:
      {==}
    case F.Conflicted{} F.Park{True{}, True{}}:
      {==}
    case F.Conflicted{} F.Park{True{}, False{}}:
      {==}
    case F.Conflicted{} F.Park{False{}, True{}}:
      {==}
    case F.Conflicted{} F.Park{False{}, False{}}:
      {==}
    case F.Conflicted{} F.Conflict{}:
      {==}
    case F.Conflicted{} F.Resume{}:
      {==}
    case F.Conflicted{} F.Observe{True{}}:
      {==}
    case F.Conflicted{} F.Observe{False{}}:
      {==}
    case F.Conflicted{} F.Expire{True{}}:
      {==}
    case F.Conflicted{} F.Expire{False{}}:
      {==}

def and_false(b: Bool) -> {Bool.and(b, False{}) == False{} : Bool}:
  match b:
    case True{}:
      {==}
    case False{}:
      {==}

def Laws.I1_deadline(age):
  {==}

def Laws.I1_parked(age):
  {==}

def Laws.I1_due():
  {==}

def Laws.I2_deploy():
  {==}

def Laws.I2_drift():
  {==}

def Laws.I2_revoke_acceptance():
  {==}

def Laws.I3_integration(stack, independent):
  {==}

def Laws.I4_stack(lease, independent):
  match lease independent :
    case True{} True{}:
      {==}
    case True{} False{}:
      {==}
    case False{} True{}:
      {==}
    case False{} False{}:
      {==}

def Laws.independent_review(lease, stack):
  match lease stack :
    case True{} True{}:
      {==}
    case True{} False{}:
      {==}
    case False{} True{}:
      {==}
    case False{} False{}:
      {==}

def Laws.I5_accept():
  {==}

def Laws.park_reason(s, future):
  match s:
    case F.Branch{}:
      {==}
    case F.DraftPR{}:
      {==}
    case F.Queued{}:
      {==}
    case F.OnIntegrationBranch{}:
      {==}
    case F.Deployed{}:
      {==}
    case F.OwnerAccepted{}:
      {==}
    case F.Merged{}:
      {==}
    case F.Parked{}:
      {==}
    case F.Conflicted{}:
      {==}

def Laws.park_expiry(s, reason):
  match s reason:
    case F.Branch{} True{}:
      {==}
    case F.Branch{} False{}:
      {==}
    case F.DraftPR{} True{}:
      {==}
    case F.DraftPR{} False{}:
      {==}
    case F.Queued{} True{}:
      {==}
    case F.Queued{} False{}:
      {==}
    case F.OnIntegrationBranch{} True{}:
      {==}
    case F.OnIntegrationBranch{} False{}:
      {==}
    case F.Deployed{} True{}:
      {==}
    case F.Deployed{} False{}:
      {==}
    case F.OwnerAccepted{} True{}:
      {==}
    case F.OwnerAccepted{} False{}:
      {==}
    case F.Merged{} True{}:
      {==}
    case F.Merged{} False{}:
      {==}
    case F.Parked{} True{}:
      {==}
    case F.Parked{} False{}:
      {==}
    case F.Conflicted{} True{}:
      {==}
    case F.Conflicted{} False{}:
      {==}

def Laws.I5_merge_source(s):
  match s:
    case F.Branch{}:
      {==}
    case F.DraftPR{}:
      {==}
    case F.Queued{}:
      {==}
    case F.OnIntegrationBranch{}:
      {==}
    case F.Deployed{}:
      {==}
    case F.OwnerAccepted{}:
      {==}
    case F.Merged{}:
      {==}
    case F.Parked{}:
      {==}
    case F.Conflicted{}:
      {==}

def Laws.I3_no_steal(o, a, expiry, until, now, h):
  %Equal.sym(Bool, Nat.is_ge(now, expiry), False{}, h) : {F.lpick(Bool.and(_, Nat.is_gt(until, now)), F.Held{a, until}, F.Held{o, expiry}) == F.Held{o, expiry} : F.Lease}
  {==}
