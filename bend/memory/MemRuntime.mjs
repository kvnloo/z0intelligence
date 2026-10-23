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

function $MODEL$has_evidence$(_view_0) {
  if (_view_0.$ === "MODEL.MemAbsent") {
    return false;
  } else if (_view_0.$ === "MODEL.MemAdmitted") {
    return true;
  } else if (_view_0.$ === "MODEL.MemRetrieved") {
    return true;
  } else {
    return true;
  }
}

function $MODEL$claimed_authority$(_view_0) {
  if (_view_0.$ === "MODEL.MemAbsent") {
    return false;
  } else if (_view_0.$ === "MODEL.MemAdmitted") {
    const _attempt_0 = _view_0["attempt"];
    return _attempt_0;
  } else if (_view_0.$ === "MODEL.MemRetrieved") {
    const _attempt_1 = _view_0["attempt"];
    return _attempt_1;
  } else {
    const _attempt_2 = _view_0["attempt"];
    return _attempt_2;
  }
}

function $MODEL$project$(_explicit_0, _policy_0, _view_0) {
  return {$: "MODEL.MemProjection", "evidence": ($MODEL$has_evidence$(_view_0)), "bounded": ($Bool$and$(_explicit_0, _policy_0)), "instruction": false};
}

function $MODEL$evidence$(_p_0) {
  const _evidence_0 = _p_0["evidence"];
  return _evidence_0;
}

function $MODEL$bounded$(_p_0) {
  const _bounded_0 = _p_0["bounded"];
  return _bounded_0;
}

function $MODEL$instruction$(_p_0) {
  const _instruction_0 = _p_0["instruction"];
  return _instruction_0;
}

function $abi_version$() {
  return 1;
}

function $Bool$and$(_a_0, _b_0) {
  if (!_a_0) {
    return false;
  } else {
    return _b_0;
  }
}
export default {
  "MODEL.has_evidence": run_lib((a0) => { const r = (run_loop($MODEL$has_evidence$((a0)))); (a0); return r; }, 1),
  "MODEL.claimed_authority": run_lib((a0) => { const r = (run_loop($MODEL$claimed_authority$((a0)))); (a0); return r; }, 1),
  "MODEL.project": run_lib((a0, a1, a2) => { const r = (run_loop($MODEL$project$((a0), (a1), (a2)))); (a0); (a1); (a2); return r; }, 3),
  "MODEL.evidence": run_lib((a0) => { const r = (run_loop($MODEL$evidence$((a0)))); (a0); return r; }, 1),
  "MODEL.bounded": run_lib((a0) => { const r = (run_loop($MODEL$bounded$((a0)))); (a0); return r; }, 1),
  "MODEL.instruction": run_lib((a0) => { const r = (run_loop($MODEL$instruction$((a0)))); (a0); return r; }, 1),
  "abi_version": run_lib(() => { const r = (run_loop($abi_version$()));  return r; }, 0),
};
