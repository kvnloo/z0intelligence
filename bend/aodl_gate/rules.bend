# AODL structural rules over the Core IR (core.bend).
#
# Each rule mirrors one check of aodl_contract.validator.validate_02 at
# kvnloo/aodl@a848270. A rule answers a list of issue codes; the gate
# allows a document only when the list is empty. Codes (also the
# Python issue code each one mirrors):
#
#    1 kind          7 endpoint      13 cycle
#    2 harness       8 schema        14 fan-in
#    3 port          9 authority     15 bounds
#    4 duplicate    10 payment       16 auction
#    5 relation     11 gate          17 privilege
#    6 self-edge    12 reachability  18 event
#
# Shape checks (JSON types, required/closed fields, id and sha256 syntax,
# delivery/provenance objects, the harness catalog file) are NOT here:
# the host refuses to emit Core IR for a document that fails them.

import Base
import ./core.bend as C

# Values
# ------

def val.of(m: Maybe<&2, C.Val>) -> C.Val:
  match m:
    case None{}:
      C.VOther{}
    case Some{v}:
      v

def val(+tab: List<&2, C.Val>, i: U32) -> C.Val:
  val.of(List.get(&2, C.Val, tab, U32.to_nat(i)))

def in_strs(+s: String, xs: List<&2, String>) -> Bool:
  match xs:
    case Nil{}:
      False{}
    case h <> t:
      Bool.or(String.eq(s, h), in_strs(s, t))

def val.in(v: C.Val, xs: List<&2, String>) -> Bool:
  match v:
    case C.VStr{s}:
      in_strs(s, xs)
    case o:
      False{}

def val.is(v: C.Val, lit: String) -> Bool:
  match v:
    case C.VStr{s}:
      String.eq(s, lit)
    case o:
      False{}

def val.is_str(v: C.Val) -> Bool:
  match v:
    case C.VStr{s}:
      True{}
    case o:
      False{}

# str(v).lower() in xs (the host already passed str(v) as a string)
def val.in_lower(v: C.Val, xs: List<&2, String>) -> Bool:
  match v:
    case C.VStr{s}:
      in_strs(String.to_lower(s), xs)
    case o:
      False{}

def is_in(+tab: List<&2, C.Val>, i: U32, xs: List<&2, String>) -> Bool:
  val.in(val(tab, i), xs)

def is_lit(+tab: List<&2, C.Val>, i: U32, lit: String) -> Bool:
  val.is(val(tab, i), lit)

# Vocabularies (validator.py constants)
# -------------------------------------

def NODE_KINDS() -> List<&2, String>:
  ["task", "executor", "model", "tool", "service", "memory", "stateStore",
   "humanGate", "environment", "artifact", "verifier"]

def EDGE_RELS() -> List<&2, String>:
  ["dependency", "data", "message", "delegation", "critique",
   "verification", "allocation", "control", "observation", "artifact"]

def EVENT_TYPES() -> List<&2, String>:
  ["spawn", "bind", "route", "retry", "cancel", "addNode", "removeNode",
   "addEdge", "removeEdge", "stateUpdate", "snapshot"]

def FAN_IN() -> List<&2, String>:
  ["all", "any", "quorum", "reducer"]

def PRIVILEGED() -> List<&2, String>:
  ["payment", "finance", "pay", "transfer", "wallet", "sudo", "install",
   "secret", "credential"]

def IRREVERSIBLE() -> List<&2, String>:
  ["merge", "deploy", "approve"]

def AUCTION_PHASES() -> List<&2, String>:
  ["announce", "bid", "award", "execute", "verify", "settle"]

# Small list helpers
# ------------------

def emit(b: Bool, code: U32, rest: List<&2, U32>) -> List<&2, U32>:
  match b:
    case True{}:
      code <> rest
    case False{}:
      rest

def when(b: Bool, xs: List<&2, U32>) -> List<&2, U32>:
  match b:
    case True{}:
      xs
    case False{}:
      Nil{}

def app(xs: List<&2, U32>, ys: List<&2, U32>) -> List<&2, U32>:
  List.append(&2, U32, xs, ys)

def mem(+x: U32, xs: List<&2, U32>) -> Bool:
  match xs:
    case Nil{}:
      False{}
    case h <> t:
      Bool.or(U32.is_eq(x, h), mem(x, t))

def any_lower(+tab: List<&2, C.Val>, xs: List<&2, U32>, +ws: List<&2, String>) -> Bool:
  match xs:
    case Nil{}:
      False{}
    case h <> t:
      Bool.or(val.in_lower(val(tab, h), ws), any_lower(tab, t, ws))

def any_is(+tab: List<&2, C.Val>, xs: List<&2, U32>, +lit: String) -> Bool:
  match xs:
    case Nil{}:
      False{}
    case h <> t:
      Bool.or(is_lit(tab, h, lit), any_is(tab, t, lit))

def not(b: Bool) -> Bool:
  Bool.not(b)

# Nodes
# -----

def harness_bad(h: U32) -> Bool:
  +x = h
  Bool.and(U32.is_ge(x, 2), U32.is_le(x, 5))

def port_dirs(+tab: List<&2, C.Val>, ps: List<&2, C.Port>) -> List<&2, U32>:
  match ps:
    case Nil{}:
      Nil{}
    case C.Port{i, d, s} <> t:
      emit(not(is_in(tab, d, ["in", "out"])), 3, port_dirs(tab, t))

def push_if(b: Bool, +i: U32, +seen: List<&2, U32>) -> List<&2, U32>:
  match b:
    case True{}:
      i <> seen
    case False{}:
      seen

# seen holds the string port ids met so far on this node
def port_dups(+tab: List<&2, C.Val>, ps: List<&2, C.Port>, +seen: List<&2, U32>) -> List<&2, U32>:
  match ps:
    case Nil{}:
      Nil{}
    case C.Port{+i, d, s} <> t:
      +isstr = val.is_str(val(tab, i))
      dup = Bool.and(isstr, mem(i, seen))
      emit(dup, 4, port_dups(tab, t, push_if(isstr, i, seen)))

def node_issues(+tab: List<&2, C.Val>, n: C.Node) -> List<&2, U32>:
  C.Node{id, +k, h, ports, caps, ceil} = n
  +hh = h
  +ps = ports
  kind_bad = not(is_in(tab, k, NODE_KINDS()))
  on_non_exec = Bool.and(U32.is_ne(hh, 0), not(is_lit(tab, k, "executor")))
  emit(kind_bad, 1,
    emit(on_non_exec, 2,
      emit(harness_bad(hh), 2,
        app(port_dirs(tab, ps), port_dups(tab, ps, Nil{})))))

def nodes_issues(+tab: List<&2, C.Val>, ns: List<&2, C.Node>) -> List<&2, U32>:
  match ns:
    case Nil{}:
      Nil{}
    case +n <> t:
      app(node_issues(tab, n), nodes_issues(tab, t))

# Ids and the node dictionary
# ---------------------------

def node_id(n: C.Node) -> U32:
  match n:
    case C.Node{i, k, h, ps, cs, cl}:
      i

def node_ids(ns: List<&2, C.Node>) -> List<&2, U32>:
  match ns:
    case Nil{}:
      Nil{}
    case n <> t:
      node_id(n) <> node_ids(t)

def edge_ids(es: List<&2, C.Edge>) -> List<&2, U32>:
  match es:
    case Nil{}:
      Nil{}
    case C.Edge{i, r, a, b, ap, bp, k, g} <> t:
      i <> edge_ids(t)

# one duplicate issue per repeated id (validator._ids)
def dup_ids(xs: List<&2, U32>, +seen: List<&2, U32>) -> List<&2, U32>:
  match xs:
    case Nil{}:
      Nil{}
    case +h <> t:
      +dup = mem(h, seen)
      emit(dup, 4, dup_ids(t, push_if(not(dup), h, seen)))

def keep_if(b: Bool, +n: C.Node, rest: List<&2, C.Node>) -> List<&2, C.Node>:
  match b:
    case True{}:
      n <> rest
    case False{}:
      rest

# the node dictionary: first occurrence of each id, in order
def uniq(ns: List<&2, C.Node>, +seen: List<&2, U32>) -> List<&2, C.Node>:
  match ns:
    case Nil{}:
      Nil{}
    case +n <> t:
      +i = node_id(n)
      +fresh = not(mem(i, seen))
      keep_if(fresh, n, uniq(t, push_if(fresh, i, seen)))

def find.pick(b: Bool, +n: C.Node, rest: Maybe<&2, C.Node>) -> Maybe<&2, C.Node>:
  match b:
    case True{}:
      Some{n}
    case False{}:
      rest

def find(ns: List<&2, C.Node>, +i: U32) -> Maybe<&2, C.Node>:
  match ns:
    case Nil{}:
      None{}
    case +n <> t:
      find.pick(U32.is_eq(node_id(n), i), n, find(t, i))

def port_id(p: C.Port) -> U32:
  match p:
    case C.Port{i, d, s}:
      i

def port_pick(b: Bool, +p: C.Port, acc: Maybe<&2, C.Port>) -> Maybe<&2, C.Port>:
  match b:
    case True{}:
      Some{p}
    case False{}:
      acc

# the LAST port with this id: validator._port_index overwrites earlier ones
def find_port(ps: List<&2, C.Port>, +i: U32, acc: Maybe<&2, C.Port>) -> Maybe<&2, C.Port>:
  match ps:
    case Nil{}:
      acc
    case +p <> t:
      find_port(t, i, port_pick(U32.is_eq(port_id(p), i), p, acc))

# Edges
# -----

def rel_issues(+tab: List<&2, C.Val>, es: List<&2, C.Edge>) -> List<&2, U32>:
  match es:
    case Nil{}:
      Nil{}
    case C.Edge{i, r, a, b, ap, bp, k, g} <> t:
      emit(not(is_in(tab, r, EDGE_RELS())), 5, rel_issues(tab, t))

def port_out(+tab: List<&2, C.Val>, fp: Maybe<&2, C.Port>, dir: String) -> List<&2, U32>:
  match fp:
    case None{}:
      [3]
    case Some{C.Port{i, d, s}}:
      emit(not(is_lit(tab, d, dir)), 3, Nil{})

def schema_of(p: C.Port) -> U32:
  match p:
    case C.Port{i, d, s}:
      s

def schema_issue(fp: Maybe<&2, C.Port>, tp: Maybe<&2, C.Port>) -> List<&2, U32>:
  match fp tp:
    case Some{p} Some{q}:
      emit(U32.is_ne(schema_of(p), schema_of(q)), 8, Nil{})
    case Some{p} None{}:
      Nil{}
    case None{} _:
      Nil{}

def node_kind(n: C.Node) -> U32:
  match n:
    case C.Node{i, k, h, ps, cs, cl}:
      k

def node_ports(n: C.Node) -> List<&2, C.Port>:
  match n:
    case C.Node{i, k, h, ps, cs, cl}:
      ps

def ports_of(+tab: List<&2, C.Val>, +na: C.Node, +nb: C.Node, +ap: U32, +bp: U32) -> List<&2, U32>:
  +fp = find_port(node_ports(na), ap, None{})
  +tp = find_port(node_ports(nb), bp, None{})
  app(port_out(tab, fp, "out"), app(port_out(tab, tp, "in"), schema_issue(fp, tp)))

def edge_body(+tab: List<&2, C.Val>, +r: U32, +ap: U32, +bp: U32, +g: List<&2, U32>,
  +na: C.Node, +nb: C.Node) -> List<&2, U32>:
  +deleg = is_lit(tab, r, "delegation")
  +ka = node_kind(na)
  +kb = node_kind(nb)
  auth = Bool.and(deleg, List.is_empty(&2, U32, g))
  pay = any_lower(tab, g, PRIVILEGED())
  vgate = Bool.and(is_lit(tab, kb, "verifier"), any_lower(tab, g, IRREVERSIBLE()))
  hgate = Bool.and(deleg, Bool.and(is_lit(tab, ka, "humanGate"), is_lit(tab, kb, "executor")))
  app(ports_of(tab, na, nb, ap, bp),
    emit(auth, 9, emit(pay, 10, emit(vgate, 11, emit(hgate, 11, Nil{})))))

def edge_ends(+tab: List<&2, C.Val>, +r: U32, +ap: U32, +bp: U32, +g: List<&2, U32>,
  fa: Maybe<&2, C.Node>, fb: Maybe<&2, C.Node>) -> List<&2, U32>:
  match fa fb:
    case None{} _:
      [7]
    case Some{na} None{}:
      [7]
    case Some{na} Some{nb}:
      edge_body(tab, r, ap, bp, g, na, nb)

def edge_issues(+tab: List<&2, C.Val>, +nd: List<&2, C.Node>, e: C.Edge) -> List<&2, U32>:
  match e:
    case C.Edge{i, +r, +a, +b, +ap, +bp, k, +g}:
      emit(U32.is_eq(a, b), 6, edge_ends(tab, r, ap, bp, g, find(nd, a), find(nd, b)))

def edges_issues(+tab: List<&2, C.Val>, +nd: List<&2, C.Node>, es: List<&2, C.Edge>) -> List<&2, U32>:
  match es:
    case Nil{}:
      Nil{}
    case +e <> t:
      app(edge_issues(tab, nd, e), edges_issues(tab, nd, t))

# Topology over edges whose endpoints both exist
# ----------------------------------------------

# dr: relation == "dependency" (fan-in counting)
# dc: dr, or edge.kind == "dependency" (cycle detection)
type DE is Data:
  DE{a: U32, b: U32, dr: Bool, dc: Bool}

def is_some(m: Maybe<&2, C.Node>) -> Bool:
  match m:
    case None{}:
      False{}
    case Some{n}:
      True{}

def has(+nd: List<&2, C.Node>, i: U32) -> Bool:
  is_some(find(nd, i))

def de_pick(b: Bool, +d: DE, rest: List<&2, DE>) -> List<&2, DE>:
  match b:
    case True{}:
      d <> rest
    case False{}:
      rest

def valid_edges(+tab: List<&2, C.Val>, +nd: List<&2, C.Node>, es: List<&2, C.Edge>) -> List<&2, DE>:
  match es:
    case Nil{}:
      Nil{}
    case C.Edge{i, r, +a, +b, ap, bp, k, g} <> t:
      +dr = is_lit(tab, r, "dependency")
      dc = Bool.or(dr, is_lit(tab, k, "dependency"))
      ok = Bool.and(has(nd, a), has(nd, b))
      de_pick(ok, DE{a, b, dr, dc}, valid_edges(tab, nd, t))

def touches(des: List<&2, DE>, +i: U32) -> Bool:
  match des:
    case Nil{}:
      False{}
    case DE{a, b, dr, dc} <> t:
      Bool.or(Bool.or(U32.is_eq(a, i), U32.is_eq(b, i)), touches(t, i))

def isolated.go(ns: List<&2, C.Node>, +des: List<&2, DE>) -> List<&2, U32>:
  match ns:
    case Nil{}:
      Nil{}
    case n <> t:
      emit(not(touches(des, node_id(n))), 12, isolated.go(t, des))

def isolated(+nd: List<&2, C.Node>, +des: List<&2, DE>) -> List<&2, U32>:
  when(Nat.is_gt(List.length(&2, C.Node, nd), 1n), isolated.go(nd, des))

def dep_targets(des: List<&2, DE>) -> List<&2, U32>:
  match des:
    case Nil{}:
      Nil{}
    case DE{a, b, dr, dc} <> t:
      match dr:
        case True{}:
          b <> dep_targets(t)
        case False{}:
          dep_targets(t)

def has_dup(xs: List<&2, U32>) -> Bool:
  match xs:
    case Nil{}:
      False{}
    case +h <> +t:
      Bool.or(mem(h, t), has_dup(t))

# a node with two or more incoming dependency edges
def multi_dep(des: List<&2, DE>) -> Bool:
  has_dup(dep_targets(des))

# Cycle detection: repeatedly drop every node with no dependency edge to
# a node still present. A round that drops nothing leaves only nodes on
# or leading into a cycle. Every other round drops at least one node, so
# len(ids) + 1 rounds suffice; running out of fuel reports a cycle
# (fails closed).

def cyc_edges(des: List<&2, DE>) -> List<&2, DE>:
  match des:
    case Nil{}:
      Nil{}
    case DE{a, b, dr, +dc} <> t:
      de_pick(dc, DE{a, b, dr, dc}, cyc_edges(t))

def out_live(des: List<&2, DE>, +ids: List<&2, U32>, +i: U32) -> Bool:
  match des:
    case Nil{}:
      False{}
    case DE{a, b, dr, dc} <> t:
      Bool.or(Bool.and(U32.is_eq(a, i), mem(b, ids)), out_live(t, ids, i))

def keep_u32(b: Bool, +i: U32, rest: List<&2, U32>) -> List<&2, U32>:
  match b:
    case True{}:
      i <> rest
    case False{}:
      rest

def peel(xs: List<&2, U32>, +des: List<&2, DE>, +ids: List<&2, U32>) -> List<&2, U32>:
  match xs:
    case Nil{}:
      Nil{}
    case +h <> t:
      keep_u32(out_live(des, ids, h), h, peel(t, des, ids))

def cycle.fin(+next: List<&2, U32>, same: Bool, k: List<&2, U32> -> Bool) -> Bool:
  match same:
    case True{}:
      not(List.is_empty(&2, U32, next))
    case False{}:
      k(next)

def cycle.go(fuel: Nat, +des: List<&2, DE>, +ids: List<&2, U32>) -> Bool:
  match fuel:
    case 0n:
      True{}
    case 1n+f:
      +next = peel(ids, des, ids)
      same = Nat.is_eq(List.length(&2, U32, next), List.length(&2, U32, ids))
      cycle.fin(next, same, xs => cycle.go(f, des, xs))

def cycle(+nd: List<&2, C.Node>, des: List<&2, DE>) -> Bool:
  +ids = node_ids(nd)
  cycle.go(1n+List.length(&2, U32, ids), cyc_edges(des), ids)

# Node dictionary checks
# ----------------------

def lower_eq(v: C.Val, +s: String) -> Bool:
  match v:
    case C.VStr{c}:
      String.eq(s, String.to_lower(c))
    case o:
      False{}

def ceil_has(+tab: List<&2, C.Val>, cl: List<&2, U32>, +s: String) -> Bool:
  match cl:
    case Nil{}:
      False{}
    case h <> t:
      Bool.or(lower_eq(val(tab, h), s), ceil_has(tab, t, s))

# a privileged capability whose raw text is not on the lowered ceiling
def leak1(+tab: List<&2, C.Val>, v: C.Val, +cl: List<&2, U32>) -> Bool:
  match v:
    case C.VStr{+s}:
      Bool.and(in_strs(String.to_lower(s), PRIVILEGED()), not(ceil_has(tab, cl, s)))
    case o:
      False{}

def leaked(+tab: List<&2, C.Val>, cs: List<&2, U32>, +cl: List<&2, U32>) -> Bool:
  match cs:
    case Nil{}:
      False{}
    case h <> t:
      Bool.or(leak1(tab, val(tab, h), cl), leaked(tab, t, cl))

def dict_issues(+tab: List<&2, C.Val>, nd: List<&2, C.Node>) -> List<&2, U32>:
  match nd:
    case Nil{}:
      Nil{}
    case C.Node{i, +k, h, ps, +cs, +cl} <> t:
      priv = leaked(tab, cs, cl)
      held = Bool.or(any_lower(tab, cs, IRREVERSIBLE()), any_lower(tab, cl, IRREVERSIBLE()))
      vgate = Bool.and(is_lit(tab, k, "verifier"), held)
      emit(priv, 17, emit(vgate, 11, dict_issues(tab, t)))

# Policies
# --------

def phases_eq(+tab: List<&2, C.Val>, xs: List<&2, U32>, ws: List<&2, String>) -> Bool:
  match xs ws:
    case Nil{} Nil{}:
      True{}
    case Nil{} w <> wt:
      False{}
    case x <> xt Nil{}:
      False{}
    case x <> xt w <> wt:
      Bool.and(is_lit(tab, x, w), phases_eq(tab, xt, wt))

def phases_ok(+tab: List<&2, C.Val>, ph: Maybe<&2, List<&2, U32>>) -> Bool:
  match ph:
    case None{}:
      False{}
    case Some{xs}:
      phases_eq(tab, xs, AUCTION_PHASES())

# payment in {None, "unsupported", False}; 0 and 0.0 equal False
def pay_ok(v: C.Val) -> Bool:
  match v:
    case C.VNull{}:
      True{}
    case C.VZero{}:
      True{}
    case C.VStr{s}:
      String.eq(s, "unsupported")
    case C.VOther{}:
      False{}

def auction_issues.pick(+tab: List<&2, C.Val>, isdict: Bool, ph: Maybe<&2, List<&2, U32>>, pay: U32) -> List<&2, U32>:
  match isdict:
    case False{}:
      [16]
    case True{}:
      emit(not(phases_ok(tab, ph)), 16, emit(not(pay_ok(val(tab, pay))), 10, Nil{}))

def auction_issues(+tab: List<&2, C.Val>, at: U32, ph: Maybe<&2, List<&2, U32>>, pay: U32) -> List<&2, U32>:
  auction_issues.pick(tab, U32.is_eq(at, 2), ph, pay)

def pol_issues(+tab: List<&2, C.Val>, pol: C.Pol, multi: Bool, budgets: Bool) -> List<&2, U32>:
  match pol:
    case C.Pol{+fan, q, red, +ks, dyn, +mc, +md, ak, at, ph, pay}:
      fan_bad = Bool.and(multi, not(is_in(tab, fan, FAN_IN())))
      quorum_bad = Bool.and(is_lit(tab, fan, "quorum"), U32.is_lt(q, 2))
      reducer_bad = Bool.and(is_lit(tab, fan, "reducer"), not(red))
      +recursive = Bool.or(any_is(tab, ks, "recursion"), dyn)
      finite = Bool.and(U32.is_ge(mc, 2), U32.is_ge(md, 2))
      auction = Bool.or(any_is(tab, ks, "auction"), ak)
      emit(fan_bad, 14, emit(quorum_bad, 14, emit(reducer_bad, 14,
        emit(Bool.and(recursive, not(finite)), 15,
          emit(Bool.and(recursive, not(budgets)), 15,
            when(auction, auction_issues(tab, at, ph, pay)))))))

# Graphs and documents
# --------------------

# live: False for observedGraph, which the validator checks against fixed
# shadow policies (fanIn all, no dynamic spawn, no auction) that no
# policy rule can fire on
def graph_issues(+tab: List<&2, C.Val>, g: C.Graph, live: Bool, pol: C.Pol, budgets: Bool) -> List<&2, U32>:
  match g:
    case C.Graph{+ns, +es}:
      +nd = uniq(ns, Nil{})
      +des = valid_edges(tab, nd, es)
      pols = when(live, pol_issues(tab, pol, multi_dep(des), budgets))
      app(nodes_issues(tab, ns),
      app(dup_ids(node_ids(ns), Nil{}),
      app(rel_issues(tab, es),
      app(dup_ids(edge_ids(es), Nil{}),
      app(edges_issues(tab, nd, es),
      app(isolated(nd, des),
      app(emit(cycle(nd, des), 13, Nil{}),
      app(pols, dict_issues(tab, nd)))))))))

def ev_issues(+tab: List<&2, C.Val>, evs: List<&2, U32>) -> List<&2, U32>:
  match evs:
    case Nil{}:
      Nil{}
    case h <> t:
      emit(not(is_in(tab, h, EVENT_TYPES())), 18, ev_issues(tab, t))

def obs_issues(+tab: List<&2, C.Val>, obs: Maybe<&2, C.Graph>, +pol: C.Pol) -> List<&2, U32>:
  match obs:
    case None{}:
      Nil{}
    case Some{g}:
      graph_issues(tab, g, False{}, pol, True{})

def doc_issues(+tab: List<&2, C.Val>, d: C.Doc) -> List<&2, U32>:
  match d:
    case C.Doc{g, +pol, b, evs, obs}:
      app(graph_issues(tab, g, True{}, pol, b),
        app(obs_issues(tab, obs, pol), ev_issues(tab, evs)))

def req_issues(q: C.Req) -> List<&2, U32>:
  match q:
    case C.Req{+tab, d}:
      doc_issues(tab, d)
