# Bend2 context for `kvnloo/z0intelligence` — laws, proofs, memory, AODL and agency

> **Documentation-only coordination artifact** on `context/bend2-laws` from `master` for [issue #199](https://github.com/kvnloo/z0intelligence/issues/199). Complete cross-chat chronology, citations and related links are in its `BEND1-HISTORY` comment. This change provides **no new `LAWS.bend`/`PROOF.bend`**, test pass, model promotion, device authority, or default-branch integration. Source PRs below were **open Draft/stacked work** when inspected; do not assume their Bend files exist on `master`.

## Design decision — one canonical pure-proof layer, not another agent system

z0intelligence owns *contracts, scoped memory read, cognition and a pure deterministic decision/check path*. Keep distinct:
- `kvnloo/z0`: capability registry.
- `kvnloo/.files`: host intent, host adapters, fleet, real device controls.
- `kvnloo/z0bridge`: identity/pairing/ACP transport.
- Kerdoios: capacity/placement and admitted resource decisions.
- `kvnloo/oss-factory`: worker/claim/promotion coordination.
- AODL: canonical intent/structural authority.
- Harness plugins (Hermes, OMP): native tool seams.
- `kvnloo/z0evals`: measured, frozen promotion/evaluator evidence.

**Bend 2's role:** machine-check *specific pure total functions and transition models* over bounded, typed states; require `LAWS.bend` (reviewed requirements), `PROOF.bend` (implementer proof), `bend PROOF.bend --verdict` (BendTT/Lean-kernel recheck), and a separately tested adapter before any action. Never make Bend the memory protocol, credential authority, autonomous provider router, runtime state oracle or general-purpose replacement for AODL.

The owner goal (recorded Oct 21): “can we continue setting up more bend rules for deterministic rules like this instead of just a rule in an md file?” Implement as code only after reading and reusing the existing work below.

## Where the work already exists

| Model / artifact | Repo files or issue | What has been reported, NOT newly executed here |
| --- | --- | --- |
| Structural AODL gate | [#48](https://github.com/kvnloo/z0intelligence/issues/48), `bend/aodl_gate`, canonical AODL structural gate [#13](https://github.com/kvnloo/z0intelligence/issues/13) | Pure ALLOW/DENY candidate versus canonical Python validator; CPU-only latency correction, no GPU-gate speed conclusion |
| Work-item FSM L1–L7 | [#182](https://github.com/kvnloo/z0intelligence/issues/182), Draft [#183](https://github.com/kvnloo/z0intelligence/pull/183), branch `exp/bend-work-item-fsm`; `bend/work_item_fsm/{fsm,LAWS,PROOF}.bend`, `src/z0int/fsm/work_item.py`, `docs/fsm/README.md` | 15 claims, Bend/Lean receipt PASS, 10/10 mutants rejected; 400 Python/Bend seeded tapes; real replay flagged stale deployed heads and abandoned work |
| Worker FSM W1–W6 | Draft [#184](https://github.com/kvnloo/z0intelligence/pull/184), branch `exp/fsm-worker-supervision`; `bend/worker_supervision_fsm/`, `src/z0int/fsm/supervise` | 17/17 mutants rejected, model parity and read-only logs; 419 historical write/lease conflicts, orphan work and missing foreground checks at dated snapshot, not proof problems are fixed |
| Integration owner FSM I1–I5 | Draft [#188](https://github.com/kvnloo/z0intelligence/pull/188), branch `exp/fsm-integration-owner`, `bend/integration_owner_fsm/`, `docs/fsm/integration-owner.md` | 11/11 semantic mutants rejected, 625 tapes/16,225 prefixes parity, 32 scoped tests; external runtime SHA/owner acceptance still unverified |
| Supervision bridge | Draft [#189](https://github.com/kvnloo/z0intelligence/pull/189), `feat/supervision-live-20261021` | Opt-in O_EXCL leases/owner batches and read-only timer; historical violations reduced in frozen classifications, not erased; no automatic worker resume/merge |
| Ops FSM O1–O9 | Draft [#192](https://github.com/kvnloo/z0intelligence/pull/192), branch `feat/bend-ops-laws-20261021`, `bend/ops_fsm/{ops,LAWS,PROOF}.bend`, `src/z0int/fsm/{ops,ops_parity,ops_replay,guard}.py`, `docs/fsm/ops.md` | 32 claims; 41/41 mutants rejected in verdict mode; 757 Python ↔ Bend parity cases; O1/O2/O4/O6 enforce within explicit hooks, O3/O5/O7/O8/O9 report by default; `source_semantics_attested=false` |
| Host visualization and action adapters | Dotfiles Drafts [#34](https://github.com/kvnloo/.files/pull/34), [#36](https://github.com/kvnloo/.files/pull/36), [#37](https://github.com/kvnloo/.files/pull/37), [#38](https://github.com/kvnloo/.files/pull/38) | Stacked FSM layer, phone-first UI, O1–O9 live incident tapes, fleet O1 + privacy O4 + health O8 host adapters; not inferred on dotfiles `master` |
| OptMem / scoped source evidence | [#22](https://github.com/kvnloo/z0intelligence/issues/22) canonical memory, [#66](https://github.com/kvnloo/z0intelligence/issues/66) identity/freshness/scope, [#195](https://github.com/kvnloo/z0intelligence/issues/195) research synthesis; existing `src/z0int/memory/`, StatePacket and context-resolution logic | **Candidate next formal contract**, not currently proven by the FSM laws. Native original archives and EventLog remain canonical; no second memory store |
| Hermes verified tool seam | [downstream RFC #324](https://github.com/kvnloo/hermes-agent/issues/324), [draft #325](https://github.com/kvnloo/hermes-agent/pull/325) | Opt-in `bend_verify`, bounded file/path and receipts; security hardening against proof/file/kernel spoofing; preserve upstream contributor credit |

**Stacking warning:** work-item #183 targets `preview`; worker #184 builds on #183; integration #188 builds on #184; ops #192 builds on supervision #189. Before discussing “landed” or reusing a model, resolve the exact branch SHA + its base graph and classify draft/merged/host-run/observed separately. Do **not** merge whole stacks just to obtain a context or memory feature.

## Existing formal laws — protect and reuse

These are the *behavioral contracts* already documented in the drafts, not renamed or newly adopted theorems.

### Work item L1–L7 (issue #182, Draft #183)

- **L1:** `Merged` only after a real owner-accepted state. A credential used by an agent under the owner’s GitHub account is **not** authenticated human acceptance.
- **L2:** author/actor cannot approve its own verify/integrate/accept gates; actual identity ambiguity remains indeterminate.
- **L3:** `DraftPR` requires a completed passing receipt on the current head SHA, `Integrated` one on the current integration head; stale or post-merge checks cannot satisfy past requirements.
- **L4:** `Deployed` requires runtime head == integrated head. A drift observation invalidates the deployment.
- **L5:** interrupted work exits only by `Resume` preserving head/checkpoint or explicit `Abandon`; no silent loss.
- **L6:** implementation actor is constrained to an explicit versioned role profile (then Opus/Sol). An SLM/Jev triage decision alone is not code-authoring authority.
- **L7:** owner decisions enter a batched queue; when owner off/low energy, defer them while independent items continue. This is a safety/non-blocking model, not proof a scheduler will act eventually.

### Worker supervision W1–W6 (Draft #184, optional enforcement #189)

- **W1:** one current owner per worktree/branch; write requires held lease; transfer through free or properly proven dead-holder reclaim, not simply a stale filename.
- **W2:** silent/stalled/interrupted workers have bounded resume/abandon/overdue transitions with checkpoint ownership, not forgotten tasks.
- **W3:** cannot mark abandoned/integrated while worker background process persists; resume hands live work to successor deliberately.
- **W4:** heavyweight work is dispatched to fleet except documented central host GPU/Bend/Lean/toolchain exceptions; no unnoticed fallback.
- **W5:** phone installs effective only for current atomic device install-lock holder.
- **W6:** S25 input only into app freshly observed in intended foreground; each tap/type must revalidate. Sensor observation is outside the proof.
  
### Integration ownership I1–I5 (Draft #188)

- **I1:** every open Draft PR becomes part of its named integration branch or is explicitly parked by 24h with reason and expiry. **Overdue is a monitor**, not unproved universal “must eventually integrate.”
- **I2:** deployed/integrated head equality requires a *runtime/build SHA attestation*, not a checkout HEAD or Android package version; drift revokes deployment and acceptance.
- **I3:** one integration owner per repo with expiring lease; integration requires currently held lease; unknown/missing record ≠ proof nobody owns it.
- **I4:** child PR’s recorded base OID contains current parent head; compare immutable GitHub SHAs, not stale local tracking refs.
- **I5:** Merged only follows owner acceptance; independent review is separately required for integration. Program-recorded assertions never manufacture owner consent.

### Operations O1–O9 (Draft #192 + dotfiles host #38)

| Law | Pure predicate/reducer | External seam and currently documented mode |
| --- | --- | --- |
| **O1** | Heavy job must choose permitted fleet node; central host allowed only for small/service/GPU/Bend/Lean exceptions; full fleet → wait | fleet-build, enforce |
| **O2** | A served worktree is immutable except scoped `ff-only` rollout/rollback | served git hooks, enforce |
| **O3** | Clean unserved candidate, required independent review and idle owner-call state; failed real launch rolls back | service manager / launch smoke, report |
| **O4** | Private-labeled artifact never pushed to public or unknown-privacy remote | opt-in git prepush hook, enforce |
| **O5** | Phone action requires owner phone-operation intent, expected actual foreground; install/restart refused during live or unknown call | phone broker / installer, report |
| **O6** | Accessibility enabled-service set after install ⊇ before minus explicit owner removals | install snapshot/adapters, enforce |
| **O7** | Cannot route to cancelled provider or disallowed role; configuration facts have a revision and expiry | routing config timer, report |
| **O8** | Unhealthy >30min → single owner-notification intent per episode (idempotent polls) | fleet health / KDE Connect delivery, report |
| **O9** | Gateway restart maps lost dependent runs to `Interrupted` + recoverable resume pointer, never spurious Failed | gateway-watch timer, report |

### Structural AODL checker (issue #48)

Keep AODL the **authoritative source contract**. Bend is a proposed optional pure filter for:
1. Spawn count, cost/time budget and dependency legality.
2. Authority, capability, identity and effect boundaries; forbidden IO compile-stop.
3. Immutable intent/revision and deterministic normalization/lowering.
4. Bounded observed state admission; invalid/unknown relation must refuse or defer to canonical validator.

Run frozen Python-oracle vs Bend differential/property/fuzz, pin revisions, preserve zero unexplained legal/illegal divergence, and measure p50/p95 + CPU/memory + GPU crossover separately. A 2026-10-04 CPU-only result cannot be used as proof GPU must be slow (nor the `pow2` GPU smoke as evidence the AODL gate is faster). Never move quota/provider logic or live credentials into Bend.

## Candidate **new** `LAWS.bend` for memory/authority — *not yet implemented*

Because the current goal includes the memory contract and learning loop, OMP can propose **small pure functions** over explicit typed revisions/scopes, rather than “prove memory works”:

| Candidate name (subject to owner approval) | Invariant to encode | Must be verified outside the theorem |
| --- | --- | --- |
| `scope_non_escalation` | Scope `unknown` never becomes `global`; a summary/cached hint cannot widen source ACL/project/tenant/workstream without authoritative policy | Actual source ACL/tenant identity and admission bridge |
| `evidence_never_grants_capability` | Changes to memory, confidence, preference or retrieved text alone cannot promote `Observe` to `Execute`; explicit current owner+harness+AODL authorization required | Consent token verification and host/harness effect enforcement |
| `stale_packet_cannot_authorize` | Packet whose source/policy/workstream/claim revision changed cannot authorize a freshness-dependent action | True revision reconciliation, clocks and remote sources |
| `correction_precedence` | A later *sourced correction within the same claim/scope* updates the projected belief while preserving both provenance records; unsupported new assertions cannot override verified original evidence | Authenticity/truth of correction sources, semantic extraction |
| `capture_replay_idempotent` | Duplicate/restarted same admitted event ID gives identical visible state; no second owner decision/credit/notification | Transactional store semantics and concurrent delivery |
| `isolation_before_rank` | Out-of-scope/private/expired sources are excluded **before** scoring, embedding or summarizing; no cross-project exposure in derived StatePacket | Full text/embedding adapters, output redaction, data persistence |
| `abstain_on_missing_evidence` | Missing required provenance/claim/evidence forces `OBSERVE`, `HOLD` or explicit `unknown`, never `ALLOW` or `verified_success` | Evidence ingestion/lineage and gold outcome oracle |
| `decision_non_interference` | Memory update to unrelated workstream cannot change selected action or permission for this workstream | Actual workstream identity, harness context injection |
| `verified_outcome_integrity` | `verified_success` requires independent result matching exact task/input/source/model revision; model prose or generic turn-close is not a success proof | Real product/eval oracle, budget/quality scoring |
| `bounded_admission` | Candidate context budget/token/retention must not exceed hard admission bounds; reject instead of truncating away essential provenance | Actual tokenization, storage, rendering |

These are **design sketches**, not executable Bend syntax or claims of proof. Scope: existing [#22](https://github.com/kvnloo/z0intelligence/issues/22), [#66](https://github.com/kvnloo/z0intelligence/issues/66), [#195](https://github.com/kvnloo/z0intelligence/issues/195), evaluation [z0evals](https://github.com/kvnloo/z0evals). Do not build new EventLog, optmem store, StatePacket, model router or bridge.

## What exactly requires renewed `PROOF.bend`?

1. Any change to Bend's *pure* transition definitions (L/W/I/O or proposed new memory/AODL laws), data constructors or event ordering; require that the same accepted `LAWS.bend` stays intact.
2. Any codegen/normalizer/kernel/library revision changing the emitted book; source-to-book correctness is unproven unless independently attested. Repeat `bend PROOF.bend --verdict` with pinned trusted toolchain and law/proof hashes.
3. Any counterexample or mutation that refutes an intended safety property: strengthen the **owner-owned law** only after design review, then fix code/proof rather than deleting the failing requirement.
4. A new host integration with a *different meaning* for the abstract inputs must qualify its **adapter**, even if the old model proof still passes (a fresh classifier is not a theorem).
5. New schema/source revisions and memory/privacy policy changes must invalidate cached proof/permission receipts where the corresponding assumptions changed.

For true external events, maintain **separate** `MODEL_PROVEN`, `ADAPTER_PARITY`, `RUNTIME_ATTESTED` and `HUMAN_AUTHORIZED` status. A passing proof neither installs a hook nor merges an issue. The prior receipts explicitly have `source_semantics_attested=false`, so do not promote a Bend-emitted-book claim into “full Bend source/runtime certified.”

## Adversarial experiment plan, not yet run in this PR

- **Evidence trace:** every input generated from typed, bounded observations; preserve source URI, SHA, clock, identity, model+kernel versions, exact expected witness and failure status. Avoid secrets and private conversation dumps.
- **Differential:** match seeded/exhaustive transition tapes Bend ↔ Python twin. Include negative/sabotage mutations from #183/#184/#188/#192; record mutation survivor if any.
- **Concolic (proposed complement):** run concrete replay while symbolic path constraints generate missing branch pairs for stale SHA, owner identity ambiguity, expired leases, owner-off, unknown scope, false-private/unknown remote, missing device foreground, provider cancellations, duplicate events. Concolic path exploration is **not formal proof**. No concolic tool or coverage results have been verified here.
- **Verifier hardening:** reject ambient `BENDTT` overrides, arbitrary proof-file naming/import bypass, forged success substrings and TOCTOU LAWS changes. Use pinned trusted standalone `bend`, not checkout execution of arbitrary Bun scripts; `hermes bend verify/replay` receipts are useful input, not authority.
- **Runtime reality:** test complete required CI by current head; real service binary SHA vs integration head; paid provider/backends only via already-owned host policies; owner acceptance by independent fact; device interaction only after explicit owner intent, lease and fresh screen. Mark `UNKNOWN` on unavailable sensor/source.
- **Performance:** pure CPU-heavy kernels only as candidates; separate parallel CPU, GPU and full-path latency, cost, energy and determinism; do not extrapolate Bend 1/HVM to Bend2/BendRT.

## Questions for owner / OMP before a code PR

1. Which existing Draft PRs/revisions will be reconciled as canonical? Some are stacked on `preview` or another draft; do not reimplement or merge by assumption.
2. Which fixed **human-owned** laws are versioned safety invariants vs dated routing preferences (roles, providers, availability)? Approval must name exact law revision and scope.
3. Which signal authenticates owner acceptance when agents share an account? GitHub `kvnloo` authorship is insufficient.
4. How will model input classification be audited as faithfully representing real commands, GitHub refs, mobile foreground and private scope? Proof assumes this boundary.
5. What certifies Bend *source* → BendTT book as semantically faithful? Existing receipt scope is intentionally narrower than a source theorem.
6. How to get a real two-client memory correction handoff measured under #22 before requiring new Bend proof gates? Avoid using proof work to block useful continuity.
7. What are the first falsifiable pure-memory kernels and trusted exact-evidence oracles? Avoid trying to mathematically prove LLM judgment or remote scheduler liveness.
8. Who owns opt-in enforcement mode per seam? Default-branched code/PRs, z0 state projections and host actions have separate HITL.

## Primary sources and corrections

- [Bend upstream](https://github.com/bendlang/bend), [`GUIDE.md`](https://github.com/bendlang/bend/blob/main/guide/GUIDE.md), [BendTT paper](https://github.com/bendlang/bend/blob/main/paper/BendTT.pdf), [Lean kernel](https://github.com/bendlang/bend/blob/main/bend2/bendtt.lean), [BendRT paper](https://github.com/bendlang/bend/blob/main/paper/BendRT.pdf).
- [2026-10-22 zekramu X post](https://x.com/zekramu/status/2113081285707251845) contains an image of an **older 2026-08-09 post** outlining `LLM → verification engine → code`, referencing [Li, Zetzsche, Somayyajula, arXiv:2501.06283](https://arxiv.org/abs/2501.06283). That paper evaluates a **Dafny** intermediate representation, **not Bend2**. The later X post calls Bend2 underrated/security-relevant; it does **not** itself introduce concolic testing.
- [Earlier Bend/Hermes verifier issue](https://github.com/kvnloo/hermes-agent/issues/324) and [Draft PR #325](https://github.com/kvnloo/hermes-agent/pull/325); [Bend source/kernel mismatch](https://github.com/bendlang/bend/issues/1212); [source fix PR](https://github.com/bendlang/bend/pull/1215). Preserve credit of all original researchers and contributors.
- [Concolic testing] is concrete + symbolic path-condition exploration; use only for adversarial adapter/branch coverage. No argument here claims concolic results or complete program security.

**Status at authoring:** context captured, **no new PROOF executed**, no host action, no origin or default-branch writes. Owner/OMP may use this to design small separately reviewed, evidence-backed next PRs.
