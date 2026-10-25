# F grants no authority and has no effects. Runtime binds session/turn/time.
import Base
import ../state_fsm/state.bend as S

def f(+available: S.Evidence, +applicable: S.Evidence, +consulted: S.Evidence) -> S.Verdict:
  S.decide(available, Bool.and(S.known(applicable), S.known(consulted)), Bool.or(Bool.not(S.affirmed(applicable)), S.affirmed(consulted)))
