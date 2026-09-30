# AODL structural gate: the effect shell around the pure kernel.
#
#   ./gate <tok> <tok> ...      decide one request given as arguments
#   ./gate                      serve: one request per stdin line, one
#                               verdict per stdout line, until EOF
#
# A request is "D <core tokens>" (a document, see core.bend) or
# "T <transition tokens>" (a proposed spawn, see transition.bend).
# A verdict is "ALLOW" or "DENY <code> <code> ...". Code 0 means the
# request did not parse: the gate fails closed on anything it cannot read.
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

def answer(line: String) -> String:
  show(G.decide(String.split(String.trim(line), ' ')))

# Serving
# -------

# complete lines, and the unterminated tail kept for the next read
def split_lines.go(segs: List<&2, String>, acc: List<&2, String>) -> List<&2, String> & String:
  match segs:
    case Nil{}:
      (List.reverse(&2, String, acc), "")
    case h <> Nil{}:
      (List.reverse(&2, String, acc), h)
    case h <> t:
      split_lines.go(t, h <> acc)

def answer_all(ls: List<&2, String>) -> IO(Unit):
  match ls:
    case Nil{}:
      IO.pure(Unit, Unit{})
    case h <> t:
      do IO<Unit>:
        IO.print(answer(h))
        answer_all(t)

def serve.lines(split: List<&2, String> & String, f: File, next: String -> File -> IO(Unit)) -> IO(Unit):
  (ls, rest) = split
  do IO<Unit>:
    answer_all(ls)
    next(rest, f)

def serve.chunk(buf: String, f: File, s: String, next: String -> File -> IO(Unit)) -> IO(Unit):
  serve.lines(split_lines.go(String.split(buf ++ s, '\n'), Nil{}), f, next)

def serve.res(buf: String, got: File & Result<&1, &1, U32 & String, String>,
  next: String -> File -> IO(Unit)) -> IO(Unit):
  (f, r) = got
  match r:
    case Fail{e}:
      File.close(f)
    case Done{SNil{}}:
      do IO<Unit>:
        answer_all(String.split(buf, '\n'))
        File.close(f)
    case Done{SCon{h, t}}:
      serve.chunk(buf, f, SCon{h, t}, next)

def serve(fuel: Nat, buf: String, f: File) -> IO(Unit):
  match fuel:
    case 0n:
      File.close(f)
    case 1n+p:
      do IO<Unit>:
        got : File & Result<&1, &1, U32 & String, String> <- File.read(f, 1048576)
        serve.res(buf, got, b => f2 => serve(p, b, f2))

def data_list(xs: List<String>) -> List<&2, String>:
  match xs:
    case Nil{}:
      Nil{}
    case h <> t:
      h <> data_list(t)

def run(args: List<String>) -> IO(Unit):
  match args:
    case Nil{}:
      IO.pure(Unit, Unit{})
    case prog <> Nil{}:
      do IO<Unit>:
        f : File <- IO.try(File, File.open("/dev/stdin", "r"))
        serve(4000000000n, "", f)
    case prog <> a <> rest:
      IO.print(show(G.decide(data_list(a <> rest))))

def main() -> IO(Unit):
  do IO<Unit>:
    args : List<String> <- IO.args()
    run(args)
