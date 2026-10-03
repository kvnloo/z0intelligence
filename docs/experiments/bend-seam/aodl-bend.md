# AODL ⇄ Bend: the existing contract and where it already carries guarantees

Written 2026-10-03 for the owner's request to find the right Bend seam for z0intelligence. This file covers only the
existing AODL contract, the Bend integration, and which guarantees actually reach which code path. It makes no
architecture choice. Everything here was read from the sources below. Statements I derived without direct evidence are
marked **INFERRED**. Nothing was written outside `/mnt/zer0models/z0-wt/wiring/bend-seam/`.

## 0. Sources (exact revisions)

| repo / artifact | ref read | notes |
| --- | --- | --- |
| kvnloo/aodl `main` | `416736c` | the only difference from `a848270` is `zer0.repo.yaml` (`git diff a848270 HEAD --stat`), so the validator is byte-identical to the frozen Bend reference |
| kvnloo/aodl `feat/aodl-canon-v1-clean` | `6823165` | `aodl-canon-1` + total validator (PR #39, open, **not on main**) |
| kvnloo/aodl `feat/aodl-core-conformance-v1` | `40e3f97` | `aodl-core-v1` `to_core()`, stacked on `6823165`, **not on main** |
| kvnloo/z0intelligence `origin/master` | `0159808` | production admission and dispatch path |
| z0intelligence `exp/bend-aodl-gate` | `a0e9578` (not merged to master) | Bend kernel, host adapter, parity harness; worktree `/mnt/zer0models/z0-wt/bend-aodl-gate` |
| z0intelligence `integrate/wiring-20261003` | `e02bcf5` | merge-base is master `0159808`. Zero diff on `aodl*.py`, `dispatch_authority.py`, `governed_worker.py`, `worker_routing.py`, `pyproject.toml` and `docs/aodl*` |
| kvnloo/bend-native | `e85e65e` | `bend_verify`, receipts, replay; `stack/STACK.md` |
| Bend guide | `/mnt/zer0models/z0-wt/wiring/bend-perf/guide.txt` (2.0.34 `--guide` text) | laws / `--verdict` semantics |
| issues | z0int #13, #47, #48; aodl #19, #21, #39; hermes #324, #388, #389, #390 | dumped read-only to `scratch/issues/` |
| synthesis | `/mnt/zer0models/github/cua-lanes/artifacts/bend-stack/SYNTHESIS.md` §7, §8 | |
| perf triage | `/mnt/zer0models/z0-wt/wiring/bend-perf/` | **still running**: `results/modes-r1.json` was empty when I read it. See §7 |

## 1. What the AODL contract is

The orchestration object is $\mathcal{O}_t=(V_t,E_t,S_t,\Pi_t,\Gamma_t)$ (aodl `README.md` "Why"). Three objects are
never substituted for one another (README; `profiles/intent-contract.md` "Three objects"):

| object | HOTL 0.2 fields | role |
| --- | --- | --- |
| intent contract | `intentGraph` + `policies` + `constraints` + `provenance` | what is wanted and what is **allowed** (Γ: budgets, gates, authority ceilings) |
| compiled plan | `plan` | what a runtime can support. z0int puts **model/provider bindings here** (`src/z0int/aodl.py` docstring, l.13, l.236 "Provider/model choice is intentionally left unbound here") |
| observed O_t | `eventLog`, `observedGraph`, node `lifecycle` | what happened. It never rewrites intent |

The doctrine that governs model choice is in `profiles/searchable-policy-kernel.md`:

> **Mechanism defines what is valid. Policy chooses among valid actions. Search may improve policy, but it never gets to
> redefine validity.**

Γ is the authority boundary. Π (routing, *model/provider routing* explicitly, l.164) is the search surface. "A learned
policy selects among legal actions; it does not define legality" (invariant 4). Fallback is "deterministic and
fail-closed" (5). Promotion must name "the contract, evaluator, and evidence bundle" (6). Schema validation and
permissions are listed as *bad* approximation targets (l.174–182). The same file maps the planes: z0int/Jev is the
bounded learned decision, Kerdoios is placement, Tokenomics is measurement, and Evolution Lab is promotion.

**Authorities in code:**

- `aodl_contract.validate()` is canonical: `validator.py` `validate_02` with 18 rule families. JSON Schema is a strict
  *subset*: it rejects 3 of the 18 invalid fixtures (README "Validate").
- `spec_revision()` is `sha256(validator.py)[:16]` (validator.py l.15). It identifies the validator's code, not the
  meaning of a document.
- Semantic identity has **two competing definitions, and neither is on aodl main**:
  - `aodl-canon-1` (`6823165`, `spec/canonicalization.md`): sorts id-keyed collections and set-like strings, keeps
    `eventLog` order, **strips every `provenance` member**, and **keeps `plan`/`eventLog`/`observedGraph`**. Output
    `"aodl-canon-1:"+sha256`. Exports `CANON_VERSION`.
  - `aodl-core-v1` (`40e3f97`, `profiles/aodl-core-v1.md`, `aodl_contract/core.py`): **keeps `provenance`** and
    **excludes `plan`/`eventLog`/`observedGraph`**. Bare hex `sha256`. Its `__init__` re-exports `semantic_fingerprint`
    from `core.py` and **does not export `CANON_VERSION`**. It says "Bend may consume AODL Core v1 in CI/proof
    experiments, but Bend does not define or own this normalization."
- z0int pins `aodl-contract @ git+…aodl.git@6823165` (master `pyproject.toml` l.23 and `.github/workflows/aodl-admission.yml`
  l.82). That is the canon-1 branch head, not main. `docs/aodl-admission.md` l.86–88 says promotion follows aodl #39.

## 2. What "enforceability" means today (five layers)

| # | layer | mechanism | owner / file |
| --- | --- | --- | --- |
| E1 | **document legality** | `validate()`; unknown version, implicit fan-in, unbounded spawn, cycles, privileged/payment grants, and unknown harness fail closed | aodl `validator.py` |
| E2 | **transition admission** | `decide_spawn(document, SpawnRequest)` → ALLOW/DENY with codes 101–106 (revision pin, `dynamic.allowed`, maxChildren, maxDepth, Γ budgets, authority ceiling). The document supplies ceiling/budgets/bounds. The caller supplies only facts about the proposed spawn | z0int master `src/z0int/aodl_admission.py` |
| E3 | **ordering / durability** | the admission receipt is fsynced *before* `intelligence.dispatch` start and provider admission. DENY never creates a dispatch row. Restart reuses the admission. Trace reuse with a different request fails closed | `aodl_dispatch.py` `ensure()`; `dispatch_authority.py` `claim()`; `docs/dispatch-authority.md` "protocol v3" |
| E4 | **identity / evidence** | `z0int.aodl_admission.v1` receipt holds the canon-1 fingerprint, `provenance.sourceHash`, revisions and latency. Tokenomics gets a one-time projection. It is never task success | `AdmissionDecision.receipt()`; `docs/aodl-admission.md` |
| E5 | **readiness** | `/readyz` is unhealthy unless `aodl_contract.CANON_VERSION == "aodl-canon-1"`. The remote executor refuses an authority whose protocol is not v3 or whose admission is not ready | `intelligence_service.py` l.38–45, l.144–145; `remote_executor.py` l.101–102 |

Bend reaches none of E1–E5 at runtime on master. In master `src/` the only mention is a docstring
(`aodl_admission.py` l.4), and
`docs/aodl-admission.md` l.18–19 says "Bend is an independent CI/shadow verifier … not on the hot path". No master CI
job runs Bend either: `aodl-admission.yml` installs only pytest and aodl@6823165 , and
`git grep -il bend origin/master -- .github` finds nothing.

## 3. How the Bend kernel encodes AODL (`exp/bend-aodl-gate` @ `a0e9578`, `bend/aodl_gate/`)

```
AODL JSON ─► z0int.bend_gate (Python host): shape checks + Core IR interning ─► U32 token line
          ─► Bend native binary (main.bend → gate.decide) ─► "ALLOW" | "DENY <codes>"
   any encode refusal / kernel error / timeout / malformed reply ─► DENY            (bend_gate.py l.652–691)
   optional: canonical validate() disagrees with a Bend ALLOW ─► DENY "canonical-mismatch"   (l.665–684)
```

- `core.bend` holds the Core IR types and a parser monad over U32 tokens. `rules.bend` mirrors `validate_02` as codes
  1–18 ("Each rule mirrors one check of aodl_contract.validator.validate_02 at kvnloo/aodl@a848270", l.3–4). Shape
  checks are **not** in Bend: "the host refuses to emit Core IR for a document that fails them" (l.15–17).
- `transition.bend` is the #13 spawn gate. `Tr{crev, rrev, dyn, maxc, maxd, kids, depth, dims, ceil, req}`. Each Nat
  travels as two U32 tokens (hi, lo), so `seen+prop` is exact. `allows = rev ∧ dyn ∧ kids ∧ depth ∧ spend ∧ auth`
  (l.156–158). Deny codes 101–106 (l.21–23).
- `gate.bend`: `decide(ts)`. Mode 1 is a document, mode 2 a transition, anything else `Deny{[0]}`.
- `main.bend` is the effect shell: one request per stdin line. It reads `/dev/stdin` via `File.read_bytes` because Bend
  has no stdin effect (README finding 7).
- Host `src/z0int/bend_gate.py` (695 lines). It interns values with Python equality so the result is `1 == 1.0 == True`,
  as the validator does (l.97–110). It refuses Nats ≥ 2^47 (`NAT_MAX`, l.69–73). `kernel_revision()` is sha256 over the
  **5 runtime sources** (`core, rules, transition, gate, main`; it excludes LAWS/PROOF, l.42, l.79–89). The pinned
  reference is `AODL_REFERENCE = kvnloo/aodl@a848270…`.

### The laws (`LAWS.bend`), mapped to AODL

| law | statement (paraphrase) | AODL field it protects |
| --- | --- | --- |
| `spawn_revision_pinned` | `allows(t) ⇒ cr == rr` | top-level `revision` |
| `spawn_needs_dynamic` | `allows(t) ⇒ dy == True` | `policies.dynamic.allowed` |
| `spawn_within_bounds` | `allows(t) ⇒ ks+1 ≤ mc ∧ dp+1 ≤ md` | `policies.dynamic.maxChildren/maxDepth` |
| `spawn_within_budget` | `allows(t) ⇒ ∀ declared dim: seen+prop ≤ limit` (**exact Nat**) | `constraints.budgets` (Γ) |
| `spawn_no_invented_authority` | `allows(t) ∧ x ∈ rq ⇒ x ∈ cl` (`Elem`, Nat equality) | parent `authorityCeiling` |
| `spend_antitone` | if a bigger spend fits, a smaller one fits | Γ monotonicity: a denial cannot be cured by asking for more |
| `doc_no_privileged_grant` | `doc_issues = [] ⇒` no edge grants a PRIVILEGED cap (case-insensitive) | `edges[].authority.grant` vs `validator.PRIVILEGED` |
| `allow_needs_parse` | `is_allow(decide ts) ⇒ parses(ts)` (full-length, known mode) | fail-closed wire protocol |

The proofs are `PROOF.bend` (350 lines). The conjunction-projection lemmas `p_rev…p_auth` and `eq_sound`, `mem_elem`
and `within_elem` are generic. The document law's proof restates the kernel's term shape and breaks on unrelated
rule edits (README finding 6).

## 4. What is proven once and what is checked per decision

What "proven" means here: `bend PROOF.bend` type-checks the laws in bend2. `--verdict` re-checks them in the
Lean-proved BendTT kernel (needs Lean 4.34.0). The Bend guide says "the translation has no proof, so read it to
confirm a law" (`guide.txt` §laws, l.~331). The result is a theorem about the **BendTT book emitted for those Bend
definitions**. bend-native states this as `verification_scope: bend-emitted-book` and `source_semantics_attested: false`
(README "Evidence contract").

| property | proven once (CI/offline) | checked per decision | path where the check runs |
| --- | --- | --- | --- |
| 101–106 transition semantics | **yes**: 5 laws + antitone, about `X.allows` in Bend | yes | **master: Python `decide_spawn`** (not the proved function). exp: the Bend binary, compiled from the same defs via the C backend (a different path from BendTT) |
| no privileged grant in an admitted doc | **yes** (`doc_no_privileged_grant`) | yes | master: `validate()` (E1). exp: Bend `rules.bend` |
| fail-closed parse | **yes** (`allow_needs_parse`) | yes | Bend only |
| cycles, self-edges, verifier merge grants, recursion bounds, ports/schemas, duplicates, reachability, fan-in, auction, harness, events, privilege-vs-ceiling | **no** | yes | validator / Bend rules. Law-mutation suite: 4 of these can be weakened and still re-prove (README table) |
| kernel ≡ `validate_02` | no. Measured (§5) | n/a | parity harness |
| host shape + encoder ≡ validator | no. Measured | yes (Python) | `bend_gate.py` |
| determinism | by construction (pure, total, terminating) | n/a | Bend |
| laws are load-bearing | checked: 9/9 well-typed weakenings rejected | n/a | `benchmarks/bend_gate_law_mutations.py` |

## 5. Parity results (seed 48; kernel `fc2e56d486f6d546`; Bend README "Results")

- Documents: **22,079 cases** = 32 fixtures + 1 z0int-emitted doc + 2,046 targeted mutations + 20,000 seeded fuzz.
  **0 unexplained disagreements and 0 false allows** (Bend ALLOW where the validator rejects). There was **1 false
  deny**: `hotl-0.1-fanin`. 0.1 is not modeled, so it is refused, which is correct fail-closed behavior and not a
  semantic miss. On 9,519 kernel-routed cases without a validator exception, the issue-code multisets agree exactly,
  and each rule code 1–18 fired ≥33 times.
- Transitions: 16,356 compared (3,887 allow). 0 disagreements with `reference_transition`. 0 with `aodl.check_budget`.
  3,644 unrepresentable proposals were denied.
- `validate()` raised `TypeError` on 1,158 of the 22,079 cases, all unhashable values. The validator fails closed only if
  every caller treats an exception as a reject. aodl `19a40465` fixes this (2,762 mutations: 252 crashes before, 0
  after; aodl #39). That fix is on the canon-1 branch z0int pins, **not on aodl main**.
- Latency (i7-4870HQ, noisy): CPython `validate()` p50 90 µs; host encode alone 97 µs; Bend round-trip 551 µs; Bend
  process per decision 2,867 µs; Python spawn reference 6.6 µs; Bend spawn round-trip 150 µs. That is the source of
  "4–25x slower". Profiling: byte scan + parse costs 0.9–2.4 ms per fixture. `clang -O3 -march=native` gained nothing.
  #48 promotion condition 2 ("material latency benefit") **fails**.

What parity does not cover: hotl-0.1, Hermes compile-stops (message/mesh/auction) and observed-graph laws are not
modeled (README "Gaps"). The parity oracle is `reference_transition` (exp branch), not master's `decide_spawn`. See G1/G2.

## 6. Receipts, replay and staleness

| receipt | producer | binds | replay / staleness |
| --- | --- | --- | --- |
| **proof receipt** (`schemas/receipt.schema.json`) | bend-native `bend_verify` / `hermes bend verify` | input manifest sha256, proof file, Bend executable, Base, kernel source + binary sha, dependency closure, verdict. `success:true` only on an exact `ALL PROOFS CHECK` | `hermes bend replay`. Changed inputs or installation → `receipt_stale`; a different kernel or verdict → `replay_mismatch`. No downloads (`BEND_HUB=http://127.0.0.1:0`). "carries no execution grant" |
| **admission receipt** `z0int.aodl_admission.v1` nested in `z0int.decision_receipt.v1` | z0int `aodl_dispatch.ensure` | canon-1 fingerprint, `sourceHash`, contract/request revision, parent id, codes, latency. Row `extra.request_sha256` = sha of the whole remote request, **including provider/model** | identical request → replay of the stored decision. Same trace with a different request → `ValueError` (fail closed) |
| **Bend decision** `GateDecision` (exp only) | `bend_gate.BendGate` | `kernel_revision` (5 runtime sources), `aodl_reference` | not persisted |

Measured on the gate project (SYNTHESIS §7): `hermes bend verify` passed 3/3 on official 2.0.34 (kernel `a7e5203d`)
and 3/3 on patched `17db447a` (`72e11a86`). Offline replay matched 2/2 in a netns with only `lo`. One-byte mutants of
LAWS/transition fail 4/4. Pristine receipts against the mutants give `receipt_stale` 4/4 without running Bend. A
cross-build replay gives `receipt_stale`. The mutant rejections came from Bend's front-end checker, not the BendTT
kernel. Plugin defects affecting this seam: P-4 (FAIL receipts cannot be replayed) and P-12 (`main.bend` is outside
the receipt closure; dependency fields are not required).

Trust root (hermes #389 final comment, 2026-10-02): stock 2.0.32–2.0.34 **mis-certify** the #1212 oracle (source False,
certified book True, PASS 25/25). **kvnloo/bend@17db447a is PROMOTABLE**: 0 stable new failures over 1,526 tests, 0
plugin false passes over 1,580 cases. It still reports `bend 2.0.34`, so it is identifiable only by kernel hash.
Upstream fixed #1212 differently (PR 1215 `e1ed2435`) and has not released it. The owner decision is open. The gate
README and the perf triage both use the stock 2.0.34 binary (`/mnt/zer0models/bend-stack/official/bend/bin/bend`).

## 7. Perf triage status

`/mnt/zer0models/z0-wt/wiring/bend-perf/bench_modes.py` splits the cost into five measured paths and one Python
baseline: `py` (validator baseline), `enc` (host encode), `rt` (adapter as shipped), `pipe` (no per-call thread),
`batch` (amortized, one process) and `js` (`bend main.bend`). It rebuilt `build/gate` from `a0e9578`. Results were
empty at 11:53, so **no conclusion yet**. One structural fact is visible in the code: `BendGate._read_line` starts a
new `threading.Thread` for every decision (`bend_gate.py` l.619–632), so the shipped `rt` number includes per-call
thread creation. Independent of the triage, `enc` (97 µs p50) alone already exceeds `validate()` (90 µs p50) in the
frozen results. **INFERRED**: no Bend runtime fix can make the *document* path beat in-process Python while the host
must walk and intern the JSON first, which matches README finding 1. The *transition* path has a different shape
(fixed-width, ~6 dims) and is the only place a batch result could change the picture.

## 8. Where the guarantees carry today, and where they stop

Each item is a fact with evidence unless marked INFERRED.

**G1. The proven budget law is not the function that runs in production.** `LAWS.spawn_within_budget` is exact-Nat
`seen+prop ≤ limit`. Master `decide_spawn` (`aodl_admission.py` l.317–327) and `aodl.check_budget` (`aodl.py` l.795)
use `float` and `> lim + 1e-12`. Probe `scratch/probe/probe_budget.py` runs real master code with a stub contract API
under the quiet-lane lock:

```
limit 2^53, seen 2^53, prop 1      -> master ALLOW ; exact-Nat fits = False
limit 2^60, seen 2^60, prop 100    -> master ALLOW ; exact-Nat fits = False
limit 10, seen 0, prop 10+5e-13    -> master ALLOW ; Bend host refuses fractional (DENY)
```

The practical magnitude is small (huge integers, or ≤1e-12 over). Still, the law's "no wrap, no float epsilon"
guarantee does **not** carry to the hot path. Bend parity could not see this because the fuzz never left the
representable range (the host refuses ≥2^47 and fractional values).

**G2. The differential tie is to the wrong function.** #13's 2026-10-01 comment says master uses "the same 101–106
semantics already differentially tested against Bend". The differential was `bend ≡ reference_transition` on the exp
branch. `reference_transition` takes the ceiling and budgets from the *caller's* `SpawnProposal`, uses `str(c)`
membership, and maps an invalid bound to 0 through `_nat_or_zero`. Master `decide_spawn` reads them from the validated
*document*, uses exact strings, fails closed on a bad bound (`contract-dynamic-shape`), and uses float budgets. Master's
only Bend link is `tests/test_aodl_admission.py::test_every_bend_transition_code`. It holds six hand-written cases,
one per code 101–106. There is no differential or fuzz test against the Bend kernel or `reference_transition`, and no
Bend job in master CI. Today the proof covers a specification sibling of the production gate, not the gate itself.

**G3. Who supplies the document.** `docs/aodl-admission.md` says "The caller cannot supply its own authority". That
holds for the *spawn* fields. In protocol v3, though, the **document itself** arrives in the request envelope
(`aodl_dispatch._envelope`: exactly `{document, spawn}`), and `decide_spawn` validates whatever document it is given.
`governed_worker.build_remote_request` loads it from `Z0INT_AODL_CONTRACT_PATH` in the calling process. I found no
host-side comparison of the received fingerprint with a pinned or allow-listed contract: `governed_contract_fingerprint`
is only *reported* in `/v1/providers` (`intelligence_service.py` l.60). **INFERRED** (grep of master `src/` for
fingerprint comparisons): a v3 client could submit its own valid, permissive contract and be admitted against it.
The receipt would record that contract's fingerprint, so the event is auditable but not prevented.

**G4. v2 bypasses admission.** `rpc()` accepts protocol 2 or 3 and sets `require_aodl = (version == 3)`.
`aodl_dispatch.evaluate` returns `None` for a v2 request without `aodl`, and `is_allowed(None)` is `True`
(`aodl_dispatch.py` l.171–176, `dispatch_authority.py` l.118–131, l.221–222). The docs call this "historical
compatibility". #47's enforcement invariant ("a harness must not be able to silently bypass AODL legality → …") is
therefore not yet met at the authority.

**G5. Model/provider choice is outside the AODL gate.** `governed_worker._provider_model` picks the provider/model from
env `Z0INT_GOVERNED_PROVIDER` or `policy.free_provider_order[0]` and requires `free_route` evidence (l.154–169).
`dispatch_authority.validate_remote` enforces `require_free_route` (l.112–114). The AODL spawn hard-codes
`requested: ["execute"]` and `proposed: {"tokens": …}` (`governed_worker.py` l.244–252). `observed_spawn_state` only
observes `tokens` (l.146–151), so budgets declared on `usd`, `premium_tokens`, `latency_ms`, `joules` or `attention`
compare against 0 observed spend. The AODL `plan` bindings that would carry model choice
(`aodl.binding_for_implementation_stage`, `runtime_contract`, `check_budget`) have **no production caller on master**.
The only `runtime_contract` caller is `project_observed_graph`. What does carry: the admission row's `request_sha256`
covers `provider`/`model`, and canon-1's fingerprint covers `plan`, so model choice is *identity-bound* in receipts.
It is not *legality-checked* by AODL. Model legality today is a worker_routing free-route policy, not a Γ rule.

**G6. Canonical identity is split, and Bend's Core IR belongs to neither definition.** The pinned canon-1 and the newer
core-v1 disagree on `provenance` (stripped vs kept) and on `plan/eventLog/observedGraph` (kept vs excluded).
Installing core-v1 as-is would fail-close **all** admission, because master requires `CANON_VERSION == "aodl-canon-1"`
and an `aodl-canon-1:` prefix (`aodl_admission.py` l.170–221). The canon-1 fingerprint changes whenever `eventLog`
grows. The Bend Core IR (an interned U32 stream in z0int `bend_gate.py`) is a third normalization. aodl #19's owner
comment says "Bend must consume AODL semantics, not silently define a second normalization". The gate README's Gap 1
asks for it to move into `aodl_contract.to_core()`. The `to_core()` that now exists (core-v1) is a canonical-JSON
identity projection, not a Bend token encoder. So the requested move has not happened, and the name now refers to
something else.

**G7. Proof identity and decision identity are not joined.** The proof receipt covers the PROOF closure, 6 files,
without `main.bend` (SYNTHESIS P-12). `GateDecision.kernel_revision` hashes 5 runtime sources without LAWS/PROOF. No
record ties "this decision came from a binary whose defs were proven under receipt R with kernel K". Master admission
receipts carry no proof reference at all.

**G8. Proven vs executed (Bend-internal).** (a) The source→BendTT translation is unproven, and stock releases
mis-certify (#1212, §6). (b) The native binary is built through the C backend, a different path from BendTT. The
runtime Nat aborts past 2^48−1 while the proofs model an unbounded Nat (README finding 2). The host clamps at 2^47 to
compensate. (c) `--verdict` stack-overflows on closed unary Nats like 2^32, which is why `transition.bend` builds 2^32
with `dbl` (l.38–45).

**G9. Validator totality.** The `TypeError` escape (1,158 cases) is fixed only on the pinned canon-1 branch. Any
consumer of aodl **main** still relies on callers treating exceptions as rejects. `decide_spawn` does this
(`contract-validation-error`).

### What already carries (positive)

- E1 and E2 fail closed on every error path in master code: missing package, wrong canon version, validator exception,
  shape error, unknown parent (`aodl_admission.py`). E3 gives exactly-once admission with replay.
- Inside Bend, for its own `allows`/`decide`, the eight laws are real theorems. They are load-bearing (9/9 weakenings
  rejected) and reproducible offline with tamper-evident receipts (§6), on the promotable patched kernel too.
- Within the modeled 0.2 fragment there are 0 false allows over 22,079 documents, with exact issue-code agreement.
  This is strong evidence that `rules.bend` is a faithful *executable spec* of `validate_02@a848270`.

## 9. What this means for the seam (constraints, not a design)

These follow from the sources above. Choosing among them is for the architecture doc.

1. Every source agrees that Bend is a **spec/proof oracle beside the canonical Python mechanism, not the mechanism**:
   aodl #19 (non-goals, kill criterion), z0int #48 (promotion conditions; "no Bend-based LLM router", "no
   provider/quota logic in Bend"), `docs/aodl-admission.md`, bend-native `STACK.md` row "AODL/Bend gate", and the
   searchable-policy-kernel doctrine. Nothing found argues for the hot path.
2. For the proofs to *carry*, the missing piece is a mechanical tie from the proven Bend function to the **production**
   function (`decide_spawn`), not to a sibling reference. That means the same arithmetic (G1), the same field sourcing
   (G2), and a CI differential plus a proof receipt referenced by kernel/source hash (G7). **INFERRED**.
3. "Which model to use" belongs in Π (policy over a legal set). The AODL/Bend contribution is to make the **legal set**
   and its invariants explicit and provable: for example, selected (provider, model) ∈ the plan-declared/ceiling-allowed
   set, Γ spend including `usd`/`premium_tokens` is antitone and bounded, and fallback is deterministic. Today that set
   is defined by worker_routing free-route policy outside AODL (G5). Carrying it down requires AODL to *state* it
   (plan bindings or ceiling caps). Bend can only prove what the contract states. **INFERRED**.
4. Prerequisites the seam must resolve first, whatever design is chosen: one canonical identity (G6: canon-1 vs core-v1,
   then aodl #39 onto main), host-pinned contracts (G3), v2 retirement or explicit `ungoverned` marking (G4), and a
   qualified Bend trust root (17db447a, or an upstream release with PR 1215 that passes the same #389 gate).

## Hard-rule breach

None. GitHub was read only (`gh issue view`). No upstream or fork writes. All files written are under
`/mnt/zer0models/z0-wt/wiring/bend-seam/`: this doc, `scratch/z0m/` (copies of master files), `scratch/issues/`
(issue dumps), and `scratch/probe/` (probe). The one execution (the G1 probe, pure Python, under a second) ran under
`flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock`. No Bend builds or runs.
