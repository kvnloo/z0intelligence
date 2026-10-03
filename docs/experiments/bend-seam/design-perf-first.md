# z0intelligence ⇄ Bend ⇄ AODL, designed from the hot path

Date: 2026-10-03. Angle: **performance first.** The question is where a correctly configured Bend runtime
(compiled native, parallel, long-lived, batched) should actually execute decisions, including model selection over
candidate sets, and where Python stays. The answer must respect measured costs.

Labels: **[MEAS]** means measured, and whose measurement is stated. **[SRC]** means read in code at the cited
revision. **[DOC]** means upstream or project documentation. **[INFERRED]** means my reasoning, not measured or
documented.

Inputs (read in full): `bend-docs.md`, `aodl-bend.md` and `z0-decisions.md` in this directory, plus
`/mnt/zer0models/z0-wt/wiring/bend-perf/{trace.md,bench_modes.py}`. Code was re-read at z0int `origin/master`
`0159808` (via `scratch/master`), the gate worktree `exp/bend-aodl-gate` `a0e9578`, Bend upstream `a950fd68`
(`src/bend/guide/GUIDE.md`), and the z0 registry at `7933914`. A new micro-measurement is in
`scratch/perf-first/legality_cost.{py,json}` (§1.2).

---

## 0. Answer

1. **Bend never executes a per-request decision on the z0int hot path. This holds even when Bend is perfectly
   configured.** The boundary floor (≈70 µs of IPC plus a Python encode that is ≥ the Python work it would replace) is
   10-15x larger than the *whole* Python legality layer for model choice at today's sizes (5-7 µs, §1.2). The legality
   layer is itself under 1% of a decision whose cost is an LLM call or a ledger scan. No `--threads`, batching or
   compile flag changes this. A single decision has nothing to parallelize, and the encoder runs before Bend sees a
   byte. The 4-25x figure from #48 is mostly this boundary, not a misconfiguration (trace.md §3). It is still a correct
   reason not to put Bend there.
2. **Bend executes where its measured strengths apply**: big, pure, integer, balanced batches in one long-lived native
   process. That gives four execution planes, all off the request path:
   - **P0, proof check (CI / promotion time):** `bend PROOF.bend --verdict` through bend-native. Warm about
     140-210 ms; cold 22-29 s.
   - **P1, snapshot verification (near-line, on change):** when the *static* routing/legality snapshot changes,
     the native kernel evaluates the model-eligibility function over the **entire finite request-class domain** in
     one parallel batch. Python computes the same table, and the two are compared bit for bit. This exhaustive
     differential, instead of sampled fuzz, is possible only because eligibility has a finite domain per snapshot
     (§4.3). It is also where a 9-12x, 16-thread native run is worth having.
   - **P2, shadow replay (async, batched):** sampled production decision receipts are replayed through the kernel in
     batches. A mismatch produces a drift receipt and a #56 deoptimization.
   - **P3, offline search (Evolution Lab, conditional):** deterministic challenger policies are replayed over
     opportunity tables. This happens *only* if a measurement shows Bend batch beating the Python/NumPy baseline for
     that challenger class.
3. **Python stays the runtime authority and the only hot-path executor.** On the request path it pays **zero**
   per-call Bend cost. It reads a precomputed `verified` flag keyed by the static snapshot id and records constant
   receipt hashes. It never hashes or calls anything Bend-related per decision.
4. **AODL carries down to model choice as a static envelope.** AODL authors it; `to_core()` projects it into integer
   Core IR (AODL-owned). It joins host policy (free-only, validated $0 routes, caps) to form the **static legality
   snapshot**. Eligibility is a pure function of (static snapshot, request class). Dynamic facts (health, quota,
   in-flight caps) are applied afterwards in Python and **may only narrow** the set. Ranking and the actual pick (Π)
   stay outside Bend.
5. **Two zero-cost hot-path changes do more for enforceability than any Bend runtime could**:
   - exact-integer budget arithmetic in production `decide_spawn`/`check_budget` (closes G1: the proven law then
     describes the production function);
   - a differential that targets master `decide_spawn` instead of the exp-branch `reference_transition` (closes G2).

   Both are Python edits with no measurable latency cost [INFERRED: Python small-int compare is no slower than float
   compare].

---

## 1. Cost budget: what each piece costs

### 1.1 Already measured elsewhere

| Item | Cost | Source |
|---|---|---|
| CPython `aodl_contract.validate()` in-process | 90 µs p50 | gate README @a0e9578 [MEAS, i7-4870HQ, noisy] |
| Python spawn reference (`reference_transition`) | 6.6 µs p50 | gate README [MEAS] |
| Host shape-check + Core IR encode (Python) | **97 µs p50**, more than the validator itself | gate README [MEAS] |
| Bend persistent-pipe round trip, document | 551 µs p50 (6.1x validate) | gate README; trace.md §1 ratios |
| Bend persistent-pipe round trip, spawn transition | 150 µs p50 (22.7x reference) | gate README |
| IPC floor | about 70 µs | gate README "Findings" 1 |
| Kernel work | about 1 µs per token (linked lists, per-byte scan); scan+parse 0.9-2.4 ms per fixture | gate README; trace.md §3 |
| Native process per decision (argv) | 2.9 ms p50 | gate README |
| Native process start floor | about 1 ms | bend-docs.md §7.3 [MEAS, indicative] |
| `bend` CLI invocation floor (Bun + Base check) | about 0.1 s | bend-docs.md §7.3 |
| `bend f.bend` with value `main` (checker normalization) | **64 s for 2^14 leaves** vs 5 ms native for 2^18 | bend-docs.md §7.3 (a trap; never used) |
| Native pure compute vs CPython | about 27x (tree sum 2^18) | bend-docs.md §7.3 [MEAS, indicative, not quiet] |
| Native 16 threads vs 1 thread, uniform work | 9-12x | BendRT paper; apple_m4 pin [DOC] |
| Native SEQ, branchy/map-heavy (hashmap) | 2.7 s vs C 0.62 s vs TS 0.89 s; it wins only once parallel (0.24 s) | bend-docs.md §7.1 [DOC] |
| `--verdict` warm / cold | 140 ms p50 (toy project) / 22.6 s (per-session temp HOME rebuilds the Lean kernel) | trace.md §2 [MEAS] |
| Kerdoios placement subprocess on the OMP turn path | up to 12 s timeout | `bridge/runtime.kerdoios_plan` L139 (z0-decisions.md D11) |
| Per-call ledger scans under flock | O(ledger) each; 5 call sites | z0-decisions.md §1.3 [SRC; cost INFERRED] |

### 1.2 New here: the Python legality layer for model choice

`scratch/perf-first/legality_cost.py` imports master's real `filter_candidates`, `rank_candidates` and
`worker_routing.plan_route`, with the real `manifests/worker_routing.v1.json`, over synthetic candidate tables.
It ran under `flock -s quiet-lane.lock`, `nice 10`, Python 3.14.7, 2,000 iterations each, on a shared and not-quiet
host. **[MEAS, indicative]**

| Function | n = 8 | n = 64 | n = 512 |
|---|---|---|---|
| `filter_candidates` (hard gates) | 4.9 µs | 28.5 µs | 216 µs |
| `rank_candidates` (float scoring, Π-side) | 4.1 µs | 15.1 µs | 132 µs |
| `plan_route` (regex category + free-route + caps) | 7.1 µs | n/a | n/a |

That is about 0.42 µs per candidate per gate pass in Python. Real sizes today [SRC]:
- `worker_routing.v1.json` has 10 providers, 2 validated free routes, `max_attempts 3` and 8 regex rules;
- `local_cognition.v1.json` has 7 models;
- the pi-ai catalog is optional (`Z0INT_PI_AI_CATALOG`; missing means local only).

### 1.3 Break-even rule (applies to every decision surface)

A Bend runtime call replaces Python work `W_py` with `encode + IPC + kernel`. Even with a perfectly shaped kernel
(arrays, no per-byte scan), `encode ≥ O(input)` in Python and `IPC ≥ 70 µs`. So **per-request Bend wins only if
`W_py ≫ 70 µs + encode`.** On the request path:

- model eligibility: 5-7 µs, which loses by 10-15x;
- spawn admission: 6.6 µs, which loses;
- document validation: 90 µs, with encode alone at 97 µs, which loses;
- catalog-scale eligibility (n = 512): 216 µs, but encoding 512 rows in Python is itself O(n) dict walking, so this
  roughly breaks even at best. It is also still under 1% of the LLM call that follows. [INFERRED]

**In batch,** encode and IPC are paid once per batch line or once per batch, and Bend's 9-12x thread scaling applies
when the batch is split into balanced halves (GUIDE L147-157). Batch is the only regime where native Bend can beat
Python. It is also the regime #48 never measured (trace.md §3, "parallelism left on the table").

**Pending measurement that sets the P1/P2 sizing (not the architecture):** `bend-perf/results/modes-r{1,2}.json` was
still empty (0 bytes, queued on quiet-timed) when I finished. Read these rows when they land:

| Triage row | Decision it settles |
|---|---|
| `pipe` vs `rt` | If `pipe` is much smaller than `rt`, the per-call thread in `BendGate._read_line` (bend_gate.py L619-632) is a measurable adapter cost. Fix it on the exp branch (one reader thread or `selectors`). It does not change this design |
| `batch_native_t1.per_decision_us` | The amortized kernel cost per decision. If it is ≤ the Python reference for the same family, P2 replay can run Bend over the full sample. Otherwise P2 samples fewer decisions per window. Either way it stays off the request path |
| `batch_native_t1_scan_only` | The share that is wire format (byte scan). If it dominates, rewrite the kernel input to fixed-width `Array<U32>` blocks before any batch use (GUIDE L172-196) |
| `batch_native_default_threads` vs `_t1` | Expected about 1x, because the gate kernel has no parallel calls (trace.md §1). A gain needs the balanced batch split of §4.4 |
| `batch_js_default_run` | The sequential JS lane. Expected to be the slowest. It confirms that JS is for TS embedding (OMP `.mjs`), not throughput |

---

## 2. Where decisions execute

```
                 ┌────────────────────────── OFF the request path ───────────────────────────┐
 AODL (owner) ──►│ to_core(): canonical doc → integer Core IR envelope   [AODL-owned, Python] │
                 │        │                                                                   │
                 │        ▼  joins host policy manifests → STATIC LEGALITY SNAPSHOT  S         │
                 │        │   id = sha256(core-envelope ‖ policy ‖ manifests ‖ impl rev)      │
                 │        ▼                                                                   │
                 │  P0  bend-native: bend PROOF.bend --verdict  (per family, on spec change)  │
                 │  P1  native kernel, ONE process, --threads N: eligible(S, q) ∀ q ∈ Q(S)    │
                 │      Python: same table → bitwise compare → parity receipt (exhaustive)    │
                 │      ⇒ verified[S.id] = {spec_hash, verdict_rcpt, parity_rcpt, bend_id}    │
                 │  P2  async: sampled decision receipts → batch replay → drift receipts      │
                 │  P3  Evolution Lab offline batch (only where measured faster)              │
                 └───────────────────────────────────────────────────────────────────────────┘
                                         │ verified[S.id]  (a file / dict; read-only at runtime)
                                         ▼
 REQUEST PATH (Python only; no Bend process, no per-call hashing of Bend artifacts)
   validate args → regex category / request class q   (Python; not Bend)
   → eligible_static = filter_candidates / plan_route legality on (S, q)  (current code, µs)
   → eligible_now = eligible_static ∩ availability(health, quota, caps)  (narrow only)
   → rank (floats) + pick, backend attempts  (Π: learned / LLM)          (not Bend)
   → Kerdoios placement (residual)                                        (not Bend)
   → dispatch_authority.claim → aodl_dispatch.ensure → decide_spawn (exact int) → call
   receipt.extra += {legality_snapshot_id, verified: bool, spec/verdict/parity hashes, aodl fingerprint}
```

**Process model.** Bend never runs inside the z0int service. P0 runs in the CI job or the bend-native plugin
(`hermes bend verify`). P1, P2 and P3 run a **long-lived native binary in batch mode**: one process, one stdin
stream, N decisions, `--threads N`. It is launched by a CI job or a z0int *offline* command such as
`z0int legality verify` (a new name, [INFERRED]), under the quiet lane on shared hosts. Nothing on the request path
waits for it. The runtime reads its *result*, a small JSON keyed by snapshot id, the same way it already reads
`manifests/*.json`.

---

## 3. The seams, exactly

### S1. AODL → z0int: the static envelope (AODL-owned projection)

- **Producer:** `aodl_contract.to_core(doc) -> CoreEnvelope`. It lives in kvnloo/aodl, per the aodl#19 owner
  constraint ("Bend must consume AODL semantics, not silently define a second normalization"). Today the Core IR token
  encoder lives in z0int `bend_gate.py`, and the `to_core()` on `feat/aodl-core-conformance-v1` 40e3f97 is a JSON
  identity projection, not this encoder (G6). Both have to converge on one AODL-owned function.
- **Data out (integers and interned enums only; F32 is axiomatic, README L230-231):**
  - `fingerprint`: the one canonical identity, after the canon-1 vs core-v1 split (G6) is resolved;
  - `authority_ceiling`: risk-class set ⊆ {read, write, destructive, publish, credential, payment};
  - `budgets`: per dimension, **integer units**. tokens and premium_tokens are already integers. latency_ms is an
    integer. usd becomes **micro-USD** and joules **millijoules**. attention needs integer units too. This is an AODL
    owner decision (bend-docs §11.4). Until it is made, the fractional dimensions are declared outside the proven
    envelope;
  - `egress`: one of {public, confidential, local_only}. Kerdoios already maps this (`kerdoios/aodl.py` `_PRIVACY`);
  - `route_order`: abstract stage order (`policies.route`);
  - `bindings`: `plan.bindings` model selectors per stage. These are mutable by Evolution Lab without changing intent
    (docs/aodl-integration.md "Pi and Gamma").
- **Cost:** once per contract load, never per decision. The envelope is cached by fingerprint for the life of the
  process. That is a natural key, not a TTL cache.

### S2. Static legality snapshot (z0int-owned)

- **Composition:** `S = (CoreEnvelope, worker_routing policy minus dynamic fields, capabilities registry,
  candidate inventory (local manifest + optional pi-ai projection), legality impl revision)`.
- **`S.id`:** sha256 over the canonical bytes of those parts. It is computed **when a component changes**: on
  manifest load, contract load or a catalog refresh. It is not computed per request. Today `routing_snapshot()`
  re-reads and re-hashes `capabilities.v1.json` on every call (intelligence.py, `registry_sha256`). This design leaves
  that alone. Changing it is a separate perf question for the ledger/snapshot path (§8).
- **Request class `q`:** `(tier, quality_required, risk_class, max_cost_class, capability bits, modality set,
  context-window bucket, category, free_only, egress)`. `category` comes from the Python regex rules in
  `plan_route` (worker_routing.py L189-191). Bend never sees text.
- **Static vs dynamic split:** availability (credentials, health backoff, quota windows, in-flight caps) is
  **dynamic**. It is excluded from `S` and applied after legality. Today `plan_route` takes
  `available_providers` inline. The refactor only reorders a pure function: `plan_route_static(S, q)` followed by
  `∩ available`. That is the precondition for C11 ("availability only narrows").

### S3. Bend family package (z0int `bend/<family>/`)

The layout copies `bend/aodl_gate` @a0e9578, which already follows upstream's convention:

| File | Owner | Content |
|---|---|---|
| `LAWS.bend` | **owner-reviewed spec of record** (GUIDE L321-322: "the human writes it") | claims C1-C13 (§5.2) |
| `<kernel>.bend` | agent | pure, total `eligible(S_core, q) -> Bits` plus `reject_reason`; arrays, not lists |
| `PROOF.bend` | agent | proofs; must import LAWS |
| `batch.bend` | agent | **effect shell only**: reads fixed-width request blocks from stdin, calls the kernel with balanced parallel splits, writes results. It is not imported by PROOF (custom/IO effects fail `--verdict`) |

**Wire format for batch (P1/P2), designed against the measured costs [INFERRED]:**
- One header block carries `S_core` as interned U32 arrays (the candidate rows), sent once per batch.
- After it, `q` records are sent as fixed-width U32 words, not decimal text scanned byte by byte. The gate's
  0.9-2.4 ms per fixture went mostly to scanning and parsing.
- Output is one bitset per `q` (⌈n/32⌉ U32 words) plus a first-failing-gate code per rejected candidate, which
  matches `Rejection.reason` order in filter_candidates L303-306.
- **Parallelism:** the kernel splits the `q` array in halves recursively (`a b = go(lo) go(hi)`). That is the
  balanced binary fork-join the scheduler "trusts absolutely" (GUIDE L147-157). Each leaf runs a candidate loop of
  equal width, so the workload is uniform. The CPU is the target; avoid `!`/GPU, because the work is divergent and
  branchy (bend-docs §7.1).
- **Nat cap:** every value is < 2^32 by construction (enums, counts and buckets), far below the runtime's 2^48 abort
  (G8b).

### S4. Receipts (bend-native + z0int; no new ledger)

| Receipt | Producer | Binds | When |
|---|---|---|---|
| verdict | bend-native `bend_verify` | input-manifest sha of the LAWS + kernel + PROOF closure (`spec_hash`), Bend bin sha, BendTT kernel sha, verdict; `verification_scope: bend-emitted-book` | P0, on spec change or Bend change |
| parity (exhaustive) | z0int offline command | `S.id`, `spec_hash`, sha of the **batch binary** (kernel *and* `batch.bend`, closing P-12), python impl revision, \|Q(S)\|, disagreement count (must be 0), table sha | P1, on `S.id` change |
| parity (sampled, family A) | z0int CI | the same, over a corpus named by AODL fingerprints plus seeded fuzz, against **master `decide_spawn`** | P0/CI, on change to aodl_admission or the gate |
| drift | P2 replayer | receipt id replayed, both answers, `S.id` | P2, only on mismatch |
| decision (existing `z0int.decision_receipt.v1`, cognition receipt) | z0int runtime | `extra`: `legality_snapshot_id`, `legality_verified` (bool), `spec_hash`, `verdict_receipt_sha256`, `parity_receipt_sha256`, `bend_identity`, `aodl_semantic_fingerprint` | every decision. The values are **constants looked up by `S.id`**; zero hashing per call |

Replay: bend-native `hermes bend replay` reproduces the verdict offline. The P1 parity is reproducible from
`(S bytes, batch binary sha, python rev)`. Decision receipts plus the `S` bytes replay through P2. This joins proof
identity to decision identity, which closes G7 [INFERRED].

### S5. Runtime check (Python, request path)

```python
# once per S change (manifest/contract/catalog load), not per request
VERIFIED = load_verified_index()            # {S.id: receipt-hash bundle}, read-only file
# per request
bundle = VERIFIED.get(snapshot.id)          # O(1) dict get, ~50 ns
receipt.extra |= (bundle or {"legality_verified": False, "legality_snapshot_id": snapshot.id})
```

`legality_verified=False` **does not change the canonical decision**: Python stays authoritative (#48 rule 3). It does
change what may *act on top of* the legality layer (S6).

### S6. Promotion coupling (#56): where "enforceable" bites

- A promoted mechanism (routine, cascade threshold, SLM gate, Jev threshold, deterministic challenger) may run in
  ACT mode only if the live `S.id` has a current verdict and exhaustive parity bundle **and** its shadow decisions
  never left `eligible_static`.
- A change to `S.id`, `spec_hash`, the Python legality revision or the Bend identity without new receipts is a #56
  **invalidator**. It deoptimizes to the deterministic gate or escalate, and the stale mechanism is never left
  looking calibrated (#56 addendum, "Deoptimization is mandatory").

Cost: one boolean per decision.

---

## 4. Why the planes are where they are (perf reasoning per plane)

### 4.1 P0: proof checking

- **Workload:** one `--verdict` per family per spec or Bend change.
- **Cost:** warm about 140-210 ms; cold 22-29 s, driven by the Lean kernel build.
- **Config that matters:**
  - share one **SHA-pinned** BendTT kernel across CI jobs and Hermes sessions. Today bend-native's per-session temp
    HOME costs about 22.6 s per session (trace.md §3), and the #388 fix (build once, pin a private copy by SHA)
    keeps that safe;
  - set `BEND_NO_TELEMETRY=1`;
  - strip `BENDTT`, `BEND_HUB`, `BEND_LIB` and `BEND_ORIGIN`;
  - always pass `--verdict` explicitly (a bare `bend PROOF.bend` runs an IO main);
  - match exactly `ALL PROOFS CHECK` and rc 0;
  - keep laws abstract over large constants (`--verdict` stack-overflows on closed unary 2^32; G8c).

### 4.2 Why not per-request (restated with the S5 numbers)

- The S5 dict lookup costs about 50 ns. A Bend round trip costs ≥ 70 µs (IPC) + encode. That is a 1,400x difference
  for the same enforcement value: the decision still runs in Python either way, and the proof attaches to the
  *function*, not the call.
- If Bend ran per request, a Bend crash or timeout would have to fail closed on the request path, adding a new
  availability risk to every decision. P1 moves that risk to snapshot time, where failure only withholds the
  `verified` flag.

### 4.3 P1: exhaustive verification of model eligibility (the main use of the Bend runtime)

- Eligibility is a function of `(S, q)`, and **Q(S) is finite**:
  - tiers: 6 + None;
  - quality: 5;
  - risk: 6;
  - cost: 4 + None;
  - 5 capability bools;
  - modalities: subsets of those in `S`;
  - context window: only the distinct `context_window` breakpoints in `S` matter, so (k + 1) buckets;
  - category × free_only × egress.

  **[INFERRED estimate]** With about 20 candidates this gives about 10^6-10^7 classes, each costing an n-wide
  candidate pass, so about 10^7-10^8 gate evaluations.
  - Python at about 0.4 µs per evaluation (§1.2) takes about 10-60 s per snapshot. That is acceptable in CI.
  - Native Bend with array rows is expected to be ≥ 10x faster per evaluation (the 27x pure-compute probe) and
    9-12x more on 16 threads. That makes it seconds.
  - **Measure before committing to a corpus size.** If the domain explodes (a large catalog), restrict `Q` to the
    classes the request validator can actually emit and record that restriction in the receipt.
- **Why it is worth having:**
  - Sampled parity (22,079 docs; 16,356 transitions) gives evidence. Exhaustive parity over Q(S) gives **equality of
    the Python function and the proven Bend function on every input that can reach them under this snapshot**. The
    remaining trust gaps are the unproven translation (#1212 class) and the shared encoder of `S`.
  - This is how a Bend proof "carries down" to the Python that actually runs, without putting Bend on the path.
- **Why Bend executes here and not only Python:** the differential needs two independent implementations. The proven
  one is the Bend function, and running it natively is the only way to get *its* answers over 10^7 inputs.
  Checker normalization would take hours (64 s for 2^14 leaves), and the JS lane is sequential.

### 4.4 P2: shadow replay

- **Workload:** for example 1-10% of decision receipts, batched every N minutes or nightly, through the same
  `batch.bend` binary.
- **Per-decision amortized cost:** set by the triage `batch_native_*` rows. It never blocks a request.
- **On mismatch:** fail closed per #48 rule 4. That means a drift receipt, `legality_verified=False` for that `S.id`,
  and invalidation of promoted mechanisms that depend on it. The canonical Python decision stands.

### 4.5 P3: Evolution Lab batch replay (conditional)

- **Fits only** deterministic challengers that can be written as pure integer functions over opportunity-table
  features: thresholds discretized to permille, rule conjunctions (≤ 2 scalar predicates, routine compiler) and tier
  floors.
- **Does not fit:** learned models, float scoring, or anything that reads text.
- **Gate:** a quiet-timed A/B of Bend batch vs Python/NumPy over the real el table. Adopt it only on a material win.
  The Bend hashmap pin (SEQ 2.7 s vs TS 0.9 s) warns that branchy table work loses unless it is parallel.
- **Owner:** Evolution Lab (registry: "experiment search, candidate evolution, promotion evidence"). z0int provides
  the kernel package.

---

## 5. What is governed, and how AODL carries down to model and provider choice

### 5.1 Decision surfaces (IDs from z0-decisions.md)

| Surface | Governed by | Law family | Executes (hot) | Bend executes |
|---|---|---|---|---|
| D7 spawn admission 101-106 | AODL Γ + ceiling | A (8 laws, exist) | Python `decide_spawn`, **exact int** | P0 + sampled parity in batch vs **master** `decide_spawn`; P2 |
| E1 document validation | AODL `validate()` | A (1 law + 18 runtime rules) | Python `validate()` (AODL-owned) | P0 + sampled corpus parity (exists) |
| D12 legal-action compiler | authority grant | B (C2) | Python `compile_actions` | P0; P1 if its domain is finitized like eligibility; P2 |
| **D1/D2/D3 provider/model legality** | AODL egress + host free-only + caps | **C (C1-C13)** | Python `plan_route` legality + `route` filters | **P0 + P1 exhaustive** + P2 |
| **cognition `filter_candidates`** | AODL ceiling/egress + tier/quality/risk/cost | **C** | Python | **P0 + P1 exhaustive** + P2 |
| D13 escalation tier | monotone floor + **egress** | D (C7, C13) | Python `EscalationPolicy.decide` | P0; P1 over (risk × verify flag × novelty × egress) |
| D14 opportunity gate | authority grant | B' (grant monotone, ABSTAIN legal) | Python | P0 + P2 |
| D17 promotion relation | #56 | E (no skip to promoted) | Python | P0 (tiny finite relation; exhaustive in P0 itself) |
| D4/D5 health, quota | Kerdoios / ledger | — | Python | **none** (dynamic; narrow-only by C11) |
| D11 placement | Kerdoios | — | Kerdoios | **none** |
| rank / pick / LLM backends | Π (learned) | envelope only (C1) | Python + models | **none** |

### 5.2 Model-eligibility laws (family C)

C1-C10 are from bend-docs §10.4. C11-C13 are new and come out of the perf split. All of them are **[INFERRED]** law
drafts for owner review (LAWS.bend is the spec of record).

| # | Law | Why |
|---|---|---|
| C1 | selected ∈ eligible_now ⊆ eligible_static | Π can only pick inside the set |
| C2 | eligible ⊆ legal actions; risk(c) ∈ authority_ceiling | #20 hard rule |
| C3 | free_only ⇒ every eligible (provider, model) has a validated $0 route | `require_free_route` |
| C4 | category local **or egress = local_only** ⇒ no non-local candidate | `plan_route` local branch + AODL egress |
| C5 | cap 0 or unmeasured ⇒ never eligible | `plan_route` L209-210 |
| C6 | quality ≥ required ∧ max_risk ≥ risk | `filter_candidates` |
| C7 | the escalation floor is monotone in risk and novelty | escalation.py `at_least` |
| C8 | an empty eligible set ⇒ no model contacted (abstain/parent) | `decide` → `abstain` |
| C9 | admitted spend ≤ budget, in exact integer units | Γ, G1 |
| C10 | a deterministic eligible list and a fixed rejection-reason order | filter_candidates docstring |
| **C11** | eligible_now = eligible_static ∩ avail ⇒ eligible_now ⊆ eligible_static (availability only narrows) | the S2 static/dynamic split; the reason health/quota stay out of Bend |
| **C12** | antitone in the envelope: a stricter AODL envelope (smaller ceiling, lower cost cap, stricter egress) never widens eligible_static | AODL can only narrow host defaults |
| **C13** | egress = local_only ⇒ the escalation tier never reaches `remote_frontier` | closes the D13 gap (no privacy input today) |

### 5.3 The carry-down chain for "which model"

1. **AODL** states the bounds: `authorityCeiling`, integer budgets, `egress`, `policies.route`, and `plan.bindings`
   (the per-stage model selectors). AODL does not pick the model.
2. `to_core()` projects them (AODL-owned). The envelope becomes part of `S`.
3. **z0int** computes eligibility from `S` and `q` (Python, proven-equal to Bend via P1).
4. **Π** ranks and picks inside the eligible set. `plan.bindings` supplies preferred selectors; rank breaks ties;
   learned and LLM backends try candidates in order.
5. **Kerdoios** places the residual (capacity, runtime, quant). It "does not decide semantic suitability".
6. **dispatch_authority** claims; `aodl_dispatch.ensure` admits; `decide_spawn` checks Γ spend.
   - The request should carry the real requested capabilities: risk classes from the D12 legal set instead of the
     constant `["execute"]`.
   - It should carry the real proposed spend across dimensions: today only `tokens` is observed. This is an owner
     decision (z0-decisions §5).
7. The receipt records the identity and the verified flag.

Today D8's provider comes from `Z0INT_GOVERNED_PROVIDER` / `free_provider_order[0]`. It should come from step 4, so
that the governed worker also picks inside `eligible_now` [INFERRED; owner question in z0-decisions §5].

---

## 6. Ownership (z0 registry `7933914`)

| Concern | Owner | Note |
|---|---|---|
| Intent, Γ, canonical identity, `to_core()` integer projection, integer units | **aodl** ("typed intent/plan contracts") | Bend consumes it and never normalizes it (aodl#19) |
| Static snapshot, legality functions, request class, receipts, runtime decisions, Bend family packages | **z0intelligence** ("personal policy, bounded decision backends, routine promotion") | Python authoritative |
| Provider routing/allocation, placement, quota model | **kerdoios** ("provider routing, allocation policy") | dynamic; narrow-only; no Bend |
| Search over challengers, P3 batch replay, promotion evidence | **evolution-lab** | P3 only after a measured win |
| Score API, frozen corpora | **z0evals** | names the P1/P2 corpora |
| Verifier mechanics: kernel pin, verdict receipts, replay, release qualification | **bend-native** (+ hermes #324/#388/#389/#390) | P0 trust root |
| Bend itself | `bend` = `verified_compilation_substrate`, `external_substrate`, `experimental` | stays experimental until P1 receipts exist |

---

## 7. Failure modes: everything fails closed to canonical Python

| Failure | Detected by | Effect on the request path | Effect on promotion |
|---|---|---|---|
| Bend binary crash/timeout in P1/P2 | job rc / timeout | **none** | `S.id` not verified, so promoted mechanisms deoptimize |
| `--verdict` FAIL, or Bend build not qualified (#389 corpus by bin + kernel sha, per release) | P0 | none | no new `spec_hash` verified; last good stays only if `S` and the spec are unchanged |
| P1 parity disagreement (any) | P1 | none (Python canonical, #48 rule 3) | `S.id` blocked; owner alert; it is a spec or impl bug by construction |
| P2 drift | P2 | none retroactively; `legality_verified=False` from then on | invalidator → deoptimize |
| `S.id` not in the verified index (new manifest, contract or catalog) | S5 | the decision proceeds canonically, receipt `legality_verified=false` | ACT by promoted mechanisms disallowed until P1 runs |
| AODL envelope projection error / unknown canon | `to_core` / E5 `/readyz` | **deny** (as today: E1/E2 fail closed) | n/a |
| A value ≥ 2^32 in Core IR | host guard | encode refuses → P1 job fails → unverified | as above |
| Fractional budget dimension (until integer units) | host | Python decides; the dimension is marked outside the proven envelope in the receipt | that dimension cannot back a promotion claim |
| Self-supplied permissive contract (G3) | host fingerprint allowlist (new, O(1)) | deny if the fingerprint is not pinned | n/a |
| v2 request without AODL (G4) | `rpc` | owner choice: retire v2, or mark the receipt `ungoverned` | ungoverned never promotable |

---

## 8. Migration from today's code (ordered; every step keeps the hot path all Python)

| Step | Change | Where | Perf effect |
|---|---|---|---|
| M0 | Read triage `modes-r{1,2}.json`. If `pipe` ≪ `rt`, replace the per-call thread in `BendGate._read_line` | `bend-perf/results`; exp `src/z0int/bend_gate.py` L619-632 | exp only |
| M1 | **Exact-int budgets** in `decide_spawn` (aodl_admission.py L317-327) and `aodl.check_budget` (L795); drop `+1e-12` | z0int master | none measurable [INFERRED] |
| M2 | Family-A differential **against master `decide_spawn`**: batch-mode native gate in CI over the AODL-fingerprint-named corpus plus fuzz, including ≥ 2^47 and fractional edges | z0int CI (no Bend job exists on master today) | CI only |
| M3 | AODL: one canonical identity (G6), `to_core()` integer envelope, integer units decision, validator TypeError fix to main (G9) | kvnloo/aodl (owner) | none on the hot path |
| M4 | Host-pinned contract fingerprint allowlist (G3); v2 retire or `ungoverned` (G4) | `aodl_dispatch` / `dispatch_authority.rpc` | O(1) |
| M5 | Split `plan_route` into a static legality part plus `∩ available`; define `RequestClass`; compute `S.id` at load | `worker_routing.py`, `cognition/candidates.py`, `intelligence.route` | none (reordering of pure code) |
| M6 | `bend/model_eligibility/` package (LAWS C1-C13, kernel, PROOF, batch shell). Offline P1 command, verified index, receipts | z0int | offline |
| M7 | `extra` fields in decision and cognition receipts (S4) | z0int receipts | one dict get per decision |
| M8 | #56 promotion requires a verified `S.id` (S6) | z0int promotion path | one bool |
| M9 | P2 replayer (sampled, batched) | z0int offline / cron | off path |
| M10 | Conditional P3 A/B in Evolution Lab | evolution-lab | offline |
| M∞ | When upstream ships #813 (C lib) or the Python target: re-measure in-process FFI cost. Only then could the proven kernel replace the Python reimplementation (parity then disappears). Keep kernels shaped as pure `eligible(S, q)` / `step(state, event)` | — | re-evaluate |

Separate from Bend, and the actual hot-path win **[INFERRED, measure first]**:
- the per-call `routing_snapshot()` rebuild (re-read and hash `capabilities.v1.json`, provider snapshots);
- the five O(ledger) scans under flock (z0-decisions §1.3);
- the 12 s Kerdoios subprocess.

These dominate decision latency far more than any legality rule. Optimizing them is a Python/ledger design question
outside this seam.

---

## 9. What NOT to put in Bend

- Any per-request call on the z0int request path (`/v1/intelligence`, `/v1/worker`, governed worker, cascade,
  bridge turn), even as a "fast structural filter". #48 promotion condition 2 fails at the boundary (§1.3).
- JSON parsing, AODL canonicalization or fingerprinting: AODL owns these, and Bend strings are linked lists.
- Regex task classification (`plan_route` rules), and anything else over free text.
- Ranking, float scores, confidence, entropy, latency estimates: F32 is axiomatic. Thresholds that enter laws are
  host-discretized integers.
- Health, backoff, quota windows, in-flight caps, posture burn-rate projections: dynamic, time-based and ledger-based.
  They are covered only by C11 (narrow-only).
- Kerdoios placement, dispatch claims, leases, receipt persistence, flock, fsync: these are effects.
- LLM routing ("no Bend-based LLM router", #48), provider/quota logic, automatic policy synthesis.
- Checker-normalized `bend f.bend` with a value `main` anywhere (64 s trap). The JS lane is for throughput nowhere;
  it is for an OMP `.mjs` import only, sequential, never authority.
- `--publish` of any z0 kernel (public and permanent). A verdict PASS is never surfaced as task success, admission or
  authority.

---

## 10. Open items

- **Measurement:** the triage rows in §1.3, the P1 domain size and wall time on a real `S`, and the P3 A/B. None of
  them changes the architecture. They size corpora and windows.
- **Owner decisions:**
  - integer units for fractional AODL budget dimensions;
  - whether LAWS.bend is owner-reviewed spec of record;
  - whether a verified `S.id` is a hard #56 precondition or advisory;
  - D8's provider from `plan.bindings`;
  - the real `requested` capabilities and spend dimensions in the governed spawn;
  - the egress bound on D13/D10;
  - the trust root: 17db447a, or Bend 2.0.35 (contains upstream #1212 fix e1ed2435), after it passes the #389 corpus
    by bin + kernel sha.
- **Not verified here:** the P1 throughput estimate (§4.3) and the claim that int arithmetic costs nothing (M1) are
  INFERRED. The §1.2 numbers are indicative, measured on a shared host.

## Hard-rule breach

None. All writes are this file and `scratch/perf-first/` (a micro-benchmark script plus its JSON output). The one
execution was pure-Python, a few seconds of CPU, under `flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock` with
`nice 10`, importing a read-only `git archive` extract. No Bend builds or runs, no git or GitHub writes, nothing
upstream. Sibling files in this directory were read, not modified.
