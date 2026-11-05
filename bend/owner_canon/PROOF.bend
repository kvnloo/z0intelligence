import Base
import ../model_routing/routing.bend as R
import ./canon.bend as K
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

def L.gui_never_steals_focus(i, w):
  w

def L.done_needs_receipt(r, w):
  w

def L.quota_backs_off(b, w):
  w

def L.delete_needs_creator_receipt(c, w):
  w

def L.public_needs_owner_ok(o, w):
  w

def L.admits_keeps_public_ok(g, gv, u, ui, d, dr, l, lb, x, xc, po, w):
  and_r(K.delete_ok(x, xc), K.public_ok(True{}, po),
    and_r(K.quota_ok(l, lb), Bool.and(K.delete_ok(x, xc), K.public_ok(True{}, po)),
      and_r(K.done_ok(d, dr), Bool.and(K.quota_ok(l, lb), Bool.and(K.delete_ok(x, xc), K.public_ok(True{}, po))),
        and_r(K.gui_ok(u, ui), Bool.and(K.done_ok(d, dr), Bool.and(K.quota_ok(l, lb), Bool.and(K.delete_ok(x, xc), K.public_ok(True{}, po)))),
          and_r(K.gpt_ok(g, gv), Bool.and(K.gui_ok(u, ui), Bool.and(K.done_ok(d, dr), Bool.and(K.quota_ok(l, lb), Bool.and(K.delete_ok(x, xc), K.public_ok(True{}, po))))), w)))))

def L.admits_keeps_gpt_route(gv, u, ui, d, dr, l, lb, x, xc, p, po, w):
  and_l(K.gpt_ok(True{}, gv), Bool.and(K.gui_ok(u, ui), Bool.and(K.done_ok(d, dr), Bool.and(K.quota_ok(l, lb), Bool.and(K.delete_ok(x, xc), K.public_ok(p, po))))), w)

def L.ordinary_call_admitted():
  {==}

def L.route_needs_o14_quota(c, q, s, o, r, g, gv, w):
  and_l(q, R.choice(s, o, r), and_r(c, Bool.and(q, R.choice(s, o, r)), and_r(K.gpt_ok(g, gv), R.admits(c, q, s, o, r), w)))

def L.route_gpt_via_fx_pi(c, q, s, o, r, gv, w):
  and_l(K.gpt_ok(True{}, gv), R.admits(c, q, s, o, r), w)
