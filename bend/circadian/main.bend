# Effect shell: print the gate's full truth table, one line per input:
#   "<phase> <cls> <a> <i> <o> <t> <k> <act>"
# phase 0..7 in z0int.human_posture.PHASES order, cls 0..3 in CLASSES order,
# flags 0/1, act 0 deliver / 1 defer / 2 digest. The Python and TypeScript
# twins are compared against this table on all 1024 inputs.

import Base
import ./gate.bend as G

def phases() -> List<&2, G.Phase>:
  [G.Sleep{}, G.Inertia{}, G.Peak{}, G.Dip{}, G.Second{}, G.WindDown{}, G.PreSleep{}, G.Disengaged{}]

def classes() -> List<&2, G.Cls>:
  [G.Critical{}, G.Decision{}, G.Report{}, G.Digest{}]

def bools() -> List<&2, Bool>:
  [False{}, True{}]

def p_id(p: G.Phase) -> String:
  match p:
    case G.Sleep{}:
      "0"
    case G.Inertia{}:
      "1"
    case G.Peak{}:
      "2"
    case G.Dip{}:
      "3"
    case G.Second{}:
      "4"
    case G.WindDown{}:
      "5"
    case G.PreSleep{}:
      "6"
    case G.Disengaged{}:
      "7"

def c_id(c: G.Cls) -> String:
  match c:
    case G.Critical{}:
      "0"
    case G.Decision{}:
      "1"
    case G.Report{}:
      "2"
    case G.Digest{}:
      "3"

def b_id(b: Bool) -> String:
  match b:
    case True{}:
      "1"
    case False{}:
      "0"

def a_id(x: G.Act) -> String:
  match x:
    case G.Deliver{}:
      "0"
    case G.Defer{}:
      "1"
    case G.ToDigest{}:
      "2"

def row(+p: G.Phase, +c: G.Cls, +a: Bool, +i: Bool, +o: Bool, +t: Bool, +k: Bool) -> String:
  p_id(p) ++ " " ++ c_id(c) ++ " " ++ b_id(a) ++ " " ++ b_id(i) ++ " " ++ b_id(o) ++ " " ++ b_id(t) ++ " " ++ b_id(k) ++ " " ++ a_id(G.gate(p, c, a, i, o, t, k))

def over_k(ks: List<&2, Bool>, +p: G.Phase, +c: G.Cls, +a: Bool, +i: Bool, +o: Bool, +t: Bool) -> List<&2, String>:
  match ks:
    case Nil{}:
      Nil{}
    case +k <> rest:
      row(p, c, a, i, o, t, k) <> over_k(rest, p, c, a, i, o, t)

def over_t(ts: List<&2, Bool>, +p: G.Phase, +c: G.Cls, +a: Bool, +i: Bool, +o: Bool) -> List<&2, String>:
  match ts:
    case Nil{}:
      Nil{}
    case +t <> rest:
      List.append(&2, String, over_k(bools(), p, c, a, i, o, t), over_t(rest, p, c, a, i, o))

def over_o(os: List<&2, Bool>, +p: G.Phase, +c: G.Cls, +a: Bool, +i: Bool) -> List<&2, String>:
  match os:
    case Nil{}:
      Nil{}
    case +o <> rest:
      List.append(&2, String, over_t(bools(), p, c, a, i, o), over_o(rest, p, c, a, i))

def over_i(xs: List<&2, Bool>, +p: G.Phase, +c: G.Cls, +a: Bool) -> List<&2, String>:
  match xs:
    case Nil{}:
      Nil{}
    case +i <> rest:
      List.append(&2, String, over_o(bools(), p, c, a, i), over_i(rest, p, c, a))

def over_a(xs: List<&2, Bool>, +p: G.Phase, +c: G.Cls) -> List<&2, String>:
  match xs:
    case Nil{}:
      Nil{}
    case +a <> rest:
      List.append(&2, String, over_i(bools(), p, c, a), over_a(rest, p, c))

def over_c(cs: List<&2, G.Cls>, +p: G.Phase) -> List<&2, String>:
  match cs:
    case Nil{}:
      Nil{}
    case +c <> rest:
      List.append(&2, String, over_a(bools(), p, c), over_c(rest, p))

def over_p(ps: List<&2, G.Phase>) -> List<&2, String>:
  match ps:
    case Nil{}:
      Nil{}
    case +p <> rest:
      List.append(&2, String, over_c(classes(), p), over_p(rest))

def main() -> IO(Unit):
  IO.print(String.join(over_p(phases()), "\n"))
