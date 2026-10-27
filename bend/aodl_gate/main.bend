# AODL structural gate: the effect shell around the pure kernel.
#
#   ./gate <tok> <tok> ...      decide one request given as arguments
#   ./gate                      serve: one request per stdin line, one
#                               verdict per stdout line, until EOF
#
# A request is a line of decimal U32 tokens separated by single spaces:
# "1 <core tokens>" is a document (core.bend), "2 <transition tokens>" a
# proposed spawn (transition.bend). A verdict is "ALLOW" or
# "DENY <code> <code> ...". Code 0 means the request did not parse (a
# non-digit byte, a token past 2^32 - 1, a truncated or padded stream):
# the gate fails closed on anything it cannot read. Blank lines are
# ignored.
#
# Only this file performs effects. gate.bend is pure and carries the laws.

import Base
import ./gate.bend as G

def show_codes(xs: List<&2, U32>) -> String:
  match xs:
    case Nil{}:
      ""
    case h <> t:
      " " ++ U32.show(h) ++ show_codes(t)

def show(v: G.Verdict) -> String:
  match v:
    case G.Allow{}:
      "ALLOW"
    case G.Deny{codes}:
      "DENY" ++ show_codes(codes)

# Byte scanner
# ------------
# acc: the token being read; intok: a digit was seen; bad: this line
# already failed; toks: the finished tokens, newest first

type Scan is Data:
  Scan{acc: U32, intok: Bool, bad: Bool, toks: List<&2, U32>}

def Scan.new() -> Scan:
  Scan{0, False{}, False{}, Nil{}}

def push_tok(s: Scan) -> Scan:
  match s:
    case Scan{acc, intok, bad, toks}:
      match intok:
        case True{}:
          Scan{0, False{}, bad, acc <> toks}
        case False{}:
          Scan{0, False{}, bad, toks}

def digit(s: Scan, +d: U32) -> Scan:
  match s:
    case Scan{+acc, intok, bad, toks}:
      over = Bool.or(U32.is_gt(acc, 429496729), Bool.and(U32.is_eq(acc, 429496729), U32.is_gt(d, 5)))
      Scan{(acc * 10 + d : U32), True{}, Bool.or(bad, over), toks}

def poison(s: Scan) -> Scan:
  match s:
    case Scan{acc, intok, bad, toks}:
      Scan{acc, intok, True{}, toks}

def line_out.pick(bad: Bool, toks: List<&2, U32>, out: List<&2, String>) -> List<&2, String>:
  match bad:
    case True{}:
      "DENY 0" <> out
    case False{}:
      show(G.decide(List.reverse(&2, U32, toks))) <> out

# a finished line: its verdict, unless the line was blank
def line_out(s: Scan, out: List<&2, String>) -> List<&2, String>:
  match s:
    case Scan{acc, intok, bad, toks}:
      match bad toks:
        case False{} Nil{}:
          out
        case False{} h <> t:
          line_out.pick(False{}, h <> t, out)
        case True{} ts:
          line_out.pick(True{}, ts, out)

def step.pick(k: Nat, +b: U32, s: Scan, out: List<&2, String>) -> Scan & List<&2, String>:
  match k:
    case 0n:
      (Scan.new(), line_out(push_tok(s), out))
    case 1n:
      (push_tok(s), out)
    case 2n:
      (digit(s, (b - 48 : U32)), out)
    case _:
      (poison(s), out)

# 0 newline, 1 space, 2 digit, 3 anything else
def byte_class.digit(d: Bool) -> Nat:
  match d:
    case True{}:
      2n
    case False{}:
      3n

def byte_class.space(sp: Bool, +b: U32) -> Nat:
  match sp:
    case True{}:
      1n
    case False{}:
      byte_class.digit(Bool.and(U32.is_ge(b, 48), U32.is_le(b, 57)))

def byte_class.nl(nl: Bool, +b: U32) -> Nat:
  match nl:
    case True{}:
      0n
    case False{}:
      byte_class.space(U32.is_eq(b, 32), b)

def byte_class(+b: U32) -> Nat:
  byte_class.nl(U32.is_eq(b, 10), b)

def step(+b: U32, st: Scan & List<&2, String>) -> Scan & List<&2, String>:
  (s, out) = st
  step.pick(byte_class(b), b, s, out)

def scan(bs: List<&2, U32>, st: Scan & List<&2, String>) -> Scan & List<&2, String>:
  match bs:
    case Nil{}:
      st
    case +b <> t:
      scan(t, step(b, st))

# Serving
# -------

def emit_lines(out: List<&2, String>) -> IO(Unit):
  match out:
    case Nil{}:
      IO.pure(Unit, Unit{})
    case h <> t:
      IO.print(String.join(List.reverse(&2, String, h <> t), "\n"))

def serve.chunk(st: Scan & List<&2, String>, f: File, next: Scan -> File -> IO(Unit)) -> IO(Unit):
  (s, out) = st
  do IO<Unit>:
    emit_lines(out)
    next(s, f)

def serve.res(s: Scan, got: File & Result<&1, &1, U32 & String, List<&2, U32>>,
  next: Scan -> File -> IO(Unit)) -> IO(Unit):
  (f, r) = got
  match r:
    case Fail{e}:
      File.close(f)
    case Done{Nil{}}:
      do IO<Unit>:
        emit_lines(line_out(push_tok(s), Nil{}))
        File.close(f)
    case Done{b <> t}:
      serve.chunk(scan(b <> t, (s, Nil{})), f, next)

def serve(fuel: Nat, s: Scan, f: File) -> IO(Unit):
  match fuel:
    case 0n:
      File.close(f)
    case 1n+p:
      do IO<Unit>:
        got : File & Result<&1, &1, U32 & String, List<&2, U32>> <- File.read_bytes(f, 1048576)
        serve.res(s, got, s2 => f2 => serve(p, s2, f2))

# Arguments
# ---------

def read_args(xs: List<String>) -> Maybe<&2, List<&2, U32>>:
  match xs:
    case Nil{}:
      Some{Nil{}}
    case h <> t:
      do Maybe<&2, List<&2, U32>>:
        x : U32 <- U32.read(h)
        rest : List<&2, U32> <- read_args(t)
        return x <> rest

def decide_args(m: Maybe<&2, List<&2, U32>>) -> String:
  match m:
    case None{}:
      "DENY 0"
    case Some{ts}:
      show(G.decide(ts))

def run(args: List<String>) -> IO(Unit):
  match args:
    case Nil{}:
      IO.pure(Unit, Unit{})
    case prog <> Nil{}:
      do IO<Unit>:
        f : File <- IO.try(File, File.open("/dev/stdin", "r"))
        serve(4000000000n, Scan.new(), f)
    case prog <> a <> rest:
      IO.print(decide_args(read_args(a <> rest)))

def main() -> IO(Unit):
  do IO<Unit>:
    args : List<String> <- IO.args()
    run(args)
