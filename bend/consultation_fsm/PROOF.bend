import Base
import ../state_fsm/state.bend as S
import ./consultation.bend as F
import ./LAWS.bend as Laws

# These helpers consume arbitrary witnesses, not finite example assumptions.
def affirmed_truth(e: S.Evidence, w: Laws.T(S.affirmed(e))) -> Laws.Truth(e):
  match e:
    case S.SAbsent{}:
      w
    case S.SBool{value}:
      w

def finish_holds(holds: Bool, w: Laws.T(S.passed(S.finish(holds)))) -> Laws.T(holds):
  match holds:
    case True{}:
      Unit{}
    case False{}:
      w

def known_holds(complete: Bool, holds: Bool, w: Laws.T(S.passed(S.decide_known(complete, holds)))) -> Laws.T(holds):
  match complete:
    case True{}:
      finish_holds(holds, w)
    case False{}:
      Empty.absurd(Laws.T(holds), w)

def decide_holds(available: S.Evidence, complete: Bool, holds: Bool, w: Laws.T(S.passed(S.decide(available, complete, holds)))) -> Laws.T(holds):
  match available:
    case S.SAbsent{}:
      Empty.absurd(Laws.T(holds), w)
    case S.SBool{value}:
      match value:
        case True{}:
          known_holds(complete, holds, w)
        case False{}:
          Empty.absurd(Laws.T(holds), w)

def Laws.required_pass_needs_consultation(available, consulted, w):
  +available = available
  +consulted = consulted
  affirmed_truth(consulted, decide_holds(available, Bool.and(S.known(S.SBool{True{}}), S.known(consulted)), Bool.or(Bool.not(S.affirmed(S.SBool{True{}})), S.affirmed(consulted)), w))

def Laws.available_positive_passes():
  Unit{}

def Laws.required_missing_receipt_violates():
  Unit{}

def Laws.fast_without_receipt_passes():
  Unit{}

def Laws.missing_available_unknown(applicable, consulted):
  Unit{}
