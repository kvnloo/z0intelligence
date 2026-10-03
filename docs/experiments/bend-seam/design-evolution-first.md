# z0intelligence ⇄ Bend ⇄ AODL, evolution-first: proofs bound the genome, learned scores choose inside it

Date: 2026-10-03. Angle (assigned): **learning/evolution-first.** Bend is the contract that bounds what learned and
evolved candidates (Evolution Lab shadows, routing policies, SLM selectors) may do. Proofs constrain the search
space and the promotion gates. Learned scores choose inside the legal set.

This doc builds on the three sibling notes in this directory and does not repeat them: `bend-docs.md` (Bend facts),
`aodl-bend.md` (the existing AODL/Bend contract, gaps G1-G9) and `z0-decisions.md` (decision surfaces D1-D20).

Labels: **[SRC]** read in source at the pin given. **[DOC]** stated in docs or issues. **[MEAS]** measured (by me
unless stated). **[INFERRED]** my reasoning, not documented or measured.

---

## 0. Answer in one page

1. **The core move.** Each law is stated over *every possible output of the learned component*, so it never names
   a particular candidate. Bend lets a law quantify over arbitrary data, including any integer score vector. One
   proof about the **selection combinator** therefore covers **every point of the evolutionary search space**.
   Evolution Lab can mutate thresholds, scorers, mechanism choice and prompts without anyone re-proving anything,
   because the genome only fills in the free parameters of a combinator that is already proven. This is the
   eBPF-verifier / `sched_ext` shape that the AODL profile already names (`profiles/searchable-policy-kernel.md`
   L190): the mechanism is fixed and verified, the policy plugs in and is searched, and fallback is the default
   mechanism. [INFERRED design; SRC for the parts]
2. **It works in Bend today.** In `scratch/evo-probe/v2/` I stated this law: "for every candidate list with
   arbitrary integer scores, the selected id is in the legal-id set the compiler produced, provided the fallback is
   in it". I proved it, and `--verdict` passes on official 2.0.34 (bin `7fafb749`, kernel `a7e5203d`) in about
   0.13 s. A mutant that forgets the legality mask, which is exactly the shape of Evolution Lab's current
   `RouterModel.predict`, is rejected. A concrete counterexample run on the mutant selects the illegal id. [MEAS, §3]
3. **Two things are proven, and everything else is chosen.**
   - **(L) Legality compilers** turn AODL Γ and authority plus the runtime state into a legal set. These already
     exist as pure Python functions: `_action_space`, `compile_actions`, `filter_candidates`, and the legality part
     of `plan_route`.
   - **(S) Selection combinators** turn (legal set, learned integer scores, deterministic fallback) into one action.
     There are four, and each is a few lines: masked argmax, threshold gate, tier clamp, and projection of an LLM
     answer.
   - The laws of (L) discharge the premises of (S). For example, "ABSTAIN is always legal" (L) is exactly the
     fallback premise that `pick_legal` (S) needs. My probe shows that premise is load-bearing.
   - Everything learned (Jev scores, SLM picks, q_route arms, ACT-gate probabilities, escalation preference,
     mechanism choice) enters only as **data** into (S).
4. **Process model.** Unchanged from the sibling notes: **Python is the only runtime authority, in-process, at µs
   cost.** Bend runs in four places only:
   - in CI, `--verdict` through bend-native, which produces receipts;
   - in CI, a parity check that runs Python against the native Bend kernel in batch;
   - optionally inside Evolution Lab, as a batch "envelope replay" over population × opportunities, which is the
     one regime where Bend's runtime might pay off (unmeasured);
   - as a nightly asynchronous shadow cross-check.

   There is no Bend process on any hot path. This follows #48's promotion rule (§7 of `bend-docs.md`) and the
   performance trace (`bend-perf/trace.md`).
5. **What evolution-first adds to the gates.** These are the changes to the promotion ladder:
   - A candidate artifact declares which proven combinator it plugs into (`combinator_id`) and which proof it was
     trained against (`spec_hash`). Its **genome schema is derived from the combinator's free parameters**, so a
     genome that tries to emit an action directly, or to touch legality, has no representation.
   - Promotion past `shadow` requires a current verdict receipt and a current parity receipt for that `spec_hash`,
     and zero envelope violations in shadow. Zero is guaranteed by construction, so any nonzero count is a tripwire
     for Python/Bend drift.
   - A change to the spec, to the Bend identity or to the AODL envelope **invalidates** the candidate and forces
     deoptimization to the deterministic gate, per #56.
6. **Model/provider choice carries down as follows.** "Which model" splits into three layers:
   - **eligibility**: AODL Γ, privacy, authority and budget become the eligible-arm set. This is proven (family C)
     and is where AODL constraints actually land.
   - **choice among eligible arms**: learned, through masked selection. This covers the q_route arm, the SLM tier,
     and the provider order inside the free-only set.
   - **placement**: Kerdoios, residual, not in Bend.

   The evolved selector rides in AODL `plan.bindings` as an artifact id. Γ stays the authority envelope. This only
   holds if AODL fixes one thing: intent identity must **exclude `plan`**. Otherwise every promotion changes the
   contract fingerprint (§6.3).
7. **The hard prerequisites are not Bend work.** The legal set is only as strong as the contract it was compiled
   from:
   - G3: callers can supply their own permissive contract;
   - G4: v2 requests bypass admission;
   - G1: float budgets;
   - G6: split canonical identity;
   - a new hazard: **evolved state construction can blind the legality compiler** (§5.3).

   Proofs about a compiler fed a forged envelope are vacuous. Fix these first (§9 migration, steps 0-2).

---

## 1. What I read beyond the sibling notes (pins)

| Source | Pin | What it contributed |
|---|---|---|
| kvnloo/evolution-lab `trunk` | `/mnt/zer0models/z0-wt/evolution-lab` @ `1c9b523` | `zer0.repo.yaml` subsystems and boundaries; `export_z0int.py` (`z0int.candidate_artifact.v1`, always `promotion.status=candidate`); `q_route/distill.py` `RouterModel.predict` L69-74 (**unmasked argmax over all arms**); `q_route/gate.py` `compile_regions` L292 (cheapest passing arm owns a region) |
| evolution-lab `exp/verified-loop-v0-run` | `/mnt/zer0models/z0-wt/evolution-lab-verified-loop` @ `1b80a4e` | `docs/prereg/verified-loop-v0.md` L36-38: "**ACT only if `legal.ACT == 1`** … Legality … is authored and is not learnable. A learned policy can only withhold ACT; it can never grant it." `verified_loop.py` L83-84 `legal_act`, L305/L315 `never_acts_when_illegal` |
| z0intelligence `origin/master` | `0159808` | `cognition/candidates.py` `filter_candidates` L291 (hard gates, deterministic order) vs `rank_candidates` L363 (float suitability score); `cognition/shadow.py` L386-391 and `cognition/cascade.py` L547-560 (an out-of-set selection is recorded as an **illegal call**, never a selection); `decision_opportunity.py` `_action_space` L119-141, `deterministic_gate` L197, `with_authority_grant` L206; `artifacts.py` (`candidate_artifact.v1` import, `ALLOWED_RUNTIMES`, refuses producer-side `promoted`); `cascade.py` `promote` L388, `observe_future` L392-411; `routines.py` `promote_credited` L533; `aodl.py` `_stage_binding` L231-240, `binding_for_implementation_stage` L800 |
| z0intelligence `origin/integrate/wiring-20261003` | `e02bcf5` | `loop_export.py` L32 `z0int.loop.training_row.v0`, L64 `legal.{ACTION}` features, L150-156 (legal bits copied from `action_space`) |
| kvnloo/aodl main | `416736c` | `profiles/searchable-policy-kernel.md` L7 (mechanism/policy split), L82-90 (Π_{t+1} = Promote(Pareto{c ∈ C : Γ(c)=pass})), L143-150 (invariants 1-6), L174-184 (bad approximation targets), L190 (`sched_ext` analogy) |
| Issues (read-only dumps in `scratch/issues/`, extracted to `scratch/evo-issues-*.txt`) | | #56 body + 2026-10-03 addendum (stages 1-9, genome, safety invariants, milestones M0-M5); #59; #55 (non-goal: "using model confidence as permission"); #53 (`evidence ≠ belief ≠ authority ≠ action ≠ outcome`); #20 ("The SLM should choose among **already legal** actions"); #48 (non-goals: "no Bend-based LLM router", "no provider/quota logic in Bend", "**no automatic policy synthesis**") |
| z0 registry | `/mnt/zer0models/z0-wt/ro/z0` @ `7933914` | ownership: evolution-lab owns "experiment search, locked corpora and splits, candidate evolution, promotion evidence", not "production runtime"; z0intelligence owns "bounded decision backends, routine promotion, specialist runtime", not "experiment promotion"; kerdoios owns "provider routing, allocation policy"; aodl owns "typed intent/plan contracts"; `suite.yaml` L315-325 `bend` = `verified_compilation_substrate`, `external_substrate`, `experimental` |
| z0evals | `/mnt/zer0models/z0-wt/ro/z0evals` @ `fb14919` | not_here: "model routing policy", "experiment optimization", "runtime verification authority" |
| Bend upstream | `src/bend` @ `a950fd68` | GUIDE L57-59 (`is Data` / `+` reuse), **L83-85 ("A closure is affine: it can be called at most once")**, L128-170 (balanced fork-join; a shared `+` value costs an atomic per read), L279-341 (laws, `--verdict`); laws can quantify over functions (`bench/checker/generics_3200/main.bend` L56 `for f: A -> B`; BendTT paper `main.typ` L317) |
| Perf triage | `/mnt/zer0models/z0-wt/wiring/bend-perf/` | `results/modes-r1.json` and `.err` still **0 bytes** at 12:18 (mtime 11:53). `trace.md` (read-only trace): the "4-25x" was a native `-o` binary on a persistent pipe; the README table supports about 6x (docs) and 23-25x (spawn); the "4" matches no row. **Not a misconfiguration; structural** |

---

## 2. The evolution-first principle, stated precisely

AODL already writes down the loop (searchable-policy-kernel L82-90):

$$\Pi_{t+1} = \operatorname{Promote}\left(\operatorname{Pareto}\{c \in C : \Gamma(c)=\text{pass}\}\right)$$

Today `Γ(c)=pass` is checked **per candidate, empirically**, in two ways:
- the shadow and cascade code drops out-of-set picks at runtime (`shadow.py` L386-391, `cascade.py` L547-560);
- verified_loop counts `illegal_act_rows` and requires 0 (`verified_loop.py` L305).

That is a test, not a guarantee. It holds only for the rows observed, and it is re-established for every
candidate.

The evolution-first design moves `Γ(c)=pass` from a property of each candidate to a **property of the candidate
family**:

```
candidate c  =  combinator K  applied to  genome g          (K fixed, proven; g searched)
law(K):  ∀ state, ∀ g ∈ Genome(K):  K(legal(state), g) ∈ legal(state) ∪ {fallback(state)}
law(L):  fallback(state) ∈ legal(state)                      (e.g. ABSTAIN always legal)
⇒        ∀ c ∈ C(K):  Γ(c) = pass                            (by construction, all of C at once)
```

Consequences:

- **The proof constrains the search space.** `Genome(K)` is exactly the set of free parameters K accepts:
  integer scores, thresholds, an arm preference, an escalation preference. Evolution Lab's mutation operators may
  only produce values of that schema. A genome that names an action directly, edits the legal set, widens
  authority, or changes the fallback **is not representable**. It is not merely "rejected later".
- **The proof constrains the promotion gate.** The gate no longer needs to establish legality for each candidate.
  It checks:
  - (a) the artifact's `combinator_id` is a proven combinator under the current `spec_hash`;
  - (b) the receipts for that `spec_hash` are current;
  - (c) shadow showed 0 envelope violations. That count is a tripwire for implementation drift, not the source of
    the guarantee.
- **Learned scores choose inside the set.** Ranking, calibration, uncertainty, cost-awareness, and Pareto
  trade-offs are all outside Bend and fully searchable. This matches #20 ("choose among already legal actions"),
  #55 (confidence is never permission), searchable-policy-kernel invariant 4, and #48's non-goal "no automatic
  policy synthesis". Bend never synthesizes a policy; it only fixes the shape that every policy must have.

Two Bend facts force the data shape [DOC + MEAS]:
- **Closures are affine** (GUIDE L83-85). A law may quantify over a function, but live Bend code cannot call a
  learned scoring closure once per candidate. The learned contribution therefore enters as a **data vector of
  integer scores**, one per candidate. Data is copyable with `+` (GUIDE L57-59).
- **F32 is axiomatic** (README L230-231). Scores and thresholds are discretized by the host (for example permille
  ints) before the combinator sees them.

Both fit the existing code. `rank_candidates` already produces a `round(score, 6)` float that maps
deterministically to an int.

---

## 3. Evidence: the selection law in Bend [MEAS]

All runs used `flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock` with `BEND_NO_TELEMETRY=1`,
`BEND_HUB`/`BEND_LIB`/`BEND_ORIGIN` unset, and HOME/TMPDIR in `scratch/evo-probe/`.

For `BENDTT` I used a **private copy of the kernel**, pinned by SHA: `scratch/evo-probe/kernel/bendtt`, sha256
`a7e5203de58d…`. That is the official 2.0.34 kernel identity in SYNTHESIS.md L34, copied read-only from
`/mnt/zer0models/cua-lane-tmp/verify-e2e-r1/bendhome/.bend/bendtt/61e0d2d9f4ddd7dd/bendtt`. Using a private pinned
copy is the #388 mitigation. The binary was `/mnt/zer0models/bend-stack/official/bend/bin/bend` (sha256
`7fafb749dd33…`).

| Run | Files (sha256[:16]) | Result |
|---|---|---|
| v1: legality as a bit carried by the candidate | `scratch/evo-probe/{select,LAWS,PROOF}.bend` | `--check-only` then `--verdict`: **ALL PROOFS CHECK**, exit 0, 129 ms wall. **Design flaw:** a buggy combinator can *launder* the bit by constructing `Cand{i, True, s}`, and the law still holds. Lesson: **legality must be membership in a set computed independently, never a field the selector returns** |
| v2: legality = `mem(lg, id(pick(...)))` against an independent legal-id list | `v2/select.bend` `bfe106436fa4b6bb`, `v2/LAWS.bend` `244123b1fe46d43f`, `v2/PROOF.bend` `eab5d43741b69b26` | `--verdict`: **ALL PROOFS CHECK**, exit 0 |
| M1: v2 with the mask removed (`choose.go(True{}, …)`, the `RouterModel.predict` shape) | `m1/select.bend` `45c06dd368bd8bcf` | `--verdict`: **FAIL** at `choose_ok` (exit 1) |
| M1 counterexample | `m1/cex.bend`: legal {0}, learner gives illegal id 9 the top score, fallback 0 | mutant selects **9** (illegal); v2 on the same input selects **0** |
| Is the fallback premise load-bearing? | `v2/cex2.bend`: no candidates, fallback id 9 ∉ {0} | selects **9**. The premise `T(mem(lg, id(a)))` is required; family L must discharge it ("ABSTAIN always legal") |

The law as proven (`v2/LAWS.bend`):

```bend
law pick_legal:
  for cs: List<S.Cand>          # any candidates with ANY integer scores (the learned part)
  for +lg: List<&2, Nat>        # legal ids from the deterministic compiler
  for +a: S.Cand                # deterministic fallback
  for w: T(S.mem(lg, S.id(a)))  # premise discharged by family L
  T(S.mem(lg, S.id(S.pick(cs, lg, a))))
```

Scope of this evidence:
- It shows that the **shape** is expressible and provable in Bend 2 with modest effort (first try for v2,
  4 helper lemmas).
- It is a theorem about the BendTT book emitted by **stock 2.0.34**, which mis-certifies #1212. This probe has no
  mutually recursive groups, but it must be re-verified on the qualified build (17db447a or a qualified 2.0.35)
  before any receipt counts.
- It says nothing about the Python `select()` that would run. That link is parity (§7).

---

## 4. Architecture: planes, seams, process model

```
 AODL (owner-authored; aodl owns)                          Evolution Lab (search; evolution-lab owns)
  Γ: authorityCeiling, budgets (integer units), privacy      genomes over Genome(K) only
  policies.route (abstract order)                            offline replay of training tables
  plan.bindings: stage -> candidate_artifact_id   ◄───────── candidate_artifact.v1 (+ envelope block)
  to_core() + intent fingerprint (excl. plan)                     ▲ selection reads z0evals score API only
        │ S0: Envelope (frozen, fingerprinted)                    │
        ▼                                                         │ S4: training_row.v0 (+ legality_spec_hash)
 z0intelligence runtime (Python, in-process, authoritative)      │
  ┌───────────────────────────────────────────────────────┐      │
  │ S1 legality compilers (family L, proven twins in Bend)│      │
  │   _action_space · compile_actions · filter_candidates │      │
  │   plan_route legality · escalation floor/ceiling      │──────┘
  │        │ LegalSet{ids, eliminated, spec_hash}          │
  │        ▼                                               │
  │ S2 selection combinators (family S, proven in Bend)   │◄── S3 learned scorers (champion + shadow
  │   masked_argmax · threshold_gate · tier_clamp ·        │     challengers): Jev, SLM, q_route, ACT gate
  │   project_answer                → one action           │     emit ONLY integer score vectors / τ
  │        │ decision receipt (+ extra.envelope)           │
  └────────┼───────────────────────────────────────────────┘
           ▼  Kerdoios placement (capacity/quota; residual)  ▼  dispatch_authority (claim, admission, call)

 Bend (never on a hot path; bend-native is the verifier)
  CI:       bend PROOF.bend --verdict per family  -> verdict receipt (qualified build only)
  CI:       Python S1/S2 vs native kernel, batch   -> parity receipt (corpus by fingerprint)
  Offline:  optional native "envelope replay" in Evolution Lab (population x opportunities)
  Nightly:  sampled decision receipts replayed through the kernel -> drift receipt on mismatch
```

### 4.1 Seam table

| Seam | Interface (in → out) | Who executes | Where proofs are checked | Process model |
|---|---|---|---|---|
| **S0 Envelope** | AODL doc → `Envelope{intent_fp, grants[], budgets{dim: int}, privacy, route_order, bindings{stage: artifact_id}}` | z0int, through `aodl_contract` (`validate`, `to_core`, fingerprint) | AODL's own tests; Bend family A laws over Core IR (existing gate) | in-process; fail closed to read-only (`_authority` already does: `decision_opportunity.py` L35-52) |
| **S1 Legality (L)** | `(Envelope, state snapshot) → LegalSet{ids: tuple[str], eliminated: [(id, reason)], spec_hash}` | Python: `_action_space`, `compile_actions`, `filter_candidates`, `plan_route` legality | `LAWS.bend` per family, `--verdict` in CI | in-process µs; already pure |
| **S2 Selection (S)** | `(LegalSet, scores: {id: int}, fallback_id) → id` and variants (§5.2) | Python `select.py`: a few lines per combinator, the single choke point | `pick_legal`-style laws, `--verdict` in CI | in-process µs |
| **S3 Scorers (learned)** | `(features) → scores {id: int}` or `τ`/`p` (permille) or a tier preference | z0int backends: Jev, SLM adapters, routine/cascade, promoted artifacts | **none.** Unprovable by design; bounded by S2 | champion in path; challengers in the #56 stage-4 shadow slot, inert |
| **S4 Training export** | turns → `training_row.v0` (counts, `legal.*` bits, `gate`) + manifest | z0int `loop_export` | parity: Bend batch recompute of `legal.*` from row counts (§7.3) | offline |
| **S5 Candidate import / promotion** | Evolution Lab `candidate_artifact.v1` (+`envelope`) → z0int `~/.z0int/specialists/<id>` status=candidate → `[sanity, replay, shadow, promoted]` | z0int `artifacts.py` + the #56 promotion code; owner approval | receipt checks only (no Bend run) | offline / owner-gated |
| **S6 Invalidation** | `{spec_hash, bend_identity, intent_fp, python_rev}` change → demote + fallback to the deterministic gate | z0int (#56 stage 8) | n/a | event-driven; reuses `observe_future` demotion |

**Where proofs are checked vs where decisions execute.** Proofs are checked only in CI and promotion tooling, by
bend-native. Decisions execute only in Python. The two are joined by **hash identity**:
- `spec_hash` = bend-native's input-manifest hash of the family's LAWS + kernel + PROOF closure, **plus**
  `main.bend` if one exists (closes P-12);
- `python_impl_rev` = sha256 of the Python S1/S2 source files.

A parity receipt binds the two hashes. A decision receipt names both. Nothing else links proof to runtime, and
nothing else is needed. This closes G7.

---

## 5. Proof families and the genome contract

### 5.1 Family L: legality compilers (the "what is legal" side)

These exist today as pure Python. Each Bend twin is a reference kernel over the Core IR plus laws. Law sketches
below are [INFERRED]; only family A exists.

| Family | Python authority | Laws (sketch) | Discharges |
|---|---|---|---|
| **A** AODL admission (exists, `exp/bend-aodl-gate` a0e9578) | `aodl_admission.decide_spawn` | 8 proven laws (revision, dynamic, bounds, exact-Nat budget, no invented authority, antitone spend, no privileged grant, allow⇒parse) | Γ envelope for spawns |
| **B** opportunity action space | `decision_opportunity._action_space` L119, `deterministic_gate` L197, `with_authority_grant` L206 | `ACT ∈ legal ⇔ blocking=∅ ∧ contested=∅ ∧ effects ⊆ grants`; **`ABSTAIN ∈ legal` always**; grant monotone (adding a grant never removes a legal action); `ESCALATE ∈ legal ⇔ contested≠∅` | fallback premise for S-gate; "authority only widens by a named user grant" |
| **B'** legal-action compiler | `cognition/actions.compile_actions` | output ⊆ input; every legal action's `risk_class ∈ authority`; authority monotone | premise for `masked_argmax` over tool/actions |
| **C** model/arm eligibility | `filter_candidates` L291 + `plan_route` legality + `intelligence.route` egress | C2-C9 of `bend-docs.md` §10.4: free_only ⇒ validated $0 route; local ⇒ no remote; cap 0 ⇒ ineligible; quality/risk floors; integer budget admission; empty ⇒ fail closed; deterministic order. **New from AODL:** `privacy=local_only` ⇒ no remote arm; `arm.cost > remaining budget` ⇒ ineligible | premise for `masked_argmax` over models/arms |
| **D** escalation bounds | `cognition/escalation` + `surface.py` | `floor(risk, verification_required, novelty)` is monotone; `ceiling(privacy, allow_remote)` is antitone in restriction | premise for `tier_clamp` |
| **E** promotion lifecycle | `cascade.promote` L388, `routines.promote_credited` L533, `observe_future` | only `credited → promoted`; `demoted` is never executed; an invalidated artifact maps to the fallback | S5/S6 gate shape |

### 5.2 Family S: selection combinators (the "learned part plugs in here" side)

Every learned decision in the codebase today reduces to one of four shapes:

| Combinator | Learned input (genome-controlled data) | Law (∀ learned input) | Today's code it replaces / wraps |
|---|---|---|---|
| `masked_argmax(legal, scores, fallback)` | `scores: {id: int}` | result ∈ legal ∪ {fallback}; ties broken by fixed id order (determinism) | **proven in the probe** (`pick_legal`). Wraps Jev scores, `rank_candidates`, **q_route `RouterModel.predict`** (unmasked today, distill.py L69-74), SLM tool choice |
| `threshold_gate(legal_act, p, τ, fallback_chain)` | `p, τ: int` (permille) | `ACT ⇒ legal_act`; non-ACT result = first legal in `ASK > OBSERVE > ABSTAIN` | the verified_loop prereg rule, already in code (`verified_loop.py` L83-93); the #55 learned gate |
| `tier_clamp(floor, ceiling, preferred)` | `preferred tier: int` | `floor ≤ result ≤ ceiling`, or `FAIL_CLOSED` (ASK/ABSTAIN) when `floor > ceiling`; monotone in floor | `EscalationPolicy.decide` jumping to `remote_frontier` without a privacy input (D13 gap, `z0-decisions.md` finding 4) |
| `project_answer(legal, answer_id, fallback)` | free-form LLM/SLM output parsed to an id | result ∈ legal ∪ {fallback}; an out-of-set answer is recorded as an illegal call | `shadow.py` L386-391, `cascade.py` L547-560 (already this behavior; the law makes it the spec) |

These are all tiny. That is the point: the proof effort concentrates on a handful of fixed shapes, and the
search space behind them is unbounded.

### 5.3 The genome contract (what Evolution Lab may mutate)

#56 lists mutation targets, cheapest first. Mapped onto the combinators:

| #56 genome item | Enters as | Allowed? | Constraint [INFERRED unless noted] |
|---|---|---|---|
| 1. Gate thresholds per family | `τ` in `threshold_gate` | **free gene** | proven for all τ; verified_loop already enforces it (prereg L36-38) [SRC] |
| 2. State construction (z0int#28) | features for the **scorer** | **free gene for scoring only** | **Hazard:** `_action_space` legality depends on `unknowns` and `contradictions`. A genome that reads fewer evidence families detects fewer contradictions and could make `ACT` legal while a conflict sits in unread evidence. **Rule: the legality compiler always reads the canonical (non-evolved) state construction; evolved state construction feeds only S3 features.** Evolution may make the scorer blind, but never the compiler |
| 3. Mechanism choice per family (el#25) | an arm preference in `masked_argmax` over family-C eligible arms | **free gene** | arm eligibility (egress, privacy, free_only, budget) is family C; the gene only ranks eligible arms. q_route's `compile_regions` (gate.py L292) is a deterministic challenger of this shape |
| 4. Prompt / question wording | changes an SLM's answer distribution | **free gene** | output goes through `project_answer`; never reaches the legal set |
| — Legal-set compiler, authority, fallback, Γ, judge (z0evals), combinator code | — | **forbidden** (unrepresentable) | #56 non-goal "allowing the learner to change its sealed judge"; AODL invariant 4; #48 "no automatic policy synthesis" [DOC] |

Mechanically, a `genome_schema` is derived from `combinator_id`. Each combinator publishes its free-parameter
schema; for example `masked_argmax.v1 → {scores: map<id, int[0..1000]>}`. Evolution Lab's `mutate.py` operators
and `export_z0int.py` validate against it. z0int import refuses an artifact whose declared output type is not the
combinator's input type. Today `export_z0int.py` exports a file blob plus metrics, and `artifacts.py` validates
only hashes, runtime and status [SRC]. The envelope block (§7.1) is the addition.

---

## 6. Model/provider selection: how AODL constraints carry down

### 6.1 Decomposition

```
AODL Γ / privacy / authority / budgets ──► family C eligibility  (PROVEN: which arms MAY run)
                                                 │ eligible arms (ids)
plan.bindings[stage] = artifact_id ─────► S3 scorer (LEARNED: which eligible arm is preferred)
                                                 │ scores {arm: int}
                                       masked_argmax / tier_clamp (PROVEN combinator)
                                                 │ chosen arm (model class / tier / provider order)
                                       Kerdoios placement (capacity, quota, co-residency; RESIDUAL)
                                                 │
                                       dispatch_authority + require_free_route recheck (EXECUTION)
```

### 6.2 What carries down, field by field

| AODL field (owner-authored) | Lands in | Today [SRC] | Change [INFERRED] |
|---|---|---|---|
| `intentGraph[*].authorityCeiling` | family B grants, family B' risk classes | feeds `_authority` (D14) and `decide_spawn` (D7) | also the source of `requested` capabilities in the governed spawn, instead of the constant `["execute"]` (governed_worker L251) |
| `constraints.budgets` (integer units) | family C `arm.cost ≤ remaining`; family A spend | only `tokens` observed (G5); `usd`/`joules` as floats (G1) | AODL integer units (micro-USD, mJ); observed spend for every declared dimension |
| privacy (`public / confidential / local_only`) | family C (no remote arm) and family D `ceiling` | not read by z0int routing; read by Kerdoios (`kerdoios/aodl.py` `_PRIVACY`) | one envelope field consumed by `intelligence.route`, `plan_route`, `EscalationPolicy` and memory injection, replacing the `'local-only '` text-prepend trick (D1) |
| `policies.route` (abstract order) | arm/tier vocabulary for `tier_clamp` | compiled, unused at runtime (D9) | vocabulary only; never a ranking |
| `plan.bindings[stage]` | **which promoted artifact supplies the scores** | `_stage_binding` leaves provider/model `"unbound"` on purpose (aodl.py L231-240); no runtime caller of `binding_for_implementation_stage` (L800) | binding = `{implementationStage, combinator_id, artifact_id, spec_hash}`; Evolution Lab changes this, never Γ |

### 6.3 One AODL decision this angle forces (owner: AODL)

`aodl-canon-1` (pinned by z0int, 6823165) **keeps `plan`** in the semantic fingerprint. `aodl-core-v1` (40e3f97)
**excludes it** (`aodl-bend.md` §1, G6). Under evolution-first, a promotion rewrites `plan.bindings`. With canon-1:
- every promotion changes `aodl_semantic_fingerprint`;
- parity corpora named by that fingerprint go stale on every promotion;
- receipts would claim that the *intent* changed when only the *implementation* did.

This contradicts `docs/aodl-integration.md` ("so Evolution Lab can change an implementation without silently
redefining user intent"; quoted in `z0-decisions.md` D9). Recommendation [INFERRED]:
- the **envelope identity** is a fingerprint that excludes `plan`, `eventLog` and `observedGraph` (core-v1-style);
- **binding identity** is the `artifact_id` plus `spec_hash` carried in the binding;
- both are recorded.

This is an aodl#39 / G6 decision, not z0int's.

### 6.4 What stays outside Bend for model choice

Regex task classification (`plan_route` `re.search`), float suitability scoring, provider health, quota windows,
Kerdoios Pareto placement, and `free_route` evidence-hash verification all stay out. The last one is an effectful
lookup that S1 consumes as a boolean per arm.

---

## 7. Receipts, replay, provenance

### 7.1 New and extended fields (no new ledger; extends existing schemas)

| Record | Field(s) added | Purpose |
|---|---|---|
| bend-native verdict receipt (exists) | none new; must include `main.bend` when present (P-12) | `spec_hash`, bin/kernel SHA, verdict, offline replay (`hermes bend replay`) |
| **parity receipt** `z0int.envelope_parity.v1` (new file type, CI artifact) | `family`, `spec_hash`, `python_impl_rev`, `bend_identity{bin, kernel}`, `corpus{training_table_manifest_sha[], shadow_receipt_range, fuzz_seed, n}`, `disagreements{false_legal, false_illegal, other}`, `mode: native-batch` | ties the proven kernel to the running Python |
| `candidate_artifact.v1` manifest (evolution-lab export → z0int import) | `envelope{family, combinator_id, genome_schema_sha, spec_hash, trained_on_tables[], intent_fp_cohort}` | the candidate declares which proof it plugs into; import refuses unknown combinators or a stale spec |
| `training_table_manifest.v0` (S4) | `legality_spec_hash`, `python_impl_rev`, `feature_schema_sha` (exists as `FEATURE_SCHEMA_SHA`, loop_export L75) | Evolution Lab replays legal masks from the same spec that produced them; tables from different specs are not pooled |
| decision receipts (`z0int.decision_receipt.v1`, cognition receipt v1, shadow receipt v1) | `extra.envelope{spec_hash, verdict_receipt_sha256, parity_receipt_sha256, intent_fp, combinator_id, artifact_id \| "deterministic"}` | every runtime decision names the proven spec it implements; mirrors `aodl_admission_receipt_id` linkage (dispatch-authority.md L52-55) |
| promotion record (#56 stage 7) | `envelope` block + `shadow_envelope_violations: 0` + the receipts above | the owner approves a candidate with its full proof lineage |
| drift receipt (nightly cross-check) | `{decision_receipt_id, python_result, kernel_result, spec_hash}`, deny-class | any mismatch demotes the family's promoted artifacts (S6) |

### 7.2 Replay

- **Verdict replay:** `hermes bend replay`, offline, with the hub disabled. A stale receipt shows up as
  `receipt_stale` (#390).
- **Decision replay:** the decision receipt carries `legal_ids` (cognition receipts already do, receipts.py L171),
  the integer scores and the fallback. Replaying the Python combinator, or the Bend kernel, over those reproduces
  the decision bit for bit. This is the determinism law C10.
- **Evolution replay:** Evolution Lab replays a genome over a training table using the table's `legal.*` masks and
  the same combinator semantics. Because the combinator is fixed, the offline result is the result the runtime
  would produce, up to S3 feature parity.

### 7.3 Where Bend's runtime is used, and only where it might pay

- **Parity in CI:** a native `-o` binary run **once per batch** over a corpus file, not per request. This avoids
  the per-call IPC floor (about 70 µs) and the per-call Python thread (`bend_gate.py` L619-632) entirely.
- **Training-table mask recompute [INFERRED feasible].** From the row's own counts the kernel can recompute
  `legal.ACT = (n_unknowns_blocking=0 ∧ n_contradictions=0 ∧ n_missing_authority=0)`, `legal.ESCALATE`
  (n_contradictions>0) and `legal.ABSTAIN` (always) (loop_export L140-156 vs `_action_space` L119-141).
  - `legal.ASK` and `legal.OBSERVE` are **not** recomputable from the row: ASK depends on per-unknown status
    restricted to blocking unknowns, and OBSERVE on the observe list. Either they are left out of the mask parity,
    or the row adds `n_unknowns_blocking_askable`. That is a TABLE_VERSION bump.
  - This is privacy-safe: counts only.
- **Evolution Lab envelope replay (optional, measure first).** Population × opportunities × candidates is the one
  workload that is pure, sizeable and splits into balanced halves, which is where Bend measured 9-12x on 16
  threads (`bend-docs.md` §7.1). Two cautions: the kernel's byte scan costs about 1 µs/token (gate README), and a
  `+` value read by every lane costs an atomic per read (GUIDE L166-168). Build it only if a quiet-timed
  measurement beats the Python replay. Otherwise keep Python replay plus sampled parity.

---

## 8. Failure modes (every path fails closed to the canonical mechanism)

| Failure | Detected by | Effect |
|---|---|---|
| Learned scorer crashes, times out, emits garbage or an out-of-set id | S2 combinator / `project_answer` | fallback id (the deterministic gate result, or ABSTAIN); recorded as an illegal call; already the behavior in `cascade.py` / `shadow.py` |
| Legal set empty | family L/C law "empty ⇒ fail closed" | no model contacted; ASK/ABSTAIN; free-only never spills to paid (#20 comment 2026-09-21) |
| `floor > ceiling` in `tier_clamp` (risk needs a frontier but privacy forbids remote) | family D | FAIL_CLOSED → ASK the user (who can grant), never a silent remote call |
| Artifact `spec_hash` ≠ current, or `combinator_id` unknown | S5 import / S6 | artifact stays `candidate` or is demoted; runtime uses the deterministic gate |
| Verdict or parity receipt missing or stale for the live `spec_hash` | promotion tooling; `/readyz`-style check [INFERRED] | no promotion past `shadow`; already-promoted artifacts of that family deoptimize (#56 "Deoptimization is mandatory") |
| Bend release changes (new bin/kernel SHA) | qualification gate (#389) | receipts on the new identity are not accepted until B389 passes; old receipts stay valid **only** for their recorded identity |
| Nightly kernel ≠ Python on a sampled decision | drift receipt | demote the family's promoted artifacts; open an issue; Python remains authoritative in the meantime |
| Envelope not trustworthy (G3 self-supplied contract, G4 v2 bypass) | host-pinned contract check (migration step 1) | deny; without this, **every** proof downstream is vacuous |
| Nat ≥ 2^48 or a fractional budget reaches the kernel | host range guard (existing `NAT_MAX` 2^47) | parity harness counts it as `unrepresentable`, never as agreement; runtime is unaffected (Python) |
| Shadow challenger affects the turn | #56 invariant "shadows are inert" | a bug; covered by existing shadow-safety tests (SYNTHESIS §4 H1-H6) |

---

## 9. Migration from today's code (ordered; each step is small and reversible)

| # | Step | Owner (repo map) | Gate / evidence |
|---|---|---|---|
| 0 | Qualify the Bend trust root: run B389 on 2.0.35 (contains the #1212 fix e1ed2435) or promote 17db447a; identify by bin+kernel SHA; per-release qualification | bend-native / hermes #389 (owner decision) | B389 corpus pass |
| 1 | Close envelope forgery: pin or allow-list contracts by intent fingerprint at the authority (G3); retire or mark `ungoverned` v2 claims (G4) | z0intelligence (#13, #47) | test: a self-supplied permissive contract is denied |
| 2 | AODL: integer budget units (G1); envelope fingerprint excludes `plan` (§6.3); merge `to_core()` (aodl#19/#39) | aodl | AODL tests; z0int pin bump |
| 3 | Extract the S2 combinators into one z0int module (`cognition/select.py` [INFERRED name]) and route the existing callers through it: `cascade.py` L547-560, `shadow.py` L386-391, the verified_loop rule, `rank_candidates` (int scores) | z0intelligence | behavior-identical: existing tests green; receipts unchanged except `extra.envelope.combinator_id` |
| 4 | Bend family S package: promote `scratch/evo-probe/v2` to `bend/select/` with all four combinators + laws; bend-native verify on the qualified build; parity vs `select.py` (batch native) | z0intelligence `bend/` (agent-written proofs; **LAWS.bend owner-reviewed**) | verdict + parity receipts |
| 5 | Family B package (`_action_space`, ABSTAIN-always-legal, grant monotonicity) — discharges the S fallback premise | z0intelligence | same |
| 6 | Extend `candidate_artifact.v1` with `envelope`; Evolution Lab `export_z0int.py` writes it; z0int `artifacts.validate_manifest` refuses unknown combinators or a stale `spec_hash`; wrap q_route `RouterModel` exports so they emit scores, not argmax | evolution-lab (export) + z0intelligence (import) | import test: an artifact without an envelope stays `candidate` only |
| 7 | Add `legality_spec_hash` to `training_table_manifest.v0`; Bend batch mask-parity over exported tables | z0intelligence (export); Evolution Lab consumes | parity receipt per table |
| 8 | Family C package (model/arm eligibility incl. AODL privacy and budget) + wire the envelope into `intelligence.route` / `plan_route` / `EscalationPolicy` as an immutable snapshot field | z0intelligence; Kerdoios unchanged | D1/D2/D13 tests; parity |
| 9 | #56 stage 7: the promotion ladder requires current receipts + 0 shadow envelope violations; stage 8: S6 invalidators wired to `observe_future` demotion | z0intelligence | M5 deoptimization drill (z0evals#74) includes "spec_hash bump ⇒ demotion" |
| 10 | (Optional, measured) Evolution Lab native envelope replay | evolution-lab | quiet-timed comparison vs Python replay; keep only if faster |

Sequencing note: steps 3-5 do not depend on 0-2 to be *built*, but **no receipt from them counts** until steps 0-2
hold. Without 0, the trust root is unqualified. Without 1, the envelope can be forged. Without 2, the proven domain
disagrees with the runtime domain.

Current blocker for the evolution angle (#56 addendum): label volume. el#24 v0 had 6 analysis rows
(`INSUFFICIENT_DATA`). The proof work here does **not** need labels, so it can proceed in parallel. It is also
cheap: the probe took one iteration.

---

## 10. What NOT to put in Bend

- **No learned model, scorer, threshold search, Pareto/MAP-Elites selection or genome mutation.** That is
  Evolution Lab's surface. Bend is not an optimizer, and has no float semantics (F32 axiomatic).
- **No ranking or "which model is best".** Bend proves which arms *may* run, never which one *should* (#48 "no
  Bend-based LLM router").
- **No policy synthesis** (#48 non-goal). Bend fixes the shape a policy plugs into; it never generates the policy.
- **No legality computed from evolved state.** The legal compiler reads canonical state only (§5.3).
- **No hot-path IPC.** No per-request Bend process; no Python→Bend call inside a turn. (Revisit only if a C
  library target (#813) or a Python target ships. Keep kernels `step(state, event) -> decision`-shaped for that.)
- **No JSON or text parsing, regex classification, provider health, quota, placement, credentials, ledgers,
  locks or IO** in the proof closure. Effects live in a shell file that `PROOF.bend` does not import.
- **No second AODL normalization.** Bend consumes `to_core()` output only (aodl#19 owner constraint).
- **No legality-as-a-field.** Laws are about membership in an independently computed set (probe v1 lesson).
- **No `--publish`.** No PASS surfaced as task success, admission or authority. No bare `bend PROOF.bend` without
  `--verdict`/`--check-only` in a gate.
- **No proofs over the judge or the outcome.** z0evals' scoring and verified-success are measured, never proven.
  "Cascade success ≥ baseline − margin" is data, not a law (`z0-decisions.md` D15-17).

---

## 11. Where this angle could be wrong (falsification and honest limits)

- **The combinator laws are almost trivial.** A Hypothesis property test in Python would catch most combinator
  bugs.
  - **Defense:** the value is (a) a human-owned spec of record in `LAWS.bend` (upstream convention: the human
    writes it, the AI does not touch it, GUIDE L319-322); (b) universal quantification instead of sampling;
    (c) composition with family L, where the real logic lives.
  - **Falsifier:** if families L and S both stay small enough that Python property tests plus parity give the same
    assurance, and the Bend packages cost more maintenance than they catch (track the law-mutation suite's
    rejections), then keep LAWS as documentation only and drop the verdict gate. That would be the #56 rule
    ("simpler mechanisms remain mandatory controls") applied to Bend itself.
- **Parity is measured, not proven.** The runtime guarantee is "proven kernel + measured equivalence". This stays
  true until #813 or a Python target lets the proven kernel run in-process. G1 shows that measured equivalence can
  miss domain edges, because the fuzz never left the representable range. Parity corpora must include out-of-range
  and fractional values, counted as `unrepresentable`.
- **Translation and compiler are unproven** (`bend-docs.md` §3): #1212 class bugs, Nat cap 2^48, the C backend vs
  BendTT. A verdict attests the emitted book only. bend-native's `source_semantics_attested: false` is correct and
  must be carried through.
- **The envelope is the weak point, not the proofs.** G3, G4, G5 and the state-construction hazard (§5.3) are all
  ways for a correct proof to be about the wrong input. Migration steps 0-2 come first for that reason.
- **Unmeasured:** whether Bend batch replay beats Python in Evolution Lab. The perf triage
  (`bend-perf/results/modes-r1.json`) was still empty, so no claim is made.

---

## 12. Owner decisions surfaced

1. #389: trust root (17db447a vs a qualified 2.0.35).
2. Is `LAWS.bend` per family an owner-reviewed spec of record, as upstream convention suggests?
3. AODL: should envelope identity exclude `plan` (§6.3)? Integer budget units (G1)?
4. Is a current verdict + parity receipt a **hard** precondition for #56 promotion past `shadow`, or advisory?
5. Should privacy/egress (`local_only`) become an AODL envelope field consumed by routing, escalation and memory
   injection, replacing the four config mechanisms (D19)?
6. Should the governed worker's provider come from `plan.bindings` (an evolved selector inside family C) instead
   of `Z0INT_GOVERNED_PROVIDER` (D8)?

---

## Appendix A: files written

- `design-evolution-first.md` (this file).
- `scratch/evo-issues-56-59.txt`, `scratch/evo-issues-55-53-20-48.txt`: text extracts of the read-only issue dumps.
- `scratch/evo-probe/`:
  - `select.bend`, `LAWS.bend`, `PROOF.bend`, `verdict.out`: v1;
  - `v2/` (`cex.bend`, `cex2.bend`);
  - `m1/` (mutant + `cex.bend`);
  - `kernel/bendtt`: a private, read-only copy of the official 2.0.34 kernel, sha256 `a7e5203d…`;
  - `home/`, `tmp/`.

## Appendix B: hard-rule compliance

- Read-only everywhere except `/mnt/zer0models/z0-wt/wiring/bend-seam/`.
- No git writes, GitHub writes or upstream writes.
- Every Bend invocation (2 checks, 3 verdicts, 3 tiny value evaluations; each under 1 s) ran under
  `flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock` with telemetry off and the trust-path env stripped.
- No builds: there was no `-o`, and no Lean kernel build, because a SHA-verified prebuilt kernel was copied.
- The kernel binary was **read** from another lane's directory (`cua-lane-tmp/verify-e2e-r1`) and copied into this
  lane's scratch. Nothing in that directory was modified.
