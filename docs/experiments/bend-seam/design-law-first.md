# Law-first seam: Bend as the single proven law layer for z0intelligence

Date: 2026-10-03. Design angle (assigned): **enforceability-first**. Bend is the one proven law layer, holding the AODL
canon compiled to proven predicates. Every z0 decision surface must pass it, and the design minimises what has to be
trusted.

Owner request: "carefully read thru all of the bend docs and analyze the optimal architecture seam for z0intelligence.
we want to use it in the RIGHT way. it has a perfect integration w/ aodl for enforceability and it needs to carry down
into z0intelligence for other parts like which model to use, etc".

This builds on the three lane notes in this directory (`bend-docs.md`, `aodl-bend.md`, `z0-decisions.md`). I re-read
the load-bearing code and docs myself; the pins are in §0. Labels:
- **[SRC]**: read in code or docs at the pin given.
- **[NOTE]**: taken from a sibling lane note and not re-verified here.
- **[INFERRED]**: my design reasoning. It is neither documented nor measured.

---

## 0. Pins and status of evidence

| Source | Pin | What I re-read for this doc |
|---|---|---|
| bendlang/bend | `src/bend` @ `a950fd68`. `v2.0.35` = `79df8d9c` (2026-10-03), which contains the #1212 fix `e1ed2435`. I re-ran `merge-base --is-ancestor`: true | GUIDE.md "Laws and Proofs" L279-348 and "Modules" L501-541 |
| Installed CLI | `/mnt/zer0models/bend-stack/official/bend/bin/bend` = 2.0.34 (stock; mis-certifies #1212) | [NOTE] bend-docs §8 |
| kvnloo/aodl | `/mnt/zer0models/z0-wt/ro/aodl` main `416736c` | `schema/hotl-0.2.schema.json`: `constraints` requires only `budgets` and `termination`, and has **no typed privacy field**. Node `authorityCeiling` is `string[]`. Port `classification` is a string. Also `spec/hotl-0.2.md` L69 (Γ includes "capability/privacy constraints") and `aodl_contract/validator.py` (`PRIVILEGED` L84, ceiling check L526-534). Issue #19 body and owner comments of 2026-09-20 and 2026-09-29 |
| z0intelligence | origin/master `0159808` (extract in `scratch/master/`) | `aodl.py` L232-241 (`_stage_binding` leaves provider/model "unbound"), L250-268 (`AodlSpend` floats; `plus` clamps at 0), L726-807 (`runtime_contract`, `check_budget`, `binding_for_implementation_stage`). `aodl_admission.py` L72-82 (`_number` → float), L317-327 (float budget + 1e-12). `worker_routing.py` L137-153 (free route), L189-221 (`plan_route`; the `local` category includes `host_local_providers`). `cognition/candidates.py` L201-240 (`CandidateModel`: no egress field), L291-360 (`filter_candidates`), L774-860 (`CandidateRungBackend.decide`). `cognition/actions.py` L298-317 (`compile_actions`, integer `budget_units`). `cognition/escalation.py` L160-164 and L248-249 (`remote_frontier` on latency exhaustion). `intelligence.py` L21-27 and L83/L100-102 (**`allow_remote` and `free_only` are caller arguments**). `dispatch_authority.py` L118-126 and L222 (`require_aodl = version==3`). `governed_worker.py` L154, L221, L251 (`requested: ["execute"]`) |
| exp/bend-aodl-gate | `/mnt/zer0models/z0-wt/bend-aodl-gate` @ `a0e9578` | `bend/aodl_gate/LAWS.bend` L1-80 (law shape and scope comment) |
| kvnloo/z0 registry | `/mnt/zer0models/z0-wt/ro/z0` `7933914` | `components.yaml` boundaries: z0intelligence owns "personal policy … bounded decision backends, routine promotion"; kerdoios owns "provider routing, allocation policy"; aodl owns "typed intent/plan contracts", not "runtime implementation"; evolution-lab owns "experiment search … promotion evidence", not "production runtime". `suite.yaml` L315-325: `bend` = `verified_compilation_substrate` / `external_substrate` / `experimental` |
| kvnloo/kerdoios | `cdb43b0` | `kerdoios/aodl.py` `_PRIVACY` (`public|confidential|local_only`). README "Local cognition placement": "It does **not** decide semantic suitability" |
| kvnloo/bend-native | `e85e65e` | `stack/STACK.md` rows "Bend proof evidence" and "AODL/Bend gate" |
| Issues | `scratch/issues/` | z0int #48 body (promotion rule 1-4; non-goals). #47 "golden governed call". #56 (invalidators, mandatory deoptimization). #20 ("Free-only mode must fail closed before paid spill"). aodl #19 |
| Perf triage | `/mnt/zer0models/z0-wt/wiring/bend-perf/results/modes-r1.json` | **Still 0 bytes at 12:05.** `bench_modes.py` was still queued on `quiet-timed` (pid 953991). Nothing here depends on its numbers. Mode X (§3.6) is gated on them |

---

## 1. The answer in one screen

1. **Put Bend at the law layer, not in the decision path.** For each decision family, Bend holds one small pure
   function, `admit(envelope, snapshot, request, decision) -> Verdict`, which is a *reference monitor*, together with
   the human-written `LAWS.bend` that `admit` is proven to enforce. The laws are theorems about `admit`. They are not
   theorems about whatever heuristic produced `decision`.
2. **AODL carries down as an `Envelope`.** This is an integer/enum projection of the *validated, host-pinned* AODL
   contract (Γ budgets, authority ceiling, privacy, revision). AODL produces it: `aodl_contract` owns it, and Bend
   never normalizes. Each z0 decision surface receives it as one more immutable snapshot field.
3. **"Which model" gets a guard, not a router.** Selection logic stays untrusted for safety:
   - regex categories, `rank_candidates`, Jev/SLM output, Kerdoios placement, `plan.bindings` selectors and Evolution
     Lab thresholds are not trusted;
   - before any model is contacted, the chosen `(provider, model, tier, spend)` must pass `admit_C` (model eligibility);
   - laws C1-C12 (§5.3) make the carry-down explicit. Examples: AODL `budgets.usd == 0` ⇒ only validated $0 routes;
     `privacy = local_only` ⇒ no off-host egress; `authorityCeiling` ⇒ risk class.
4. **Python enforces on the hot path. Bend proves the spec and checks the implementation off-path.** The Python `admit`
   is the only z0 code that must be trusted for safety. It is tied to the Bend `admit` by:
   - a verdict receipt;
   - bounded-exhaustive and fuzz parity run in Bend's fast mode (native batch);
   - a `law_set_id` stamped on every decision receipt;
   - #56 invalidators that force deoptimization when any of these goes stale.
5. **One choke point per effect.** The guard sits at `dispatch_authority.claim` (through the existing
   admission-before-dispatch-start step) and before every in-process model contact. Callers may *narrow* the envelope
   (`allow_remote=false`, `free_only=true`) but never widen it.
6. **Bend's runtime is used where it is strong.** Strong means batch parity, shadow replay and Evolution Lab
   population replay. Per-decision IPC on in-process microsecond paths is where it is weak. A conjunctive native
   cross-check at the remote-dispatch choke point (Mode X, §3.6) is an owner-gated option for later. It is affordable
   there because the guarded effect costs 10^3-10^5 times more than the check [INFERRED, pending triage numbers].

---

## 2. Principle: shrink the trusted base to one predicate per family

Today every Python function that produces a model or action choice is trusted for safety. That includes
`intelligence.route`, `worker_routing.plan_route`, `filter_candidates`, `EscalationPolicy.decide`,
`governed_worker._provider_model` and `compile_actions`. A bug in any of them can contact a paid, remote or
over-privileged model. Each also gets its rule from a different source (§0: caller args, config, env, constants), and
**none of them from AODL** except spawn admission (z0-decisions §1.1, [SRC] confirmed: `_stage_binding` "unbound",
`check_budget`/`binding_for_implementation_stage` have no production caller).

The law-first design separates **choosing** from **permitting**:

```
            untrusted for safety (trusted only for quality)          trusted for safety (small, proven spec)
 ┌───────────────────────────────────────────────────────────┐   ┌──────────────────────────────────────────┐
 │ regex category, rank, Jev, SLM output, Kerdoios placement,│   │ admit_F(envelope, snapshot, req, decision)│
 │ plan.bindings selectors, Evolution Lab thresholds, posture│──▶│  Python on hot path  ≡(parity)≡  Bend      │
 └───────────────────────────────────────────────────────────┘   │  LAWS_F proven about the Bend admit_F     │
                                                                 └──────────────────────────────────────────┘
```

This is the doctrine already written down, given teeth:
- "Mechanism defines what is valid. Policy chooses among valid actions" (aodl `profiles/searchable-policy-kernel.md`);
- "A learned policy selects among legal actions; it does not define legality" (same file, invariant 4);
- #20 hard rule: "Rejecting an action here is authoritative; no backend can re-admit it" (`cognition/actions.py`).

**Trusted computing base for a correct ALLOW, before and after [INFERRED]:**

| Component | Today | Law-first steady state (Mode P) |
|---|---|---|
| AODL `validate()` + canon + fingerprint | trusted (AODL-owned) | trusted (AODL-owned), plus `envelope()` projection, also AODL-owned and tested there |
| Which contract applies | **caller-supplied in v3 (G3)** | host-pinned allowlist of fingerprints; caller cannot supply |
| Snapshot assembly (manifests, evidence hashes, ledger-derived caps) | trusted | trusted (class B). Content-hashed into every receipt so it can be replayed |
| Route/rank/learned/Kerdoios/heuristics | **trusted for safety** | **untrusted**: output must pass `admit` |
| `admit_F` Python | n/a | trusted. Small; parity-tied to Bend `admit_F` |
| Choke-point completeness ("every model contact calls admit") | n/a | trusted. Structural test plus #47 single authority. Bend cannot prove this (§10) |
| Bend checker + `safe.ts` translation + `.bendtt` parser | n/a | trusted for *law validity*, qualified per release by the #389 corpus by binary/kernel SHA. The Lean kernel itself is proven |
| Bend C compiler/runtime | n/a | trusted only for parity runs and the shadow. In Mode X it becomes an extra *conjunct*, so a false allow needs Python **and** Bend to be wrong |

---

## 3. The seams, exactly

The architecture has six seams. S1-S5 are the steady state. S6 is optional and owner-gated.

```
 AODL repo (contracts)                     z0intelligence (runtime + its laws)                bend-native (verifier)
 ─────────────────────                     ──────────────────────────────────                ─────────────────────
 validate() ─ canonicalize ─ fingerprint
        │
 S1  envelope(doc) -> Envelope (ints/enums) ─────────┐
 proof/Core.bend  (Envelope type, A-laws) ───┐       │
                                             │ vendored by SHA
                                             ▼       ▼
                         S2  bend/<family>/{LAWS,admit,PROOF,main}.bend ──CI──▶ verify --verdict ─▶ verdict receipt
                                             │                                   (qualified bin+kernel SHA)
                                             │ native batch binary
                                             ▼
                         S5  parity: z0int.law.admit_F (prod import) vs native admit_F ─▶ parity receipt
                                             │
                 hot path ───────────────────┼──────────────────────────────────────────────────────────
  request ─▶ snapshot(+Envelope) ─▶ untrusted chooser ─▶ S3 z0int.law.admit_F (Python) ─┬─ DENY ─▶ canonical fallback
                                                                                         └─ ALLOW ─▶ S4 receipt
                                                                                                   ─▶ Kerdoios? ─▶ admit again
                                                                                                   ─▶ dispatch_authority.claim ─▶ call
                 off path ──────────────────────────────────────────────────────────────────────────────
  decision receipts (law records) ─▶ S5' native batch replay ─▶ drift receipt ─▶ #56 deoptimize
  [S6, gated] dispatch choke point: Python admit ∧ native admit sidecar
```

### 3.1 S1: AODL → Envelope (owned by AODL)

- **Interface [INFERRED proposal]:** `aodl_contract.envelope(doc, *, node_id) -> Envelope`. It takes a doc that has
  already passed `validate()` and `canonicalize()`. It is the Bend-facing projection that aodl#19 and the gate README
  "Gap 1" ask to move out of z0int. Today the name `to_core()` on `feat/aodl-core-conformance-v1` means a JSON identity
  projection, not this ([NOTE] aodl-bend G6). The new function needs its own name or its own profile.
- **Data out:** integers and enums only. They are interned by AODL, not by the z0int host.

  ```
  Envelope {
    fp           : str   # aodl-canon-1 fingerprint; identity only, never enters Bend
    revision     : Nat
    authority    : Nat   # bitmask over risk classes {read, write, destructive, publish, credential, payment},
                         # from the governing node's authorityCeiling (validator PRIVILEGED set)
    budgets      : List<(dim, limit)>  # declared dims only; integer units (see below); missing dim = unbounded
    privacy      : {public | confidential | local_only}   # REQUIRES an AODL schema addition (see below)
    max_tier     : Nat?  # optional escalation ceiling from policies.route (abstract order); absent = no ceiling
    dyn          : {allowed, maxChildren, maxDepth}       # already used by family A
  }
  ```
- **Integer units (AODL owner decision; resolves the float/Nat split G1 in the aodl-bend note):**
  - `usd` becomes micro-USD; `joules` becomes millijoules; `attention` becomes milli-units; `tokens`,
    `premium_tokens` and `latency_ms` stay integers.
  - Until the canon changes, z0int converts **conservatively**: `floor` limits and `ceil` spends. Then
    `ceil(spend) ≤ floor(limit)` implies `spend ≤ limit`, so the conversion is sound [INFERRED].
  - The host range guard stays below 2^47, because the BendRT Nat aborts at 2^48 while the theory is unbounded
    ([NOTE] bend-docs §3).
- **Privacy:**
  - HOTL 0.2 names privacy as part of Γ (`spec/hotl-0.2.md` L69), but the schema has no typed field [SRC].
  - Kerdoios already ingests `privacy: public|confidential|local_only` [SRC].
  - Proposal: type `constraints.privacy` with those three values. This adds no new node kinds, as
    `docs/aodl-integration.md` and #20 require.
  - **Until AODL types it, the envelope carries `privacy = local_only` whenever the field is absent.** This is the
    fail-closed default. It narrows today's behaviour, so the governed remote path needs that field first (migration
    M2).
- **Pinning (prerequisite; fixes G3):**
  - The host, not the request, chooses which contract applies.
  - `aodl_dispatch.ensure` compares the request's fingerprint with a host-pinned allowlist, or loads the contract
    host-side by id.
  - A mismatch gives DENY `contract-not-pinned`.

### 3.2 S2: Bend proof packages (the law layer)

- **Layout [INFERRED]. Law ownership follows semantic ownership:**
  - `kvnloo/aodl: proof/{Core.bend, LAWS.bend, PROOF.bend}`. These hold the AODL Core types including `Envelope`,
    plus the family-A laws about AODL semantics: structural rules, spawn transition, no invented authority. This is
    the layout aodl#19 itself proposes (`proof/Core.bend, Laws.bend, Proof.bend`). It is a *contract* artifact and
    fits AODL's registry boundary "typed intent/plan contracts". The current gate kernel (`bend/aodl_gate`, `a0e9578`)
    migrates here once the encoder moves to `envelope()`/Core.
  - `kvnloo/z0intelligence: bend/<family>/{LAWS.bend, admit.bend, PROOF.bend, main.bend}` for z0's own decision
    families B, C, D and E (§4). Each vendors `aodl/proof/Core.bend` at a pinned SHA. Bend modules import by relative
    path (`import ./x.bend as M`, GUIDE L516-576) or by hub content hash, so vendoring is the only option. **Never
    `--publish`** (GUIDE L527-540: public and permanent).
- **Convention, taken from upstream as is (GUIDE L319-332):**
  - `LAWS.bend` is the owner-reviewed spec of record ("the human writes it, the AI does not touch it").
  - `PROOF.bend` is written by an agent.
  - A law can be proven in another file (`def M.name`), so AODL-owned claims can be proven from z0int if needed.
- **Shape rules [INFERRED, from the gate's findings]:**
  - Every function is `step(state, event) -> decision`-shaped, so it drops into WONTFIX #813 or a future Python target
    unchanged.
  - Laws are stated over `admit`'s **input/output predicate**, not over kernel internals. This avoids the brittleness
    of the gate's README finding 6, where a proof restated the term shape.
  - Domains are finite enums plus bounded `Nat`. No F32 anywhere: F32 is axiomatic, README L230-231.
  - No large closed Nat constants inside `--verdict`. The gate hit a stack overflow on 2^32 (G8c).
- **Two meta-laws every family must carry [INFERRED]:**
  - **Non-vacuity:** a reference chooser `ref_F(env, snap, req)` exists, and `admit_F(env, snap, req, ref_F(...)) =
    Allow` for all inputs. The guard is never "deny everything".
  - **Safe fallback is always admitted:** `admit_F(env, snap, req, FALLBACK_F) = Allow`, where FALLBACK_F is ABSTAIN,
    PARENT_ONLY or "no model contacted". So fail-closed is always a lawful state.
- **Load-bearing check:** a mutation suite in the style of `benchmarks/bend_gate_law_mutations.py`. Every well-typed
  weakening of `admit` must break some law, as the gate's 9 of 9 did.

### 3.3 S3: the runtime guard (owned by z0int, Python, hot path)

- **Interface [INFERRED]:** module `z0int/law/`, one function per family:

  ```python
  admit_C(env: Envelope, snap: CandidateSnapshot, req: EligibilityRequest, dec: ModelDecision) -> LawVerdict
  LawVerdict = {allowed: bool, codes: tuple[int, ...], law_set_id: str, family: "C"}
  ```
- **Pure.** Stdlib only, integers only, no I/O. The production module itself is imported by the parity harness. That
  fixes G2: parity must run against the function that runs, not a sibling reference.
- **Choke points.** One per effect class; this is the completeness property of §2:

  | Effect | Choke point (master code) | Guard |
  |---|---|---|
  | remote provider call through dispatch | `dispatch_authority.claim` L118 → `aodl_dispatch.ensure` (already fsynced before dispatch-start) | family A (`decide_spawn`) **and** family C on the request's `(provider, model)`, which `request_sha256` already covers. One admission receipt, extended codes |
  | `/v1/intelligence` capability route | `intelligence.execute` (after `route` L67-107) | family C on the chosen entry; `PARENT_ONLY` is FALLBACK_C |
  | in-process cognition backends | `CandidateRungBackend.decide` before `backend.decide(request)` (candidates.py L834) | family C per attempt; family B (`compile_actions`) output ⊇ selected action; family D on tier |
  | memory cloud injection | `memory/seam.py` inject (branch `41aec0f`) | family E egress |
  | Kerdoios placement result | after `kerdoios_plan` returns, before contact | family C again: placement must be in the eligible set (never widens) |
- **Caller arguments narrow the envelope and never widen it:**
  - `allow_remote_eff = args.allow_remote ∧ env.privacy ≠ local_only`;
  - `free_only_eff = args.free_only ∨ policy.free_only ∨ env.budgets.usd = 0`.

  Today `allow_remote` and `free_only` are caller arguments ([SRC] `intelligence.py` L21-27, L83, L100-102), and
  egress authority works by prepending "local-only " to the task text. That trick becomes irrelevant to safety: the
  guard reads the envelope.
- **Deny codes [INFERRED proposal; same numeric space as the gate]:** 0 parse; 1-18 doc rules; 101-106 transition;
  **111** not in the eligible set; **112** egress; **113** no validated $0 route under `free_only_eff`; **114** cap
  zero or unmeasured; **115** quality floor; **116** candidate risk ceiling; **117** tier above ceiling or escalation
  non-monotone; **118** integer budget; **119** risk class outside authority. A mode-3 request in `gate.decide`
  becomes the family-C kernel entry.

### 3.4 S4: receipts and binding (no new ledger)

Details are in §7. In short, every decision receipt that a guard touched carries the following in `extra`. This
mirrors how `aodl_admission_receipt_id` is linked today (dispatch-authority.md L52-55 [NOTE]):
- `law_set_id`;
- `law_family`;
- `law_codes`;
- `envelope_fp` (the AODL fingerprint);
- `snapshot_sha256`;
- `law_record`: the integer-encoded `(env, snap-row subset, req, dec)` tuple, so the shadow can replay it exactly.

### 3.5 S5: proof and parity in CI; shadow off-path

- **Process model.** Mode P has **no Bend process in serving**. Bend runs in three places:
  1. **CI `bend-laws` job** in each repo that owns a package:
     - runs bend-native `verify` (`--verdict`) with the qualified binary pinned by SHA, plus `BEND_NO_TELEMETRY=1`;
     - strips `BENDTT`, `BEND_HUB`, `BEND_LIB` and `BEND_ORIGIN`, and stays offline;
     - must match the exact line `ALL PROOFS CHECK` with exit 0, and must **never** use bare `bend PROOF.bend`
       (bend-docs §4, book_run trap);
     - produces the verdict receipt.
  2. **CI parity job:**
     - compiles `admit_F` with `-o` into a native batch binary (one process, many records);
     - runs it over a bounded-exhaustive corpus plus seeded fuzz plus the AODL fixture corpus, named by canon
       fingerprints, against the production Python `admit_F`;
     - produces a parity receipt.
     - This batch run is where Bend is fast: native, no per-call IPC, no thread per call (bend-docs §7.3: about 27x
       CPython on pure compute [NOTE, indicative]).
  3. **Shadow replayer.** Periodic and off-path. It feeds sealed ledger slices of `law_record`s to the same batch
     binary. Any verdict disagreement produces a fail-closed drift receipt (§8).
- **Bounded-exhaustive parity [INFERRED]:**
  - Family C domains are small enums: egress about 4, cost class 4, quality about 5, risk 6, tiers 6, privacy 3, plus
    Bool flags and Nat abstracted to boundary values {0, 1, limit-1, limit, limit+1, 2^47-1}.
  - Every single-row table × request × decision combination is enumerable, around 10^5-10^6 records, which suits
    batch native.
  - On top of that come random multi-row tables.
  - This is much stronger evidence than random fuzz alone, and it is what makes "Python admit ≡ Bend admit" credible
    enough to trust.

### 3.6 S6 (owner-gated): Mode X, a conjunctive native cross-check at the dispatch choke point

- **What it is.** A persistent native `admit` sidecar with a line protocol. It has the shape of `BendGate`, minus the
  per-call reader thread at bend_gate.py L619-632. It runs **only** at the remote-dispatch choke point. The final
  verdict is `python_admit ∧ native_admit`. Bend can only deny additionally, so "canonical AODL semantics remain
  authoritative" holds, which is #48 promotion rule 3.
- **Failure handling.** A timeout or crash leaves the decision to the canonical Python verdict, recorded as
  `cross_check: unavailable`. A disagreement gives DENY plus a drift receipt (#48 rule 4).
- **Why it is affordable there [INFERRED, numbers pending triage].** The gate measured a 150 µs spawn round trip and a
  551 µs document round trip ([NOTE] aodl-bend §5). Both are small next to a remote provider call that takes hundreds
  of ms to seconds and the existing O(ledger) scans under `flock` ([NOTE] z0-decisions §1.3).
- **What it buys.** #48 rule 2 counts a "verification benefit" as material: a false allow would need both
  implementations to be wrong at the same time, and the native side runs a binary compiled from the proven source.
- **Gates:**
  - perf triage `pipe`/`batch` numbers recorded;
  - a qualified Bend build (#389);
  - an owner decision.
- **Not used for:** in-process cognition, memory injection with a 300 ms deadline, or route. There the guard is
  microseconds of Python and IPC would dominate.

---

## 4. Which decisions are governed, by which law family

Families:
- **A**: AODL admission (exists; AODL-owned laws);
- **B**: legal-action compiler;
- **C**: model/provider eligibility;
- **D**: escalation and autonomy;
- **E**: egress and visibility;
- **P**: promotion lifecycle.

Surface ids are those of `z0-decisions.md` §2.

| Surface | Family | Enforcement point | What is proven (Bend) | What stays untrusted |
|---|---|---|---|---|
| D7 spawn admission (`decide_spawn`) | A | `aodl_dispatch.ensure` (exists) | 8 gate laws + structural rules (exist); parity re-pointed to **master** `decide_spawn`, integer arithmetic | — |
| D9 Γ budget + plan bindings | A + C | inside `admit_C` (budget law C9) | exact integer budget; antitone spend | which binding Evolution Lab writes |
| D12 `compile_actions` | B | output consumed by cognition + `requested` capabilities | `legal ⊆ declared`; `risk ∈ authority`; authority-monotone; budget-fits; ABSTAIN legal | shortcut rules' choice among legal |
| D1 capability route | C (+E) | `intelligence.execute` | C1-C12 on chosen entry | regex/heuristics, tiny-task rule |
| D2/D3 worker routing, explicit plan | C | `dispatch_authority.claim` | C3 free, C5 cap, C4 local, C1 membership | category regex, order |
| D8 governed worker provider pick | C | same | as D2; provider comes from `plan.bindings ∩ eligible` (replaces env pick) | binding choice |
| D11 preflight + Kerdoios placement | C | admit after placement | placement ∈ eligible | Kerdoios Pareto/quota choice |
| D13 escalation tier | D | `CognitionCascade` before each tier | tier monotone in risk; `verification ⇒ tier ≥ min`; **`remote_frontier` only if egress allows**, so latency exhaustion under `local_only` yields ABSTAIN, not remote | confidence/margin/entropy thresholds (discretized to integer permille by host) |
| D14 DecisionOpportunity gate | D | `deterministic_gate`, `with_authority_grant` | grant monotone + named user; ABSTAIN always legal; ACT ⇔ no blockers | learned gate (el#24) |
| D19 egress / D18 memory scope | E | memory inject, route, capture | egress lattice: `local_only ⇒ endpoint ∈ {loopback, host}`; visibility is ancestor-only prefix order | ranking of memories |
| D15-D17 routine/cascade promotion | P | registry transition functions | only `credited → promoted`; `demoted ⇒ not executed`; frontier terminal | mining, threshold search |
| D4/D5 caps, quota | — (input to C as snapshot) | snapshot | none: cap 0 or unmeasured feeds C5 | quota model (Kerdoios) |
| D6 dispatch protocol | — | — | not Bend (effects, locks). Optional offline replay spec of the receipt state machine later | — |
| D10 posture | — (input to C) | snapshot | none; posture may only remove candidates, and `admit` makes widening impossible | burn-rate projection |
| D20 AO spawn shadow | (A later) | shadow | low priority | — |

Priority order for new packages: **C** (the owner's "which model" ask, and the biggest unguarded surface), then **B**,
then **D**, then **E**, then **P**. A already exists and needs only the migration of §9 M1.

---

## 5. Model and provider selection: the carry-down in detail

### 5.1 Pipeline

```
AODL doc (pinned) ─validate─canon─▶ Envelope (S1)
plan.bindings[stage].selector  (Evolution Lab writes; z0int compiles; mutable without changing intent)
        │
        ▼ resolve selectors → candidate ids
CandidateSnapshot (z0int; manifests + validated_free_routes + caps + health; sha256 = snapshot id)
        │
        ▼ eligible_C(env, snap, req)   ← the Python twin of the proven Bend function; pure; deterministic order
eligible set + rejections (codes 111-119)
        │
        ▼ untrusted: rank_candidates / Jev / SLM / plan_route order / posture / Kerdoios placement
proposed decision dec = (candidate | NONE, tier, proposed spend ints)
        │
        ▼ admit_C(env, snap, req, dec)   (S3)  ── DENY ─▶ FALLBACK_C (NONE / PARENT_ONLY / ABSTAIN); receipt
        ▼ ALLOW
dispatch_authority.claim / backend.decide  (request_sha256 binds provider/model; receipt carries law_set_id)
```

Two functions are proven in Bend: `eligible_C` and `admit_C`. Their link is a law, C1: `admit_C` allows a non-NONE
decision only if it is in `eligible_C`. Python carries twins of both, and both twins are tied by parity.

### 5.2 Required data additions [INFERRED]

- `CandidateModel` gets **`egress_class ∈ {loopback, host, tailnet, third_party}`**. Today it has no locality field
  ([SRC] candidates.py L201-216), and `cost_class="local"` is a cost, not a place.
- `worker_routing` providers get the same field. The `local` category today includes `host_local_providers`, for
  example "a tailnet GPU box" ([SRC] worker_routing.py L192-195). **Owner decision:** does `local_only` admit
  `tailnet`? The fail-closed default is no.
- `CandidateSnapshot` rows are fully interned:
  `{cand, provider, egress, cost_class, validated_free: Bool, quality, max_risk, tiers_mask, cap: Nat, measured: Bool}`.
  - `validated_free` is computed by the host from `free_route()`: exact provider/model match, `validated is True`,
    `price_usd == 0`, `evidence_sha256` present.
  - Bend sees only the Bool. The evidence hash goes in the receipt (class B).

### 5.3 Laws for family C (LAWS.bend claims; drafted from invariants the code already states)

Notation: `A = admit_C(env,snap,req,dec) = Allow`, `c = dec.candidate ≠ NONE`, `E = eligible_C(env,snap,req)`.

| # | Law | Carry-down source | Code it replaces as safety authority |
|---|---|---|---|
| C1 | `A ∧ c ⇒ c ∈ E` | #20 hard rule | cascade out-of-set rejection (portfolio L328-332) |
| C2 | `E ⊆ snap` and output order is a function of inputs only (determinism, no duplicates) | filter_candidates docstring "fixed so a replay reproduces it" | — |
| C3 | `(env.budgets.usd = 0 ∨ req.free_only ∨ snap.policy_free_only) ∧ c ∈ E ⇒ c.validated_free` | **AODL Γ usd=0 ⇒ free-only** (new carry-down); #20 "fail closed before paid spill" | `require_free_route` as sole authority |
| C4 | `env.privacy = local_only ∧ c ∈ E ⇒ c.egress ∈ {loopback, host}` | AODL Γ privacy | `allow_remote` text-prefix trick; `e['local']` |
| C5 | `c ∈ E ⇒ c.cap > 0 ∧ c.measured` | snapshot | plan_route L209-210 |
| C6 | `c ∈ E ⇒ quality(c) ≥ req.quality ∧ max_risk(c) ≥ req.risk` | request class | filter_candidates gates |
| C7 | `A ∧ c ⇒ req.risk ∈ env.authority` | **AODL authorityCeiling** | constant `requested=["execute"]` (governed_worker L251) |
| C8 | `A ∧ c ⇒ dec.tier ≤ env.max_tier` (when declared), and `dec.tier ≥ req.floor_tier` | AODL `policies.route` ceiling; surface floor | escalation `at_least` only |
| C9 | `A ∧ c ⇒ ∀ declared dim d: seen_d + dec.spend_d ≤ limit_d` (exact Nat) and spend antitone | AODL Γ budgets | float `+1e-12` (G1) |
| C10 | `env.budgets.premium_tokens = 0 ∧ c ∈ E ⇒ c.cost_class ≠ metered_premium` | AODL Γ | — (unenforced today) |
| C11 (meta) | `admit_C(…, NONE) = Allow` (fallback always lawful) | #48 rule 4; ABSTAIN always legal | — |
| C12 (meta) | `c₀ = head(E) ⇒ admit_C(…, c₀) = Allow` (non-vacuity) | — | — |

All thresholds are integers that the host discretizes before the call: confidence in permille, latency in ms,
micro-USD. F32 cannot be proven (README L230-231).

### 5.4 What Bend never does for model choice

- It never ranks, scores or picks.
- It never classifies task text: no regex, no strings.
- It never reads quota or health. Those arrive as `cap` and `measured`, already computed.
- It never resolves `plan.bindings` selectors.

These are consistent with #48's non-goals ("no Bend-based LLM router", "no provider/quota logic in Bend") and with
Kerdoios's boundary ("does not decide semantic suitability").

---

## 6. Ownership (per the z0 registry, `components.yaml` @ `7933914`)

| Concern | Owner | Why it sits there |
|---|---|---|
| Validation, canon, fingerprint, **`envelope()` projection**, integer units, typed `constraints.privacy` | **kvnloo/aodl** | "typed intent/plan contracts". aodl#19: "Bend must consume AODL semantics, not silently define a second normalization" |
| `proof/Core.bend` (`Envelope` and Core types) + family-A LAWS/PROOF | **kvnloo/aodl** | aodl#19's own proposed `proof/` layout. Laws are contract artifacts |
| Family B/C/D/E/P LAWS (owner-reviewed), `admit` kernels, PROOFs, Python `z0int.law.*` guards, choke-point wiring, snapshot assembly, receipts | **kvnloo/z0intelligence** | owns "personal policy … bounded decision backends, routine promotion"; #20 "owns the cognition/model-selection plane" |
| Placement (runtime/quant/co-residency), quota model, Pareto among the **eligible** set | **kvnloo/kerdoios** | "provider routing, allocation policy"; never widens the set (C1 re-check after placement) |
| `plan.bindings` selector search, challenger populations, promotion evidence; **batch Bend replay of deterministic challengers** | **kvnloo/evolution-lab** (+ z0evals as judge) | "experiment search … promotion evidence", not production runtime |
| Verifier wrapper, kernel pin, receipts, offline replay, per-release qualification corpus | **kvnloo/bend-native** (with hermes #389/#390) | STACK.md "Bend proof evidence": emitted-book scope, source semantics not attested |
| Bend toolchain | upstream bendlang/bend; kvnloo/bend patch fork only until 2.0.35 qualifies | registry `external_substrate`, `experimental` |
| Registry status change of `bend` (`experimental` → something stronger) | kvnloo/z0 | only after M4 evidence exists |

---

## 7. Receipts, replay and provenance

**Identities:**
- `law_pkg_hash(F)` is the bend-native input-manifest sha256 over LAWS + admit + PROOF + vendored Core **+ main.bend**.
  Including `main.bend` fixes G7/P-12, where `main.bend` sat outside the closure.
- `law_set_id` is the sha256 of the sorted `{F: law_pkg_hash(F)}` plus the qualified `{bend_bin_sha, kernel_sha}`.
  The version string is not used: a patched build still says 2.0.34.
- `guard_rev(F)` is the sha256 of the Python `z0int/law/<F>.py`.

**Receipts:**

| Receipt | Producer | Binds | Stale when |
|---|---|---|---|
| verdict | bend-native `verify --verdict` (CI) | `law_pkg_hash`, bin/kernel SHA, `ALL PROOFS CHECK` | any package file, Bend identity, or vendored Core changes |
| parity | z0int CI parity job (native batch vs production Python) | `law_pkg_hash`, `guard_rev`, corpus manifest (AODL fingerprints + enumerator seed + bounds), counts: false-allow **must be 0**, false-deny explained | `guard_rev` or `law_pkg_hash` changes |
| decision (existing `z0int.decision_receipt.v1` / admission v1 / cognition v1) | runtime | `extra`: `law_set_id`, `law_family`, `law_codes`, `envelope_fp`, `snapshot_sha256`, `law_record`, `verdict_receipt_sha256`, `parity_receipt_sha256` | — |
| drift | shadow replayer | decision receipt id, both verdicts, `law_set_id` | — (terminal; triggers deopt) |

**Replay.**
- bend-native already gives offline replay of verdicts, with `receipt_stale` and `replay_mismatch` ([NOTE]
  aodl-bend §6).
- Decision replay is new and cheap: `law_record` is the exact integer input. Replaying it through the native batch
  binary reproduces the verdict without the original process, ledger or network. This is the data path for the
  shadow and for Evolution Lab.

**Readiness.**
- `/readyz` already requires `CANON_VERSION` (E5).
- Add one more requirement for the governed and remote paths: the running `guard_rev` values must have current parity
  receipts for the live `law_set_id`, and that `law_set_id` must have verdict receipts.
- If not, the remote governed path is unready, and in-process cognition deoptimizes as in §8.

---

## 8. Failure modes (all fail closed to the canonical conservative path)

| Failure | Detected by | Behaviour |
|---|---|---|
| Contract not validated, not pinned, wrong canon | `aodl_dispatch.ensure` | DENY (`contract-*`), no dispatch row (exists, plus the pin check) |
| Envelope projection error, value ≥ 2^47, non-integer where integer required | `envelope()` / host encoder | DENY 0 (parse); never coerced |
| Python `admit` denies | guard | FALLBACK_F (NONE / PARENT_ONLY / ABSTAIN). Lawful by C11. **Never escalate to remote as a fallback** |
| Guard raises | guard wrapper | treated as DENY |
| Choke point bypass (v2 without aodl, `is_allowed(None)=True`) | structural test | **removed**: v2 model contact is denied or marked `ungoverned` and excluded from promotion (G4) |
| Verdict or parity receipt missing or stale for live `law_set_id` | readiness + #56 invalidator | remote governed path unready; cognition deoptimizes to deterministic/ABSTAIN; promotions frozen |
| Shadow replay disagrees | replayer | drift receipt → the mechanism that produced the decision is demoted (#56: "Deoptimization is mandatory"); if the disagreement is in `admit` itself, the parity receipt is invalidated → previous row |
| Mode X sidecar timeout or crash | adapter | canonical Python verdict stands; `cross_check: unavailable` recorded |
| Mode X disagreement | adapter | DENY + drift receipt |
| New Bend release | #389 qualification | not used until the B389 corpus passes by bin/kernel SHA; until then the previous qualified identity stays pinned |
| `--verdict` PASS but translation wrong (#1212 class) | not detectable by the verdict | mitigations: qualified build; parity against production Python independently tests the *executed* semantics; `-o PROOF.bendtt` read for new laws |

---

## 9. Migration from today's code

Each step starts with failing tests (red/green), lands on a branch, and changes no default behaviour until its own
exit check passes.

**M0. Prerequisites (no Bend work):**
- G3: host-pinned contract fingerprints in `aodl_dispatch.ensure`.
- G4: retire v2 model contact or make it explicitly ungoverned.
- G1: integer arithmetic in `decide_spawn`. Use conservative floor/ceil until AODL has integer units. Fix
  `check_budget` before it gets any caller, because `AodlSpend.plus` clamps at 0 and would let a negative proposal
  lower the projected spend ([SRC] aodl.py L258-266).
- G6: one canonical identity (canon-1 vs core-v1), then aodl #39 onto main.
- #389: qualify 2.0.35 against the B389 corpus.

  Exit check: master tests plus the probe `scratch/probe/probe_budget.py` show master DENY on the 2^53 and 2^60 cases.

**M1. Tie family A to production:**
- Re-point the parity harness from `reference_transition` to master `decide_spawn` (G2).
- Add a `bend-laws` CI job: bend-native verify and native batch parity.
- Include LAWS/PROOF in `kernel_revision` and `main.bend` in the receipt closure (G7).
- Stamp `law_set_id` on admission receipts.
- Replace the per-call reader thread in `BendGate._read_line`.

  Exit check: parity on master's function with 0 false allows; receipts joined.

**M2. AODL side (AODL owner):**
- typed `constraints.privacy`;
- integer units in the canon;
- `envelope()` with tests;
- `proof/Core.bend` holding the `Envelope` type;
- move the gate's Python Core encoder into AODL, which closes aodl#19's constraint.

  Exit check: the AODL fixture corpus yields envelopes, and the z0int gate consumes them with unchanged parity.

**M3. z0int guards, shadow first:**
- Add `z0int/law/admit_C.py` (+ `eligible_C`), `admit_B`, `admit_D`.
- Add `egress_class` to candidates and providers.
- Thread the Envelope into `routing_snapshot`, `CandidateRungBackend`, `EscalationPolicy` and `governed_worker`.
- Compute `requested` from the D12 legal set's risk classes.
- Pick the provider from `plan.bindings ∩ eligible` instead of `Z0INT_GOVERNED_PROVIDER`.
- Log verdicts into receipts without enforcing.

  Exit check: a shadow period shows the guard's would-deny set (expected: remote_frontier under local_only, env-picked
  non-free providers). The owner reviews it, then enforcement flips per choke point.

**M4. Bend family-C package:**
- `bend/model_eligibility/{LAWS,admit,PROOF,main}.bend`, with LAWS C1-C12 reviewed by the owner;
- the mutation suite;
- bounded-exhaustive parity in native batch;
- verdict and parity receipts bound into decision receipts.

  Then the same for B, D, E and P in priority order.

**M5. #56 promotion gate:** a mechanism is promotable only with current law receipts and zero envelope exits in
shadow. Any change to the law package, guard or Bend identity is an invalidator.

**M6 (owner-gated):**
- Mode X sidecar at `dispatch_authority.claim`, after the perf triage numbers.
- Evolution Lab batch replay of deterministic challengers in native Bend.
- Watch WONTFIX #813 and the Python target. If either lands, the proven `admit` can replace the Python twin in
  process, and the parity step goes away.

---

## 10. What NOT to put in Bend

- No routing, ranking, scoring, model selection, uncertainty or LLM routing (#48 non-goal).
- No provider health, quota, caps accounting or placement. These are Kerdoios and z0int snapshot inputs (#48
  non-goal).
- No JSON parsing, AODL canonicalization, fingerprinting or a second normalization. AODL owns these (aodl#19).
- No regex or text classification of tasks, and no strings beyond interned ids.
- No floats claimed as proven. F32 is axiomatic.
- No ledgers, locks, leases, idempotency, persistence, network, credentials or dispatch (#47; GUIDE "IO": effects stay
  outside proofs).
- Not on any per-request in-process hot path through IPC (cognition, memory inject, route).
- No promotion logic based on measured outcomes. "cascade success ≥ baseline − margin" is data, not a law.
- Do not expect Bend to prove that every model contact passes the guard. That is a structural property of the host;
  #47 single authority plus tests cover it.
- No `--publish`, no Hub dependencies, no bare `bend PROOF.bend` in a gate.
- A PASS is never task success, admission or authority (bend-native README).

---

## 11. Residual risks [INFERRED]

1. **Translation is unproven and releases churn.** There were 36 releases in 16 days, each with `--verdict` fixes.
   Per-release qualification is mandatory. Parity against production Python is the independent check on *executed*
   semantics.
2. **The snapshot is trusted.** If `validated_free` or `egress_class` is mis-set in a manifest, the guard is
   faithfully wrong. Mitigations: evidence hashes in receipts, plus owner review of manifest diffs.
3. **Choke-point completeness is not provable.** A new code path that contacts a model without `admit` defeats the
   design. Mitigation: one test that enumerates the call sites of provider and backend clients.
4. **Availability under fail-closed.** Making `local_only` the default when privacy is absent, together with the M3
   flip, will deny today's remote canary until contracts declare privacy. The shadow-first rollout is how that cost
   becomes visible before it bites.
5. **Laws too weak.** A law set that passes but is vacuous protects nothing. The mutation suite and the non-vacuity
   meta-law C12 address this.
6. **Perf triage still empty.** Mode X stays unbuilt until it lands.

## 12. Owner decisions surfaced

- Integer units for `usd`, `joules` and `attention` in the AODL canon, and a typed `constraints.privacy` (AODL owner).
- Whether `local_only` admits `tailnet` hosts.
- Whether `budgets.usd = 0` ⇒ free-only (C3) and `premium_tokens = 0` ⇒ no premium (C10) become canon semantics.
- Whether LAWS.bend files are owner-reviewed spec of record (upstream convention).
- Whether law receipts become a hard precondition for #56 promotion and for `/readyz` on the governed path.
- The #389 trust root: 17db447a, or 2.0.35 after qualification.
- Mode X (§3.6): adopt it after the triage, or keep Bend entirely off serving.

## Hard-rule breach

None.
- Read-only everywhere except this file. No builds or runs.
- The only commands were `git merge-base`/`git log`/`git tag` on the read-only clone `src/bend`, plus `grep`, `sed`
  and `python3 -c` JSON reads of existing scratch files.
- No GitHub or upstream writes.
