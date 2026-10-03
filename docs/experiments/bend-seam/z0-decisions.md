# z0intelligence decision surfaces that AODL/Bend could govern

Date: 2026-10-03. Every source was read-only. Scratch extracts are in `bend-seam/scratch/` (`git archive` of
`origin/master` and `origin/integrate/wiring-20261003`, plus GitHub issue bodies fetched read-only with `gh issue view`).
Anything not read directly in code or docs is marked **INFERRED**.

## 0. Pinned sources

| Source | Identity |
|---|---|
| z0intelligence `origin/master` | `0159808f` (local `master` is `0563ed7a`, 2 behind; every line ref below is on `0159808`) |
| z0intelligence `origin/integrate/wiring-20261003` | `e02bcf5` (capture/privacy, outcome verifier, loop export) |
| z0intelligence local branches | `feat/wire-memory-core-20261003` `772ba7d`; `feat/wire-memory-harnesses-20261003` `41aec0f` (memory surface, inject seam; **not** in the wiring branch) |
| z0intelligence `exp/bend-aodl-gate` | worktree `/mnt/zer0models/z0-wt/bend-aodl-gate` @ `a0e95785` (Bend 2.0.34 kernel, `bend/aodl_gate/README.md`) |
| kvnloo/aodl | `/mnt/zer0models/z0-wt/ro/aodl` main `416736c`; `origin/feat/aodl-core-conformance-v1` `40e3f97` (`profiles/aodl-core-v1.md`, `to_core()`) |
| kvnloo/bend-native | `/mnt/zer0models/z0-wt/ro/bend-native` `e85e65e` (`stack/STACK.md`) |
| kvnloo/kerdoios | `/mnt/zer0models/z0-wt/ro/kerdoios` `cdb43b0` (`docs/local-cognition-placement.md`, `kerdoios/aodl.py`, `kerdoios/quota/*`) |
| kvnloo/z0 registry | `/mnt/zer0models/z0-wt/ro/z0` `7933914` (`registry/components.yaml`, `registry/suite.yaml`) |
| Bend guide | `bend guide` text for 2.0.34, as captured in `/mnt/zer0models/z0-wt/wiring/bend-perf/guide.txt` |
| Bend run-mode triage | `/mnt/zer0models/z0-wt/wiring/bend-perf/bench_modes.py`. `results/modes-r1.json` was **empty (0 bytes)** when this was written, so no triage result is cited. |
| Stack synthesis | `/mnt/zer0models/github/cua-lanes/artifacts/bend-stack/SYNTHESIS.md` |
| Issues | z0int #13 #20 #47 #48 #53 #55 #56 #59 #63 #66; hermes-agent #324 #388 #389 #390 (`scratch/issues/all.md`) |

## 1. Results first

1. **"Which model to use" ignores AODL today.** The functions that turn AODL into a runtime contract
   are `aodl.runtime_contract` (L726), `aodl.check_budget` (L763) and `aodl.binding_for_implementation_stage` (L800).
   None of them has a caller under `src/` other than `aodl.py`; only tests use them (grep on `0159808`). Two paths
   decide the model instead:
   - `intelligence.route` (L67) reads `manifests/capabilities.v1.json`, `manifests/worker_routing.v1.json`,
     host overrides and health snapshots.
   - `governed_worker._provider_model` (L154) takes `Z0INT_GOVERNED_PROVIDER`, or else `free_provider_order[0]`.

   AODL reaches runtime in one place only: the spawn admission gate (`aodl_admission.decide_spawn`). That gate gets
   `requested=["execute"]`, which is hard-coded at `governed_worker.build_remote_request` L251. The compiler side is
   intentional: `aodl._stage_binding` (L232) leaves provider/model `"binding": "unbound"` "so Kerdoios or a harness
   compiler may resolve" it. Carrying AODL down into model choice is therefore a **missing edge**, not an existing
   integration that needs to be ported to Bend.
2. **The Bend integration that already exists covers one surface**, the #13 spawn transition (codes 101–106) plus the
   validate_02 structural rules. Within the kernel's own Core IR, 8 laws are proven (`spawn_revision_pinned`,
   `spawn_within_bounds`, `spawn_within_budget`, `spawn_no_invented_authority`, `doc_no_privileged_grant`,
   `allow_needs_parse`, and two more). Parity with Python is **measured, not proven**: 22,079 doc cases with 0 false
   allows and 1 false deny, and 16,356 transition fuzz cases with 0 disagreements. All of this is in
   `bend/aodl_gate/README.md` @ `a0e95785`. Python stays authoritative: `aodl_admission.py` docstring L1-6,
   `docs/aodl-admission.md`, the #13 comment of 2026-10-01, and `STACK.md` row "AODL/Bend gate".
3. **The Python decision rules are not what makes the hot path slow.** The spawn reference runs at 6.6 µs p50 and the
   in-process validator at 90 µs p50 (gate README). The governed path, though, does several full canonical-ledger scans
   under `flock` per call:
   - `dispatch_authority.previous` L40
   - `provider_saturation._rows` L23
   - `quota_budget.rows` L17
   - `aodl_dispatch.previous` L89
   - `governed_worker._latest_receipts` L64

   Each scan is O(ledger), and the turn path also includes a Kerdoios subprocess with a 12 s timeout
   (`bridge/runtime.kerdoios_plan` L139, called from `turn_open` L360-362). **INFERRED:** at any realistic ledger
   size these costs dominate. Moving pure rules into a Bend process would add an IPC floor, measured at ~70 µs in the
   gate README "Findings", without removing them.
4. **Four unrelated mechanisms enforce egress/privacy, and AODL is the source of none of them**:
   - `allow_remote` in `intelligence.route`, which works by prepending `'local-only '` to the task text so that the
     regex rule `\blocal-only\b` in `worker_routing.v1.json` selects category `local`;
   - `entry['local']` in the same function (L102);
   - `allow_cloud_injection` in `memory.json`, per harness (`memory/seam.py` on `41aec0f`, L57-62 and L252);
   - capture `privacy_class` `content_free|request_opt_in` (`harness_capture.privacy_class` L138 on `e02bcf5`).

   Meanwhile `docs/aodl-integration.md` puts "local-only personalized artifacts and confidential receipts" into Gamma,
   and Kerdoios already maps AODL privacy `public|confidential|local_only` (`kerdoios/aodl.py` `_PRIVACY`).
   `cognition/escalation.EscalationPolicy.decide` can return `remote_frontier` when the local latency budget is
   exhausted, and it takes no privacy input (grep `local_only|privacy|allow_remote` in `cognition/` returns no policy
   hit).
5. **Most decision surfaces are already written as pure, deterministic functions over explicit inputs.** That is the
   precondition for Bend laws and differential checking, and these surfaces need no redesign to get them. The
   per-surface "Bend fit" column below sorts them.

Fit classes used below (INFERRED classification; it applies the Bend guide's constraints and #48's promotion rule):

- **A: law target.** The function is pure and total over small typed inputs. State its invariants as Bend
  `LAWS.bend`, prove them over a modeled twin, and run differential fuzz against the Python authority in CI and shadow.
  Python stays on the hot path.
- **B: snapshot rule.** Pure once given a frozen snapshot, but the snapshot is assembled from an effectful ledger,
  environment or files. Bend can only check the rule over a frozen, content-hashed snapshot. Snapshot construction
  stays Python.
- **C: learned or LLM.** The decision itself is not provable. What can be proven is its *envelope*: the output must
  belong to the compiled legal set, tiers are monotone, and authority never widens.
- **D: effect or ownership.** IO, locking, idempotency or credentials. Not for Bend: the guide routes effects through
  host C/JS functions only, and "proofs, termination and the GPU never touch host code".

## 2. Inventory

Owner column: by default the z0 registry `components.yaml` (`z0intelligence.boundaries.owns`: personal policy,
bounded decision backends, routine promotion, specialist runtime; `kerdoios.owns`: provider routing, allocation
policy; `aodl.owns`: typed intent/plan contracts). Where an issue or doc refines ownership, it is cited. Latency is
what code or docs state; "hot" means the call sits on a user-turn or dispatch critical path.

| # | Surface | Current implementation (file:function @0159808 unless noted) | Owner | Decided by | Hot path / latency budget | AODL today | Bend fit |
|---|---|---|---|---|---|---|---|
| D1 | Capability route kind: `PARENT_ONLY` / `JEV_FUNCTION` / `LAYA_FUNCTION` / `LOCAL_MODEL` / `REMOTE_MODEL` | `intelligence.route` L67-107 ("Pure decision"); snapshot `routing_snapshot` L42; exec `execute` L120; entry `dispatch` L176 | z0int (#20: "owns the cognition/model-selection plane") | rule over manifests plus evidence hashes (`automatic.evidenced`) | hot, inside `/v1/intelligence`; no explicit budget; MCP client timeout 120 s (`intelligence_mcp.route_worker`) | none | A (route is pure given the snapshot) + B (snapshot) |
| D2 | Provider/model order for text workers; free-only policy | `worker_routing.plan_route` L189; `free_route` L137; `free_required` L145; `require_free_route` L149; recheck in `execute_plan` L418 and `dispatch_authority.emit` L189-191 | z0int policy; Kerdoios owns placement (#20 comment 2026-09-21) | regex rules + fixed orders + exact allowlist `validated_free_routes` (evidence sha256, `price_usd==0`) | hot; `timeout_s: 20`, `max_attempts: 3` (`worker_routing.v1.json`) | none | A |
| D3 | Explicit provider/model selection | `worker_routing.explicit_plan` L379, `dispatch_worker` L392 (`/v1/worker`) | z0int | caller-chosen, validated against registry | hot | none | A (membership + free check) |
| D4 | Provider admission, caps and health backoff | `provider_saturation.acquire` L65, `_state` L31, `release` L89; caps `provider_caps` | z0int dispatch authority | rule over receipts (cooldown 60 s, error TTL 30 s) | hot; full ledger scan | none | B for the cap/backoff rule, D for permits |
| D5 | Quota projection (rpm/tpm/rpd/tpd, header reset) | `quota_budget.project` L25 (imports `kerdoios.quota.model`); `reservation` L11 | Kerdoios (quota model) via z0int ledger (#20: "z0intelligence should treat Kerdoios' remaining free capacity as an execution constraint/objective, not own provider quota accounting") | rule; conservative rolling windows | hot; full ledger scan | none | B |
| D6 | Dispatch authority: claim, idempotent replay, no takeover, ordered receipts, completion (#47 leases) | `dispatch_authority.run` L87, `claim` L118, `emit` L153, `complete` L201, `rpc` L217 | z0int (#47: "Do not build another LLM gateway or scheduler") | deterministic protocol | hot; `flock` + ledger scans | v3 protocol requires admission (`claim(require_aodl=version==3)` L222) | D for the protocol; A for the state machine as a spec (INFERRED) |
| D7 | AODL structural spawn admission, codes 101–106 (#13, #48) | `aodl_admission.decide_spawn` L124; persisted by `aodl_dispatch.ensure` L130 before dispatch-start | AODL validates and fingerprints; z0int owns the decision (#13 "Ownership") | rule | hot; measured 6.6 µs p50 for the Python reference (gate README) | **yes**: the only live AODL edge | A, **already done** for the kernel (`exp/bend-aodl-gate`) |
| D8 | Governed worker envelope: provider pick, observed state, frozen governance receipt | `governed_worker.build_remote_request` L190, `_provider_model` L154, `observed_spawn_state` L78, `prepare_remote_request` L261 | z0int host (doc `aodl-governed-worker.md`: "The harness cannot choose placement") | env/config + ledger reconstruction | hot (canary only, `Z0INT_GOVERNED_REMOTE=1`) | contract loaded; provider is **not** derived from it; `requested=["execute"]` hard-coded | B |
| D9 | AODL Gamma runtime budget + plan bindings → implementation stage | `aodl.check_budget` L763, `runtime_contract` L726, `binding_for_implementation_stage` L800, `compile_aodl` L295, `_stage_binding` L232 | AODL declares, z0int checks (`docs/aodl-integration.md`) | rule | **not called at runtime** | compiled, unused | A (it is the natural carry-down seam, see §3) |
| D10 | Resource posture BURN/BALANCED/OFFLOAD/RESERVE | `posture.evaluate_pool` L149, `evaluate` L283, `recommend` L248; applied by `worker_routing.posture_annotate` L346 (shadow unless `posture_enforce`) | z0int; Kerdoios inventory is a read-only input | rule (arithmetic burn-rate projection) | hot, fail-open | none | A (evaluate) + B (pools from codexbar/Kerdoios caches) |
| D11 | Preflight ladder routine → specialist → local_model → model, then Kerdoios placement | `preflight.run_preflight` L173; `bridge/runtime.BridgeRuntime.turn_open` L355-362; `bridge/runtime.kerdoios_plan` L139 (subprocess `python -m kerdoios plan`, 12 s timeout) | z0int decides "needs model"; Kerdoios places (`docs/cascade-compiler.md` "Relationship to Kerdoios"; kerdoios `docs/local-cognition-placement.md`: "Kerdoios does NOT decide semantic suitability") | rule + promoted artifacts; Kerdoios = Pareto/optimizer (`kerdoios/optimize.plan`) | hot on the OMP bridge turn; 12 s subprocess cap | none on z0 side; Kerdoios ingests AODL-shaped specs (`kerdoios/aodl.work_requirement_from_aodl`) | A for the ladder order and fail-open; D for the Kerdoios call |
| D12 | Legal action compiler (capability → dependency → permission → budget → shortcut) | `cognition/actions.compile_actions` L298 ("Rejecting an action here is authoritative; no backend can re-admit it", #20 hard rule) | z0int | rule; pure stdlib | hot (cascade, shadow, bridge) | none (authority passed as an argument) | **A, prime target** |
| D13 | Cognition escalation tier: deterministic → tiny_specialist → bounded_jev → orchestrator_slm → general_slm → remote_frontier | `cognition/escalation.EscalationPolicy.decide` L166, `accept_or_escalate` L132, thresholds `EscalationThresholds` L34 (`escalation-v1`); driver `cognition/cascade.CognitionCascade.run` L361 | z0int (#20 control-plane split); #56/#59 demote the fixed ladder to candidate mechanisms | rule over learned signals (confidence, margin, entropy) | hot; `latency_budget_floor_ms 1500` | none; no privacy input | A for the policy (monotone, versioned) + C for backend outputs |
| D14 | DecisionOpportunity + selective autonomy ACT / OBSERVE / ASK / ABSTAIN / ESCALATE (#53, #55) | `decision_opportunity._authority` L34, `_action_space` L119, `build_decision_opportunity` L144, `deterministic_gate` L197, `with_authority_grant` L206 | z0int runtime; AODL authors authority (#53) | rule (the #55 baseline); learned gate only in Evolution Lab shadow (#56 addendum M0) | **off** hot path: detached child 1–3 s (`claude_code.emit_opportunity_async`) | yes: `authorityCeiling` grants + `aodl-canon-1` fingerprint; invalid contract → read-only | A (gate, action space, grant monotonicity) + C (learned gate) |
| D15 | Routine compiler/registry, promotion, future-drift demotion (#56) | `routines.mine_routines` L442, `credit_sealed` L516, `promote_credited` L533, `RoutineRegistry._decide` L559, `observe_future` L585 | z0int (registry: "routine promotion") | learned offline (predicate mining), rule at runtime (≤2 scalar predicates) | runtime "zero-token and sub-millisecond" (`docs/routine-compiler.md`) | promoted routines compiled into `plan.bindings` (`aodl._promoted_routines` L224) | A (matching + status transitions) |
| D16 | Cascade compiler thresholds, sealed credit, promote, deoptimize | `cascade.compile_cascade` L295, `credit_sealed` L356, `promote` L388, `observe_future` L392, `decide_row` L185 | z0int; Evolution Lab searches (`docs/evolution-lab.md`) | learned threshold search on dev, rule at runtime | offline compile; runtime `decide_row` is µs (INFERRED) | `policies.route` = abstract order; thresholds in `plan.bindings` (`docs/aodl-integration.md`) | A (status lattice, frontier always terminal) |
| D17 | Promotion lifecycle | `cognition/manifest.PROMOTION_STATES` L27 (`candidate, shadow, credited, promoted, rejected, unavailable`); routine/cascade `status`; #56 addendum ladder `[sanity, replay, shadow, promoted]`, owner approval per family | z0int; Evolution Lab = evidence; z0evals = judge (#56 addendum table) | human approval + sealed rule | offline | none | A (transition relation: no skip to `promoted` without `credited`) |
| D18 | Memory scope/visibility (#66, #63) | `memory_contract.MemoryScope.is_visible_to` L209 (ancestor-only); `memory/surface.ScopePolicy.admits` L104 and `claim_injection` L610 @`772ba7d` | z0int memory control plane; "visibility contract, not an authorization system" (`docs/memory-control-plane-contract.md`) | rule | brief built inside the 300 ms inject deadline | none (`instruction_capability=True` rejected: "belongs in the existing policy/AODL surface") | A |
| D19 | Privacy/egress: remote context, memory cloud injection, capture text | `intelligence.route` L83, L100-102; `paid_jev_exception` L55; `memory/seam.py` `allow_cloud_injection` @`41aec0f`; `harness_capture.privacy_class` L138 @`e02bcf5`; `memory-client.mjs` `DEADLINE_MS=300` | owner decision per harness ("never an env var", seam.py L57) | config rule | hot (300 ms memory deadline; route) | none | A (egress lattice) + B (config) |
| D20 | AO spawn-decision shadow | `ao_bridge.spawn_decision` L140 (`ao-shadow-baseline-v1`, "deterministic_baseline_no_model_call") | AO executes, z0int advises (`ao_bridge.py` docstring) | rule (shadow) | shadow | none | A (low priority) |

## 3. Per-surface invariants and seam notes

The invariants come from code and docs. "Law candidate" lines are **INFERRED**: they restate invariants the code
already enforces, phrased as Bend laws. None of them exists in Bend today, except the D7 laws.

**D1 `intelligence.route`.** Invariants in code:
- `automatic` admits only entries whose evidence is integrity-checked (L74-76).
- Under `free_only`, candidates are only `free_route` hits or the single `paid_jev_exception`: typesafe `jev-1.13.0`
  `evidence_sufficiency` (L55-64, L78-80).
- `evidence_sufficiency` requires `allow_remote` (L83).
- `verification_needed` requires `experimental` (L90).
- Tiny tasks (`tiny_parent_tokens_max 256`) and tasks under the observed overhead (30,804 ms) go to `PARENT_ONLY`
  (L94-98).
- Without `allow_remote`, only `e['local']` entries are eligible (L102).

Today only `evidence_sufficiency` is `eligible: true` in `capabilities.v1.json`. Law candidates:
- `free_only ⇒ selected ∈ validated_free_routes ∪ {paid_jev_exception}`;
- `¬allow_remote ⇒ selected.local ∨ kind=PARENT_ONLY`;
- `automatic ∧ ¬evidenced ⇒ PARENT_ONLY`.

Seam: route is the place where an AODL-carried envelope would enter. It would pass as an additional immutable
snapshot field, the way `registry_sha256` does today.

**D2 / D3 worker routing.**
- Exact allowlist: credits and model-name guesses are "not $0" (L137-142).
- The execute-time recheck "stale or forged plans cannot introduce paid fallback" (L417-418).
- `free_only` with a provider-reported nonzero cost turns a completed call into `failed` (L312;
  `dispatch_authority.emit` L194-195).
- Caps of `None`/0 skip the provider (L209).

Law candidate: every candidate in `plan_route(...).candidates` with `free_required` has a `free_route` entry. The
regex category rules (`rules[]`) are a learned-free heuristic, not authority.

**D4 / D5 admission and quota.** Health is reconstructed from receipts. The quota is "rolling conservative windows
plus provider header reset; never midnight refill" with `no_paid_spill` (L89-90). Unknown usage is reserved
conservatively (L56). These decisions are pure only relative to the ledger snapshot plus `now`, which makes them B.
#47's invariant "snapshot reports unknown quota as unknown, never zero" belongs here.

**D6 dispatch authority (#47).**
- "A started claim is never reassigned … No lease expiry can authorize a second execution" (module docstring).
- Trace reuse with a different fingerprint is a hard error (L49-50).
- Exactly one physical attempt per explicit worker (L181).
- The output hash must match the physical receipt (L211).

This is an effectful protocol (D). Bend has no Python FFI and only host C/JS effects (guide "IO and Concurrency").
**INFERRED:** at most, the receipt state machine (`started → physical started → terminal → completed`, order
indices) could be written as a Bend spec with laws such as "no completion without exactly one terminal physical
event", and checked against replayed ledgers offline.

**D7 AODL admission.** The caller cannot supply authority. Revision, dynamic bounds, budgets and `authorityCeiling`
are read from the validated document. Any API, canon or shape problem fails closed. Admission is persisted *before*
dispatch-start, so a denial reserves no provider capacity (`docs/aodl-admission.md` "Dispatch integration").

The Bend kernel proves the same rules in exact Nat arithmetic. Python uses floats with a `1e-12` epsilon
(`aodl_admission.py` L325, `aodl.check_budget` L795). Bend refuses fractional budgets ("F32 is axiomatic"). Gaps
recorded in the gate README:
- the Core IR encoder and shape checks live in z0int and must move into `aodl_contract.to_core()`; that now exists on
  aodl `origin/feat/aodl-core-conformance-v1`, but is unmerged on main `416736c`;
- hotl-0.1, the Hermes compile-stops and the observed-graph laws are not modeled;
- the gate is not wired into the controller.

AODL Core v1 says: "Bend may consume AODL Core v1 in CI/proof experiments, but Bend does not define or own this
normalization."

**D8 governed worker.**
- The harness cannot supply provider/model/AODL/state.
- A frozen governance envelope per trace means a retry cannot change its own `live_children` or spend.
- Unknown prior token usage fails closed (L213-218).
- The provider must be an exact validated $0 route (L167).

Gap (finding 1): the provider/model is env/config-selected, and AODL `requested` capabilities are a constant.

**D9 Gamma + bindings.** `check_budget`: a missing dimension is unbounded, and an exceeded declared dimension fails
closed. `AodlSpend.plus` clamps at 0 (L258-266). `binding_for_implementation_stage` resolves `plan.bindings` by
`implementationStage` in `plan.route.order`. This is the existing typed slot for "which model": AODL's `policies.route`
carries the abstract order, and `plan.bindings` carries concrete checkpoint/provider selectors and learned thresholds
"so Evolution Lab can change an implementation without silently redefining user intent" (`docs/aodl-integration.md`
"Pi and Gamma"). It has no runtime consumer.

**D10 posture.** `evaluate` is documented as "a pure, deterministic function of (pools, now, thresholds)". The group
precedence is OFFLOAD > RESERVE > BURN > BALANCED. Enforcement is opt-in and fail-open. When BURN is enforced, every
offload candidate is emptied (`posture_annotate` L355-360). That interacts with D1/D2 and is not bounded by AODL.

**D11 preflight + Kerdoios.** The order is routine → specialist → local_model → model (residual), and missing coverage
means `model`. Kerdoios answers only "can it run locally now / which runtime-quant / co-resident?". Semantic suitability
stays in z0int (`manifests/local_cognition.v1.json` `role_defaults`, e.g. `bounded_scorer: nanojev_06b`). #13 lists
Kerdoios placement as a non-goal of the gate. #48 says "no provider/quota logic in Bend". The #47 golden chain is
AODL gate → Kerdoios placement → dispatch claim → admission lease → call. In code, `dispatch_authority.claim` runs
`validate_remote` → AODL `ensure` → `started` with no Kerdoios step (**INFERRED** from L118-132: placement is not in the
governed chain today).

**D12 legal action compiler.**
- Pure, replayable, with a recorded reason for every elimination.
- Authority is granted per risk class (`read, write, destructive, publish, credential, payment`) and "never inferred
  from a model decision".

Law candidates:
- `compile_actions` output ⊆ input;
- every legal action has `risk_class ∈ authority`;
- monotonicity: adding authority never removes a legal action;
- every learned choice (D13 backends, shadow) ∈ legal set.

The last law is the #20 hard rule and the C-envelope for every learned surface.

**D13 escalation.**
- Tiers only move rightwards (`at_least`, L120-122).
- High-risk classes and `verification_required` force at least `orchestrator_slm`.
- Novel state forces `general_slm`.
- An exhausted latency budget jumps to `remote_frontier`.

Law candidates: tier monotone in risk; `verification_required ⇒ tier ≥ verify_min_tier`. Gap: no egress precondition
on `remote_frontier` (finding 4).

**D14 DecisionOpportunity.**
- `evidence ≠ belief ≠ authority ≠ action ≠ outcome`.
- Unknown ≠ false ≠ absent.
- Authority widens only through `with_authority_grant(granted_by_user=…)`. "Model confidence has no entry point".
- An unvalidatable AODL contract yields read-only.
- ABSTAIN is always legal.
- `deterministic_gate` uses the fixed priority ACT > ESCALATE > ASK > OBSERVE > ABSTAIN. It is the #55 falsification
  baseline.

Law candidates:
- `ACT.legal ⇔ blocked_by = ∅`;
- `ABSTAIN` always legal;
- grant is monotone and requires a named user;
- `semantic_id` changes whenever source revisions change. This one is a hash property and stays checked, not proven.

The learned gate (Evolution Lab el#24) is C: it "only *recommends*; AODL / deterministic authority can deny" (#56
addendum).

**D15 / D16 / D17 compilation and promotion (#56).**
- Rule language: conjunctions of ≤2 scalar `eq/ge/le`, no eval.
- No-match fails open to the specialist.
- Sealed rows never reach mining.
- Promotion only from `credited`, both in code (`promote_credited` L533-535, `cascade.promote` L388-389) and in the
  manifest states.
- Future drift demotes.
- In the cascade, frontier is terminal and always available.
- Owner approval per candidate and family; "Deoptimization is mandatory" (#56 addendum).

Law candidates: status transition relation; `demoted ⇒ not executed`; cascade success ≥ baseline − margin is
**measured** (data), never a law.

**D18 memory scope.** The scope hierarchy `global → user → project → repo → task` must be contiguous. Visibility is
ancestor-only and is applied before ranking. `ScopePolicy.admits` adds a cross-harness boundary. There is a single
injection owner per turn (`O_EXCL` marker). Memory is never instruction authority. Law candidate:
`is_visible_to` is a prefix order (reflexive, transitive, antisymmetric on paths); siblings are never visible.

**D19 egress.**
- Shadow memory never reaches the model.
- canary/on returns context only within 300 ms.
- A non-loopback model endpoint needs per-harness `allow_cloud_injection`, an owner decision in `memory.json` only.
- Capture defaults to `content_free`.
- The paid Jev exception still requires `allow_remote` (L134-135).

All of this is config-sourced, with no AODL tie (finding 4).

## 4. What "carrying AODL down" would mean, per surface (INFERRED proposal, for the seam doc)

The data and code above support one consistent pattern. It needs no new AODL kinds, which `docs/aodl-integration.md`
("No new node kinds") and #20 ("AODL intentionally gets no model-specific tracker") require.

1. **Authored inputs (AODL, owner-authored):**
   - `intentGraph[*].authorityCeiling`, which already feeds D7 and D14;
   - `constraints.budgets`, which already feeds D7 and is unused by D9;
   - `constraints` privacy / local-only, already named in aodl-integration.md and read by Kerdoios;
   - `policies.route` abstract order.

   These flow to D1/D2/D12/D13/D19 as a frozen, fingerprinted envelope (`aodl-canon-1` / Core v1).
2. **Compiled bindings (z0int/Evolution Lab, mutable without changing intent):** `plan.bindings` per stage. These are
   model selectors, thresholds and routine ids, so D9 → D1/D11/D13 model choice. Provider placement stays residual and
   belongs to Kerdoios (D11).
3. **Runtime decisions (Python, authoritative, hot path):** D1–D20 as listed. They consume the envelope as one more
   immutable snapshot field and record its fingerprint on every receipt. This matches D7's existing
   `aodl_semantic_fingerprint` and `intent_source_hash`.
4. **Bend (CI and shadow, never authority):**
   - LAWS over the class-A functions, in priority order: D12 legal set ⊆ authority; D14 grant monotonicity; D1/D2
     free-only and egress; D13 tier monotonicity; D17 promotion relation; D18 visibility order; the D7 laws already
     proven.
   - Each law is paired with a differential fuzz harness against the Python function, as the D7 experiment did.
   - The A/B/C/D split follows the evidence: #48's promotion rule (material latency benefit) fails at this boundary
     (gate README "Findings" 1).
   - The Bend guide offers `-o f.mjs` (JS import of non-IO defs) and native `-o` binaries; it documents no Python
     embedding.
   - bend-native's receipts/replay (#390 accepted) and release qualification (#389: stock 2.0.34 certified a
     wrong-value book on issue 1212; candidate `17db447a` promotable) are the trust root for those CI verdicts.

## 5. Open questions for the owner (not decided here)

- Should `remote_frontier` escalation (D13) and `posture` BURN/OFFLOAD (D10) be bounded by an AODL egress constraint,
  or stay config-only like D19?
- Should D8's provider come from `plan.bindings` (D9) instead of `Z0INT_GOVERNED_PROVIDER`?
- Should the AODL `requested` capabilities come from the D12 legal set's risk classes instead of the constant
  `["execute"]`?
- `aodl_contract.to_core()` is unmerged on aodl main (`416736c`). Bend parity depends on it (gate README "Gaps" 1).
- The `bend-perf` run-mode triage had no results when this was written, so the "4–25x" figure is still the
  gate-README measurement: native `-o` binary, `--threads 1`, persistent pipe with a per-call reader thread, run
  during a concurrent pytest.
