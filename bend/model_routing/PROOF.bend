import Base
import ./routing.bend as R
import ./LAWS.bend as L

def and_l(+a: Bool, -b: Bool, w: L.T(Bool.and(a, b))) -> L.T(a):
  match a:
    case True{}:
      Unit{}
    case False{}:
      Empty.absurd(L.T(False{}), w)

def and_r(a: Bool, -b: Bool, w: L.T(Bool.and(a, b))) -> L.T(b):
  match a:
    case True{}:
      w
    case False{}:
      Empty.absurd(L.T(b), w)

def L.o14_capability(c, q, s, o, r, w):
  and_l(c, Bool.and(q, R.choice(s, o, r)), w)

def L.o14_quota(c, q, s, o, r, w):
  and_l(q, R.choice(s, o, r), and_r(c, Bool.and(q, R.choice(s, o, r)), w))

def L.o14_argmin_without_reason(c, q, o, w):
  match o:
    case True{}:
      Unit{}
    case False{}:
      and_r(q, R.choice(True{}, False{}, False{}), and_r(c, Bool.and(q, R.choice(True{}, False{}, False{})), w))

def L.o14_unknown_never_credit(o):
  {==}
