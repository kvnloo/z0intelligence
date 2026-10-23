// Generated from Runtime.bend (+ unchanged WorkItem.bend, git blob 3e0e9922) by bend 2.0.34; do not hand-edit.
// Page subset of kvnloo/.files@2f69c19 docs/spec-diagram/fsm: work-item machine only, metadata filtered from model.json.
function word_to_u32(w) {
  let x = 0;
  for (let i = 0; w.$ === "WCon"; i++) {
    x |= Number(w.head) << i;
    w = w.tail;
  }
  return x >>> 0;
}

function u32_to_word(x) {
  let w = {$: "WNil"};
  for (let i = 31; i >= 0; i--) {
    w = {$: "WCon", head: ((x >>> i) & 1) === 1, tail: w};
  }
  return w;
}

function cmp_new(a, b) {
  return {$: a < b ? "LT"
    : a === b ? "EQ" : "GT"};
}

function nat_divmod(a, b) {
  return b === 0 ? {$: "Tuple", fst: 0, snd: a}
    : {$: "Tuple", fst: Math.trunc(a / b), snd: a % b};
}

function nat_chk(n) {
  if (n > 281474976710655) {
    throw "bend: a Nat past the largest immediate 2^48-1";
  }
  return n;
}

function nat_host(n) {
  const int = typeof n === "bigint" || Number.isInteger(n);
  if (int && n >= 0 && n <= 2 ** 53) {
    return Number(n);
  }
  return { [Symbol.toPrimitive]() { throw "bend: a Nat past the largest immediate 2^48-1"; } };
}

function f32_show(x) {
  if (x !== x) {
    return "nan";
  }
  if (!Number.isFinite(x) || Object.is(x, -0)) {
    return x < 0 ? "-inf"
      : x === 0 ? "-0" : "inf";
  }
  let s = "x";
  for (let p = 1; p <= 9 && f32_round(s) !== x; p += 1) {
    s = String(Number(x.toExponential(p - 1)));
  }
  return s;
}

function f32_bits(x) {
  return new Uint32Array(new Float32Array([x]).buffer)[0];
}

function f32_from_bits(u) {
  return new Float32Array(new Uint32Array([u]).buffer)[0];
}

function f32_read(s) {
  const re = /^\s*[+-]?((\d+\.?\d*|\.\d+)(e[+-]?\d+)?|inf(inity)?|nan)$/i;
  const v = f32_round(s.replace(/inf\w*/i, "Infinity"));
  return re.test(s) ? {$: "Some", value: v} : {$: "None"};
}

const f32_round = function f32_round(s) {
  const d = Number(s);
  const a = Math.abs(d);
  const f = Math.fround(a);
  const g = 2 * a - Math.min(f, 2 ** 128);
  if (g === f || Math.fround(g) !== g || g === Infinity) {
    return Math.sign(d) * f;
  }
  let k = 0;
  while (a * 2 ** k % 1 !== 0) {
    k += 1;
  }
  const [, i, r, e] = /(\d*)\.?(\d*)(?:e([+-]?\d+))?$/i.exec(s);
  const n = Number(e ?? 0) - r.length;
  const x = BigInt(i + r) * 2n ** BigInt(k) * 10n ** BigInt(Math.max(n, 0));
  const y = BigInt(a * 2 ** k) * 10n ** BigInt(Math.max(-n, 0));
  return Math.sign(d) * (x === y || x > y !== g > f ? f : g);
};

function char_new(code) {
  if (code > 0x10FFFF || (code >= 0xD800 && code <= 0xDFFF)) {
    throw "bend: " + code + " is not a Unicode scalar value";
  }
  return String.fromCodePoint(code);
}

// Array
// =====

function array_new(d, v) {
  if (d > 31) {
    throw "bend: an array past the deepest block class 31";
  }
  return Array(2 ** d).fill(v);
}

function array_node(a, b) {
  if (a.length !== b.length) {
    throw "bend: runtime fail-stop";
  }
  return a.concat(b);
}

function array_rmw(a, i, f) {
  const at = i % a.length;
  const old = a[at];
  a[at] = f(old);
  return {$: "Tuple", fst: a, snd: old};
}

// Run
// ===

function run_tail(f, x) {
  return {$: "$JMP", f: f.j?.f === f ? f.j : f, x: [x]};
}

function run_clo(j) {
  const f = (x) => run_loop(j(x));
  f.j = j;
  j.f = f;
  return f;
}

function run_loop(r) {
  while (r !== null && typeof r === "object" && r.$ === "$JMP") {
    r = r.f(...r.x);
  }
  return r;
}

function run_lib(f, n) {
  return (...a) => a.length < n ? run_lib((...b) => f(...a, ...b), n - a.length)
    : f(...a);
}

// Effect
// ======

const $0eff = Object.create(null);

function io_eff(k, run, need) {
  if (k in $0eff) {
    throw new Error("bend: two effects register " + k);
  }
  $0eff[k] = { run, need };
}
// Program
// =======

function $WorkItem$coder$(_r_0) {
  if (_r_0.$ === "WorkItem.Opus") {
    return true;
  } else if (_r_0.$ === "WorkItem.Sol") {
    return true;
  } else {
    return false;
  }
}

function $WorkItem$is_ci$(_r_0) {
  if (_r_0.$ === "WorkItem.Ci") {
    return true;
  } else {
    return false;
  }
}

function $WorkItem$is_owner$(_r_0) {
  if (_r_0.$ === "WorkItem.Owner") {
    return true;
  } else {
    return false;
  }
}

function $WorkItem$operator$(_r_0) {
  if (_r_0.$ === "WorkItem.Ci") {
    return true;
  } else if (_r_0.$ === "WorkItem.Opus") {
    return true;
  } else {
    return false;
  }
}

function $WorkItem$merger$(_r_0) {
  if (_r_0.$ === "WorkItem.Ci") {
    return true;
  } else if (_r_0.$ === "WorkItem.Owner") {
    return true;
  } else {
    return false;
  }
}

function $WorkItem$steward$(_r_0) {
  if (_r_0.$ === "WorkItem.Owner") {
    return true;
  } else if (_r_0.$ === "WorkItem.Opus") {
    return true;
  } else {
    return false;
  }
}

function $WorkItem$rid$(_a_0) {
  const _id_0 = _a_0["id"];
  return _id_0;
}

function $WorkItem$rrole$(_a_0) {
  const _role_0 = _a_0["role"];
  return _role_0;
}

function $WorkItem$same$(_a_0, _au_0) {
  return $Nat$is_eq$(($WorkItem$rid$(_a_0)), ($WorkItem$rid$(_au_0)));
}

function $WorkItem$other$(_a_0, _au_0) {
  return $Bool$not$(($Nat$is_eq$(($WorkItem$rid$(_a_0)), ($WorkItem$rid$(_au_0)))));
}

function $WorkItem$pick$(_ok_0, _x_0, _y_0) {
  if (_ok_0) {
    return _x_0;
  } else {
    return _y_0;
  }
}

function $WorkItem$verify_out$(_gate_0, _good_0, _au_0, _hd_0) {
  if (_gate_0) {
    return $WorkItem$pick$(_good_0, {$: "WorkItem.DraftPR", "au": _au_0, "hd": _hd_0}, {$: "WorkItem.Implementing", "au": _au_0, "hd": _hd_0});
  } else {
    return {$: "WorkItem.Verifying", "au": _au_0, "hd": _hd_0};
  }
}

function $WorkItem$verify_gate$(_a_0, _au_0) {
  return $Bool$and$(($WorkItem$is_ci$(($WorkItem$rrole$(_a_0)))), ($WorkItem$other$(_a_0, _au_0)));
}

function $WorkItem$receipt_good$(_r_0, _hd_0) {
  const _sha_0 = _r_0["sha"];
  const _pass_0 = _r_0["pass"];
  return $Bool$and$(_pass_0, ($Nat$is_eq$(_sha_0, _hd_0)));
}

function $WorkItem$integrate_gate$(_a_0, _au_0) {
  return $Bool$and$(($WorkItem$operator$(($WorkItem$rrole$(_a_0)))), ($WorkItem$other$(_a_0, _au_0)));
}

function $WorkItem$accept_gate$(_a_0, _au_0) {
  return $Bool$and$(($WorkItem$is_owner$(($WorkItem$rrole$(_a_0)))), ($WorkItem$other$(_a_0, _au_0)));
}

function $WorkItem$from_proposed$(_e_0) {
  if (_e_0.$ === "WorkItem.Claim") {
    const _a_0 = _e_0["a"];
    return $WorkItem$pick$(($WorkItem$coder$(($WorkItem$rrole$(_a_0)))), {$: "WorkItem.Claimed", "au": _a_0}, {$: "WorkItem.Proposed"});
  } else if (_e_0.$ === "WorkItem.Abandon") {
    const _a_1 = _e_0["a"];
    return $WorkItem$pick$(($WorkItem$steward$(($WorkItem$rrole$(_a_1)))), {$: "WorkItem.Abandoned"}, {$: "WorkItem.Proposed"});
  } else {
    return {$: "WorkItem.Proposed"};
  }
}

function $WorkItem$from_claimed$(_au_0, _e_0) {
  if (_e_0.$ === "WorkItem.Start") {
    const _a_0 = _e_0["a"];
    return $WorkItem$pick$(($WorkItem$same$(_a_0, _au_0)), {$: "WorkItem.Implementing", "au": _au_0, "hd": 0}, {$: "WorkItem.Claimed", "au": _au_0});
  } else if (_e_0.$ === "WorkItem.Interrupt") {
    return {$: "WorkItem.Interrupted", "au": _au_0, "hd": 0};
  } else if (_e_0.$ === "WorkItem.Abandon") {
    const _a_1 = _e_0["a"];
    return $WorkItem$pick$(($WorkItem$steward$(($WorkItem$rrole$(_a_1)))), {$: "WorkItem.Abandoned"}, {$: "WorkItem.Claimed", "au": _au_0});
  } else {
    return {$: "WorkItem.Claimed", "au": _au_0};
  }
}

function $WorkItem$from_implementing$(_au_0, _hd_0, _e_0) {
  if (_e_0.$ === "WorkItem.Push") {
    const _a_0 = _e_0["a"];
    const _sha_0 = _e_0["sha"];
    return $WorkItem$pick$(($WorkItem$same$(_a_0, _au_0)), {$: "WorkItem.Implementing", "au": _au_0, "hd": _sha_0}, {$: "WorkItem.Implementing", "au": _au_0, "hd": _hd_0});
  } else if (_e_0.$ === "WorkItem.Submit") {
    const _a_1 = _e_0["a"];
    return $WorkItem$pick$(($WorkItem$same$(_a_1, _au_0)), {$: "WorkItem.Verifying", "au": _au_0, "hd": _hd_0}, {$: "WorkItem.Implementing", "au": _au_0, "hd": _hd_0});
  } else if (_e_0.$ === "WorkItem.Block") {
    return {$: "WorkItem.Blocked", "au": _au_0, "hd": _hd_0};
  } else if (_e_0.$ === "WorkItem.Interrupt") {
    return {$: "WorkItem.Interrupted", "au": _au_0, "hd": _hd_0};
  } else {
    return {$: "WorkItem.Implementing", "au": _au_0, "hd": _hd_0};
  }
}

function $WorkItem$from_verifying$(_au_0, _hd_0, _e_0) {
  if (_e_0.$ === "WorkItem.Verify") {
    const _a_0 = _e_0["a"];
    const _r_0 = _e_0["r"];
    return $WorkItem$verify_out$(($WorkItem$verify_gate$(_a_0, _au_0)), ($WorkItem$receipt_good$(_r_0, _hd_0)), _au_0, _hd_0);
  } else if (_e_0.$ === "WorkItem.Block") {
    return {$: "WorkItem.Blocked", "au": _au_0, "hd": _hd_0};
  } else if (_e_0.$ === "WorkItem.Interrupt") {
    return {$: "WorkItem.Interrupted", "au": _au_0, "hd": _hd_0};
  } else {
    return {$: "WorkItem.Verifying", "au": _au_0, "hd": _hd_0};
  }
}

function $WorkItem$integrate_out$(_gate_0, _au_0, _hd_0, _ish_0) {
  if (_gate_0) {
    return {$: "WorkItem.Integrated", "au": _au_0, "hd": _hd_0, "ig": _ish_0};
  } else {
    return {$: "WorkItem.DraftPR", "au": _au_0, "hd": _hd_0};
  }
}

function $WorkItem$from_draft$(_au_0, _hd_0, _e_0) {
  if (_e_0.$ === "WorkItem.Integrate") {
    const _a_0 = _e_0["a"];
    const _ish_0 = _e_0["ish"];
    const _r_0 = _e_0["r"];
    return $WorkItem$integrate_out$(($Bool$and$(($WorkItem$integrate_gate$(_a_0, _au_0)), ($WorkItem$receipt_good$(_r_0, _ish_0)))), _au_0, _hd_0, _ish_0);
  } else {
    return {$: "WorkItem.DraftPR", "au": _au_0, "hd": _hd_0};
  }
}

function $WorkItem$deploy_out$(_ok_0, _au_0, _hd_0, _ig_0, _served_0) {
  if (_ok_0) {
    return {$: "WorkItem.Deployed", "au": _au_0, "hd": _hd_0, "ig": _ig_0, "dp": _served_0};
  } else {
    return {$: "WorkItem.Integrated", "au": _au_0, "hd": _hd_0, "ig": _ig_0};
  }
}

function $WorkItem$from_integrated$(_au_0, _hd_0, _ig_0, _e_0) {
  if (_e_0.$ === "WorkItem.Deploy") {
    const _a_0 = _e_0["a"];
    const _served_0 = _e_0["served"];
    return $WorkItem$deploy_out$(($Bool$and$(($WorkItem$operator$(($WorkItem$rrole$(_a_0)))), ($Nat$is_eq$(_served_0, _ig_0)))), _au_0, _hd_0, _ig_0, _served_0);
  } else {
    return {$: "WorkItem.Integrated", "au": _au_0, "hd": _hd_0, "ig": _ig_0};
  }
}

function $WorkItem$observe_out$(_ok_0, _au_0, _hd_0, _ig_0, _dp_0) {
  if (_ok_0) {
    return {$: "WorkItem.Deployed", "au": _au_0, "hd": _hd_0, "ig": _ig_0, "dp": _dp_0};
  } else {
    return {$: "WorkItem.Integrated", "au": _au_0, "hd": _hd_0, "ig": _ig_0};
  }
}

function $WorkItem$accept_out$(_ok_0, _au_0, _hd_0, _ig_0, _dp_0, _a_0) {
  if (_ok_0) {
    return {$: "WorkItem.Accepted", "au": _au_0, "hd": _hd_0, "ig": _ig_0, "ap": _a_0};
  } else {
    return {$: "WorkItem.Deployed", "au": _au_0, "hd": _hd_0, "ig": _ig_0, "dp": _dp_0};
  }
}

function $WorkItem$from_deployed$(_au_0, _hd_0, _ig_0, _dp_0, _e_0) {
  if (_e_0.$ === "WorkItem.Observe") {
    const _served_0 = _e_0["served"];
    return $WorkItem$observe_out$(($Nat$is_eq$(_served_0, _ig_0)), _au_0, _hd_0, _ig_0, _dp_0);
  } else if (_e_0.$ === "WorkItem.Accept") {
    const _a_0 = _e_0["a"];
    return $WorkItem$accept_out$(($WorkItem$accept_gate$(_a_0, _au_0)), _au_0, _hd_0, _ig_0, _dp_0, _a_0);
  } else {
    return {$: "WorkItem.Deployed", "au": _au_0, "hd": _hd_0, "ig": _ig_0, "dp": _dp_0};
  }
}

function $WorkItem$from_accepted$(_au_0, _hd_0, _ig_0, _ap_0, _e_0) {
  if (_e_0.$ === "WorkItem.Merge") {
    const _a_0 = _e_0["a"];
    return $WorkItem$pick$(($WorkItem$merger$(($WorkItem$rrole$(_a_0)))), {$: "WorkItem.Merged", "au": _au_0, "hd": _hd_0, "ig": _ig_0, "ap": _ap_0}, {$: "WorkItem.Accepted", "au": _au_0, "hd": _hd_0, "ig": _ig_0, "ap": _ap_0});
  } else {
    return {$: "WorkItem.Accepted", "au": _au_0, "hd": _hd_0, "ig": _ig_0, "ap": _ap_0};
  }
}

function $WorkItem$from_blocked$(_au_0, _hd_0, _e_0) {
  if (_e_0.$ === "WorkItem.Unblock") {
    const _a_0 = _e_0["a"];
    return $WorkItem$pick$(($WorkItem$steward$(($WorkItem$rrole$(_a_0)))), {$: "WorkItem.Implementing", "au": _au_0, "hd": _hd_0}, {$: "WorkItem.Blocked", "au": _au_0, "hd": _hd_0});
  } else if (_e_0.$ === "WorkItem.Abandon") {
    const _a_1 = _e_0["a"];
    return $WorkItem$pick$(($WorkItem$steward$(($WorkItem$rrole$(_a_1)))), {$: "WorkItem.Abandoned"}, {$: "WorkItem.Blocked", "au": _au_0, "hd": _hd_0});
  } else {
    return {$: "WorkItem.Blocked", "au": _au_0, "hd": _hd_0};
  }
}

function $WorkItem$from_interrupted$(_au_0, _hd_0, _e_0) {
  if (_e_0.$ === "WorkItem.Resume") {
    const _a_0 = _e_0["a"];
    return $WorkItem$pick$(($WorkItem$coder$(($WorkItem$rrole$(_a_0)))), {$: "WorkItem.Implementing", "au": _a_0, "hd": _hd_0}, {$: "WorkItem.Interrupted", "au": _au_0, "hd": _hd_0});
  } else if (_e_0.$ === "WorkItem.Abandon") {
    const _a_1 = _e_0["a"];
    return $WorkItem$pick$(($WorkItem$steward$(($WorkItem$rrole$(_a_1)))), {$: "WorkItem.Abandoned"}, {$: "WorkItem.Interrupted", "au": _au_0, "hd": _hd_0});
  } else {
    return {$: "WorkItem.Interrupted", "au": _au_0, "hd": _hd_0};
  }
}

function $WorkItem$step$(_s_0, _e_0) {
  if (_s_0.$ === "WorkItem.Proposed") {
    return $WorkItem$from_proposed$(_e_0);
  } else if (_s_0.$ === "WorkItem.Claimed") {
    const _au_0 = _s_0["au"];
    return $WorkItem$from_claimed$(_au_0, _e_0);
  } else if (_s_0.$ === "WorkItem.Implementing") {
    const _au_1 = _s_0["au"];
    const _hd_0 = _s_0["hd"];
    return $WorkItem$from_implementing$(_au_1, _hd_0, _e_0);
  } else if (_s_0.$ === "WorkItem.Verifying") {
    const _au_2 = _s_0["au"];
    const _hd_1 = _s_0["hd"];
    return $WorkItem$from_verifying$(_au_2, _hd_1, _e_0);
  } else if (_s_0.$ === "WorkItem.DraftPR") {
    const _au_3 = _s_0["au"];
    const _hd_2 = _s_0["hd"];
    return $WorkItem$from_draft$(_au_3, _hd_2, _e_0);
  } else if (_s_0.$ === "WorkItem.Integrated") {
    const _au_4 = _s_0["au"];
    const _hd_3 = _s_0["hd"];
    const _ig_0 = _s_0["ig"];
    return $WorkItem$from_integrated$(_au_4, _hd_3, _ig_0, _e_0);
  } else if (_s_0.$ === "WorkItem.Deployed") {
    const _au_5 = _s_0["au"];
    const _hd_4 = _s_0["hd"];
    const _ig_1 = _s_0["ig"];
    const _dp_0 = _s_0["dp"];
    return $WorkItem$from_deployed$(_au_5, _hd_4, _ig_1, _dp_0, _e_0);
  } else if (_s_0.$ === "WorkItem.Accepted") {
    const _au_6 = _s_0["au"];
    const _hd_5 = _s_0["hd"];
    const _ig_2 = _s_0["ig"];
    const _ap_0 = _s_0["ap"];
    return $WorkItem$from_accepted$(_au_6, _hd_5, _ig_2, _ap_0, _e_0);
  } else if (_s_0.$ === "WorkItem.Merged") {
    const _au_7 = _s_0["au"];
    const _hd_6 = _s_0["hd"];
    const _ig_3 = _s_0["ig"];
    const _ap_1 = _s_0["ap"];
    return {$: "WorkItem.Merged", "au": _au_7, "hd": _hd_6, "ig": _ig_3, "ap": _ap_1};
  } else if (_s_0.$ === "WorkItem.Blocked") {
    const _au_8 = _s_0["au"];
    const _hd_7 = _s_0["hd"];
    return $WorkItem$from_blocked$(_au_8, _hd_7, _e_0);
  } else if (_s_0.$ === "WorkItem.Interrupted") {
    const _au_9 = _s_0["au"];
    const _hd_8 = _s_0["hd"];
    return $WorkItem$from_interrupted$(_au_9, _hd_8, _e_0);
  } else {
    return {$: "WorkItem.Abandoned"};
  }
}

function $WorkItem$run$($0, $1) {
  for (;;) {
    {
      const _es_0 = $0;
      const _s_0 = $1;
      if (_es_0.$ === "Nil") {
        return _s_0;
      } else {
        const _e_0 = _es_0["head"];
        const _t_0 = _es_0["tail"];
        $0 = _t_0;
        $1 = ($WorkItem$step$(_s_0, _e_0));
        continue;
      }
    }
  }
}

function $WorkItem$is_off$(_av_0) {
  if (_av_0.$ === "WorkItem.Off") {
    return true;
  } else {
    return false;
  }
}

function $WorkItem$owner_event$(_e_0) {
  if (_e_0.$ === "WorkItem.Claim") {
    const _a_0 = _e_0["a"];
    return $WorkItem$is_owner$(($WorkItem$rrole$(_a_0)));
  } else if (_e_0.$ === "WorkItem.Start") {
    const _a_1 = _e_0["a"];
    return $WorkItem$is_owner$(($WorkItem$rrole$(_a_1)));
  } else if (_e_0.$ === "WorkItem.Push") {
    const _a_2 = _e_0["a"];
    return $WorkItem$is_owner$(($WorkItem$rrole$(_a_2)));
  } else if (_e_0.$ === "WorkItem.Submit") {
    const _a_3 = _e_0["a"];
    return $WorkItem$is_owner$(($WorkItem$rrole$(_a_3)));
  } else if (_e_0.$ === "WorkItem.Verify") {
    const _a_4 = _e_0["a"];
    return $WorkItem$is_owner$(($WorkItem$rrole$(_a_4)));
  } else if (_e_0.$ === "WorkItem.Integrate") {
    const _a_5 = _e_0["a"];
    return $WorkItem$is_owner$(($WorkItem$rrole$(_a_5)));
  } else if (_e_0.$ === "WorkItem.Deploy") {
    const _a_6 = _e_0["a"];
    return $WorkItem$is_owner$(($WorkItem$rrole$(_a_6)));
  } else if (_e_0.$ === "WorkItem.Observe") {
    return false;
  } else if (_e_0.$ === "WorkItem.Accept") {
    const _a_7 = _e_0["a"];
    return $WorkItem$is_owner$(($WorkItem$rrole$(_a_7)));
  } else if (_e_0.$ === "WorkItem.Merge") {
    const _a_8 = _e_0["a"];
    return $WorkItem$is_owner$(($WorkItem$rrole$(_a_8)));
  } else if (_e_0.$ === "WorkItem.Block") {
    const _a_9 = _e_0["a"];
    return $WorkItem$is_owner$(($WorkItem$rrole$(_a_9)));
  } else if (_e_0.$ === "WorkItem.Unblock") {
    const _a_10 = _e_0["a"];
    return $WorkItem$is_owner$(($WorkItem$rrole$(_a_10)));
  } else if (_e_0.$ === "WorkItem.Interrupt") {
    return false;
  } else if (_e_0.$ === "WorkItem.Resume") {
    const _a_11 = _e_0["a"];
    return $WorkItem$is_owner$(($WorkItem$rrole$(_a_11)));
  } else {
    const _a_12 = _e_0["a"];
    return $WorkItem$is_owner$(($WorkItem$rrole$(_a_12)));
  }
}

function $WorkItem$needs_kevin$(_s_0) {
  if (_s_0.$ === "WorkItem.Deployed") {
    return true;
  } else {
    return false;
  }
}

function $WorkItem$astep$go$(_hold_0, _s_0, _e_0) {
  if (_hold_0) {
    return _s_0;
  } else {
    return $WorkItem$step$(_s_0, _e_0);
  }
}

function $WorkItem$astep$(_av_0, _s_0, _e_0) {
  return $WorkItem$astep$go$(($Bool$and$(($WorkItem$is_off$(_av_0)), ($WorkItem$owner_event$(_e_0)))), _s_0, _e_0);
}

function $WorkItem$board_step$(_xs_0, _i_0, _av_0, _e_0) {
  if (_xs_0.$ === "Nil") {
    return {$: "Nil"};
  } else {
    const _x_0 = _xs_0["head"];
    const _t_0 = _xs_0["tail"];
    if (_i_0 === 0) {
      return {$: "Con", "head": ($WorkItem$astep$(_av_0, _x_0, _e_0)), "tail": _t_0};
    } else {
      const _k_0 = (_i_0 - 1);
      return {$: "Con", "head": _x_0, "tail": ($WorkItem$board_step$(_t_0, _k_0, _av_0, _e_0))};
    }
  }
}

function $WorkItem$nth$($0, $1) {
  for (;;) {
    {
      const _xs_0 = $0;
      const _i_0 = $1;
      if (_xs_0.$ === "Nil") {
        return {$: "WorkItem.Abandoned"};
      } else {
        const _x_0 = _xs_0["head"];
        const _t_0 = _xs_0["tail"];
        if (_i_0 === 0) {
          return _x_0;
        } else {
          const _k_0 = (_i_0 - 1);
          $0 = _t_0;
          $1 = _k_0;
          continue;
        }
      }
    }
  }
}

function $abi_version$() {
  return 1;
}

function $Nat$is_eq$(_a_0, _b_0) {
  return $Cmp$is_eq$(cmp_new(_a_0, _b_0));
}

function $Bool$not$(_b_0) {
  if (!_b_0) {
    return true;
  } else {
    return false;
  }
}

function $Bool$and$(_a_0, _b_0) {
  if (!_a_0) {
    return false;
  } else {
    return _b_0;
  }
}

function $Cmp$is_eq$(_c_0) {
  if (_c_0.$ === "EQ") {
    return true;
  } else {
    return false;
  }
}

function $0m0(v) {
  const top = [v];
  for (let at = top, key = 0;;) {
    switch (v.$) {
      case "WorkItem.Actor": at = at[key] = {...v, "id": nat_host(v["id"])}; return top[0];
      default: throw "bend: WorkItem.Actor has no tag " + v?.$ + " (its tags: WorkItem.Actor); a tag names its constructor as the"
      + " loading file sees it, which a later version will make the same"
      + " everywhere (#1105)";
    }
  }
}

function $0m1(v) {
  const top = [v];
  for (let at = top, key = 0;;) {
    switch (v.$) {
      case "WorkItem.Actor": at = at[key] = {...v, "id": BigInt(v["id"])}; return top[0];
      default: throw "bend: WorkItem.Actor has no tag " + v?.$ + " (its tags: WorkItem.Actor); a tag names its constructor as the"
      + " loading file sees it, which a later version will make the same"
      + " everywhere (#1105)";
    }
  }
}

function $0m2(v) {
  const top = [v];
  for (let at = top, key = 0;;) {
    switch (v.$) {
      case "WorkItem.Proposed": at[key] = v; return top[0];
      case "WorkItem.Claimed": at = at[key] = {...v, "au": $0m0(v["au"])}; return top[0];
      case "WorkItem.Implementing": at = at[key] = {...v, "au": $0m0(v["au"]), "hd": nat_host(v["hd"])}; return top[0];
      case "WorkItem.Verifying": at = at[key] = {...v, "au": $0m0(v["au"]), "hd": nat_host(v["hd"])}; return top[0];
      case "WorkItem.DraftPR": at = at[key] = {...v, "au": $0m0(v["au"]), "hd": nat_host(v["hd"])}; return top[0];
      case "WorkItem.Integrated": at = at[key] = {...v, "au": $0m0(v["au"]), "hd": nat_host(v["hd"]), "ig": nat_host(v["ig"])}; return top[0];
      case "WorkItem.Deployed": at = at[key] = {...v, "au": $0m0(v["au"]), "hd": nat_host(v["hd"]), "ig": nat_host(v["ig"]), "dp": nat_host(v["dp"])}; return top[0];
      case "WorkItem.Accepted": at = at[key] = {...v, "au": $0m0(v["au"]), "hd": nat_host(v["hd"]), "ig": nat_host(v["ig"]), "ap": $0m0(v["ap"])}; return top[0];
      case "WorkItem.Merged": at = at[key] = {...v, "au": $0m0(v["au"]), "hd": nat_host(v["hd"]), "ig": nat_host(v["ig"]), "ap": $0m0(v["ap"])}; return top[0];
      case "WorkItem.Blocked": at = at[key] = {...v, "au": $0m0(v["au"]), "hd": nat_host(v["hd"])}; return top[0];
      case "WorkItem.Interrupted": at = at[key] = {...v, "au": $0m0(v["au"]), "hd": nat_host(v["hd"])}; return top[0];
      case "WorkItem.Abandoned": at[key] = v; return top[0];
      default: throw "bend: WorkItem.Item has no tag " + v?.$ + " (its tags: WorkItem.Proposed, WorkItem.Claimed, WorkItem.Implementing, WorkItem.Verifying, WorkItem.DraftPR, WorkItem.Integrated, WorkItem.Deployed, WorkItem.Accepted, WorkItem.Merged, WorkItem.Blocked, WorkItem.Interrupted, WorkItem.Abandoned); a tag names its constructor as the"
      + " loading file sees it, which a later version will make the same"
      + " everywhere (#1105)";
    }
  }
}

function $0m3(v) {
  const top = [v];
  for (let at = top, key = 0;;) {
    switch (v.$) {
      case "WorkItem.Proposed": at[key] = v; return top[0];
      case "WorkItem.Claimed": at = at[key] = {...v, "au": $0m1(v["au"])}; return top[0];
      case "WorkItem.Implementing": at = at[key] = {...v, "au": $0m1(v["au"]), "hd": BigInt(v["hd"])}; return top[0];
      case "WorkItem.Verifying": at = at[key] = {...v, "au": $0m1(v["au"]), "hd": BigInt(v["hd"])}; return top[0];
      case "WorkItem.DraftPR": at = at[key] = {...v, "au": $0m1(v["au"]), "hd": BigInt(v["hd"])}; return top[0];
      case "WorkItem.Integrated": at = at[key] = {...v, "au": $0m1(v["au"]), "hd": BigInt(v["hd"]), "ig": BigInt(v["ig"])}; return top[0];
      case "WorkItem.Deployed": at = at[key] = {...v, "au": $0m1(v["au"]), "hd": BigInt(v["hd"]), "ig": BigInt(v["ig"]), "dp": BigInt(v["dp"])}; return top[0];
      case "WorkItem.Accepted": at = at[key] = {...v, "au": $0m1(v["au"]), "hd": BigInt(v["hd"]), "ig": BigInt(v["ig"]), "ap": $0m1(v["ap"])}; return top[0];
      case "WorkItem.Merged": at = at[key] = {...v, "au": $0m1(v["au"]), "hd": BigInt(v["hd"]), "ig": BigInt(v["ig"]), "ap": $0m1(v["ap"])}; return top[0];
      case "WorkItem.Blocked": at = at[key] = {...v, "au": $0m1(v["au"]), "hd": BigInt(v["hd"])}; return top[0];
      case "WorkItem.Interrupted": at = at[key] = {...v, "au": $0m1(v["au"]), "hd": BigInt(v["hd"])}; return top[0];
      case "WorkItem.Abandoned": at[key] = v; return top[0];
      default: throw "bend: WorkItem.Item has no tag " + v?.$ + " (its tags: WorkItem.Proposed, WorkItem.Claimed, WorkItem.Implementing, WorkItem.Verifying, WorkItem.DraftPR, WorkItem.Integrated, WorkItem.Deployed, WorkItem.Accepted, WorkItem.Merged, WorkItem.Blocked, WorkItem.Interrupted, WorkItem.Abandoned); a tag names its constructor as the"
      + " loading file sees it, which a later version will make the same"
      + " everywhere (#1105)";
    }
  }
}

function $0m4(v) {
  const top = [v];
  for (let at = top, key = 0;;) {
    switch (v.$) {
      case "WorkItem.Receipt": at = at[key] = {...v, "sha": nat_host(v["sha"])}; return top[0];
      default: throw "bend: WorkItem.Receipt has no tag " + v?.$ + " (its tags: WorkItem.Receipt); a tag names its constructor as the"
      + " loading file sees it, which a later version will make the same"
      + " everywhere (#1105)";
    }
  }
}

function $0m5(v) {
  const top = [v];
  for (let at = top, key = 0;;) {
    switch (v.$) {
      case "WorkItem.Receipt": at = at[key] = {...v, "sha": BigInt(v["sha"])}; return top[0];
      default: throw "bend: WorkItem.Receipt has no tag " + v?.$ + " (its tags: WorkItem.Receipt); a tag names its constructor as the"
      + " loading file sees it, which a later version will make the same"
      + " everywhere (#1105)";
    }
  }
}

function $0m6(v) {
  const top = [v];
  for (let at = top, key = 0;;) {
    switch (v.$) {
      case "WorkItem.Claim": at = at[key] = {...v, "a": $0m0(v["a"])}; return top[0];
      case "WorkItem.Start": at = at[key] = {...v, "a": $0m0(v["a"])}; return top[0];
      case "WorkItem.Push": at = at[key] = {...v, "a": $0m0(v["a"]), "sha": nat_host(v["sha"])}; return top[0];
      case "WorkItem.Submit": at = at[key] = {...v, "a": $0m0(v["a"])}; return top[0];
      case "WorkItem.Verify": at = at[key] = {...v, "a": $0m0(v["a"]), "r": $0m4(v["r"])}; return top[0];
      case "WorkItem.Integrate": at = at[key] = {...v, "a": $0m0(v["a"]), "ish": nat_host(v["ish"]), "r": $0m4(v["r"])}; return top[0];
      case "WorkItem.Deploy": at = at[key] = {...v, "a": $0m0(v["a"]), "served": nat_host(v["served"])}; return top[0];
      case "WorkItem.Observe": at = at[key] = {...v, "served": nat_host(v["served"])}; return top[0];
      case "WorkItem.Accept": at = at[key] = {...v, "a": $0m0(v["a"])}; return top[0];
      case "WorkItem.Merge": at = at[key] = {...v, "a": $0m0(v["a"])}; return top[0];
      case "WorkItem.Block": at = at[key] = {...v, "a": $0m0(v["a"])}; return top[0];
      case "WorkItem.Unblock": at = at[key] = {...v, "a": $0m0(v["a"])}; return top[0];
      case "WorkItem.Interrupt": at[key] = v; return top[0];
      case "WorkItem.Resume": at = at[key] = {...v, "a": $0m0(v["a"])}; return top[0];
      case "WorkItem.Abandon": at = at[key] = {...v, "a": $0m0(v["a"])}; return top[0];
      default: throw "bend: WorkItem.Ev has no tag " + v?.$ + " (its tags: WorkItem.Claim, WorkItem.Start, WorkItem.Push, WorkItem.Submit, WorkItem.Verify, WorkItem.Integrate, WorkItem.Deploy, WorkItem.Observe, WorkItem.Accept, WorkItem.Merge, WorkItem.Block, WorkItem.Unblock, WorkItem.Interrupt, WorkItem.Resume, WorkItem.Abandon); a tag names its constructor as the"
      + " loading file sees it, which a later version will make the same"
      + " everywhere (#1105)";
    }
  }
}

function $0m7(v) {
  const top = [v];
  for (let at = top, key = 0;;) {
    switch (v.$) {
      case "WorkItem.Claim": at = at[key] = {...v, "a": $0m1(v["a"])}; return top[0];
      case "WorkItem.Start": at = at[key] = {...v, "a": $0m1(v["a"])}; return top[0];
      case "WorkItem.Push": at = at[key] = {...v, "a": $0m1(v["a"]), "sha": BigInt(v["sha"])}; return top[0];
      case "WorkItem.Submit": at = at[key] = {...v, "a": $0m1(v["a"])}; return top[0];
      case "WorkItem.Verify": at = at[key] = {...v, "a": $0m1(v["a"]), "r": $0m5(v["r"])}; return top[0];
      case "WorkItem.Integrate": at = at[key] = {...v, "a": $0m1(v["a"]), "ish": BigInt(v["ish"]), "r": $0m5(v["r"])}; return top[0];
      case "WorkItem.Deploy": at = at[key] = {...v, "a": $0m1(v["a"]), "served": BigInt(v["served"])}; return top[0];
      case "WorkItem.Observe": at = at[key] = {...v, "served": BigInt(v["served"])}; return top[0];
      case "WorkItem.Accept": at = at[key] = {...v, "a": $0m1(v["a"])}; return top[0];
      case "WorkItem.Merge": at = at[key] = {...v, "a": $0m1(v["a"])}; return top[0];
      case "WorkItem.Block": at = at[key] = {...v, "a": $0m1(v["a"])}; return top[0];
      case "WorkItem.Unblock": at = at[key] = {...v, "a": $0m1(v["a"])}; return top[0];
      case "WorkItem.Interrupt": at[key] = v; return top[0];
      case "WorkItem.Resume": at = at[key] = {...v, "a": $0m1(v["a"])}; return top[0];
      case "WorkItem.Abandon": at = at[key] = {...v, "a": $0m1(v["a"])}; return top[0];
      default: throw "bend: WorkItem.Ev has no tag " + v?.$ + " (its tags: WorkItem.Claim, WorkItem.Start, WorkItem.Push, WorkItem.Submit, WorkItem.Verify, WorkItem.Integrate, WorkItem.Deploy, WorkItem.Observe, WorkItem.Accept, WorkItem.Merge, WorkItem.Block, WorkItem.Unblock, WorkItem.Interrupt, WorkItem.Resume, WorkItem.Abandon); a tag names its constructor as the"
      + " loading file sees it, which a later version will make the same"
      + " everywhere (#1105)";
    }
  }
}

function $0m8(v) {
  const top = [v];
  for (let at = top, key = 0;;) {
    switch (v.$) {
      case "Nil": at[key] = v; return top[0];
      case "Con": at = at[key] = {...v, "head": $0m6(v["head"])}; key = "tail"; v = v[key]; continue;
      default: throw "bend: List has no tag " + v?.$ + " (its tags: Nil, Con); a tag names its constructor as the"
      + " loading file sees it, which a later version will make the same"
      + " everywhere (#1105)";
    }
  }
}

function $0m9(v) {
  const top = [v];
  for (let at = top, key = 0;;) {
    switch (v.$) {
      case "Nil": at[key] = v; return top[0];
      case "Con": at = at[key] = {...v, "head": $0m7(v["head"])}; key = "tail"; v = v[key]; continue;
      default: throw "bend: List has no tag " + v?.$ + " (its tags: Nil, Con); a tag names its constructor as the"
      + " loading file sees it, which a later version will make the same"
      + " everywhere (#1105)";
    }
  }
}

function $0m10(v) {
  const top = [v];
  for (let at = top, key = 0;;) {
    switch (v.$) {
      case "Nil": at[key] = v; return top[0];
      case "Con": at = at[key] = {...v, "head": $0m2(v["head"])}; key = "tail"; v = v[key]; continue;
      default: throw "bend: List has no tag " + v?.$ + " (its tags: Nil, Con); a tag names its constructor as the"
      + " loading file sees it, which a later version will make the same"
      + " everywhere (#1105)";
    }
  }
}

function $0m11(v) {
  const top = [v];
  for (let at = top, key = 0;;) {
    switch (v.$) {
      case "Nil": at[key] = v; return top[0];
      case "Con": at = at[key] = {...v, "head": $0m3(v["head"])}; key = "tail"; v = v[key]; continue;
      default: throw "bend: List has no tag " + v?.$ + " (its tags: Nil, Con); a tag names its constructor as the"
      + " loading file sees it, which a later version will make the same"
      + " everywhere (#1105)";
    }
  }
}
export default {
  "WorkItem.coder": run_lib((a0) => { const r = (run_loop($WorkItem$coder$((a0)))); (a0); return r; }, 1),
  "WorkItem.is_ci": run_lib((a0) => { const r = (run_loop($WorkItem$is_ci$((a0)))); (a0); return r; }, 1),
  "WorkItem.is_owner": run_lib((a0) => { const r = (run_loop($WorkItem$is_owner$((a0)))); (a0); return r; }, 1),
  "WorkItem.operator": run_lib((a0) => { const r = (run_loop($WorkItem$operator$((a0)))); (a0); return r; }, 1),
  "WorkItem.merger": run_lib((a0) => { const r = (run_loop($WorkItem$merger$((a0)))); (a0); return r; }, 1),
  "WorkItem.steward": run_lib((a0) => { const r = (run_loop($WorkItem$steward$((a0)))); (a0); return r; }, 1),
  "WorkItem.rid": run_lib((a0) => { const r = BigInt(run_loop($WorkItem$rid$($0m0(a0)))); $0m1(a0); return r; }, 1),
  "WorkItem.rrole": run_lib((a0) => { const r = (run_loop($WorkItem$rrole$($0m0(a0)))); $0m1(a0); return r; }, 1),
  "WorkItem.same": run_lib((a0, a1) => { const r = (run_loop($WorkItem$same$($0m0(a0), $0m0(a1)))); $0m1(a0); $0m1(a1); return r; }, 2),
  "WorkItem.other": run_lib((a0, a1) => { const r = (run_loop($WorkItem$other$($0m0(a0), $0m0(a1)))); $0m1(a0); $0m1(a1); return r; }, 2),
  "WorkItem.pick": run_lib((a0, a1, a2) => { const r = $0m3(run_loop($WorkItem$pick$((a0), $0m2(a1), $0m2(a2)))); (a0); $0m3(a1); $0m3(a2); return r; }, 3),
  "WorkItem.verify_out": run_lib((a0, a1, a2, a3) => { const r = $0m3(run_loop($WorkItem$verify_out$((a0), (a1), $0m0(a2), nat_host(a3)))); (a0); (a1); $0m1(a2); BigInt(a3); return r; }, 4),
  "WorkItem.verify_gate": run_lib((a0, a1) => { const r = (run_loop($WorkItem$verify_gate$($0m0(a0), $0m0(a1)))); $0m1(a0); $0m1(a1); return r; }, 2),
  "WorkItem.receipt_good": run_lib((a0, a1) => { const r = (run_loop($WorkItem$receipt_good$($0m4(a0), nat_host(a1)))); $0m5(a0); BigInt(a1); return r; }, 2),
  "WorkItem.integrate_gate": run_lib((a0, a1) => { const r = (run_loop($WorkItem$integrate_gate$($0m0(a0), $0m0(a1)))); $0m1(a0); $0m1(a1); return r; }, 2),
  "WorkItem.accept_gate": run_lib((a0, a1) => { const r = (run_loop($WorkItem$accept_gate$($0m0(a0), $0m0(a1)))); $0m1(a0); $0m1(a1); return r; }, 2),
  "WorkItem.from_proposed": run_lib((a0) => { const r = $0m3(run_loop($WorkItem$from_proposed$($0m6(a0)))); $0m7(a0); return r; }, 1),
  "WorkItem.from_claimed": run_lib((a0, a1) => { const r = $0m3(run_loop($WorkItem$from_claimed$($0m0(a0), $0m6(a1)))); $0m1(a0); $0m7(a1); return r; }, 2),
  "WorkItem.from_implementing": run_lib((a0, a1, a2) => { const r = $0m3(run_loop($WorkItem$from_implementing$($0m0(a0), nat_host(a1), $0m6(a2)))); $0m1(a0); BigInt(a1); $0m7(a2); return r; }, 3),
  "WorkItem.from_verifying": run_lib((a0, a1, a2) => { const r = $0m3(run_loop($WorkItem$from_verifying$($0m0(a0), nat_host(a1), $0m6(a2)))); $0m1(a0); BigInt(a1); $0m7(a2); return r; }, 3),
  "WorkItem.integrate_out": run_lib((a0, a1, a2, a3) => { const r = $0m3(run_loop($WorkItem$integrate_out$((a0), $0m0(a1), nat_host(a2), nat_host(a3)))); (a0); $0m1(a1); BigInt(a2); BigInt(a3); return r; }, 4),
  "WorkItem.from_draft": run_lib((a0, a1, a2) => { const r = $0m3(run_loop($WorkItem$from_draft$($0m0(a0), nat_host(a1), $0m6(a2)))); $0m1(a0); BigInt(a1); $0m7(a2); return r; }, 3),
  "WorkItem.deploy_out": run_lib((a0, a1, a2, a3, a4) => { const r = $0m3(run_loop($WorkItem$deploy_out$((a0), $0m0(a1), nat_host(a2), nat_host(a3), nat_host(a4)))); (a0); $0m1(a1); BigInt(a2); BigInt(a3); BigInt(a4); return r; }, 5),
  "WorkItem.from_integrated": run_lib((a0, a1, a2, a3) => { const r = $0m3(run_loop($WorkItem$from_integrated$($0m0(a0), nat_host(a1), nat_host(a2), $0m6(a3)))); $0m1(a0); BigInt(a1); BigInt(a2); $0m7(a3); return r; }, 4),
  "WorkItem.observe_out": run_lib((a0, a1, a2, a3, a4) => { const r = $0m3(run_loop($WorkItem$observe_out$((a0), $0m0(a1), nat_host(a2), nat_host(a3), nat_host(a4)))); (a0); $0m1(a1); BigInt(a2); BigInt(a3); BigInt(a4); return r; }, 5),
  "WorkItem.accept_out": run_lib((a0, a1, a2, a3, a4, a5) => { const r = $0m3(run_loop($WorkItem$accept_out$((a0), $0m0(a1), nat_host(a2), nat_host(a3), nat_host(a4), $0m0(a5)))); (a0); $0m1(a1); BigInt(a2); BigInt(a3); BigInt(a4); $0m1(a5); return r; }, 6),
  "WorkItem.from_deployed": run_lib((a0, a1, a2, a3, a4) => { const r = $0m3(run_loop($WorkItem$from_deployed$($0m0(a0), nat_host(a1), nat_host(a2), nat_host(a3), $0m6(a4)))); $0m1(a0); BigInt(a1); BigInt(a2); BigInt(a3); $0m7(a4); return r; }, 5),
  "WorkItem.from_accepted": run_lib((a0, a1, a2, a3, a4) => { const r = $0m3(run_loop($WorkItem$from_accepted$($0m0(a0), nat_host(a1), nat_host(a2), $0m0(a3), $0m6(a4)))); $0m1(a0); BigInt(a1); BigInt(a2); $0m1(a3); $0m7(a4); return r; }, 5),
  "WorkItem.from_blocked": run_lib((a0, a1, a2) => { const r = $0m3(run_loop($WorkItem$from_blocked$($0m0(a0), nat_host(a1), $0m6(a2)))); $0m1(a0); BigInt(a1); $0m7(a2); return r; }, 3),
  "WorkItem.from_interrupted": run_lib((a0, a1, a2) => { const r = $0m3(run_loop($WorkItem$from_interrupted$($0m0(a0), nat_host(a1), $0m6(a2)))); $0m1(a0); BigInt(a1); $0m7(a2); return r; }, 3),
  "WorkItem.step": run_lib((a0, a1) => { const r = $0m3(run_loop($WorkItem$step$($0m2(a0), $0m6(a1)))); $0m3(a0); $0m7(a1); return r; }, 2),
  "WorkItem.run": run_lib((a0, a1) => { const r = $0m3(run_loop($WorkItem$run$($0m8(a0), $0m2(a1)))); $0m9(a0); $0m3(a1); return r; }, 2),
  "WorkItem.is_off": run_lib((a0) => { const r = (run_loop($WorkItem$is_off$((a0)))); (a0); return r; }, 1),
  "WorkItem.owner_event": run_lib((a0) => { const r = (run_loop($WorkItem$owner_event$($0m6(a0)))); $0m7(a0); return r; }, 1),
  "WorkItem.needs_kevin": run_lib((a0) => { const r = (run_loop($WorkItem$needs_kevin$($0m2(a0)))); $0m3(a0); return r; }, 1),
  "WorkItem.astep.go": run_lib((a0, a1, a2) => { const r = $0m3(run_loop($WorkItem$astep$go$((a0), $0m2(a1), $0m6(a2)))); (a0); $0m3(a1); $0m7(a2); return r; }, 3),
  "WorkItem.astep": run_lib((a0, a1, a2) => { const r = $0m3(run_loop($WorkItem$astep$((a0), $0m2(a1), $0m6(a2)))); (a0); $0m3(a1); $0m7(a2); return r; }, 3),
  "WorkItem.board_step": run_lib((a0, a1, a2, a3) => { const r = $0m11(run_loop($WorkItem$board_step$($0m10(a0), nat_host(a1), (a2), $0m6(a3)))); $0m11(a0); BigInt(a1); (a2); $0m7(a3); return r; }, 4),
  "WorkItem.nth": run_lib((a0, a1) => { const r = $0m3(run_loop($WorkItem$nth$($0m10(a0), nat_host(a1)))); $0m11(a0); BigInt(a1); return r; }, 2),
  "abi_version": run_lib(() => { const r = (run_loop($abi_version$()));  return r; }, 0),
};

export const bendMetadata = {"schema":1,"title":"Bend work-item statechart (page subset)","scope":"Work-item machine only, plus incident counts for two worker laws shown in chapter 01.","trustBoundary":"Bend decides every next state. The adapter only converts JSON to Bend constructors and back, and labels an unchanged state with the guarding law from this file. A replay is not an authorization, an admission or verified success, and it does not observe any live host.","machines":{"work-item":{"title":"Loop 1 · work-item lifecycle","stateType":"WorkItem.Item","eventType":"WorkItem.Ev","step":"WorkItem.astep(availability, state, event)","plane":"work-item","facts":{"availability":{"type":"WorkItem.Avail","default":"DeepWork","description":"Kevin's self-reported availability (ROLES-v0): an owner action while Off is held in the needs-Kevin queue (L7)."}}}},"types":{"WorkItem.Role":{"enum":["Owner","Opus","Sol","Chat","Free","Jev","Slm","Ci"]},"WorkItem.Actor":{"ctors":{"Actor":{"id":"Nat","role":"WorkItem.Role"}}},"WorkItem.Receipt":{"ctors":{"Receipt":{"sha":"Nat","pass":"Bool"}}},"WorkItem.Avail":{"enum":["DeepWork","LowEnergy","Off"]},"WorkItem.Item":{"ctors":{"Proposed":{},"Claimed":{"au":"WorkItem.Actor"},"Implementing":{"au":"WorkItem.Actor","hd":"Nat"},"Verifying":{"au":"WorkItem.Actor","hd":"Nat"},"DraftPR":{"au":"WorkItem.Actor","hd":"Nat"},"Integrated":{"au":"WorkItem.Actor","hd":"Nat","ig":"Nat"},"Deployed":{"au":"WorkItem.Actor","hd":"Nat","ig":"Nat","dp":"Nat"},"Accepted":{"au":"WorkItem.Actor","hd":"Nat","ig":"Nat","ap":"WorkItem.Actor"},"Merged":{"au":"WorkItem.Actor","hd":"Nat","ig":"Nat","ap":"WorkItem.Actor"},"Blocked":{"au":"WorkItem.Actor","hd":"Nat"},"Interrupted":{"au":"WorkItem.Actor","hd":"Nat"},"Abandoned":{}}},"WorkItem.Ev":{"ctors":{"Claim":{"a":"WorkItem.Actor"},"Start":{"a":"WorkItem.Actor"},"Push":{"a":"WorkItem.Actor","sha":"Nat"},"Submit":{"a":"WorkItem.Actor"},"Verify":{"a":"WorkItem.Actor","r":"WorkItem.Receipt"},"Integrate":{"a":"WorkItem.Actor","ish":"Nat","r":"WorkItem.Receipt"},"Deploy":{"a":"WorkItem.Actor","served":"Nat"},"Observe":{"served":"Nat"},"Accept":{"a":"WorkItem.Actor"},"Merge":{"a":"WorkItem.Actor"},"Block":{"a":"WorkItem.Actor"},"Unblock":{"a":"WorkItem.Actor"},"Interrupt":{},"Resume":{"a":"WorkItem.Actor"},"Abandon":{"a":"WorkItem.Actor"}}}},"states":{"work-item":[{"id":0,"tag":"Proposed","machine":"work-item","owner":"PROPOSED","title":"Proposed","summary":"Someone wrote down a piece of work. Nobody owns it yet.","point":{"x":24,"y":34}},{"id":1,"tag":"Claimed","machine":"work-item","owner":"CLAIMED","title":"Claimed","summary":"A coding lane (Opus or Sol only) took it. That actor becomes the author.","point":{"x":244,"y":34}},{"id":2,"tag":"Implementing","machine":"work-item","owner":"IMPLEMENTING","title":"Implementing","summary":"The author is writing code and pushing commits; the head SHA moves.","point":{"x":464,"y":34}},{"id":3,"tag":"Verifying","machine":"work-item","owner":"VERIFYING","title":"Verifying","summary":"The author submitted. A CI actor who is not the author checks a receipt bound to this exact head.","point":{"x":684,"y":34}},{"id":4,"tag":"DraftPR","machine":"work-item","owner":"DRAFT PR","title":"DraftPR","summary":"A passing receipt for the head opened a draft PR.","point":{"x":684,"y":228}},{"id":5,"tag":"Integrated","machine":"work-item","owner":"INTEGRATED","title":"Integrated","summary":"Ci or Opus (not the author) landed it on an integration branch with a passing receipt for that head.","point":{"x":684,"y":422}},{"id":6,"tag":"Deployed","machine":"work-item","owner":"DEPLOYED · NEEDS KEVIN","title":"Deployed","summary":"The running service serves exactly the integrated head. The item now waits in the batched needs-Kevin queue.","point":{"x":464,"y":422}},{"id":7,"tag":"Accepted","machine":"work-item","owner":"OWNER ACCEPTED","title":"OwnerAccepted","summary":"Kevin (the owner, never the author) looked at the deployed result and said yes.","point":{"x":244,"y":422}},{"id":8,"tag":"Merged","machine":"work-item","owner":"MERGED","title":"Merged","summary":"Ci or the owner merged to the default branch, carrying the owner's acceptance.","point":{"x":24,"y":422},"terminal":true},{"id":9,"tag":"Blocked","machine":"work-item","owner":"BLOCKED","title":"Blocked","summary":"Stuck on something outside the work. Only the owner or Opus unblocks or abandons.","point":{"x":464,"y":228}},{"id":10,"tag":"Interrupted","machine":"work-item","owner":"INTERRUPTED","title":"Interrupted","summary":"The worker died or was stopped. Its pushed head is kept; only Resume or an explicit Abandon moves it.","point":{"x":244,"y":228}},{"id":11,"tag":"Abandoned","machine":"work-item","owner":"ABANDONED","title":"Abandoned","summary":"An explicit decision by the owner or Opus to drop the work.","point":{"x":24,"y":228},"terminal":true}]},"laws":[{"id":"L1","loop":"work-item","name":"No merge without owner acceptance","text":"Merged is reachable only from OwnerAccepted, and that acceptance came from the owner, who is not the author.","prevents":"Agents (which act under Kevin's GitHub account) merging to main before Kevin has seen the deployed result.","incidents":{"count":6,"label":"PRs merged with no recorded owner acceptance of a deployed head","source":"replay-20261021 law1:merged_without_owner_acceptance"}},{"id":"L2","loop":"work-item","name":"No self-approval","text":"Verify (Ci), Integrate (Ci or Opus) and Accept (owner) are each passed only by an actor of the right role who is not the author.","prevents":"An agent grading or landing its own work.","incidents":{"count":5,"label":"integrations by the author's own account (indeterminate: agents share the owner's GitHub identity)","source":"replay-20261021 law2:self_integrated"}},{"id":"L3","loop":"work-item","name":"Receipt bound to the head","text":"A draft PR needs a passing test receipt for exactly the head SHA; integration needs one for the integration head.","prevents":"\"Tests passed\" on an older commit being used to land a newer one.","incidents":{"count":39,"label":"integrated while the head had no passing receipt","source":"replay-20261021 law3:integrated_without_head_receipt"}},{"id":"L4","loop":"work-item","name":"Deployed == integrated","text":"Deploy succeeds only when the service serves exactly the integrated head; observing any other head takes \"deployed\" away.","prevents":"The stale-service bug: a service quietly running an old or diverged build.","incidents":{"count":6,"label":"services stale (1), diverged (1) or missing merged commits (4)","source":"replay-20261021 law4:*"}},{"id":"L5","loop":"work-item","name":"Interrupted is never dropped","text":"Interrupted leaves only by Resume (a coder takes the pushed head over) or an explicit Abandon by the owner or Opus.","prevents":"Half-done work silently disappearing when a worker dies.","incidents":{"count":13,"label":"interrupted items with no resume, PR or abandon decision","source":"replay-20261021 law5:interrupted_dropped"}},{"id":"L6","loop":"work-item","name":"Free models never implement","text":"Only Opus or Sol can claim or resume work; free models, Jev, SLMs and chat apps pick, rank and advise.","prevents":"Code written by a lane the owner ruled out for code.","incidents":{"count":0,"label":"no free-model claims seen; the model makes them unreachable","source":"fsm-work-item-v0 proof"}},{"id":"L7","loop":"work-item","name":"Owner is a batched queue, not a blocker","text":"While Kevin is Off, an owner action is held and the item waits in the needs-Kevin queue; everything else keeps moving.","prevents":"Pinging Kevin outside a deep-work block, or one owner decision freezing other work.","incidents":{"count":3,"label":"items in the needs-Kevin queue at report time (availability: off)","source":"replay-20261021 needs_kevin"}},{"id":"W1","loop":"worker","name":"One owner per worktree","text":"A held lease passes on only by Reclaim with evidence the holder is dead; only the holder writes or releases.","prevents":"Two sessions editing the same worktree or branch at once.","incidents":{"count":419,"label":"writes by a second session while another held the worktree (19 worktrees)","source":"supervise-20261021 L1:worktree_two_owners"}},{"id":"W4","loop":"worker","name":"Heavy work off the desktop","text":"Heavy work starts on the desktop only with a declared GPU or toolchain exception; otherwise it runs on the the build machine or a phone.","prevents":"Builds starving the memory-tight desktop and killing other workers.","incidents":{"count":1695,"label":"heavy commands on the desktop with no exception (97 since the fleet came online; 1,310 excepted)","source":"supervise-20261021 L4:heavy_on_desktop"}}],"guards":[{"machine":"work-item","from":"Proposed","event":"Claim","laws":["L6"],"text":"Only Opus or Sol may claim; free models, Jev, SLMs and chat apps never implement."},{"machine":"work-item","from":"*","event":"Abandon","laws":["L5"],"text":"Only the owner or the Opus orchestrator may abandon."},{"machine":"work-item","from":"Claimed","event":"Start","laws":["L2"],"text":"Only the claimant (author) starts."},{"machine":"work-item","from":"Implementing","event":"Push","laws":["L2"],"text":"Only the author pushes."},{"machine":"work-item","from":"Implementing","event":"Submit","laws":["L2"],"text":"Only the author submits."},{"machine":"work-item","from":"Verifying","event":"Verify","laws":["L2","L3"],"text":"A Ci actor who is not the author decides; only a passing receipt for exactly this head opens the draft PR."},{"machine":"work-item","from":"DraftPR","event":"Integrate","laws":["L2","L3"],"text":"Ci or Opus, not the author, with a passing receipt bound to the integration head."},{"machine":"work-item","from":"Integrated","event":"Deploy","laws":["L4"],"text":"An operator deploys, and the served head must equal the integrated head."},{"machine":"work-item","from":"Deployed","event":"Observe","laws":["L4"],"text":"A service seen serving any other head drops back to Integrated.","kind":"observation"},{"machine":"work-item","from":"Deployed","event":"Accept","laws":["L1","L2","L7"],"text":"Only the owner, who is not the author, accepts."},{"machine":"work-item","from":"Accepted","event":"Merge","laws":["L1"],"text":"Ci or the owner merges after acceptance."},{"machine":"work-item","from":"*","event":"Merge","laws":["L1"],"text":"Merge is possible only from OwnerAccepted."},{"machine":"work-item","from":"*","event":"Accept","laws":["L1"],"text":"Acceptance is possible only for a deployed result."},{"machine":"work-item","from":"*","event":"Integrate","laws":["L3"],"text":"Integration is possible only from a draft PR that passed verification."},{"machine":"work-item","from":"Blocked","event":"Unblock","laws":[],"text":"Only the owner or Opus unblocks."},{"machine":"work-item","from":"Interrupted","event":"Resume","laws":["L5","L6"],"text":"A coder (Opus or Sol) resumes, keeping the pushed head."}],"sources":[{"path":"WorkItem.bend","upstream":"kvnloo/z0intelligence@b4c06eefb1fb276e4b999f9e59bbf58bbd9a6f74:bend/work_item_fsm/fsm.bend","gitBlob":"3e0e9922c8d9b13eb6f7647f8fb6128adcf009ea","kind":"bend-model"}],"proofs":[{"project":"bend/work_item_fsm","pr":"kvnloo/z0intelligence#183","commit":"eef21051b1910bcda14542fe5859414f629e4fad","lawsBlob":"b564659c0471b66022c813933c4a99c0b342011e","proofBlob":"db234e6c68f363dbe56571dc749cc9203b3614ea","verdict":"pass","bend":"2.0.34","verifiedAt":"2026-10-21T03:45:45Z","scope":"bend-emitted-book; source_semantics_attested=false"}],"provenance":{"producer":"docs/spec-diagram/fsm/build.mjs","compilerPin":"bend 2.0.34","compilerIdentitySha256":"66e5faf92d75d6e10a11427c8e4f3df72a4c14b1263a04431fdc015a5aa21317","note":"Same compiler identity that produced the PROOF.bend pass receipts. The .files architecture diagram keeps its own 2.0.37 pin."}};
export const bendSourceSha256 = {"Runtime.bend":"aad44780f549d4a402db9560ac1f650cc3a86062d802d4919bfa650a13f1d2b1","WorkItem.bend":"f0465c394b77fed96e577568a7523b139e184dcabf973017bfbe681e74aaf29f"};
