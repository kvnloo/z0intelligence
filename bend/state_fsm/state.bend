# A–E state-claim predicates. This model has no effects and grants no authority.
# Python validates live event identities before constructing these abstract facts.
# Missing keys and every non-bool value map to SAbsent; only exact Python bools
# map to SBool. In particular 0/1 and strings are not Boolean evidence.
# Available false is unavailable evidence, not a disproved claim.
# All mandatory evidence is checked before any false predicate is classified.
# Sources: operations/state-laws/20261024/{STATE-LAWS,HERMES-VIEW,UNII-VIEW,
# PEER-BRIEF}.md. The shared A–E facts contract supersedes their draft sketches.

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

# A. Current, scoped observation agrees with the claim (including lifecycle claims).
def a(+available: Evidence, +current: Evidence, +scope_matches: Evidence, +agrees: Evidence) -> Verdict:
  decide(available, Bool.and(known(current), Bool.and(known(scope_matches), known(agrees))), Bool.and(affirmed(current), Bool.and(affirmed(scope_matches), affirmed(agrees))))

# B. Disk and loaded process bindings match the canonical deployment manifest.
def b(+available: Evidence, +disk_matches: Evidence, +process_matches: Evidence, +canonical: Evidence) -> Verdict:
  decide(available, Bool.and(known(disk_matches), Bool.and(known(process_matches), known(canonical))), Bool.and(affirmed(disk_matches), Bool.and(affirmed(process_matches), affirmed(canonical))))

# C. Current revocation epoch, no active hits, complete searched coverage.
def c(+available: Evidence, +epoch_current: Evidence, +no_active_hits: Evidence, +coverage_complete: Evidence) -> Verdict:
  decide(available, Bool.and(known(epoch_current), Bool.and(known(no_active_hits), known(coverage_complete))), Bool.and(affirmed(epoch_current), Bool.and(affirmed(no_active_hits), affirmed(coverage_complete))))

# D. Typed, verified output with positive work, good transport and current target.
def d(+available: Evidence, +typed_receipt: Evidence, +output_verified: Evidence, +work_positive: Evidence, +transport_ok: Evidence, +target_current: Evidence) -> Verdict:
  decide(available, Bool.and(known(typed_receipt), Bool.and(known(output_verified), Bool.and(known(work_positive), Bool.and(known(transport_ok), known(target_current))))), Bool.and(affirmed(typed_receipt), Bool.and(affirmed(output_verified), Bool.and(affirmed(work_positive), Bool.and(affirmed(transport_ok), affirmed(target_current))))))

# E. Measured claim agrees at the exact observation scope (including rates/capabilities).
def e(+available: Evidence, +measured: Evidence, +scope_matches: Evidence, +agrees: Evidence) -> Verdict:
  decide(available, Bool.and(known(measured), Bool.and(known(scope_matches), known(agrees))), Bool.and(affirmed(measured), Bool.and(affirmed(scope_matches), affirmed(agrees))))

