import Base
import ./standing.bend as S
import ./LAWS.bend as L

def and_l(+a: Bool, -b: Bool, w: L.T(Bool.and(a, b))) -> L.T(a):
  match a:
    case True{}:
      Unit{}
    case False{}:
      Empty.absurd(L.T(False{}), w)

def and_r(+a: Bool, -b: Bool, w: L.T(Bool.and(a, b))) -> L.T(b):
  match a:
    case True{}:
      w
    case False{}:
      Empty.absurd(L.T(b), w)

def L.gpt_only_via_fx_pi(v, w):
  w

def L.delete_needs_creator_receipt(c, w):
  w

def L.public_needs_owner_ok(o, w):
  w

def L.admits_keeps_gpt(gv, x, xc, p, po, w):
  and_l(S.gpt_ok(True{}, gv), Bool.and(S.delete_ok(x, xc), S.public_ok(p, po)), w)

def L.admits_keeps_delete(g, gv, xc, p, po, w):
  and_l(S.delete_ok(True{}, xc), S.public_ok(p, po), and_r(S.gpt_ok(g, gv), Bool.and(S.delete_ok(True{}, xc), S.public_ok(p, po)), w))

def L.admits_keeps_public(g, gv, x, xc, po, w):
  and_r(S.delete_ok(x, xc), S.public_ok(True{}, po), and_r(S.gpt_ok(g, gv), Bool.and(S.delete_ok(x, xc), S.public_ok(True{}, po)), w))

def L.receipted_delete_admitted():
  {==}

def L.ordinary_call_admitted():
  {==}
