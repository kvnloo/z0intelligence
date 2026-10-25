# AODL structural gate: the proofs of LAWS.bend.

import Base
import ./core.bend as C
import ./rules.bend as R
import ./transition.bend as X
import ./gate.bend as G
import ./LAWS.bend as Laws

# Proof kit
# ---------

def and_l(+a: Bool, -b: Bool, w: Laws.T(Bool.and(a, b))) -> Laws.T(a):
  match a:
    case True{}:
      Unit{}
    case False{}:
      Empty.absurd(Laws.T(False{}), w)

def and_r(+a: Bool, -b: Bool, w: Laws.T(Bool.and(a, b))) -> Laws.T(b):
  match a:
    case True{}:
      w
    case False{}:
      Empty.absurd(Laws.T(b), w)

def or_split(+a: Bool, -b: Bool, w: Laws.T(Bool.or(a, b))) -> Or(Laws.T(a), Laws.T(b)):
  match a:
    case True{}:
      Inl{Unit{}}
    case False{}:
      Inr{w}

def eq_sound(a: Nat, b: Nat, w: Laws.T(Nat.is_eq(a, b))) -> {a == b : Nat}:
  match a b:
    case 0n 0n:
      {==}
    case 0n 1n+q:
      Empty.absurd({0n == 1n+q : Nat}, w)
    case 1n+p 0n:
      Empty.absurd({1n+p == 0n : Nat}, w)
    case 1n+p 1n+q:
      %eq_sound(p, q, w) : {1n+p == 1n+_ : Nat}
      {==}

# Transition gate
# ---------------
# allows(t) = rev && (dyn && (kids && (depth && (spend && auth)))); the
# p_* lemmas project one conjunct out of an allow

def R5(+t: X.Tr) -> Bool:
  Bool.and(X.spend_ok(t), X.auth_ok(t))

def R4(+t: X.Tr) -> Bool:
  Bool.and(X.depth_ok(t), R5(t))

def R3(+t: X.Tr) -> Bool:
  Bool.and(X.kids_ok(t), R4(t))

def R2(+t: X.Tr) -> Bool:
  Bool.and(X.dyn_ok(t), R3(t))

def p_rev(+t: X.Tr, w: Laws.T(X.allows(t))) -> Laws.T(X.rev_ok(t)):
  and_l(X.rev_ok(t), R2(t), w)

def p_r2(+t: X.Tr, w: Laws.T(X.allows(t))) -> Laws.T(R2(t)):
  and_r(X.rev_ok(t), R2(t), w)

def p_dyn(+t: X.Tr, w: Laws.T(X.allows(t))) -> Laws.T(X.dyn_ok(t)):
  and_l(X.dyn_ok(t), R3(t), p_r2(t, w))

def p_r3(+t: X.Tr, w: Laws.T(X.allows(t))) -> Laws.T(R3(t)):
  and_r(X.dyn_ok(t), R3(t), p_r2(t, w))

def p_kids(+t: X.Tr, w: Laws.T(X.allows(t))) -> Laws.T(X.kids_ok(t)):
  and_l(X.kids_ok(t), R4(t), p_r3(t, w))

def p_r4(+t: X.Tr, w: Laws.T(X.allows(t))) -> Laws.T(R4(t)):
  and_r(X.kids_ok(t), R4(t), p_r3(t, w))

def p_depth(+t: X.Tr, w: Laws.T(X.allows(t))) -> Laws.T(X.depth_ok(t)):
  and_l(X.depth_ok(t), R5(t), p_r4(t, w))

def p_r5(+t: X.Tr, w: Laws.T(X.allows(t))) -> Laws.T(R5(t)):
  and_r(X.depth_ok(t), R5(t), p_r4(t, w))

def p_spend(+t: X.Tr, w: Laws.T(X.allows(t))) -> Laws.T(X.spend_ok(t)):
  and_l(X.spend_ok(t), X.auth_ok(t), p_r5(t, w))

def p_auth(+t: X.Tr, w: Laws.T(X.allows(t))) -> Laws.T(X.auth_ok(t)):
  and_r(X.spend_ok(t), X.auth_ok(t), p_r5(t, w))

def Laws.spawn_revision_pinned(cr, rr, dy, mc, md, ks, dp, ds, cl, rq, w):
  eq_sound(cr, rr, p_rev(X.Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}, w))

def bool_true(b: Bool, w: Laws.T(b)) -> {b == True{} : Bool}:
  match b:
    case True{}:
      {==}
    case False{}:
      Empty.absurd({False{} == True{} : Bool}, w)

def Laws.spawn_needs_dynamic(cr, rr, dy, mc, md, ks, dp, ds, cl, rq, w):
  bool_true(dy, p_dyn(X.Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}, w))

def Laws.spawn_within_bounds(cr, rr, dy, mc, md, ks, dp, ds, cl, rq, w):
  +ww = w
  (p_kids(X.Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}, ww), p_depth(X.Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}, ww))

# Budgets

def dim_fits(d: X.Dim, w: Laws.T(X.dim_ok(d))) -> Laws.DimFits(d):
  match d:
    case X.Dim{has, limit, seen, prop}:
      match has:
        case False{}:
          Unit{}
        case True{}:
          w

def all_fit(ds: List<&2, X.Dim>, w: Laws.T(X.budget_ok(ds))) -> Laws.AllFit(ds):
  match ds:
    case Nil{}:
      Unit{}
    case +d <> +t:
      +ww = w
      (dim_fits(d, and_l(X.dim_ok(d), X.budget_ok(t), ww)), all_fit(t, and_r(X.dim_ok(d), X.budget_ok(t), ww)))

def Laws.spawn_within_budget(cr, rr, dy, mc, md, ks, dp, ds, cl, rq, w):
  all_fit(ds, p_spend(X.Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}, w))

# Authority

def mem_elem.go(x: Nat, h: Nat, -t: List<&2, Nat>,
  o: Or(Laws.T(Nat.is_eq(x, h)), Laws.T(X.nat_mem(x, t))),
  rec: Laws.T(X.nat_mem(x, t)) -> Laws.Elem(x, t)) -> Laws.Elem(x, h <> t):
  match o:
    case Inl{e}:
      Inl{eq_sound(x, h, e)}
    case Inr{r}:
      Inr{rec(r)}

def mem_elem(+x: Nat, cl: List<&2, Nat>, w: Laws.T(X.nat_mem(x, cl))) -> Laws.Elem(x, cl):
  match cl:
    case Nil{}:
      w
    case +h <> +t:
      mem_elem.go(x, h, t, or_split(Nat.is_eq(x, h), X.nat_mem(x, t), w), r => mem_elem(x, t, r))

def within_elem.go(-x: Nat, -h: Nat, -t: List<&2, Nat>, -cl: List<&2, Nat>,
  m: Laws.Elem(x, h <> t), hm: Laws.Elem(h, cl),
  rec: Laws.Elem(x, t) -> Laws.Elem(x, cl)) -> Laws.Elem(x, cl):
  match m:
    case Inl{e}:
      %Equal.sym(Nat, x, h, e) : Laws.Elem(_, cl)
      hm
    case Inr{r}:
      rec(r)

def within_elem(rq: List<&2, Nat>, +cl: List<&2, Nat>, w: Laws.T(X.within(rq, cl)),
  -x: Nat, m: Laws.Elem(x, rq)) -> Laws.Elem(x, cl):
  match rq:
    case Nil{}:
      Empty.absurd(Laws.Elem(x, cl), m)
    case +h <> +t:
      +ww = w
      within_elem.go(x, h, t, cl, m,
        mem_elem(h, cl, and_l(X.nat_mem(h, cl), X.within(t, cl), ww)),
        r => within_elem(t, cl, and_r(X.nat_mem(h, cl), X.within(t, cl), ww), x, r))

def Laws.spawn_no_invented_authority(cr, rr, dy, mc, md, ks, dp, ds, cl, rq, w, x, m):
  within_elem(rq, cl, p_auth(X.Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}, w), x, m)

# Spend is antitone

def le_add(s: Nat, +p: Nat, +q: Nat, w: Laws.T(Nat.is_le(p, q))) ->
  Laws.T(Nat.is_le(Nat.add(s, p), Nat.add(s, q))):
  match s:
    case 0n:
      w
    case 1n+k:
      le_add(k, p, q, w)

def le_trans(a: Nat, b: Nat, c: Nat, ab: Laws.T(Nat.is_le(a, b)), bc: Laws.T(Nat.is_le(b, c))) ->
  Laws.T(Nat.is_le(a, c)):
  match a b c:
    case 0n b0 0n:
      Unit{}
    case 0n b0 1n+c1:
      Unit{}
    case 1n+a1 0n c0:
      Empty.absurd(Laws.T(Nat.is_le(1n+a1, c0)), ab)
    case 1n+a1 1n+b1 0n:
      Empty.absurd(Laws.T(False{}), bc)
    case 1n+a1 1n+b1 1n+c1:
      le_trans(a1, b1, c1, ab, bc)

def Laws.spend_antitone(limit, seen, p, q, le, w):
  le_trans(Nat.add(seen, p), Nat.add(seen, q), limit, le_add(seen, p, q, le), w)

# Documents: no privileged grant
# ------------------------------

def E(xs: List<&2, U32>) -> Data:
  Laws.T(List.is_empty(&2, U32, xs))

def app_l(xs: List<&2, U32>, -ys: List<&2, U32>, w: E(R.app(xs, ys))) -> E(xs):
  match xs:
    case Nil{}:
      Unit{}
    case h <> t:
      Empty.absurd(E(h <> t), w)

def app_r(xs: List<&2, U32>, -ys: List<&2, U32>, w: E(R.app(xs, ys))) -> E(ys):
  match xs:
    case Nil{}:
      w
    case h <> t:
      Empty.absurd(E(ys), w)

def emit_rest(c: Bool, -code: U32, -rest: List<&2, U32>, w: E(R.emit(c, code, rest))) -> E(rest):
  match c:
    case True{}:
      Empty.absurd(E(rest), w)
    case False{}:
      w

def emit_not(c: Bool, -code: U32, -rest: List<&2, U32>, w: E(R.emit(c, code, rest))) -> Laws.T(Bool.not(c)):
  match c:
    case True{}:
      Empty.absurd(Laws.T(False{}), w)
    case False{}:
      Unit{}

def body_np(+tab: List<&2, C.Val>, +r: U32, +ap: U32, +bp: U32, +g: List<&2, U32>,
  +na: C.Node, +nb: C.Node, w: E(R.edge_body(tab, r, ap, bp, g, na, nb))) ->
  Laws.T(Bool.not(R.any_lower(tab, g, R.PRIVILEGED()))):
  +deleg = R.is_lit(tab, r, "delegation")
  +ka = R.node_kind(na)
  +kb = R.node_kind(nb)
  auth = Bool.and(deleg, List.is_empty(&2, U32, g))
  +vgate = Bool.and(R.is_lit(tab, kb, "verifier"), R.any_lower(tab, g, R.IRREVERSIBLE()))
  +hgate = Bool.and(deleg, Bool.and(R.is_lit(tab, ka, "humanGate"), R.is_lit(tab, kb, "executor")))
  +pay = R.any_lower(tab, g, R.PRIVILEGED())
  +rest = R.emit(vgate, 11, R.emit(hgate, 11, Nil{}))
  e1 = app_r(R.ports_of(tab, na, nb, ap, bp), R.emit(auth, 9, R.emit(pay, 10, rest)), w)
  e2 = emit_rest(auth, 9, R.emit(pay, 10, rest), e1)
  emit_not(pay, 10, rest, e2)

def ends_np(+tab: List<&2, C.Val>, +r: U32, +ap: U32, +bp: U32, +g: List<&2, U32>,
  fa: Maybe<&2, C.Node>, fb: Maybe<&2, C.Node>, w: E(R.edge_ends(tab, r, ap, bp, g, fa, fb))) ->
  Laws.T(Bool.not(R.any_lower(tab, g, R.PRIVILEGED()))):
  match fa fb:
    case None{} _:
      Empty.absurd(Laws.T(Bool.not(R.any_lower(tab, g, R.PRIVILEGED()))), w)
    case Some{na} None{}:
      Empty.absurd(Laws.T(Bool.not(R.any_lower(tab, g, R.PRIVILEGED()))), w)
    case Some{na} Some{nb}:
      body_np(tab, r, ap, bp, g, na, nb, w)

def edge_np(+tab: List<&2, C.Val>, +nd: List<&2, C.Node>, +i: U32, +r: U32, +a: U32, +b: U32,
  +ap: U32, +bp: U32, +k: U32, +g: List<&2, U32>,
  w: E(R.edge_issues(tab, nd, C.Edge{i, r, a, b, ap, bp, k, g}))) ->
  Laws.T(Bool.not(R.any_lower(tab, g, R.PRIVILEGED()))):
  ends_np(tab, r, ap, bp, g, R.find(nd, a), R.find(nd, b),
    emit_rest(U32.is_eq(a, b), 6, R.edge_ends(tab, r, ap, bp, g, R.find(nd, a), R.find(nd, b)), w))

def edges_np(+tab: List<&2, C.Val>, +nd: List<&2, C.Node>, es: List<&2, C.Edge>,
  w: E(R.edges_issues(tab, nd, es))) -> Laws.NoPrivGrant(tab, es):
  match es:
    case Nil{}:
      Unit{}
    case C.Edge{+i, +r, +a, +b, +ap, +bp, +k, +g} <> +t:
      +ww = w
      +e = {C.Edge{i, r, a, b, ap, bp, k, g} : C.Edge}
      (edge_np(tab, nd, i, r, a, b, ap, bp, k, g, app_l(R.edge_issues(tab, nd, e), R.edges_issues(tab, nd, t), ww)),
       edges_np(tab, nd, t, app_r(R.edge_issues(tab, nd, e), R.edges_issues(tab, nd, t), ww)))

# the graph's issue list, from the edges part on (graph_issues' tail)
def G4(+tab: List<&2, C.Val>, +ns: List<&2, C.Node>, +es: List<&2, C.Edge>, +pol: C.Pol, +b: Bool) -> List<&2, U32>:
  +nd = R.uniq(ns, Nil{})
  +des = R.valid_edges(tab, nd, es)
  pols = R.when(True{}, R.pol_issues(tab, pol, R.multi_dep(des), b))
  R.app(R.edges_issues(tab, nd, es),
  R.app(R.isolated(nd, des),
  R.app(R.emit(R.cycle(nd, des), 13, Nil{}),
  R.app(pols, R.dict_issues(tab, nd)))))

def G3(+tab: List<&2, C.Val>, +ns: List<&2, C.Node>, +es: List<&2, C.Edge>, +pol: C.Pol, +b: Bool) -> List<&2, U32>:
  R.app(R.dup_ids(R.edge_ids(es), Nil{}), G4(tab, ns, es, pol, b))

def G2(+tab: List<&2, C.Val>, +ns: List<&2, C.Node>, +es: List<&2, C.Edge>, +pol: C.Pol, +b: Bool) -> List<&2, U32>:
  R.app(R.rel_issues(tab, es), G3(tab, ns, es, pol, b))

def G1(+tab: List<&2, C.Val>, +ns: List<&2, C.Node>, +es: List<&2, C.Edge>, +pol: C.Pol, +b: Bool) -> List<&2, U32>:
  R.app(R.dup_ids(R.node_ids(ns), Nil{}), G2(tab, ns, es, pol, b))

def G5(+tab: List<&2, C.Val>, +ns: List<&2, C.Node>, +es: List<&2, C.Edge>, +pol: C.Pol, +b: Bool) -> List<&2, U32>:
  +nd = R.uniq(ns, Nil{})
  +des = R.valid_edges(tab, nd, es)
  pols = R.when(True{}, R.pol_issues(tab, pol, R.multi_dep(des), b))
  R.app(R.isolated(nd, des),
  R.app(R.emit(R.cycle(nd, des), 13, Nil{}),
  R.app(pols, R.dict_issues(tab, nd))))

def Laws.doc_no_privileged_grant(tab, ns, es, pol, b, evs, obs, w):
  +gi = R.graph_issues(tab, C.Graph{ns, es}, True{}, pol, b)
  w0 = app_l(gi, R.app(R.obs_issues(tab, obs, pol), R.ev_issues(tab, evs)), w)
  w1 = app_r(R.nodes_issues(tab, ns), G1(tab, ns, es, pol, b), w0)
  w2 = app_r(R.dup_ids(R.node_ids(ns), Nil{}), G2(tab, ns, es, pol, b), w1)
  w3 = app_r(R.rel_issues(tab, es), G3(tab, ns, es, pol, b), w2)
  w4 = app_r(R.dup_ids(R.edge_ids(es), Nil{}), G4(tab, ns, es, pol, b), w3)
  +nd = R.uniq(ns, Nil{})
  w5 = app_l(R.edges_issues(tab, nd, es), G5(tab, ns, es, pol, b), w4)
  edges_np(tab, nd, es, w5)

# Fail closed

def doc_of_ok(m: Maybe<&2, C.Req>, w: Laws.T(Laws.is_allow(G.doc.of(m)))) -> Laws.T(Laws.is_some.doc(m)):
  match m:
    case None{}:
      w
    case Some{q}:
      Unit{}

def tr_of_ok(m: Maybe<&2, X.Tr>, w: Laws.T(Laws.is_allow(G.tr.of(m)))) -> Laws.T(Laws.is_some.tr(m)):
  match m:
    case None{}:
      w
    case Some{t}:
      Unit{}

def mode_ok(n: Nat, t: List<&2, U32>, w: Laws.T(Laws.is_allow(G.decide.mode(n, t)))) ->
  Laws.T(Laws.parses.mode(n, t)):
  match n:
    case 0n:
      w
    case 1n:
      doc_of_ok(C.parse(t), w)
    case 2n:
      tr_of_ok(X.parse(t), w)
    case 3n+k:
      w

def Laws.allow_needs_parse(ts, w):
  match ts:
    case Nil{}:
      w
    case h <> t:
      mode_ok(U32.to_nat(h), t, w)
