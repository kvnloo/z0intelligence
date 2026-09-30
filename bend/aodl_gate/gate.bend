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

def doc(ts: List<&2, String>) -> Verdict:
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

def tr(ts: List<&2, String>) -> Verdict:
  tr.of(X.parse(ts))

# Requests
# --------

def decide.tr(ist: Bool, t: List<&2, String>) -> Verdict:
  match ist:
    case True{}:
      tr(t)
    case False{}:
      Deny{[0]}

def decide.mode(isd: Bool, ist: Bool, t: List<&2, String>) -> Verdict:
  match isd:
    case True{}:
      doc(t)
    case False{}:
      decide.tr(ist, t)

def decide(ts: List<&2, String>) -> Verdict:
  match ts:
    case Nil{}:
      Deny{[0]}
    case +h <> t:
      decide.mode(String.eq(h, "D"), String.eq(h, "T"), t)
