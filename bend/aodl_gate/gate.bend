# The pure gate: request tokens in, verdict out. No effects.

import Base
import ./core.bend as C
import ./rules.bend as R
import ./transition.bend as X

type Verdict is Data:
  Allow{}
  Deny{codes: List<&2, U32>}

def of_issues(xs: List<&2, U32>) -> Verdict:
  match xs:
    case Nil{}:
      Allow{}
    case h <> t:
      Deny{h <> t}

# Documents
# ---------

def doc.of(m: Maybe<&2, C.Req>) -> Verdict:
  match m:
    case None{}:
      Deny{[0]}
    case Some{q}:
      of_issues(R.req_issues(q))

def doc(ts: List<&2, U32>) -> Verdict:
  doc.of(C.parse(ts))

# Transitions
# -----------

def tr.pick(+t: X.Tr, ok: Bool) -> Verdict:
  match ok:
    case True{}:
      Allow{}
    case False{}:
      Deny{X.reasons(t)}

def tr.of(m: Maybe<&2, X.Tr>) -> Verdict:
  match m:
    case None{}:
      Deny{[0]}
    case Some{+t}:
      tr.pick(t, X.allows(t))

def tr(ts: List<&2, U32>) -> Verdict:
  tr.of(X.parse(ts))

# Requests
# --------

# mode 1: a document; mode 2: a transition; anything else is refused
def decide.mode(n: Nat, t: List<&2, U32>) -> Verdict:
  match n:
    case 1n:
      doc(t)
    case 2n:
      tr(t)
    case _:
      Deny{[0]}

def decide(ts: List<&2, U32>) -> Verdict:
  match ts:
    case Nil{}:
      Deny{[0]}
    case h <> t:
      decide.mode(U32.to_nat(h), t)
