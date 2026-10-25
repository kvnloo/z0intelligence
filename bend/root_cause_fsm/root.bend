# Report-only root-cause eligibility predicates; no effects or enforcement authority.
# R3 models rollback recommendation eligibility, never execution.
# R7 age_expired is derived from age_seconds >= 259200 by the parity adapter.
import Base

type Evidence is Data:
  SAbsent{}
  SBool{value: Bool}

type Verdict is Data:
  SUnknown{}
  SPass{}
  SViolation{}

def known(e: Evidence) -> Bool:
  match e:
    case SAbsent{}:
      False{}
    case SBool{value}:
      True{}

def affirmed(e: Evidence) -> Bool:
  match e:
    case SAbsent{}:
      False{}
    case SBool{value}:
      value

def finish(holds: Bool) -> Verdict:
  match holds:
    case True{}:
      SPass{}
    case False{}:
      SViolation{}

def decide_known(complete: Bool, holds: Bool) -> Verdict:
  match complete:
    case True{}:
      finish(holds)
    case False{}:
      SUnknown{}

def decide(available: Evidence, complete: Bool, holds: Bool) -> Verdict:
  match available:
    case SAbsent{}:
      SUnknown{}
    case SBool{value}:
      match value:
        case True{}:
          decide_known(complete, holds)
        case False{}:
          SUnknown{}

def passed(v: Verdict) -> Bool:
  match v:
    case SPass{}:
      True{}
    case _:
      False{}

def unknown(v: Verdict) -> Bool:
  match v:
    case SUnknown{}:
      True{}
    case _:
      False{}

def violated(v: Verdict) -> Bool:
  match v:
    case SViolation{}:
      True{}
    case _:
      False{}

def r1(+target: Evidence, +before_metric: Evidence, +functional_probe: Evidence, +expiry_valid: Evidence) -> Verdict:
  decide(SBool{True{}}, Bool.and(known(target), Bool.and(known(before_metric), Bool.and(known(functional_probe), known(expiry_valid)))), Bool.and(affirmed(target), Bool.and(affirmed(before_metric), Bool.and(affirmed(functional_probe), affirmed(expiry_valid)))))

def r2(+same_metric: Evidence, +in_bounds_twice: Evidence, +separated_24h: Evidence) -> Verdict:
  decide(SBool{True{}}, Bool.and(known(same_metric), Bool.and(known(in_bounds_twice), known(separated_24h))), Bool.and(affirmed(same_metric), Bool.and(affirmed(in_bounds_twice), affirmed(separated_24h))))

def r3(+probe_failed: Evidence, +owned: Evidence, +leased: Evidence) -> Verdict:
  decide(SBool{True{}}, Bool.and(known(probe_failed), Bool.and(known(owned), known(leased))), Bool.and(affirmed(probe_failed), Bool.and(affirmed(owned), affirmed(leased))))

def r4(+same_window: Evidence, +share_at_least_30: Evidence) -> Verdict:
  decide(SBool{True{}}, Bool.and(known(same_window), known(share_at_least_30)), Bool.and(affirmed(same_window), affirmed(share_at_least_30)))

def r7(+report_only: Evidence, +same_finding: Evidence, +age_expired: Evidence) -> Verdict:
  decide(SBool{True{}}, Bool.and(known(report_only), Bool.and(known(same_finding), known(age_expired))), Bool.and(affirmed(report_only), Bool.and(affirmed(same_finding), affirmed(age_expired))))


def r3_finish(v: Verdict, failed: Bool) -> Nat:
  match v:
    case SUnknown{}:
      0n
    case SPass{}:
      3n
    case SViolation{}:
      match failed:
        case True{}:
          0n
        case False{}:
          1n
# Full R3 result codes: 0 escalate, 1 allow, 3 rollback recommendation.
def r3_result(+probe_failed: Evidence, owned: Evidence, leased: Evidence) -> Nat:
  r3_finish(r3(probe_failed, owned, leased), affirmed(probe_failed))

