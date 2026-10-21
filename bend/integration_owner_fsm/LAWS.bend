# I1-I5 are local safety/monitor laws. External facts and liveness are not proven.
import Base
import ./fsm.bend as F

# I5 all-event entry safety, including park/conflict/observe/expiry events.
def safe_entry(s: F.St, t: F.St) -> Bool:
  match s t:
    case F.OwnerAccepted{} _:
      True{}
    case F.Merged{} _:
      True{}
    case _ F.Merged{}:
      False{}
    case _ _:
      True{}

law I5_all_events:
  for +s: F.St
  for e: F.Ev
  {safe_entry(s, F.step(s, e)) == True{} : Bool}

law park_expires:
  {F.step(F.Parked{}, F.Expire{True{}}) == F.Queued{} : F.St}

law I1_deadline:
  for +age: Nat
  {F.overdue(age, True{}, False{}) == False{} : Bool}

law I1_parked:
  for +age: Nat
  {F.overdue(age, False{}, True{}) == False{} : Bool}

law I1_due:
  {F.overdue(86400n, False{}, False{}) == True{} : Bool}

law I2_deploy:
  {F.step(F.OnIntegrationBranch{}, F.Deploy{False{}}) == F.OnIntegrationBranch{} : F.St}

law I2_drift:
  {F.step(F.Deployed{}, F.Observe{False{}}) == F.OnIntegrationBranch{} : F.St}

law I2_revoke_acceptance:
  {F.step(F.OwnerAccepted{}, F.Observe{False{}}) == F.OnIntegrationBranch{} : F.St}

law I3_integration:
  for +stack: Bool
  for +independent: Bool
  {F.step(F.Queued{}, F.Integrate{False{}, stack, independent}) == F.Queued{} : F.St}

law I4_stack:
  for +lease: Bool
  for +independent: Bool
  {F.step(F.Queued{}, F.Integrate{lease, False{}, independent}) == F.Queued{} : F.St}

law independent_review:
  for +lease: Bool
  for +stack: Bool
  {F.step(F.Queued{}, F.Integrate{lease, stack, False{}}) == F.Queued{} : F.St}

law I5_accept:
  {F.step(F.Deployed{}, F.Accept{False{}}) == F.Deployed{} : F.St}

law park_reason:
  for +s: F.St
  for +future: Bool
  {F.step(s, F.Park{False{}, future}) == s : F.St}

law park_expiry:
  for +s: F.St
  for +reason: Bool
  {F.step(s, F.Park{reason, False{}}) == s : F.St}

def merge_target(s: F.St) -> F.St:
  match s:
    case F.OwnerAccepted{}:
      F.Merged{}
    case _:
      s

law I5_merge_source:
  for +s: F.St
  {F.step(s, F.Merge{}) == merge_target(s) : F.St}

law I3_no_steal:
  for +o: Nat
  for +a: Nat
  for +expiry: Nat
  for +until: Nat
  for +now: Nat
  for h: {Nat.is_ge(now, expiry) == False{} : Bool}
  {F.acquire(F.Held{o, expiry}, a, until, now) == F.Held{o, expiry} : F.Lease}
