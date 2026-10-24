# Abstract gate laws, not floating-point scorer correctness or rollout approval.
import Base
import ./routing.bend as R

def T(b: Bool) -> Data:
  match b:
    case True{}:
      Unit
    case False{}:
      Empty

law o14_capability:
  for c: Bool
  for q: Bool
  for s: Bool
  for o: Bool
  for r: Bool
  for w: T(R.admits(c, q, s, o, r))
  T(c)

law o14_quota:
  for c: Bool
  for q: Bool
  for s: Bool
  for o: Bool
  for r: Bool
  for w: T(R.admits(c, q, s, o, r))
  T(q)

law o14_argmin_without_reason:
  for c: Bool
  for q: Bool
  for o: Bool
  for w: T(R.admits(c, q, True{}, o, False{}))
  T(o)

law o14_unknown_never_credit:
  for o: Bool
  {R.credit(False{}, o) == False{} : Bool}
