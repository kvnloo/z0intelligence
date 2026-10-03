# Bend for z0intelligence: what it is for, and where the seam goes

Date: 2026-10-03. Scope: owner request "read through all of the Bend docs and work out the best architecture seam
for z0intelligence ... it has a perfect integration with AODL for enforceability and it needs to carry down into
z0intelligence for other parts like which model to use".

Labels used here: **[DOC]** means upstream documentation says it. **[SRC]** means I read it in the source.
**[MEAS]** is a measurement (whose is stated). **[INFERRED]** is my reasoning. It is not documented or measured.

---

## 0. Short answer

1. **Bend 2 is a proof-carrying specification language with a fast native compiler.** It is not a service runtime
   and not an embeddable library. Upstream describes its purpose this way: humans state laws (`LAWS.bend`), an AI
   writes the code and its proof (`PROOF.bend`), and `bend PROOF.bend` is the gate (GUIDE.md L319-326;
   README L41-104). It runs compiled code fast, but only as a native binary (`-o`). It cannot be called in-process
   from Python today (WONTFIX #813: "a native library target for pure defs ... planned, not scheduled").
2. **Upstream's documented integration pattern** is "pure, laws-carrying core + thin effect shell". Laws are checked
   in CI or before commit. For mathematical validity you use `--verdict`, which rechecks with the Lean-proven
   BendTT kernel. Every demo app, servers included, follows this shape (demos/io_http_server has its own LAWS/PROOF).
3. **For z0intelligence, the right seam is the *legality envelope*, not the hot path.** Bend should hold the
   proven reference spec of every deterministic gate that bounds a decision:
   - AODL structural/transition admission (already done, codes 1-18 and 101-106);
   - the legal-action compiler;
   - **the model-eligibility filter**: which candidates *may* be chosen, never which one *is* chosen.

   Python stays the runtime authority. It is tied to the spec by differential parity plus verdict receipts bound
   to the AODL `aodl-canon-1` fingerprint. Learned and ranking components, including "which model", choose only
   inside a Bend-proven envelope. This matches the existing project rule "a learned policy selects among legal
   actions; it does not define legality" (aodl profiles/searchable-policy-kernel.md L148) and #20/#55/#56.
4. **"Bend is 4-25x slower than Python" is a boundary result, not a Bend result.** The #48 gate ran a correctly
   built native binary. Most of the cost is the host encoding JSON in Python, which takes as long as the whole
   validator. On top of that come IPC, a Python thread created per call, and a kernel that scans decimal text into
   linked lists. On pure compute a native Bend binary beat CPython by about 27x in my probe.

   One mode really is a misconfiguration trap: `bend file.bend` with a value-returning `main` is normalized by the
   *checker*. It took 64 s where the native binary took 5 ms on 16x more work [MEAS, mine, §7]. Use it for
   offline/batch work, not per-request IPC.
5. **Provenance action:** Bend **2.0.35 was released today (2026-10-03)** and contains the upstream fix for #1212
   (e1ed2435, PR #1215). That makes it the first stock release that can retire the kvnloo/bend@17db447a patch.
   It still has to pass the same #389 qualification corpus first, identified by binary and kernel hash, not by
   version string.

---

## 1. What I read (pins)

| Source | Pin |
|---|---|
| bendlang/bend upstream (cloned read-only here: `src/bend`) | `a950fd68` (HEAD 2026-10-03 18:48 +0200); tag `v2.0.35` = `79df8d9c`; v2.0.34 = `7d8a3eb0` |
| Installed CLI | `/mnt/zer0models/bend-stack/official/bend/bin/bend` reports `bend 2.0.34` (help text matches `--help`); guide/EFFECTS/SHADERS identical to the release layout |
| Read in full | README.md, AGENTS.md, WONTFIX.txt, guide/GUIDE.md, guide/EFFECTS.md, guide/SHADERS.md, CHANGELOG 2.0.33-2.0.35 (others skimmed), bend2/main.ts (CLI), comp.ts `io_run`/`js_lib`/`js_book`, safe.ts header + kernel section, bendtt.lean header + CLAIMS + PROOF outline, paper sources bend2/docs/BendTT/main.typ + BendRT/main.typ (intro, contract, effects, JS, results, limitations, mechanization), bench/runtime/_pin_/apple_m4*.txt, demos layout |
| kvnloo/bend fork | `/mnt/zer0models/github/bend-fork` detached at `17db447a` ("fix(safe): keep non-members out of mutual groups", refs kvnloo/bend#1, bendlang/bend#1212) |
| kvnloo/bend-native | `/mnt/zer0models/z0-wt/ro/bend-native` `e85e65e` (README, stack/STACK.md) |
| kvnloo/aodl | `/mnt/zer0models/z0-wt/ro/aodl` main `416736c`; canon commit `02bf06c` (aodl-canon-1); profiles/searchable-policy-kernel.md; issue #19 |
| z0intelligence | origin/master `0159808f`; integrate/wiring-20261003 `e02bcf5c`; exp/bend-aodl-gate worktree `a0e9578` (bend/aodl_gate/*, src/z0int/bend_gate.py) |
| z0int code read | worker_routing.py + manifests/worker_routing.v1.json, aodl_admission.py, cognition/{candidates,actions,surface,escalation}.py (signatures + filters), docs/{aodl-integration,aodl-admission,dispatch-authority,local-cognition-portfolio}.md |
| Issues (read-only `gh issue view`) | z0int #13 #20 #47 #48 #53 #55 #56 #59; hermes-agent #324 #388 #389 #390; aodl #19; kvnloo/bend #1 |
| kerdoios | README L110-117 (placement only) |
| z0 registry | `registry/suite.yaml` L315-325: `bend` = `verified_compilation_substrate`, `external_substrate`, `experimental` |
| Artifacts | `/mnt/zer0models/github/cua-lanes/artifacts/bend-stack/SYNTHESIS.md` |
| Perf triage | `/mnt/zer0models/z0-wt/wiring/bend-perf/bench_modes.py` (read). `results/modes-r1.json` was **still empty** at writing time: the run was queued on the quiet-timed lock. See §7.4 |

---

## 2. What Bend 2 is, and what it is not

- **It is a new language, not Bend 1 or HVM.** README L222: "Bend 2 is a new language. Bend 1 programs and HVM do
  not carry over." The runtime is **BendRT**: one C file that is both the CPU program and the GPU kernel
  (GUIDE L637-651). The checker and compiler are TypeScript running on Bun (AGENTS.md L5-19). Any doc or plan
  that talks about "HVM backends" or "gen-c" describes Bend 1 and is stale for us. [DOC]
- **Stated purpose:** "an ambiguity-free language to communicate intents to AIs, a compiler that mechanically checks
  that the AI implemented these intents ... and runs that code fast" (GUIDE L3-7). It has three pillars: laws and
  proofs, fast checking, and fast parallel native code (README L13-104). [DOC]
- **Language shape:** Python-like syntax with Haskell/Lean semantics. Values are pure and affine (used at most
  once), with `+` for reusable `Data`. Termination is mandatory and there is no mutual recursion (`@unsafe` opts
  out and voids proofs). Matches cannot be computed and there is no `if`. Almost nothing is inferred
  (GUIDE L55-126, README L219-253). [DOC]
- **Numbers:** only `Nat`, `U32` and `F32`. There is no U64, I64 or F64. **F32 is axiomatic: nothing about floating
  point can be proven** (README L230-231). At runtime a `Nat` past 2^48-1 fail-stops, while the theory assumes an
  unbounded Nat (WONTFIX L125-127). [DOC]
- **Strings are linked lists of characters**, so text processing is slow. There is no JSON, regex, HTTP, TLS or
  DNS in Base (README L232-235; WONTFIX L92-94). [DOC]
- **Maturity:** "The compiler (not kernel) is 99% AI-written and not yet fully audited." "The checker has no proof
  and may have bugs; `--verdict` uses a proven kernel." (README L245-246). There have been 36 releases in 16 days
  (2.0.0 on 2026-09-17, 2.0.35 on 2026-10-03, per CHANGELOG headings). [DOC]

**What Bend is FOR [DOC + INFERRED]:**
- pure, total and deterministic decision functions over small finite data, with laws stated by humans and proofs
  written by agents (the LAWS/PROOF gate);
- divide-and-conquer compute over sizeable data, compiled native, where it is near C on one core, about 10x on 16
  cores, and fast on a GPU for uniform numeric work.

**What Bend is NOT FOR [DOC + INFERRED]:**
- per-request work in a Python process over IPC;
- JSON or text parsing, regex classification, float arithmetic you want proven;
- effects, persistence, ledgers, scheduling;
- anything that needs a library call from Python;
- being the source of normalization or semantics that another system (AODL) owns.

---

## 3. The proof system: what is proven, what is checked, what is executed

These are the facts that make "enforceable" precise.

| Layer | What it does | Status | Source |
|---|---|---|---|
| `bend.ts` checker (TypeScript) | type, termination and affinity check; `bend PROOF.bend` prints ALL PROOFS CHECK plus "Use --verdict for mathematical validity." | **unproven**, "may have bugs" | README L246; main.ts L81, L715-729 |
| Promise scan | FAIL if any non-Base def is `@unsafe` or foreign, or names one transitively | mechanical; part of every verdict | main.ts L731-765 |
| `safe.ts` elaborator, bend2 to BendTT text | translates the *checked* book into kernel syntax | **unproven translation**. #1212 was a bug here | safe.ts L1-41; BendTT paper L1042-1047 ("Bend reaches BendTT through a translation, written in TypeScript, that has no proof") |
| Parser of `.bendtt` in Lean | | **not verified** | BendTT paper L1042-1043 |
| BendTT kernel `Book.check` (Lean 4.34.0) | rechecks every def | **proven**: `consistent` (no accepted def has type Empty), `sound`, `confluent`, `sr`, `progress`, `halts`, `empty`; no axioms beyond Lean's | bendtt.lean L1529-1560; paper L1006-1047 |
| `-o X.bendtt` | writes the translation so a human can read what a law actually states | read-to-confirm | GUIDE L331-332 |
| Compiler `comp.ts` to C, CUDA, Metal or JS | the code that **runs** | **unverified**. "the companion paper's theorems stop at the calculus"; correctness rests on checksums and the type system | BendRT paper L606-616 |
| Native runtime | Nat cap 2^48, ring of 1024 tasks, count saturates at 2^24 (fail-stop, no fallback) | runtime limits outside the theory | BendRT L608-611; WONTFIX L118-127 |
| JS lane | sequential; F32 NaN payload differs from C; deep recursion may overflow | C is the reference | GUIDE L170; WONTFIX L108-113, L141-144 |

**Consequences [INFERRED from the rows above]:**
- A verdict PASS attests that **the emitted BendTT book** is consistent and its live code halts. It does not attest
  that the source means what you think. #1212 showed a stock PASS on a book that computes True while the source
  computes False (hermes #389: 2.0.32, 2.0.33 and 2.0.34 all mis-certify; SYNTHESIS §5). bend-native already
  records `verification_scope: bend-emitted-book` and `source_semantics_attested: false`, which is exactly right.
- A law is a theorem about **Bend functions over the Bend datatypes**. It is never a theorem about the Python host,
  the encoder, the C runtime, or task success. The gate README states this correctly
  (bend/aodl_gate/README.md L46-68).
- The gate's own run found a real proven-vs-executed gap. A proof over unbounded `Nat` met a runtime abort past
  2^48 (gate README finding 2). Laws over `Nat` need a host-side range guard or a bounded type.
- `--verdict` refuses anything that reaches `@unsafe` or foreign code. **So the proof closure must be pure, and
  effects live in a separate shell file that the proof does not import.** The gate does this: `main.bend` is the
  only file with effects.
- 2.0.35 changelog: "`--verdict --publish` is refused instead of publishing unchecked (#1191)". There are many
  `--verdict` fixes in every release, so verdict behaviour changes per release. [DOC]

---

## 4. Runtimes, run modes and CLI semantics

| Invocation | What actually happens | Speed character | Source |
|---|---|---|---|
| `bend f.bend`, `main : IO(..)` | check, then **compile to JS and run in-process in Bun** (`new Function(...)`) | **sequential** (forks run in sequence); about 0.1 s CLI floor | comp.ts L1294-1298; BendRT paper L516-524; GUIDE L170 |
| `bend f.bend`, `main` returns a value | check, then **normalize `main` with the checker** (`term_snf`) and print | **"slow for big work"**. Lazy; WONTFIX #775 | GUIDE L559-560; main.ts L849-860 |
| `bend f.bend`, no `main` | check only, then a verdict without the kernel | about 0.1 s | main.ts L851-853 |
| `bend f.bend --check-only` | check plus promise scan; no run | about 0.1 s | main.ts L242, L300-302 |
| `bend f.bend --verdict` | check, scan, `safe.ts` elaboration, Lean kernel binary | warm about 140-210 ms; **cold kernel build 22-29 s** (cached in `~/.bend/bendtt/<hash>` or `$BENDTT`) | safe.ts L1472-1533; hermes #388; SYNTHESIS §6 |
| `bend f.bend -o f` | emit one C file, then `clang -O3 -std=c11 ... -lpthread -lm` (clang 14+; 19+ and CUDA 12 for `!`) | **near-C sequential (0.8-1.5x of C twin), 9-12x on 16 threads** | main.ts L358-445; BendRT paper L526-567 |
| `./f --threads N`, `--gpu off`, `--gpu 4GB` | native run; `!` calls go to the GPU only at the root of an IO step | | GUIDE L554-557; WONTFIX L51-52 |
| `-o f.c`, `-o f.js`, `-o f.mjs`, `-o f.bendtt` | emit only; `.mjs` = ES module of the **non-IO, non-foreign, non-template** defs | | main.ts L358-369; comp.ts L3193-3221 |
| `--checkup` (not in `--help`) | checks and runs each import alone | | main.ts L246, L323-352 |

**CLI gotchas that matter for a gate [SRC]:**
- Exit code: 0 on PASS, 1 on FAIL or error (main.ts L301, L318-319, L715-729). The exact success line is
  `ALL PROOFS CHECK`. bend-native requires the exact normalized line, not a substring (hermes #324 checkpoint,
  bug 3).
- `bend PROOF.bend` **without flags runs `main` if PROOF.bend has an IO main** (book_run, L849-856). A gate must
  pass `--verdict` (or `--check-only`) explicitly. Never rely on the bare form.
- `PROOF.bend` beside a `LAWS.bend` must import it (main.ts L809-813). Arbitrary proof basenames bypass this.
  bend-native requires the canonical basename (hermes #324, bug 2).
- **Telemetry:** a daily `GET https://bend-lang.com/check?v=&os=&arch=` unless `BEND_NO_TELEMETRY=1` (main.ts
  L174-207). Ambient `BENDTT`, `BEND_HUB`, `BEND_LIB` and `BEND_ORIGIN` change the trust path (hermes #324 bug 1).
  bend-native strips them. Any z0int call site must do the same.
- The installed `bend` ignores a project's `.env` and `bunfig.toml`. `bun bend2/main.ts` from a checkout does not
  (README L116-117; build-bend.sh). Use only the compiled binary on untrusted inputs.

---

## 5. FFI, IO and embedding: can it be called as a library or a long-lived process?

| Mode | Works today? | Caller | Notes |
|---|---|---|---|
| **Native binary as a long-lived process** (stdin/stdout line protocol, event loop) | yes | any (Python via `subprocess`) | This is what `z0int.bend_gate.BendGate` does. The process start floor is about 1 ms [MEAS]. There is a per-call IPC floor of about 70 µs (gate README finding 1) |
| **Native binary per decision** (argv) | yes | any | 2.9 ms p50 measured by the gate. Not for hot paths |
| **ES module `-o f.mjs`, or `import X from "./x.bend"` under Bun/Node with the loader** | yes | **JS/TS only** | Pure non-IO defs are callable in-process. Constructors are `{$: "Name", ...}` and `Nat` is `BigInt`. Arrays are passed by reference and mutated in place (GUIDE L410-415). **JS lane = sequential**. Could serve OMP (TS) in-process [INFERRED] |
| **Python in-process** | **no** | | No Python target; "Lua, Luau and Python are planned" (README L236). No C library target: WONTFIX #813 "Exporting chosen defs with a header and a lifecycle is planned, not scheduled" (WONTFIX L146-148). aodl#19 already cites #813 as the desired `step(state, event) -> decision` model |
| **C via emitted `.c`** | technically | C | One C file with its own `main`. Runtime internals have **no ABI promise**; "Rebuild your effects with every update" (EFFECTS.md "Compatibility") |
| **Custom effects** (`.c` + `.js` twins) | yes | | Foreign defs are promises: any def reaching one **fails `--verdict`**. Keep effects out of the proof closure |
| **CLI as a verifier** (`--verdict`) | yes | any | The bend-native plugin is the hardened wrapper: captured inputs, private hash-pinned kernel, receipts, offline replay (§8) |

**[INFERRED] Today's documented right way to put Bend inside a Python system:**
1. Use it at build and CI time as a verifier.
2. Optionally, ship a compiled native binary that speaks a small line protocol, with the host owning parsing,
   effects and fail-closed handling.

That is exactly the structure of `exp/bend-aodl-gate`. The structure is right. The mistake would only be to promote
the binary onto the per-request hot path.

---

## 6. Upstream's own "right way" to use Bend [DOC]

- **The laws convention** (GUIDE L319-326):
  - `LAWS.bend` imports the code and states open claims; *the human writes it, the AI does not touch it*.
  - `PROOF.bend` imports `LAWS.bend` and proves each law with a def of the same name; *the AI writes it*.
  - `bend PROOF.bend` is the gate. `--verdict` is the mathematical one.
- **Agent instructions** (README L119-131): "run `bend guide`", "use `LAWS.bend` to keep important rules", "run
  `bend PROOF.bend` before committing", "parallelize the code whenever possible".
- **A law may be filled in another file** (`def M.name(..)`), so a proof can ship separately from its claim
  (GUIDE L525-526). This lets the spec (LAWS) and its proof have separate owners.
- **Effects stay outside proofs:** "Only the event loop runs them, so proofs, termination and the GPU never touch
  host code" (GUIDE L406-409).
- **Parallelism contract:** only balanced binary fork-join. The runtime "trusts the equal-parts promise
  absolutely". Skewed work silently loses its parallelism (GUIDE L147-157; BendRT L606-608).
- **"Bend works best on the back-end, on Linux or macOS"** (README L143). For fast iteration use the JS lane,
  because native compiles are slow (README L242).
- **Keep laws abstract over large constants and depths.** The checker is not beta-optimal and some terms blow up
  (WONTFIX L37-46). The gate hit a stack overflow on a closed `Nat.mul(65536n, 65536n)` in `--verdict`
  (gate README finding 3).

---

## 7. Performance model

### 7.1 Official pins (apple_m4.txt, 2026-09-17, `bench/runtime/_pin_`) [DOC/MEAS upstream]

| | Bend SEQ | Bend 16 threads | C | TS |
|---|---|---|---|---|
| bfs, editdist, nbody, mandelbrot (uniform numeric) | about 1-1.3x C | about 9-12x faster than SEQ | 1x | 1-3x slower than C |
| **hashmap** (branchy, map-heavy) | 2.741 s | 0.238 s | 0.622 s | **0.892 s** |
| lexer (strings) | 2.144 s | 0.198 s | 1.036 s | 3.543 s |

Sequential Bend loses to TypeScript on hashmap-style work. It wins only once parallel. The GPU is bimodal: 52-67x
on uniform work, and slower than 16 CPU threads on divergent work like n-queens and symreg (BendRT L560-567).
Compile time for these benches is 0.55-1.5 s each (pin "COMPILER" column).

### 7.2 What #48 measured (gate README L83-98) [MEAS, gate run, i7-4870HQ, noisy]

- CPython `validate()`: 90 µs p50.
- Host shape plus encode alone: **97 µs p50**, more than the entire validator.
- Bend round-trip for documents: 551 µs. Spawn transition: 150 µs, against a Python reference of 6.6 µs.
- One process per decision: 2.9 ms.
- About 1 µs per token in the kernel; IPC floor about 70 µs; `clang -march=native` gained nothing.

### 7.3 My probe (indicative only: shared lock, machine not quiet; `scratch/probe/`, medians of 3) [MEAS]

| Run | Median wall |
|---|---|
| `bend version` | 0.027 s |
| `bend hello.bend` (check plus JS run) | 0.108 s |
| `bend io.bend --check-only` | 0.107 s |
| `bend io.bend`, JS lane, tree sum 2^18 leaves | 0.128 s |
| **`bend pure.bend`, value `main` normalized by the checker, only 2^14 leaves** | **64.2 s** |
| native `-o`, `--threads 1`, 2^18 | 0.005 s |
| native `--threads 4`, 2^18 | 0.003 s |
| native hello (process floor) | 0.001 s |
| `python3 tree.py`, 2^18 | 0.136 s (`python3 -c pass` = 0.009 s) |

What this shows:
1. On pure compute a native Bend binary is about 27x faster than CPython, including process start.
2. The checker-normalized mode is roughly 10^4-10^5x slower than native. It must never appear in a benchmark or a
   gate path.
3. Every `bend` CLI invocation costs about 0.1 s of Bun startup plus Base check before any work is done.

### 7.4 Was "Bend is 4-25x slower than Python" a misconfiguration?

The perf triage at `/mnt/zer0models/z0-wt/wiring/bend-perf/` is designed to settle this. Its modes are py, enc, rt,
pipe, batch-native-t1, batch-native-default, batch-JS, scan-only and process start. At writing time it was still
waiting for an exclusive quiet-timed window and `results/modes-r1.json` was empty. Read it when it lands.

From source and the numbers above:

- **Not the checker or JS trap.** `build_kernel()` compiles a native binary with `-o` and `BEND_NO_TELEMETRY=1`.
  `BendGate` runs it persistently with `--threads 1` (bend_gate.py L554-602). [SRC]
- **Boundary costs that are configuration-sensitive [INFERRED, the triage should confirm]:**
  - `BendGate._read_line` **creates a Python thread per call** to implement the timeout (bend_gate.py L619-632).
    Thread start in CPython is typically tens of µs, a visible share of the 150 µs spawn round-trip. The triage's
    `pipe` mode isolates this.
  - Requests are **decimal ASCII tokens scanned byte by byte into `List<U32>`** (main.bend L1-80). Profiling already
    put scan and parse at 0.9-2.4 ms per fixture.
  - Kernel tables are linked lists and closures (about 1 µs per token).
  - One request per round trip; `batch` mode amortizes this.
- **Structural, not fixable by configuration:**
  - The host must parse JSON and encode the Core IR in Python. That alone costs as much as the canonical
    validator (97 µs against 90 µs).
  - Any IPC adds a floor that a 6.6 µs in-process check can never beat.
- **Conclusion [INFERRED]:**
  - Per-decision hot-path use loses regardless of tuning. Bend sequential is about C speed, but the decision is
    microseconds of branchy work behind a Python encoder and a pipe.
  - Batched or parallel use can plausibly win: replaying thousands of opportunities or a candidate population
    in one native process.
  - Use the triage's `batch_native_*` and `scan_only` rows to size this.

---

## 8. Versioning, provenance and known issues

- **Identify Bend by hash, not version.** The patched candidate 17db447a still prints `bend 2.0.34` (hermes #389
  comment 2026-10-03; SYNTHESIS §2). Identities in use:
  - official 2.0.34: bin `7fafb749`, kernel `a7e5203d`;
  - patched: bin `b8473e16`, kernel `72e11a86`;
  - Lean 4.34.0: lean `e8baaa71`.

  `build-bend.sh` reproduces the official binary byte for byte from the tag with Bun 1.3.14.
- **#1212 (source-to-BendTT drift):**
  - Stock 2.0.32-2.0.34 mis-certify (PASS on a book with the wrong value).
  - Downstream fix kvnloo/bend@17db447a, two lines in safe.ts `item_emit` and `args`, is PROMOTABLE per B389.
  - The upstream fix e1ed2435 ("Resolve BendTT recursive groups by membership", PR #1215, +163/-46 in safe.ts) is
    **in v2.0.35** (`git merge-base --is-ancestor e1ed2435 v2.0.35` → true).
  - SYNTHESIS §5 said "unreleased". That was true when written but is now stale.
  - Per #389 ("No 'latest is best' assumption"), **2.0.35 must pass the same B389 corpus before replacing the
    patch.** The upstream regression test `tests/proof/group_dead_forward.bend` is part of 2.0.35.
- **Kernel trust root (#388):**
  - The shared `~/.bend/bendtt/<hash>/bendtt` cache can be swapped for an always-pass binary: 10/10 forged passes.
  - Fix: build once per process, then pin a private copy by SHA. This gave 0/10 forged passes and 10/10
    mutations rejected.
- **Dependencies (#390):**
  - Bend trusts cached Hub bytes without re-hashing (B390 F3).
  - The bend-native receipt recomputes manifests, and offline replay works.
  - Publishing is public and permanent (GUIDE L534-540). **Never `--publish` z0 kernels.**
- **Upstream limits relevant to us** (README L219-253, WONTFIX):
  - no U64 or F64 yet (owned upstream, no PRs accepted);
  - no cancellation of blocking effects;
  - one event loop;
  - no separate compilation;
  - errors are terse; no debugger or profiler;
  - clang only.
- **Release churn:** a verdict-related fix ships in almost every release (2.0.35 alone has 6 `--verdict` items).
  Plan for a **per-release qualification gate**, not a one-time pin. [INFERRED]

---

## 9. The current AODL to Bend integration (state of the art in our repos)

- **AODL owner constraint** (aodl#19 comment 2026-09-20): "Bend must consume AODL semantics, not silently define a
  second normalization ... Canonicalization / semantic fingerprinting should be specified and tested independently
  in AODL first." Kill criterion: if Bend needs parsing, effects, persistence, runtime state or target execution,
  it stays experimental.
- **AODL canon** (`02bf06c`): `aodl-canon-1` canonicalize plus `semantic_fingerprint()`. The commit message says:
  "This is the normalized input the Bend proof kernel (#19, z0int#48) should attest."
- **z0int exp/bend-aodl-gate** (`a0e9578`):
  - Core IR, 18 structural rules, the spawn transition gate (101-106), and 8 proven laws: revision pinned, dynamic
    required, bounds, exact-Nat budget, no invented authority, antitone spend, no privileged grant, and fail
    closed on unparsed input.
  - Parity: 22,079 cases, 0 false allows. Laws are load-bearing in 9 of 9 cases.
  - **Gap 1:** the Core IR normalizer lives in z0int Python, which violates the AODL constraint until
    `aodl_contract.to_core()` exists (gate README "Gaps").
- **z0int master:**
  - Python `aodl_admission.py` is the runtime authority, with the same 101-106 codes "intentionally match the Bend
    experiment" (docs/aodl-admission.md L38).
  - Bend is "an independent CI/shadow verifier ... not on the hot path".
  - Admission is fsynced before dispatch-start (docs/dispatch-authority.md L40-50).
- **bend-native:** a hardened verifier wrapper. It records receipts with the input manifest and the binary,
  kernel-source and kernel-binary hashes; replay is offline. A PASS is never success, admission or authority
  (README L60-131; STACK.md).
- **Numeric mismatch [SRC, INFERRED]:**
  - Python admission checks budgets as `float` (`_number()` → `float`, `math.isfinite`; aodl_admission.py L72-79),
    over the dimensions tokens, premium_tokens, latency_ms, usd, joules and attention.
  - The Bend gate models exact `Nat` and **refuses** fractional budgets (gate README finding 7).
  - So the proven spec and the authority disagree on the domain of `usd` and `joules` today. Fail-closed on the
    Bend side hides this; the proofs do not cover it.

---

## 10. The seam: how Bend should carry into z0intelligence

### 10.1 Principle

> **Bend proves the envelope; Python enforces the envelope at runtime; learned components choose inside it.**

This restates three existing invariants in z0intelligence and AODL:
- "Ordinary computer science goes in the compiler, not the model. ... A learned model only ever chooses among
  actions the compiler already declared legal" (local-cognition-portfolio.md L11-21);
- "A learned policy selects among legal actions; it does not define legality" (searchable-policy-kernel.md L148);
- "deterministic authority remains downstream/orthogonal" (z0int #55).

What is missing today is a **machine-checked statement of the envelope**. Bend is the tool for exactly that.

### 10.2 Layering

```
AODL (owner of intent and semantics)
  aodl-canon-1 canonicalize + semantic_fingerprint            [exists]
  to_core(): canonical doc -> Core IR (integers, interned ids) [TODO in aodl_contract, per aodl#19]
          |
          |  Core IR + fingerprint (the ONLY input Bend ever sees)
          v
z0int/bend/<family>/   (proof package per decision family; pure; no effects)
  LAWS.bend   - human/owner-owned claims (the spec)
  <kernel>.bend - reference implementation over Core IR
  PROOF.bend  - agent-owned proofs
  main.bend   - effect shell (stdin line protocol), NOT imported by PROOF
          |
          |  CI / promotion time only:
          |   1. bend PROOF.bend --verdict via bend-native  -> verdict receipt
          |   2. differential parity: Python impl vs native kernel (batch mode) -> parity receipt
          v
z0int runtime (Python, hot path, authoritative)
  aodl_admission.py         (structural + transition)        <-- family A
  cognition/actions.py      (legal-action compiler)          <-- family B
  cognition/candidates.py   (filter_candidates hard gates)   <-- family C  "which model MAY run"
  cognition/surface.py      (required class, escalate floor) <-- family D
  worker_routing.plan_route (free_only / local / caps legality) <-- family C'
          |
          v  learned / ranking choice INSIDE the eligible set (Jev, SLMs, rank_candidates)
          v  Kerdoios placement (capacity, quota) -- never semantic suitability
          v  dispatch_authority (claim, admission receipt, physical call)
```

### 10.3 Ownership (unchanged where it already exists)

| Concern | Owner | Bend's role |
|---|---|---|
| Intent, authority, budgets, canonical form, fingerprint, Core IR projection | **AODL** | consumes the Core IR; never normalizes |
| Laws (claims) for each decision family | **owner, via review**: LAWS.bend is the spec of record | states them |
| Reference kernels and proofs | z0int `bend/` (agent-written) | checks them (`--verdict`) |
| Runtime decisions and receipts | z0int Python (aodl_admission, cognition, worker_routing, dispatch_authority) | none on the hot path |
| Verifier mechanics (kernel pin, receipts, replay) | **bend-native** | the wrapper z0int CI calls |
| Model *choice*, ranking, uncertainty, learned policies | z0int cognition + Evolution Lab | bounded *by* laws; not computed in Bend |
| Placement, quota, capacity | **Kerdoios** | none (kerdoios README L117: "does not decide semantic suitability") |
| Registry status | z0 `registry/suite.yaml` | stays `experimental` / `external_substrate` until promotion evidence exists |

### 10.4 "Which model to use": what Bend should and should not prove

**Out of Bend's scope (by design):**
- The choice itself: rank, uncertainty, Jev score, SLM output.
- Regex task classification (`plan_route` uses `re.search` over task text, worker_routing.py L189-191).
- Float thresholds.
- Provider health and quota.

Bend has no regex, F32 is axiomatic, and quota belongs to Kerdoios.

**In scope: the eligibility envelope as a pure function over interned enums and integers.** The input is a finite
candidate table plus a request class. The output is the eligible set plus rejection reasons.

**Candidate laws [INFERRED, drafted from the code's own stated invariants]:**

| # | Law (informal) | Mirrors |
|---|---|---|
| C1 | every selected candidate is in `eligible(state)` (selection never leaves the set) | cascade rejects out-of-set picks (portfolio L328-332, L362-364) |
| C2 | `eligible ⊆ legal_actions` and the risk class of each eligible action is within granted authority | actions.py stage 3 "permission" |
| C3 | `free_only` ⇒ every eligible (provider, model) has a validated $0 route entry | `require_free_route`, recheck at execution (worker_routing L149-153, L341-343) |
| C4 | category `local` ⇒ no eligible candidate has a non-local cohort (no remote fallback) | plan_route L192-196; rules `remote_fallback: False` |
| C5 | a provider with cap 0 or unmeasured is never eligible | plan_route L209-210 |
| C6 | `quality_rank(c) ≥ required` and `max_risk(c) ≥ risk` for every eligible c | filter_candidates gates |
| C7 | monotone escalation: effective tier = max(policy, floor); raising uncertainty class never lowers the floor | surface.py, "surface can only ever raise the floor" (portfolio L317-318) |
| C8 | fail closed: no rung has a candidate ⇒ the allowed set is empty and no model is contacted | portfolio L313-315, L365-366 |
| C9 | budget admission is cheapest-first, and the admitted total is ≤ budget, in exact integer units | actions.py stage 4 |
| C10 | determinism and order: the same inputs give the same eligible list and the same rejection order | "fixed so a replay reproduces it exactly" (filter_candidates docstring) |
| A1-A8 | the existing gate laws (revision, dynamic, bounds, budget, authority, grant, parse) | bend/aodl_gate LAWS.bend |

Every law above holds over an abstract model. **Thresholds must be integers** (for example permille confidence and
integer latency or cost units), discretized by the host before the call, because F32 cannot be proven.
[DOC: README L231]

### 10.5 How it becomes *enforceable* (not decorative)

Proof alone does not enforce anything, because the runtime runs Python. The enforcement chain [INFERRED design,
built only from parts that already exist]:

1. **Spec identity.** Each family has a `spec_hash` = bend-native's input-manifest hash of the LAWS + kernel + PROOF
   closure. Add `main.bend` to the runtime identity (P-12 notes it is excluded).
2. **Verdict receipt.** `hermes bend verify` / bend-native `verify` gives a receipt binding:
   - `spec_hash`;
   - Bend binary SHA and kernel SHA (qualified release only, §8);
   - verdict.
3. **Parity receipt.** Python implementation versus the native kernel in **batch mode** over:
   - a frozen corpus named by the `aodl-canon-1` fingerprints of its contracts;
   - plus seeded fuzz.

   It records `python_impl_revision`, `spec_hash` and the disagreement counts. The pattern is
   `benchmarks/bend_gate_parity.py`.
4. **Runtime binding.** Every runtime decision receipt (`z0int.decision_receipt.v1`, cognition receipt v1) records
   in `extra`:
   - `spec_hash`, `verdict_receipt_sha256`, `parity_receipt_sha256`;
   - `aodl_semantic_fingerprint` (already recorded for admission).

   This mirrors how `aodl_admission_receipt_id` is already linked (dispatch-authority.md L52-55).
5. **Gate on promotion, not per call.** Under #56 (mechanism-neutral promotion), a candidate mechanism (rule, cache,
   SLM, Jev threshold) is promotable only if:
   - its legality layer has a current verdict and parity receipt for the live `spec_hash`;
   - its shadow decisions never left the envelope.

   If `spec_hash`, the Python revision or the Bend identity changes without new receipts, that is a #56
   *invalidator*: deoptimize to the conservative path (deterministic gate or escalate) and fail closed.
6. **Optional runtime cross-check (shadow).** Sample decisions and replay them through the native kernel off the
   hot path. Any disagreement gives a deny-class drift receipt plus demotion. This is the #48 promotion rule 4
   ("any mismatch fails closed to the canonical validator"), applied asynchronously so it costs no latency.

This yields: **laws are checked when they change, implementations are checked when they change, and every runtime
decision names the proven spec revision it implements.** That is the AODL-to-z0int enforceability chain carried down
to model eligibility.

### 10.6 Where Bend *runtime* can still pay off [INFERRED, needs measurement]

- **Offline or batch evaluation in Evolution Lab and z0evals** (#56 addendum stages 4-5):
  - replay the training table × candidate deterministic policies × opportunities in one native process;
  - the work is pure, sizeable and splits into balanced halves, which is the shape where Bend measured 9-12x on 16
    threads and about 27x over CPython single-threaded in my probe;
  - shadow population scoring of *deterministic* challengers is a natural fit.
- **TS harnesses (OMP)** could import the eligibility kernel as an `.mjs` in-process, sequential, for a local
  pre-filter. It is never the authority.
- **Hot path only after** WONTFIX #813 (C library target) or the planned Python target lands. At that point the
  proven kernel could replace the Python reimplementation and the parity step disappears; only the translation and
  compiler stay unproven. Keep kernels shaped as `step(state, event) -> decision` so this becomes a drop-in.

### 10.7 Explicit non-goals (consistent with #48, aodl#19, #20)

- No Bend LLM router.
- No provider or quota logic in Bend.
- No JSON parsing in Bend.
- No per-request IPC on the hot path.
- No `--publish` of z0 kernels.
- No PASS surfaced as task success, admission or authority.
- No second normalization of AODL.
- No float semantics claimed as proven.

---

## 11. Ordered next steps

1. **Read the perf triage results** (`bend-perf/results/modes-r{1,2}.json`) when they land. Record the
   `rt`/`pipe`/`batch`/`scan_only` split in the gate README to settle the 4-25x question. If `pipe` is much smaller
   than `rt`, replace the per-call thread in `BendGate._read_line` with a single reader or a selector. This is a
   correctness-neutral fix and stays in the experimental branch.
2. **Qualify Bend 2.0.35** with the B389 corpus (the bend-native `exp/b389-20261002` harness), identified by
   bin and kernel SHA.
   - If it passes, retire the 17db447a patch: an owner decision under #389. The choice is between promoting
     B, promoting C2, or waiting for 2.0.35.
   - Also re-run the AODL gate proof (`hermes bend verify .../bend/aodl_gate`) on 2.0.35.
3. **AODL `to_core()`** in `aodl_contract`, with tests and the `TypeError` fix already done in `19a4046`. Move the
   gate's Python Core IR encoder there. This unblocks aodl#19's own acceptance.
4. **Integer budget units in AODL canon** (for example micro-USD and millijoules), or an explicit rule that
   fractional dimensions are outside the proven envelope. This removes the float/Nat split (§9). An AODL decision,
   not z0int's.
5. **Add family C (model eligibility) as a Bend proof package** next to `bend/aodl_gate`:
   - Core IR = interned candidate rows plus the request class.
   - Laws C1-C10.
   - Parity against `filter_candidates` + `plan_route` legality + `compile_actions`.
   - Shadow only.
6. **Bind receipts** (§10.5 step 4) in `extra` of the existing decision and cognition receipts. No new ledger.
7. **Wire the #56 promotion check** to require a current verdict and parity receipt for the legality layer of any
   promoted mechanism.

## 12. Owner decisions surfaced

- #389: promote 17db447a or C2, or adopt 2.0.35 after qualification.
- Whether LAWS.bend files are owner-reviewed spec of record, as upstream's convention ("the human writes it, the AI
  does not touch it", GUIDE L321-322) suggests for z0int.
- Integer units for fractional AODL budget dimensions.
- Whether a Bend parity or verdict receipt becomes a hard precondition for #56 promotion, or stays advisory.

## Appendix: scratch produced here

- `src/bend`: read-only upstream clone at `a950fd68`.
- `scratch/z0int-master/`: `git archive` of the z0int files read (origin/master `0159808f`).
- `scratch/issues/*.json`: read-only issue dumps.
- `scratch/probe/`: probe programs, `run.py`, `probe-results.json`. Native builds were made with
  HOME and TMPDIR redirected into the scratch dir and `BEND_NO_TELEMETRY=1`. Run under
  `flock -s quiet-lane.lock`.
