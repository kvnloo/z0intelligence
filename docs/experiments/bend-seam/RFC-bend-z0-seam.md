# RFC (draft): Bend in z0, the law-layer seam

Status: draft for owner review. Date: 2026-10-03.
Owner request: "please carefully read thru all of the bend docs and analyze the optimal architecture seam for
z0intelligence. we want to use it in the RIGHT way. it has a perfect integration w/ aodl for enforceability and it
needs to carry down into z0intelligence for other parts like which model to use, etc"

This RFC starts from the design that all three judges ranked first, `design-law-first.md` (law-first, a reference
monitor at every effect). It grafts in the best parts of the two runners-up, `design-evolution-first.md` and
`design-perf-first.md`, and fixes every fatal or near-fatal flaw the judges found. Sibling evidence notes in this
directory: `bend-docs.md`, `aodl-bend.md`, `z0-decisions.md`.

Labels:
- **[SRC]**: read in code or docs at the pin given.
- **[MEAS]**: measured; the producer is named.
- **[INFERRED]**: design reasoning, not yet documented or measured.

Pins:

| Component | Pin |
|---|---|
| Bend upstream | `a950fd68` (clone `src/bend`); `v2.0.35` = `79df8d9c` contains the #1212 fix `e1ed2435` (merge-base checked by judges) |
| Installed CLI | `/mnt/zer0models/bend-stack/official/bend/bin/bend` = stock 2.0.34 (bin `7fafb749…`, kernel `a7e5203d…`). It mis-certifies #1212 |
| kvnloo/bend fork | patched `17db447a` |
| z0intelligence | `origin/master` `0159808`; `exp/bend-aodl-gate` `a0e9578`; `integrate/wiring-20261003` `e02bcf5` |
| kvnloo/aodl | main `416736c`; canon-1 `6823165`; core-v1 `40e3f97` |
| Other repos | kerdoios `cdb43b0`; evolution-lab `1c9b523`; z0 registry `7933914`; bend-native `e85e65e` |

---

## 1. The answer: what is the RIGHT way to use Bend in z0

Bend is z0's **law layer, not a runtime component**. For each decision family the owner writes the laws in
`LAWS.bend`. Those laws are about one small, pure integer **reference monitor**:

```
admit_F(envelope, snapshot, request, decision) -> Verdict
```

An agent writes `PROOF.bend`. `bend-native` checks the proofs with `--verdict` (Lean-kernel recheck) on a
**release-qualified** Bend binary and kernel, pinned by SHA.

The AODL contract supplies the content of those laws. It is loaded host-side, never caller-supplied, and projected by
AODL into an integer `Envelope` (authority ceiling, integer Γ budgets, privacy, tier ceiling). That Envelope is the
input every law quantifies over.

Inside z0intelligence, everything that *chooses* is untrusted for safety and only picks inside the legal set:
- regex categories, `rank_candidates`, Jev and SLM output;
- Evolution Lab genomes and `plan.bindings` selectors;
- Kerdoios placement;
- environment-picked providers.

Safety then depends on two things:
1. one Python twin of the proven monitor, called at **every effect choke point** before any model, provider, egress
   or action is touched. That includes re-checking after Kerdoios placement and before the OMP bridge hands a
   placement to an external harness.
2. a structural test that no choke point is bypassed.

The Python twin is tied to the Bend function by:
- an exhaustive or bounded-exhaustive differential, run in Bend's one strong regime (a long-lived native batch
  process);
- receipts that stamp a `law_set_id` on every decision;
- #56 invalidators that force deoptimization the moment anything goes stale.

Bend never ranks, routes, parses, reads health or quota, or sits in a per-request IPC path. Measured Python legality
costs 5-7 µs, while one Bend round trip is at least 70 µs of IPC plus an encode as large as the work itself.

"Which model" is answered three ways:
- by **law** (which models *may* run, given AODL and host policy);
- by **learned or heuristic choice** inside that set (which one *should* run);
- by **Kerdoios** (where it runs), which is re-checked and can never widen the set.

---

## 2. Architecture (text diagram)

```
 kvnloo/aodl (contracts, owner-authored)                   kvnloo/evolution-lab (search, offline)
  validate → canon-1 → fingerprint                           genomes over Genome(K) only
  envelope(doc,node) → Envelope{ints/enums}                  candidate_artifact.v1 + envelope{combinator_id,
  envelope_fp  (excludes plan, eventLog, observedGraph)        genome_schema_sha, spec_hash}
  proof/Core.bend (Envelope type) + family-A LAWS/PROOF              │ import (z0int refuses unknown/stale)
        │ host loads contract BY ID (never caller-supplied)            ▼
        ▼                                                   plan.bindings[stage] = {artifact_id, spec_hash}
 ┌──────────────────────── z0intelligence (Python, in-process, authoritative) ─────────────────────────────┐
 │ snapshot S = Envelope + static policy + candidate rows (+egress_class, validated_free) ; S.id at load     │
 │   request class q (Python regex/validator; missing risk ⇒ highest class, never "read")                    │
 │       │                                                                                                   │
 │       ▼ eligible_C(S,q)  [THE production eligibility fn; filter_candidates/plan_route_static delegate]    │
 │   E_static ──∩ available(health, quota, caps)──▶ E_now      (C11: availability only narrows)              │
 │       │                                                                                                   │
 │       ▼ untrusted choosers emit integer SCORES only: rank, Jev, SLM, q_route (wrapped), posture           │
 │   select.py: masked_argmax · threshold_gate · tier_clamp · project_answer   (proven combinators)         │
 │       │ proposed decision dec                                                                             │
 │       ▼ admit_C(env,S,q,dec)  ── DENY ─▶ FALLBACK (NONE / PARENT_ONLY / ABSTAIN / ASK); never remote      │
 │       ▼ ALLOW → Kerdoios placement → admit_C AGAIN (placement ∈ E_now)                                   │
 │       ▼ effect choke points: dispatch_authority.claim→aodl_dispatch.ensure (A ∧ C) · intelligence.execute │
 │         · CandidateRungBackend.decide · bridge/runtime OMP turn (before placements[0] leaves) · memory   │
 │       ▼ receipt.extra{law_set_id, law_codes, envelope_fp, snapshot_id, law_record, receipt shas}         │
 └───────────────────────────────────────────────────────────────────────────────────────────────────────────┘
        ▲ verified index {law_set_id, S.id → bundle} (read-only file, loaded on change; /readyz gate)
 ┌──────────────────────── off the request path (Bend executes only here) ────────────────────────────────┐
 │ P0  CI: bend-native verify --verdict (qualified bin+kernel SHA, telemetry off, env stripped) → verdict   │
 │ P1  CI/on S change: native -o batch binary, eligible_C/admit_C over Q(S) (per row × class) vs the        │
 │     production Python → exhaustive parity receipt (0 false allows; Q restrictions recorded)              │
 │ P2  async: sampled law_records → same batch binary → drift receipt → #56 deoptimize + promotion freeze   │
 │ P3  Evolution Lab envelope replay (only if a quiet-timed A/B beats Python)                               │
 │ X   (owner-gated, later) native admit sidecar at remote dispatch only; canonical verdict stands on      │
 │     mismatch per #48 rule 4 unless the owner amends it                                                   │
 └─────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Seam specification

### 3.1 S1: AODL → Envelope (owner: kvnloo/aodl)

**Interface [INFERRED name].** `aodl_contract.envelope(doc, *, node_id) -> Envelope`, applied to a doc that has
already passed `validate()` and canonicalization. This is the integer projection that aodl#19 asks AODL to own:
"Bend must consume AODL semantics, not silently define a second normalization". It has its own name because
`to_core()` on `feat/aodl-core-conformance-v1` (`40e3f97`) is a JSON identity projection (G6).

```
Envelope {
  envelope_fp : str    # fingerprint over the canon EXCLUDING plan, eventLog, observedGraph (core-v1 style); never enters Bend
  revision    : Nat
  authority   : Nat    # bitmask {read, write, destructive, publish, credential, payment} from authorityCeiling
  budgets     : List<(dim, limit)>   # integer units: usd→micro-USD, joules→mJ, attention→milli; tokens, premium_tokens, latency_ms already ints
  privacy     : public | confidential | local_only   # NEW typed constraints.privacy; absent ⇒ local_only (fail closed)
  max_tier    : Nat?   # from policies.route, when declared
  dyn         : {allowed, maxChildren, maxDepth}
}
Binding { stage, combinator_id, artifact_id, spec_hash }   # binding identity, separate from envelope_fp
```

**Identity fix (graft from evolution-first §6.3, adopted by all three judges).**
- canon-1's `semantic_fingerprint` (canonical.py L77-80 @ `6823165`) strips only `provenance`, so it keeps `plan` and
  `eventLog` [SRC, judge-verified].
- If law corpora, pins or receipts were keyed on it, every Evolution Lab `plan.bindings` rewrite and every `eventLog`
  append would invalidate them. Law-first's fingerprint allowlist had this flaw.
- Fix:
  - law identity uses `envelope_fp`;
  - binding identity is `{artifact_id, spec_hash}`;
  - both are recorded.
- Excluding `plan` from the fingerprint must not reopen G3 (security review). So **bindings are host-side state**,
  loaded with the contract by id, and each must resolve to a *promoted* artifact with the current `spec_hash`.

**Pinning (G3).**
- The default is that **the host loads the contract by id**. A request-carried AODL document (v3 today) is ignored for
  authority and compared only for diagnostics.
- A mismatch gives `DENY contract-not-pinned`.

**Integer units.**
- Until AODL adopts integer units, z0int converts conservatively: `floor(limit)`, `ceil(spend)`. Then
  `ceil(spend) ≤ floor(limit)` implies `spend ≤ limit` [INFERRED, sound].
- Range guard: every value stays below 2^47. The runtime Nat fail-stops past 2^48-1 (WONTFIX L125-126).
- Values that are out of range or fractional are DENY 0 (parse) at runtime. In parity they count as
  `unrepresentable`, never as agreement.

### 3.2 S2: law packages (proof)

**Layout.**
- `kvnloo/aodl: proof/{Core.bend, LAWS.bend, PROOF.bend}` holds the Envelope type and the family-A laws. This is the
  aodl#19 layout. The current gate `bend/aodl_gate` @ `a0e9578` migrates here.
- `kvnloo/z0intelligence: bend/<family>/{LAWS.bend, <kernel>.bend, PROOF.bend, batch.bend}` holds the other families.

**Imports.**
- Vendor `Core.bend` **by SHA**, as a deliberate choice.
- Correction to law-first: vendoring is not the *only* option. `bend.ts` L968-969 accepts `../` and absolute import
  paths. Vendoring wins because it makes the proof closure hermetic and hashable.
- Never `--publish`: it is public and permanent (GUIDE L527-540).

**Roles (upstream convention, GUIDE L319-332).**
- `LAWS.bend` is owner-reviewed, the spec of record. The agent does not edit it.
- `PROOF.bend` is written by the agent.
- A law may be proven in another file (`def M.name`, GUIDE L525-526).

**Law shape rules** (hard rules, each checked by a mutation-suite case):
1. **Legality is membership in an independently computed set, never a field the chooser returns.** Evidence: probe
   v1 (`scratch/evo-probe/`), where a selector laundered `Cand{i, True, s}` and the law still held. v2 fixes this with
   `mem(lg, id(pick(...)))`.
2. Laws are stated over the monitor's input/output predicate, not over kernel internals.
3. Every law takes integer or enum inputs only. No F32 (README L231: axiomatic).
4. No large closed Nat constants inside `--verdict` (stack overflow at 2^32, G8c).
5. Every family carries two meta-laws:
   - **fallback admitted**: `admit_F(…, FALLBACK_F) = Allow`;
   - **non-vacuity**: a reference chooser's output is admitted.
6. **Legality compilers read canonical state only.** Evolved state construction may feed scorer features, never
   `_action_space`, `compile_actions` or `eligible_C`. Otherwise a genome that reads fewer evidence families sees
   fewer contradictions, and ACT becomes legal (evolution-first §5.3 hazard).
7. The effect shell (`batch.bend`) is **not** in the PROOF closure, and no `@unsafe` def (for example
   `Array.fork`) appears in it.
   - Correction to law-first and perf-first: Base IO effects do *not* fail `--verdict`.
   - Only `@unsafe` defs and foreign defs outside Base are promises (`main.ts book_promises` L737-741).
   - The separation is still kept for hermeticity.

**Gate invocation.**
- Always run `bend PROOF.bend --verdict` and match exactly `ALL PROOFS CHECK` with rc 0.
- A bare `bend PROOF.bend` with no main runs `cli_verdict(book,false)`, which skips the kernel. With an IO main it
  runs the main instead (`main.ts book_run` L849-856).
- Environment: `BEND_NO_TELEMETRY=1`; `BENDTT`, `BEND_HUB`, `BEND_LIB` and `BEND_ORIGIN` stripped; offline.
- The BendTT kernel is a private, SHA-pinned copy shared across CI and Hermes sessions (#388). This removes the 22.6 s
  per-session cold rebuild (`bend-perf/trace.md` §2).

### 3.3 S3: runtime guard (execution; owner: z0intelligence)

**Module `z0int/law/`.** It is pure, stdlib-only, integers only and has no I/O. **The parity harness imports the
production module itself** (G2: parity must run against the function that actually runs).

```python
eligible_C(env, snap, req) -> tuple[EligibleRow, ...]   # deterministic order; + rejections (code per row)
admit_C(env, snap, req, dec) -> LawVerdict               # dec = (candidate|NONE, tier, spend ints)
LawVerdict = {allowed: bool, codes: tuple[int, ...], law_set_id: str, family: "C"}
```

**Fewer implementations (simplicity graft).**
- `filter_candidates` and the new `plan_route_static` **delegate to** `eligible_C`. They are not twins of it.
- That leaves one Python eligibility function, one Python `admit`, and their Bend counterparts. Law-first had three
  Python eligibility implementations.

**Selection choke point (graft from evolution-first).**
- `cognition/select.py` holds `masked_argmax`, `threshold_gate`, `tier_clamp` and `project_answer`. `cascade.py`
  L547-560, `shadow.py` L386-391, the verified_loop rule and the `rank_candidates` int scores all route through it.
- `select.py` guards *selection*; `admit_C` guards *effects*. Both are proven.
- `tier_clamp` with `floor > ceiling` yields ASK. That is the concrete fix for D13: today latency exhaustion jumps to
  `remote_frontier` (escalation.py L246-249) even under `local_only`.

**Caller arguments narrow the envelope and never widen it:**
- `allow_remote_eff = args.allow_remote ∧ env.privacy ≠ local_only`;
- `free_only_eff = args.free_only ∨ policy.free_only ∨ env.budgets.usd = 0`.

This closes the `allow_remote` / `'local-only '` text-prefix hole (`intelligence.py` L21-27, L100-102 [SRC]).

**Request class is not a trusted fail-open input.**
- Today `request.risk_class or "read"` fails open into C6/C7 (`cognition/candidates.py` L782, L798, L814
  [SRC, re-verified]).
- Fix: a missing risk class becomes the highest class. If that class exceeds `env.authority`, the result is DENY 119.

**Admission idempotency.**
- `aodl_dispatch.previous()` (L89-104 [SRC]) replays the last durable ALLOW for a `(trace_id, request_sha256)`.
- Fix: put `law_set_id` and `guard_rev` into the idempotency key, or treat a prior ALLOW under a different
  `law_set_id` as absent and re-evaluate it.

**Deny codes** share the gate's numeric space:
- 0 parse; 1-18 doc rules; 101-106 transition (existing);
- new 111-119:

| Code | Meaning |
|---|---|
| 111 | not in E |
| 112 | egress |
| 113 | no validated $0 route |
| 114 | cap zero or unmeasured |
| 115 | quality floor |
| 116 | candidate risk ceiling |
| 117 | tier |
| 118 | integer budget |
| 119 | risk outside authority |

A guard exception counts as DENY.

### 3.4 Process model and proof vs execution

| Where | What runs | Bend involved? | Latency class |
|---|---|---|---|
| Request path (z0int service, Hermes bridge) | Python `eligible_C`, `select.py`, `admit_C`, plus one dict lookup of the verified bundle | **No.** No Bend process and no per-call hashing of Bend artifacts | µs |
| CI `bend-laws` job (per owning repo) | P0 `--verdict`; P1 parity | yes, as a CLI and as a native `-o` batch binary | seconds to minutes |
| On `S.id` change (manifest, contract or catalog load) | P1 exhaustive parity for that snapshot (offline command, e.g. `z0int law verify` [INFERRED name]) | native batch, one process, `--threads N`, quiet lane | seconds to minutes [INFERRED, must be measured] |
| Async (cron or nightly) | P2 replay of sampled `law_record`s | native batch | off path |
| Evolution Lab | P3 envelope replay of deterministic challengers | native batch, only after a measured win | offline |
| Mode X (deferred, owner-gated) | persistent native `admit` sidecar at **remote dispatch only** | yes | about 150-551 µs per call [NOTE: gate README, other host] against provider calls of hundreds of ms or more |

**Proof vs execution.** Proof attaches to the *function*, not to the call. Each step has its own scope:
- The verdict attests the BendTT book emitted from source.
- Translation is unproven (#1212 class; README: "The compiler (not kernel) is 99% AI-written and not yet fully
  audited").
- Parity attests that the executed Python equals the executed native Bend on the enumerated domain.
- Production enforcement is the Python twin.

The runtime guarantee is therefore "proven spec, plus measured equivalence over an enumerated domain, plus a
structural choke-point test". It is **not** a machine-checked end-to-end proof. That stays true until WONTFIX #813 (a
C library, "planned, not scheduled") or a Python target lands. At that point the proven kernel could run in process
and the twin would retire. Keep every kernel shaped as `step(state, event) -> decision` for that day.

**Native batch kernel constraints** (correcting a flaw in perf-first's P1):
- `Array` is a single-owner `Type` (base.bend L70; GUIDE L183-196). It cannot be shared across parallel lanes as
  written.
- The q-array is split by halving its `ANode{xs,ys}` tree into balanced fork-join (GUIDE L147-157).
- `S_core` (about 20 rows) is passed either as `+` Data, costing one atomic per read (GUIDE L166-168), or by
  `Array.clone` per split. Choose by measurement.
- Never use `@unsafe Array.fork`.
- Wire format: fixed-width U32 words, all values below 2^32, with `S_core` sent once per batch. No decimal byte
  scanning, which was most of the gate's 0.9-2.4 ms per fixture.

### 3.5 Receipts (no new ledger)

**Identities.**
- `law_pkg_hash(F)` is the bend-native input-manifest sha256 over LAWS + kernel + PROOF + vendored Core + `main.bend`
  / `batch.bend`. This closes G7/P-12.
- `law_set_id` is the sha256 of the sorted `{F: law_pkg_hash}` plus the qualified `{bend_bin_sha, kernel_sha}`.
  Version strings are never used, because a patched build still prints 2.0.34.
- `guard_rev(F)` is the sha256 of `z0int/law/<F>.py`.

| Receipt | Producer | Binds | Stale when |
|---|---|---|---|
| verdict | bend-native (P0) | `law_pkg_hash`, bin and kernel SHA, `ALL PROOFS CHECK`, `verification_scope: bend-emitted-book` | package, Core or Bend identity changes |
| parity | z0int CI / P1 | `law_pkg_hash`, `guard_rev`, batch-binary sha, `S.id` (P1) or corpus manifest (A: AODL `envelope_fp`s plus seed plus bounds), `\|Q\|` and any **Q restriction**, counts. **False allow must be 0**; false denies are explained; unrepresentable inputs are counted separately | `guard_rev`, `law_pkg_hash` or `S.id` changes |
| drift | P2 replayer, or Mode X | decision receipt id, both verdicts, `law_set_id` | terminal; triggers deoptimization |
| decision / admission / cognition (existing v1 schemas) | runtime | `extra`: `law_set_id`, `law_family`, `law_codes`, `envelope_fp`, `binding{artifact_id, spec_hash, combinator_id}`, `snapshot_id`, `law_record` (integer input tuple for exact replay), `verdict_receipt_sha256`, `parity_receipt_sha256`, `legality_verified` | — |
| `candidate_artifact.v1` | evolution-lab export, z0int import | `envelope{family, combinator_id, genome_schema_sha, spec_hash, trained_on_tables}` | import refuses an unknown `combinator_id` or a stale `spec_hash` |
| `training_table_manifest.v0` | z0int `loop_export` | `legality_spec_hash`, `python_impl_rev` | tables from different specs are never pooled |

The receipt hash fields are **constants looked up by `S.id` / `law_set_id`** when a snapshot loads. They are never
hashed per decision (perf-first graft).

**Readiness.** `/readyz` for the governed and remote paths requires current verdict and parity receipts for the live
`law_set_id` and `guard_rev`s. When they are stale:
- the remote governed path is unready;
- in-process cognition deoptimizes to the deterministic gate or ABSTAIN;
- promotions freeze.

---

## 4. How AODL enforceability carries down

**Principle.** AODL states the legal envelope. Host policy may only narrow it (C12). Availability may only narrow it
(C11). Choosers pick inside it. Every effect re-checks it.

### 4.1 Surface table

| Surface (z0-decisions id) | Law (family: claims) | Who decides inside the legal set | Enforcement point | Fail mode |
|---|---|---|---|---|
| Which contract applies (G3) | A: host-pinned; caller cannot supply | nobody (host config) | `aodl_dispatch.ensure` loads by id | DENY `contract-not-pinned`, no dispatch row |
| Spawn admission (D7) | A: 8 gate laws; **exact integer** budget (C9 shape) | AO planner | `aodl_dispatch.ensure` → master `decide_spawn` | DENY 101-106; no child |
| Γ budget / spend (D9) | C9: `seen_d + spend_d ≤ limit_d`, exact Nat, antitone spend | the chooser proposes spend | `admit_C` at every model contact; `decide_spawn` | DENY 118 → FALLBACK; never a clamped spend (G1, `AodlSpend.plus` L258-266) |
| Capability route `/v1/intelligence` (D1) | C1, C3, C4, C6, C7, C12 | regex category, tiny-task rule, rank | `intelligence.execute`, after `route` | `PARENT_ONLY` (FALLBACK_C) |
| Worker routing / explicit plan (D2/D3) | C1, C3 (free ⇒ validated $0), C4 (`local_only` ⇒ egress ∈ {loopback, host}), C5 (cap > 0 ∧ measured), C11 | `plan_route` order, category | `dispatch_authority.claim` → `ensure` (A ∧ C on the request's provider/model) | DENY 111-114; no paid spill (#20 "fail closed before paid spill") |
| **Governed worker provider (D8)** | C: provider ∈ `plan.bindings` ∩ E_now (replaces the `Z0INT_GOVERNED_PROVIDER` env pick, governed_worker L155) | the binding's artifact (scores) | same claim | DENY → no dispatch; never fall back to an env default |
| Cognition model choice (`CandidateRungBackend`) | C1-C12 per attempt; `masked_argmax` law (`pick_legal`) | Jev, SLM, `rank_candidates`, q_route (wrapped to emit scores) | `select.py`, then `admit_C` before `backend.decide` (candidates.py ~L834) | ABSTAIN; out-of-set answer recorded as an illegal call |
| Escalation tier (D13) | D: floor monotone in risk/novelty; C8 `tier ≤ max_tier`; **C13 `local_only` ⇒ never `remote_frontier`** | confidence, margin and entropy thresholds (integer permille) | `tier_clamp` in `CognitionCascade` before each tier | `floor > ceiling` ⇒ ASK the user, never a silent remote call |
| Kerdoios placement (D11) | C1 re-check: placement ∈ E_now (never widens) | Kerdoios Pareto, quota, co-residency | `admit_C` after `kerdoios_plan` returns | DENY → FALLBACK; placement discarded |
| OMP bridge turn | C1/C4 re-check on `placements[0]` provider/model (bridge/runtime.py ~L381-396 [SRC]) | Kerdoios | guard **before** the placement is returned to the out-of-process harness | the turn runs on the local fallback or ABSTAINs |
| Legal-action compile (D12) | B: `legal ⊆ declared`; `risk ∈ authority`; authority-monotone; ABSTAIN legal | shortcut rules, SLM tool choice | `compile_actions` output consumed by cognition; `requested` capabilities derived from it (replaces constant `["execute"]`, governed_worker L251) | action dropped; ABSTAIN |
| ACT / opportunity gate (D14, #55) | B': `ACT ∈ legal ⇔ blocking = ∅ ∧ contested = ∅ ∧ effects ⊆ grants`; grant monotone; ABSTAIN always legal; `threshold_gate` law | learned gate probability `p` against τ (el#24) | `threshold_gate` → `deterministic_gate` / `with_authority_grant` | first legal of ASK > OBSERVE > ABSTAIN; confidence is never permission (#55) |
| Memory inject / egress (D18/D19) | E: `local_only ⇒ endpoint ∈ {loopback, host}`; visibility is ancestor-prefix | memory ranking | `memory/seam.py` inject | no injection |
| Promotion (D15-D17, #56) | P: only `credited → promoted`; demoted ⇒ not executed; promotion requires current receipts and 0 shadow envelope exits | Evolution Lab evidence, z0evals judge, owner | #56 promotion code; artifact import | stays `candidate`; invalidator ⇒ deoptimize |
| Evolution Lab genome | combinator laws hold ∀ genome, so a genome that names an action, edits legality or touches the fallback is unrepresentable | mutation operators | `genome_schema` from `combinator_id`; z0int `artifacts.validate_manifest` | import refused |
| Health, quota, caps, posture (D4/D5/D10) | none in Bend; C11 (only narrow) | Kerdoios / ledger | snapshot inputs `cap`, `measured` | cap 0 or unmeasured ⇒ ineligible (C5) |

### 4.2 Family C laws (draft for owner review; LAWS.bend is the spec of record)

Notation: `A = admit_C(env,snap,req,dec) = Allow`; `c = dec.candidate ≠ NONE`; `E = eligible_C(env,snap,req)`;
`E_now = E ∩ avail`.

| # | Law | Carry-down source |
|---|---|---|
| C1 | `A ∧ c ⇒ c ∈ E_now` (membership in an independently computed set) | #20 hard rule; `pick_legal` |
| C2 | E ⊆ snap; deterministic order; no duplicates | filter_candidates docstring |
| C3 | `(usd = 0 ∨ free_only_eff) ∧ c ∈ E ⇒ c.validated_free` | **AODL Γ usd = 0** |
| C4 | `privacy = local_only ∧ c ∈ E ⇒ c.egress ∈ {loopback, host}` | **AODL privacy** |
| C5 | `c ∈ E ⇒ cap > 0 ∧ measured` | snapshot |
| C6 | quality ≥ required ∧ max_risk ≥ req.risk | request class |
| C7 | `A ∧ c ⇒ req.risk ∈ env.authority` | **AODL authorityCeiling** |
| C8 | `A ∧ c ⇒ req.floor_tier ≤ dec.tier ≤ env.max_tier` | **AODL `policies.route`** |
| C9 | exact-Nat budget per declared dimension; antitone | **AODL Γ budgets** |
| C10 | `premium_tokens = 0 ⇒ no metered_premium` | **AODL Γ** |
| C11 | availability only narrows: `E_now ⊆ E` | perf-first static/dynamic split |
| C12 | antitone in the envelope: a stricter envelope never widens E | AODL narrows host defaults |
| C13 | `local_only ⇒ tier ≠ remote_frontier` | closes D13 |
| M1 (meta) | `admit_C(…, NONE) = Allow` | #48 rule 4; ABSTAIN legal |
| M2 (meta) | `admit_C(…, head(E)) = Allow` when E ≠ ∅ (non-vacuity) | — |

The seed proof for C1 is `scratch/evo-probe/v2` (`pick_legal`). On stock 2.0.34 it gives ALL PROOFS CHECK, rc 0, and
mutant m1 fails at `choose_ok` with rc 1. Two judges reproduced this independently (scratch/judge-bend,
scratch/judge-verify). It **must be re-verified on the qualified build** before any receipt counts.

### 4.3 Owner decisions this carry-down needs

1. Type `constraints.privacy` (`public | confidential | local_only`) in the AODL schema. Today `constraints` requires
   only budgets and termination (`416736c`); HOTL spec L69 names privacy as part of Γ.
2. Integer budget units in the canon.
3. `envelope_fp` excludes `plan`, `eventLog` and `observedGraph` (aodl#39 / G6).
4. Does `local_only` admit `tailnet` `host_local_providers` (worker_routing L193-195)? Fail-closed default: no.
5. Should `usd = 0 ⇒ free-only` (C3) and `premium_tokens = 0 ⇒ no premium` (C10) become canon semantics?
6. The absent-privacy default (`local_only`) will deny today's remote governed canary until contracts declare
   privacy. Choose: declare privacy in the canary contract before the enforcement flip, or accept the outage during
   shadow.

---

## 5. What stays in Python, and why

| Stays in Python | Why |
|---|---|
| All hot-path enforcement (`eligible_C`, `select.py`, `admit_C`) | 5-7 µs in Python vs ≥ 70 µs IPC plus an encode ≥ the work, per call ([MEAS] perf-first `scratch/perf-first/legality_cost.json`; gate README). A sidecar crash would also become a request-path availability risk |
| AODL validation, canonicalization, fingerprint, `envelope()` | AODL owns them (aodl#19). Bend strings are linked lists |
| Regex task classification, request-class derivation | text; and the fail-closed default of §3.3 |
| Ranking, float scores, confidence, entropy, latency estimates | F32 is axiomatic. Only host-discretized integer thresholds enter laws |
| Health, backoff, quota windows, in-flight caps, posture | dynamic and ledger-based; covered by C11 only |
| Kerdoios placement, dispatch claims, leases, receipts, flock, fsync, credentials, network | effects (#47); Bend has none of this and should not |
| Choke-point completeness | a structural property of the host. One test enumerates every provider and backend client call site |
| Learned models, genome mutation, Pareto search | Evolution Lab; #48 non-goals "no Bend-based LLM router", "no provider/quota logic in Bend", "no automatic policy synthesis" |
| Outcome-based promotion metrics | data, not laws ("cascade success ≥ baseline − margin") |

---

## 6. Performance expectations

**Perf triage status at 12:24.** `/mnt/zer0models/z0-wt/wiring/bend-perf/results/modes-r1.json` and `.err` are
**0 bytes** (mtime 11:53). `bench_modes.py` is still queued under `quiet-timed` (pid 953991, blocked on `flock -x`).
None of the triage rows exists yet. What is established:

- **[SRC/trace] "4-25x slower" was not a misconfiguration** (`bend-perf/trace.md`). The gate was already a native `-o`
  binary on a persistent pipe with `--threads 1`, compared against in-process CPython.
  - The frozen README table supports about 6x (docs) and 23-25x (spawn). The "4" matches no row.
  - The cost is structural: an IPC floor of about 70 µs; a host encode of 97 µs vs 90 µs for the validator; a serial
    linked-list, per-byte kernel; nothing to parallelize per decision.
- **[MEAS, indicative, shared host] Python legality:**
  - `filter_candidates`: 4.9 / 28.5 / 216 µs at n = 8 / 64 / 512;
  - `plan_route`: 7.1 µs;
  - real sizes: 10 providers, 7 local models, 2 validated $0 routes.

  This is the quantitative case against per-request Bend.
- **Real hot-path costs are not Bend-addressable** [SRC, cost INFERRED]:
  - the per-call `routing_snapshot()` rebuild (re-reads and re-hashes `capabilities.v1.json`);
  - five O(ledger) scans under flock;
  - the Kerdoios subprocess with up to a 12 s timeout.

  Fix them in Python, outside this RFC.
- **[DOC] Where Bend can win:** large, pure, integer, balanced batches in one native process. 16-thread scaling is
  8.8-12.1x uniform (BendRT paper). Pure compute is about 27x CPython on a tree sum (`bend-docs.md` §7.3, indicative).
  - The JS lane is sequential (GUIDE L170).
  - Checker normalization is a trap: 64 s for 2^14 leaves (GUIDE L559-560).
- **`--verdict` cost:** about 140 ms warm on a toy project and 22.6 s cold per Hermes session (temp HOME). The shared
  SHA-pinned kernel removes the cold cost.

**Must be measured before sizing** (each under the quiet lane; none changes the architecture):

| Measurement | Decides |
|---|---|
| triage `batch_native_t1.per_decision_us` and `_scan_only` | P2 sampling rate; whether the U32 rewrite is mandatory before any batch use |
| triage `batch_native_default_threads` vs `_t1` | expected ≈ 1x for the current gate; confirms the balanced split is needed |
| triage `pipe_roundtrip_us` vs `adapter_roundtrip_us` (p50/p95) | Mode X affordability; whether `BendGate._read_line`'s per-call thread (bend_gate.py L619-632) is the cost |
| \|Q(S)\| on the real snapshot | P1 corpus size. perf-first estimated 10^6-10^7 classes; a judge re-estimated about 9×10^7 [INFERRED]. Mitigation: enumerate **per candidate row × the request-class fields that row's gates read, at the row's boundary values** (law-first single-row style). Restrict Q to classes the validator can emit, and record the restriction in the parity receipt |
| P1 wall time, Python vs native batch, on that Q | whether P1 runs per `S` change or only in CI |
| `--verdict` warm time for the real family-C package | CI budget |
| P3 Bend batch vs Python/NumPy over the real el table | whether P3 exists at all |

---

## 7. Ownership per repo (registry `components.yaml` @ `7933914`)

| Repo | Owns in this seam |
|---|---|
| **kvnloo/aodl** | `envelope()` projection; `envelope_fp` (excludes plan/eventLog/observedGraph); integer units; typed `constraints.privacy`; `proof/Core.bend` + family-A LAWS/PROOF; moving the gate's Core encoder out of z0int |
| **kvnloo/z0intelligence** | `z0int/law/*` guards; `cognition/select.py`; snapshot assembly + `egress_class`; choke-point wiring + completeness test; `bend/<family>/` packages B/C/D/E/P (LAWS owner-reviewed); P1/P2 commands; verified index; receipt `extra`; `/readyz`; #56 gates; `artifacts.validate_manifest` envelope checks |
| **kvnloo/kerdoios** | placement, quota model and Pareto **inside E_now**. No change except receiving E_now as an input. Never widens the set (re-checked) |
| **kvnloo/evolution-lab** | `candidate_artifact.v1` envelope block; `genome_schema` per combinator; wrapping q_route `RouterModel.predict` (distill.py L69-74, an unmasked argmax) so it emits scores only; P3 (only after a measured win) |
| **kvnloo/z0evals** | names the frozen corpora for parity and the judge; never proves |
| **kvnloo/bend-native** (+ hermes fork #324/#388/#389/#390) | verify wrapper, pinned kernel, verdict receipts, offline replay, per-release qualification by bin + kernel SHA |
| **bendlang/bend** upstream (kvnloo/bend patch fork only until 2.0.35 qualifies) | toolchain. Registry stays `experimental` until M4 evidence exists |
| **kvnloo/z0** | registry status change for `bend` |

---

## 8. Migration (each milestone starts with a red test; no default behaviour changes until its exit check passes)

| M | Work | Owner | Red test first | Exit check |
|---|---|---|---|---|
| **M0 trust root** | Run the B389 corpus on 2.0.35 (bin + kernel SHA), or pin 17db447a. Share the SHA-pinned kernel across CI and Hermes | bend-native / hermes #389, #388 | B389 corpus row for #1212 fails on stock 2.0.34 | corpus passes on the chosen identity. **No verdict receipt from 2.0.32-2.0.34 counts** |
| **M1 envelope integrity** | G3: load the contract by id host-side. G4: retire v2 model contact or mark it `ungoverned` (never promotable). G1: exact integers in `decide_spawn` (drop `+1e-12`, aodl_admission L325); fix the `AodlSpend.plus` clamp before `check_budget` gets a caller | z0int (#13, #47) | (a) a self-supplied permissive contract is denied; (b) 2^53 and 2^60 budget probes (`scratch/probe/probe_budget.py`) DENY; (c) a negative proposal cannot lower spend | all three green on master |
| **M1b fail-open inputs** | Missing `risk_class` ⇒ highest class (candidates.py L782/L798/L814). `law_set_id` + `guard_rev` in the admission idempotency key | z0int | a request without `risk_class` is not treated as `read`; a prior ALLOW is not replayed after `law_set_id` changes | green |
| **M2 tie family A to production** | Re-point parity from `reference_transition` to master `decide_spawn` (G2). Native batch parity in a CI `bend-laws` job. `main.bend` in the closure (G7) | z0int | parity harness imports master `decide_spawn`; the out-of-range/fractional corpus counts as unrepresentable | 0 false allows; verdict + parity receipts on the M0 identity |
| **M3 AODL side** | typed `constraints.privacy`; integer units; `envelope()`; `envelope_fp` excluding plan/eventLog/observedGraph; `proof/Core.bend` | aodl (owner decisions §4.3) | the fixture corpus yields Envelopes; a `plan.bindings` rewrite leaves `envelope_fp` unchanged | z0int consumes Envelopes with unchanged family-A parity |
| **M4 z0int guards, shadow first** | `z0int/law/{eligible_C, admit_C}`; `filter_candidates` / `plan_route_static` delegate; `∩ available` split; `egress_class`; `select.py`; Envelope threaded into `routing_snapshot`, `CandidateRungBackend`, `EscalationPolicy`, `governed_worker`, memory, bridge; caller args narrow only; D8 provider from `plan.bindings ∩ E_now`; `requested` from D12 | z0int | property tests for C1-C13 + M1/M2 in Python; choke-point completeness test (every provider/backend client call site passes through a guard); `local_only` + latency exhaustion ⇒ ASK, not `remote_frontier` | shadow period logs would-deny verdicts; owner reviews the set (expected: remote_frontier under local_only, env-picked non-free providers, the remote canary if privacy is undeclared); enforcement flips per choke point |
| **M5 family-C Bend package** | `bend/model_eligibility/` (LAWS C1-C13 owner-reviewed, seeded from `evo-probe/v2`); mutation suite (including the probe-v1 laundering case and an m1-style unmasked argmax); P1 per-row exhaustive parity in native batch; receipts bound into decision `extra`; `/readyz` gate | z0int | each mutant must FAIL `--verdict`; a seeded Python bug must break P1 parity | ALL PROOFS CHECK on the M0 identity; P1 0 false allows; `/readyz` turns unready when a receipt is stale |
| **M6 evolution coupling** | `candidate_artifact.v1` envelope block; import refuses unknown combinators or a stale `spec_hash`; q_route wrapper; `legality_spec_hash` in the training manifest; #56 stages 7-8 invalidators wired to `observe_future` demotion | evolution-lab + z0int | an artifact without an envelope stays `candidate`; a `spec_hash` bump demotes (z0evals#74 drill) | drill passes |
| **M7 families B, then D** | `_action_space` / `compile_actions` (discharges the fallback premise); escalation floor/ceiling + `tier_clamp`. **Defer E and P** until C/B/D show value (§9) | z0int | as M5 | as M5 |
| **M8 P2 replay** | sampled `law_record` batch replay; drift ⇒ deoptimize + freeze | z0int | an injected Python/Bend divergence produces a drift receipt and a demotion | green |
| **M9 (owner-gated)** | Mode X at remote dispatch only, after the triage numbers. On mismatch the canonical verdict stands, plus a drift receipt and a promotion freeze (#48 rule 4), **unless** the owner amends #48 rule 4 to make it conjunctive DENY. P3 in Evolution Lab only after a quiet-timed win | owner | — | — |
| **M∞** | Watch WONTFIX #813 / a Python target. If either lands, re-measure in-process cost; the proven kernel may then replace the twin | — | — | — |

Sequencing: M4-M7 can be *built* in parallel with M0-M3, but **no receipt counts** until M0 (qualified trust root),
M1 (an unforgeable envelope) and M3 (proven domain = runtime domain) hold.

---

## 9. Falsification criteria (any one triggers a re-scope)

1. **Laws add nothing.** Track the mutation suite and real bugs per family. Suppose that after M5/M7 a family's Bend
   package catches nothing that Python property tests plus parity would miss, and costs more to maintain. Then keep
   its LAWS.bend as documentation only and drop that family's verdict gate. This is the #56 rule "simpler mechanisms
   remain mandatory controls" applied to Bend, and it matches the aodl#19 kill criterion.
2. **Parity is unaffordable.** If |Q(S)| cannot be enumerated per row × class at a cost the owner accepts, and
   bounded-exhaustive plus fuzz misses a seeded bug, the "carries down" claim is too weak. Re-scope to promotion-only
   evidence and say so in receipts.
3. **Bypass found.** If any model contact path is found without a guard after M4, the completeness test is wrong.
   Stop the rollout until it is fixed.
4. **Translation drift.** If a qualified release later shows source↔BendTT drift that affects a shipped law package,
   the verdicts for that identity are void. Re-verify on a new trust root, and freeze promotions meanwhile.
5. **Snapshot errors dominate.** If incidents trace mainly to mis-set `validated_free` / `egress_class` manifests, not
   to choosers, the guard is "faithfully wrong". Shift investment to manifest evidence and owner review of diffs.
6. **Perf surprise.** If the triage shows batch native is not faster than Python for P1/P2, drop native execution and
   keep P0 verdicts plus Python-only property and exhaustive tests. The architecture does not change, because nothing
   on the request path depends on Bend speed.
7. **Availability cost.** If shadow shows the fail-closed defaults (`local_only`, highest risk) deny a large share of
   legitimate work, and AODL contracts cannot declare the fields promptly, reopen the defaults with the owner. Never
   silently loosen them.

---

## 10. Issues this RFC updates

| Issue | Update |
|---|---|
| **z0int #48** (Bend proof kernel for the AODL gate) | Outcome: Bend does **not** become a hot structural filter (promotion rule 2 fails at the boundary: 5-7 µs Python vs ≥ 70 µs IPC + encode). Bend becomes the law layer, with proof (P0) plus exhaustive parity (P1) plus replay (P2). Family A moves to aodl `proof/`. Parity is re-pointed to master `decide_spawn`. Mode X stays optional and keeps rule 4 (canonical stands on mismatch) unless amended. Non-goals unchanged |
| **z0int #13** (AODL synchronous structural gate) | Add: host loads the contract by id (G3); `envelope()` consumed at every choke point; `law_set_id` / `envelope_fp` recorded alongside wire version and contract revision; drift receipts come from P2 / Mode X |
| **z0int #47** (dispatch leases, enforceable chain) | The chain becomes: AODL envelope → `eligible_C` → choosers → `admit_C` → Kerdoios → `admit_C` re-check → dispatch claim (A ∧ C) → harness. Add the choke-point completeness test, v2 retirement (G4), the D8 provider from `plan.bindings ∩ E_now`, the OMP bridge guard, and idempotency keyed on `law_set_id` |
| **z0int #55** (selective autonomy) | ACT legality comes from family B' and `threshold_gate`. A learned gate can only withhold ACT, never grant it. Confidence is never permission (now a law, not prose) |
| **z0int #56** (validity, drift, deoptimization, promotion) | Invalidators: change of `law_pkg_hash`, `guard_rev`, Bend identity, `envelope_fp` or `S.id` without receipts. Promotion past shadow requires current receipts and 0 shadow envelope exits. Genome = combinator free parameters only; the canonical-state rule for legality compilers |
| **hermes #324** (Bend proof adapter, minimal slice) | Scope of the verifier in this seam: `--verdict` only, exact `ALL PROOFS CHECK` + rc 0, env stripped, no `--publish`, PASS never surfaced as task success. Receipts carry `law_pkg_hash` including `main.bend` / `batch.bend` |
| hermes #388 / #389 / #390 (related) | #388: a shared SHA-pinned kernel (removes the 22.6 s cold start). #389: M0 is a hard precondition for every receipt. #390: offline, no Hub |

---

## 11. Residual risks

- **The snapshot is trusted** (manifests, `validated_free`, `egress_class`). Evidence hashes go in receipts, and the
  owner reviews manifest diffs.
- **The two twins share a spec and an encoder.** Parity tests implementation equality, not spec correctness.
  Owner-reviewed laws plus the mutation suite address the spec side. In Mode X, independence is overstated: the
  native binary comes from the unverified `comp.ts` path [INFERRED].
- **Release churn:** 36 releases in 16 days (2.0.0 on 09-17 to 2.0.35 on 10-03). Per-release qualification is
  mandatory, and there is no "latest is best".
- **Choke-point completeness is tested, not proven.**
- **Perf triage is unmeasured** (§6). Sizing of P1/P2/Mode X is pending.

---

## 12. Hard-rule breach

None.
- The only write is this file, `/mnt/zer0models/z0-wt/wiring/bend-seam/RFC-bend-z0-seam.md`.
- All other access was read-only: the three designs, `bend-perf/trace.md`, the results directory, `ps`, issue JSON
  dumps in `scratch/issues/`, and greps of `scratch/master` (candidates.py L782/L798/L814,
  `aodl_dispatch.previous` L89-104, bridge/runtime.py placements[0]).
- No Bend runs or builds; no git, GitHub or upstream writes.
