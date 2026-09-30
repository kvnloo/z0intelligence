# The synchronous transition gate (z0intelligence#13, #48): may this
# proposed spawn run under the frozen AODL contract, given the observed
# state the host reports?
#
#   request := crev rrev dyn maxc maxd kids depth dims ceil req
#   dims    := count (has limit seen prop)*
#   ceil    := count id*        the parent's authorityCeiling (interned)
#   req     := count id*        capabilities the spawn asks for
#
#   crev  frozen contract revision      rrev  revision the proposer used
#   dyn   policies.dynamic.allowed      maxc/maxd  declared bounds
#   kids  live children of the parent   depth      the parent's depth
#   has/limit  a declared Gamma budget dimension (has=0: unbounded)
#   seen/prop  observed spend so far and the spend this spawn proposes
#
# Every count, revision and budget is a Nat sent as two U32 tokens (hi,
# lo), so spend never wraps: seen + prop is exact. The host refuses
# (fails closed) on values it cannot represent this way, such as
# fractional budgets or numbers past 2^48.
#
# Deny codes: 101 revision mismatch, 102 dynamic spawn not allowed,
# 103 child count past maxChildren, 104 depth past maxDepth,
# 105 a budget dimension exceeded, 106 authority not in the ceiling.

import Base
import ./core.bend as C

type Dim is Data:
  Dim{has: Bool, limit: Nat, seen: Nat, prop: Nat}

type Tr is Data:
  Tr{crev: Nat, rrev: Nat, dyn: Bool, maxc: Nat, maxd: Nat, kids: Nat,
    depth: Nat, dims: List<&2, Dim>, ceil: List<&2, Nat>, req: List<&2, Nat>}

# Parser
# ------

# a Nat sent as two U32 tokens, hi and lo: hi * 2^32 + lo
def p_natr() -> C.Parse(Nat):
  do C.Parse<Nat>:
    hi : U32 <- C.p_u32()
    lo : U32 <- C.p_u32()
    return Nat.add(Nat.mul(U32.to_nat(hi), Nat.mul(65536n, 65536n)), U32.to_nat(lo))

def p_dim() -> C.Parse(Dim):
  do C.Parse<Dim>:
    h : Bool <- C.p_bool()
    l : Nat <- p_natr()
    s : Nat <- p_natr()
    p : Nat <- p_natr()
    return Dim{h, l, s, p}

def p_nats() -> C.Parse(List<&2, Nat>):
  C.p_list(~Nat, ~p_natr())

def p_tr() -> C.Parse(Tr):
  do C.Parse<Tr>:
    cr : Nat <- p_natr()
    rr : Nat <- p_natr()
    dy : Bool <- C.p_bool()
    mc : Nat <- p_natr()
    md : Nat <- p_natr()
    ks : Nat <- p_natr()
    dp : Nat <- p_natr()
    ds : List<&2, Dim> <- C.p_list(~Dim, ~p_dim())
    cl : List<&2, Nat> <- p_nats()
    rq : List<&2, Nat> <- p_nats()
    return Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}

def parse.done(r: Maybe<&1, Tr & List<&2, U32>>) -> Maybe<&2, Tr>:
  match r:
    case None{}:
      None{}
    case Some{(t, Nil{})}:
      Some{t}
    case Some{(t, h <> r2)}:
      None{}

def parse(ts: List<&2, U32>) -> Maybe<&2, Tr>:
  parse.done(p_tr()(ts))

# Checks
# ------

def nat_mem(+x: Nat, xs: List<&2, Nat>) -> Bool:
  match xs:
    case Nil{}:
      False{}
    case h <> t:
      Bool.or(Nat.is_eq(x, h), nat_mem(x, t))

# every requested capability is on the ceiling: no invented authority
def within(xs: List<&2, Nat>, +cl: List<&2, Nat>) -> Bool:
  match xs:
    case Nil{}:
      True{}
    case h <> t:
      Bool.and(nat_mem(h, cl), within(t, cl))

def dim_ok(d: Dim) -> Bool:
  match d:
    case Dim{has, limit, seen, prop}:
      match has:
        case False{}:
          True{}
        case True{}:
          Nat.is_le(Nat.add(seen, prop), limit)

def budget_ok(ds: List<&2, Dim>) -> Bool:
  match ds:
    case Nil{}:
      True{}
    case h <> t:
      Bool.and(dim_ok(h), budget_ok(t))

def rev_ok(t: Tr) -> Bool:
  match t:
    case Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}:
      Nat.is_eq(cr, rr)

def dyn_ok(t: Tr) -> Bool:
  match t:
    case Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}:
      dy

def kids_ok(t: Tr) -> Bool:
  match t:
    case Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}:
      Nat.is_le(1n+ks, mc)

def depth_ok(t: Tr) -> Bool:
  match t:
    case Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}:
      Nat.is_le(1n+dp, md)

def spend_ok(t: Tr) -> Bool:
  match t:
    case Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}:
      budget_ok(ds)

def auth_ok(t: Tr) -> Bool:
  match t:
    case Tr{cr, rr, dy, mc, md, ks, dp, ds, cl, rq}:
      within(rq, cl)

# the gate's decision: every check holds
def allows(+t: Tr) -> Bool:
  Bool.and(rev_ok(t), Bool.and(dyn_ok(t), Bool.and(kids_ok(t),
    Bool.and(depth_ok(t), Bool.and(spend_ok(t), auth_ok(t))))))

def emit(b: Bool, code: U32, rest: List<&2, U32>) -> List<&2, U32>:
  match b:
    case False{}:
      code <> rest
    case True{}:
      rest

# the reasons for a denial: one code per failed check
def reasons(+t: Tr) -> List<&2, U32>:
  emit(rev_ok(t), 101, emit(dyn_ok(t), 102, emit(kids_ok(t), 103,
    emit(depth_ok(t), 104, emit(spend_ok(t), 105, emit(auth_ok(t), 106, Nil{}))))))
