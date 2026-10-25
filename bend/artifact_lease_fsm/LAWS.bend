import Base
import ./lease.bend as G

def T(b: Bool) -> Data:
  match b:
    case True{}:
      Unit
    case False{}:
      Empty

law g_needs_expired:
  for e: Bool
  for a: Bool
  for n: Bool
  for s: Bool
  for r: Bool
  for w: T(G.eligible(e, a, n, s, r))
  T(e)

law g_needs_allowlisted:
  for e: Bool
  for a: Bool
  for n: Bool
  for s: Bool
  for r: Bool
  for w: T(G.eligible(e, a, n, s, r))
  T(a)

law g_needs_nonlive:
  for e: Bool
  for a: Bool
  for n: Bool
  for s: Bool
  for r: Bool
  for w: T(G.eligible(e, a, n, s, r))
  T(n)

law g_needs_no_symlink:
  for e: Bool
  for a: Bool
  for n: Bool
  for s: Bool
  for r: Bool
  for w: T(G.eligible(e, a, n, s, r))
  T(s)

law g_needs_archive:
  for e: Bool
  for a: Bool
  for n: Bool
  for s: Bool
  for r: Bool
  for w: T(G.eligible(e, a, n, s, r))
  T(r)

law g_complete:
  for e: Bool
  for a: Bool
  for n: Bool
  for s: Bool
  for r: Bool
  for he: T(e)
  for ha: T(a)
  for hn: T(n)
  for hs: T(s)
  for hr: T(r)
  T(G.eligible(e, a, n, s, r))
