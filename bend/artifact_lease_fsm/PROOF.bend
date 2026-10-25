# Arbitrary Boolean obligations; no assumed axioms or finite enumeration.
import Base
import ./lease.bend as G
import ./LAWS.bend as Laws

def left(+a: Bool, -b: Bool, w: Laws.T(Bool.and(a, b))) -> Laws.T(a):
  match a:
    case True{}:
      Unit{}
    case False{}:
      Empty.absurd(Laws.T(False{}), w)

def right(a: Bool, -b: Bool, w: Laws.T(Bool.and(a, b))) -> Laws.T(b):
  match a:
    case True{}:
      w
    case False{}:
      Empty.absurd(Laws.T(b), w)

def intro(a: Bool, -b: Bool, ha: Laws.T(a), hb: Laws.T(b)) -> Laws.T(Bool.and(a, b)):
  match a:
    case True{}:
      hb
    case False{}:
      Empty.absurd(Laws.T(Bool.and(False{}, b)), ha)

def Laws.g_needs_expired(e, a, n, s, r, w):
  left(e, Bool.and(a, Bool.and(n, Bool.and(s, r))), w)

def Laws.g_needs_allowlisted(e, a, n, s, r, w):
  left(a, Bool.and(n, Bool.and(s, r)), right(e, Bool.and(a, Bool.and(n, Bool.and(s, r))), w))

def Laws.g_needs_nonlive(e, a, n, s, r, w):
  left(n, Bool.and(s, r), right(a, Bool.and(n, Bool.and(s, r)), right(e, Bool.and(a, Bool.and(n, Bool.and(s, r))), w)))

def Laws.g_needs_no_symlink(e, a, n, s, r, w):
  left(s, r, right(n, Bool.and(s, r), right(a, Bool.and(n, Bool.and(s, r)), right(e, Bool.and(a, Bool.and(n, Bool.and(s, r))), w))))

def Laws.g_needs_archive(e, a, n, s, r, w):
  right(s, r, right(n, Bool.and(s, r), right(a, Bool.and(n, Bool.and(s, r)), right(e, Bool.and(a, Bool.and(n, Bool.and(s, r))), w))))

def Laws.g_complete(e, a, n, s, r, he, ha, hn, hs, hr):
  intro(e, Bool.and(a, Bool.and(n, Bool.and(s, r))), he, intro(a, Bool.and(n, Bool.and(s, r)), ha, intro(n, Bool.and(s, r), hn, intro(s, r, hs, hr))))
