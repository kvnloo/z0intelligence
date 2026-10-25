import Base
import ../state_fsm/state.bend as S
import ./consultation.bend as F

def T(b: Bool) -> Data:
  match b:
    case True{}:
      Unit
    case False{}:
      Empty

def Truth(e: S.Evidence) -> Data:
  match e:
    case S.SAbsent{}:
      Empty
    case S.SBool{value}:
      T(value)

law required_pass_needs_consultation:
  for available: S.Evidence
  for consulted: S.Evidence
  for w: T(S.passed(F.f(available, S.SBool{True{}}, consulted)))
  Truth(consulted)

law available_positive_passes:
  T(S.passed(F.f(S.SBool{True{}}, S.SBool{True{}}, S.SBool{True{}})))

law required_missing_receipt_violates:
  T(S.violated(F.f(S.SBool{True{}}, S.SBool{True{}}, S.SBool{False{}})))

law fast_without_receipt_passes:
  T(S.passed(F.f(S.SBool{True{}}, S.SBool{False{}}, S.SBool{False{}})))

law missing_available_unknown:
  for applicable: S.Evidence
  for consulted: S.Evidence
  T(S.unknown(F.f(S.SAbsent{}, applicable, consulted)))
