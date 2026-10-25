# O14 abstract admission projection. Numeric scoring lives ONLY in Kerdoios.
import Base

def choice(scored: Bool, optimal: Bool, reason: Bool) -> Bool:
  match scored:
    case False{}:
      True{}
    case True{}:
      Bool.or(optimal, reason)

def admits(capability: Bool, quota: Bool, scored: Bool, optimal: Bool, reason: Bool) -> Bool:
  Bool.and(capability, Bool.and(quota, choice(scored, optimal, reason)))

def credit(scored: Bool, optimal: Bool) -> Bool:
  Bool.and(scored, optimal)
