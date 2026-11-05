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

def L.gui_never_steals_focus(i, w):
  w

def L.done_needs_receipt(r, w):
  w

def L.quota_backs_off(b, w):
  w

def L.admits_keeps_gui(ui, d, dr, l, lb, w):
  and_l(K.gui_ok(True{}, ui), Bool.and(K.done_ok(d, dr), K.quota_ok(l, lb)), w)

def L.admits_keeps_done(u, ui, dr, l, lb, w):
  and_l(K.done_ok(True{}, dr), K.quota_ok(l, lb), and_r(K.gui_ok(u, ui), Bool.and(K.done_ok(True{}, dr), K.quota_ok(l, lb)), w))

def L.admits_keeps_quota(u, ui, d, dr, lb, w):
  and_r(K.done_ok(d, dr), K.quota_ok(True{}, lb), and_r(K.gui_ok(u, ui), Bool.and(K.done_ok(d, dr), K.quota_ok(True{}, lb)), w))

def L.ordinary_call_admitted():
  {==}

def L.route_needs_capability(c, q, s, o, r, w):
  and_l(c, Bool.and(q, R.choice(s, o, r)), and_r(True{}, R.admits(c, q, s, o, r), w))

def L.route_needs_quota(c, q, s, o, r, w):
  and_l(q, R.choice(s, o, r), and_r(c, Bool.and(q, R.choice(s, o, r)), and_r(True{}, R.admits(c, q, s, o, r), w)))
