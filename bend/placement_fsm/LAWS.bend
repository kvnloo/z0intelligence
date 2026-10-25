import Base
import ./placement.bend as S

def T(b: Bool) -> Data:
  match b:
    case True{}:
      Unit
    case False{}:
      Empty

law unavailable_unknown:
  for agent: Bool
  for scoped: Bool
  T(S.unknown(S.h(False{}, agent, scoped)))

law unscoped_agent_violation:
  T(S.violated(S.h(True{}, True{}, False{})))

law scoped_agent_pass:
  T(S.passed(S.h(True{}, True{}, True{})))

law non_agent_pass:
  for scoped: Bool
  T(S.passed(S.h(True{}, False{}, scoped)))
