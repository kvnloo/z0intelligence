# AODL Core IR for the Bend structural gate.
#
# The host (z0int.bend_gate) turns an AODL/HOTL 0.2 JSON document into a
# flat stream of decimal U32 tokens. Every string or scalar the rules look
# at is interned into a value table first, so the kernel never sees JSON:
#
#   table   := count entry*
#   entry   := 0 len char*        a JSON string (chars are code points)
#            | 1                  JSON null / an absent key (Python .get)
#            | 2                  a value equal to False / 0 / 0.0
#            | 3                  any other non-string value
#
# Two references are the same value iff they carry the same table index:
# the host interns with Python equality, so 1 == 1.0 == True share an
# index exactly as they compare equal in the canonical validator.
#
# This file only declares the IR and its parser. rules.bend holds the
# checks; LAWS.bend states what is proven about them.

import Base

# Values and references
# ---------------------

type Val is Data:
  VStr{s: String}
  VNull{}
  VZero{}
  VOther{}

# A node port: id, direction, schema (all table indexes)
type Port is Data:
  Port{id: U32, dir: U32, schema: U32}

# harness: 0 none, 1 catalog executor, 2 control-room, 3 other catalog
# kind, 4 id not in catalog.supported, 5 no catalog row
type Node is Data:
  Node{id: U32, kind: U32, harness: U32, ports: List<&2, Port>,
    caps: List<&2, U32>, ceil: List<&2, U32>}

# ekind is edge.get("kind"): the validator treats kind == "dependency"
# as a dependency for cycle detection, even on a 0.2 edge
type Edge is Data:
  Edge{id: U32, rel: U32, src: U32, dst: U32, sport: U32, dport: U32,
    ekind: U32, grant: List<&2, U32>}

type Graph is Data:
  Graph{nodes: List<&2, Node>, edges: List<&2, Edge>}

# quorum/maxc/maxd: 0 when not an int (or negative), else value + 1.
# phases: None when policies.auction.phases is not a list.
# atag: 0 no auction key, 1 auction present but not an object, 2 object.
# pay: table index of auction.get("payment", "unsupported").
type Pol is Data:
  Pol{fan: U32, quorum: U32, reducer: Bool, kinds: List<&2, U32>,
    dyn: Bool, maxc: U32, maxd: U32, akey: Bool, atag: U32,
    phases: Maybe<&2, List<&2, U32>>, pay: U32}

type Doc is Data:
  Doc{g: Graph, pol: Pol, budgets: Bool, events: List<&2, U32>,
    obs: Maybe<&2, Graph>}

# Parser
# ------

def Toks() -> Type:
  List<&2, String>

def Parse(-A: Type) -> Type:
  List<&2, String> -> Maybe<&1, A & List<&2, String>>

def Parse.pure(-A: Type, x: A) -> Parse(A):
  ts => Some{(x, ts)}

def Parse.run(-A: Type, -B: Type, r: Maybe<&1, A & List<&2, String>>,
  f: A -> Parse(B)) -> Maybe<&1, B & List<&2, String>>:
  match r:
    case None{}:
      None{}
    case Some{(x, ts)}:
      f(x, ts)

def Parse.bind(-A: Type, -B: Type, m: Parse(A), f: A -> Parse(B)) -> Parse(B):
  ts => Parse.run(A, B, m(ts), f)

def p_u32.fin(m: Maybe<&2, U32>, t: List<&2, String>) ->
  Maybe<&1, U32 & List<&2, String>>:
  match m:
    case None{}:
      None{}
    case Some{x}:
      Some{(x, t)}

def p_u32.go(ts: List<&2, String>) -> Maybe<&1, U32 & List<&2, String>>:
  match ts:
    case Nil{}:
      None{}
    case h <> t:
      p_u32.fin(U32.read(h), t)

def p_u32() -> Parse(U32):
  ts => p_u32.go(ts)

def p_nat() -> Parse(Nat):
  do Parse<Nat>:
    x : U32 <- p_u32()
    return U32.to_nat(x)

def p_bool.of(x: U32) -> Bool:
  U32.is_ne(x, 0)

def p_bool() -> Parse(Bool):
  do Parse<Bool>:
    x : U32 <- p_u32()
    return p_bool.of(x)

def p_many(~A: Data, ~p: Parse(A), n: Nat) -> Parse(List<&2, A>):
  match n:
    case 0n:
      Parse.pure(List<&2, A>, Nil{})
    case 1n+k:
      do Parse<List<&2, A>>:
        x : A <- p
        xs : List<&2, A> <- p_many(~A, ~p, k)
        return x <> xs

def p_list(~A: Data, ~p: Parse(A)) -> Parse(List<&2, A>):
  do Parse<List<&2, A>>:
    n : Nat <- p_nat()
    xs : List<&2, A> <- p_many(~A, ~p, n)
    return xs

def p_ids() -> Parse(List<&2, U32>):
  p_list(~U32, ~p_u32())

def p_char() -> Parse(Char):
  do Parse<Char>:
    c : U32 <- p_u32()
    return Chr{c}

def p_string() -> Parse(String):
  do Parse<String>:
    cs : List<&2, Char> <- p_list(~Char, ~p_char())
    return String.from_list(cs)

def p_val.pick(n: Nat) -> Parse(Val):
  match n:
    case 0n:
      do Parse<Val>:
        s : String <- p_string()
        return VStr{s}
    case 1n:
      Parse.pure(Val, VNull{})
    case 2n:
      Parse.pure(Val, VZero{})
    case 3n:
      Parse.pure(Val, VOther{})
    case 4n+k:
      ts => None{}

def p_val() -> Parse(Val):
  do Parse<Val>:
    tag : U32 <- p_u32()
    v : Val <- p_val.pick(U32.to_nat(tag))
    return v

def p_table() -> Parse(List<&2, Val>):
  p_list(~Val, ~p_val())

def p_port() -> Parse(Port):
  do Parse<Port>:
    i : U32 <- p_u32()
    d : U32 <- p_u32()
    s : U32 <- p_u32()
    return Port{i, d, s}

def p_node() -> Parse(Node):
  do Parse<Node>:
    i : U32 <- p_u32()
    k : U32 <- p_u32()
    h : U32 <- p_u32()
    ps : List<&2, Port> <- p_list(~Port, ~p_port())
    cs : List<&2, U32> <- p_ids()
    cl : List<&2, U32> <- p_ids()
    return Node{i, k, h, ps, cs, cl}

def p_edge() -> Parse(Edge):
  do Parse<Edge>:
    i : U32 <- p_u32()
    r : U32 <- p_u32()
    a : U32 <- p_u32()
    b : U32 <- p_u32()
    ap : U32 <- p_u32()
    bp : U32 <- p_u32()
    k : U32 <- p_u32()
    g : List<&2, U32> <- p_ids()
    return Edge{i, r, a, b, ap, bp, k, g}

def p_graph() -> Parse(Graph):
  do Parse<Graph>:
    ns : List<&2, Node> <- p_list(~Node, ~p_node())
    es : List<&2, Edge> <- p_list(~Edge, ~p_edge())
    return Graph{ns, es}

def p_phases.pick(present: Bool) -> Parse(Maybe<&2, List<&2, U32>>):
  match present:
    case False{}:
      Parse.pure(Maybe<&2, List<&2, U32>>, None{})
    case True{}:
      do Parse<Maybe<&2, List<&2, U32>>>:
        xs : List<&2, U32> <- p_ids()
        return Some{xs}

def p_phases() -> Parse(Maybe<&2, List<&2, U32>>):
  do Parse<Maybe<&2, List<&2, U32>>>:
    present : Bool <- p_bool()
    ph : Maybe<&2, List<&2, U32>> <- p_phases.pick(present)
    return ph

def p_pol() -> Parse(Pol):
  do Parse<Pol>:
    fan : U32 <- p_u32()
    q : U32 <- p_u32()
    red : Bool <- p_bool()
    ks : List<&2, U32> <- p_ids()
    dyn : Bool <- p_bool()
    mc : U32 <- p_u32()
    md : U32 <- p_u32()
    ak : Bool <- p_bool()
    at : U32 <- p_u32()
    ph : Maybe<&2, List<&2, U32>> <- p_phases()
    pay : U32 <- p_u32()
    return Pol{fan, q, red, ks, dyn, mc, md, ak, at, ph, pay}

def p_obs.pick(present: Bool) -> Parse(Maybe<&2, Graph>):
  match present:
    case False{}:
      Parse.pure(Maybe<&2, Graph>, None{})
    case True{}:
      do Parse<Maybe<&2, Graph>>:
        g : Graph <- p_graph()
        return Some{g}

def p_obs() -> Parse(Maybe<&2, Graph>):
  do Parse<Maybe<&2, Graph>>:
    present : Bool <- p_bool()
    g : Maybe<&2, Graph> <- p_obs.pick(present)
    return g

def p_doc() -> Parse(Doc):
  do Parse<Doc>:
    g : Graph <- p_graph()
    pol : Pol <- p_pol()
    b : Bool <- p_bool()
    evs : List<&2, U32> <- p_ids()
    obs : Maybe<&2, Graph> <- p_obs()
    return Doc{g, pol, b, evs, obs}

# A whole request: the value table, then the document. Trailing tokens
# are refused, so a truncated or padded stream never parses.
type Req is Data:
  Req{tab: List<&2, Val>, doc: Doc}

def p_req() -> Parse(Req):
  do Parse<Req>:
    tab : List<&2, Val> <- p_table()
    doc : Doc <- p_doc()
    return Req{tab, doc}

def parse.done(r: Maybe<&1, Req & List<&2, String>>) -> Maybe<&2, Req>:
  match r:
    case None{}:
      None{}
    case Some{(q, Nil{})}:
      Some{q}
    case Some{(q, h <> t)}:
      None{}

def parse(ts: List<&2, String>) -> Maybe<&2, Req>:
  parse.done(p_req()(ts))
