# Local cognition portfolio (z0intelligence#20)

Measured local-model portfolio for tool calling and orchestration, plus the
deterministic machinery that decides what a learned model is even allowed to
choose between.

Related: evolution-lab#23 (tournament) · tokenomics#4 (telemetry) · kerdoios#50
(placement) · oh-my-pi#83 (OMP adapter lane) · frontier-kb#27 (evidence) · z0#5
(registry view).

## The rule this implements

> Ordinary computer science goes in the compiler, not the model.

    observe -> compile (deterministic) -> tiny specialist -> JEV
            -> orchestrator SLM -> general SLM -> remote frontier

Tool schemas, legal actions, dependencies, ordering, ready sets, budgets,
permissions, retries and persistent state are all deterministic. A learned model
only ever chooses among actions the compiler already declared legal — and a
learned answer that names anything else is rejected.

## Components

| Piece | Where | What it owns |
|---|---|---|
| Legal-action compiler | `src/z0int/cognition/actions.py` | capability / dependency / permission / budget / deterministic-shortcut filtering, replayable eliminations |
| Shared capability model | `src/z0int/cognition/candidates.py` | one provider-neutral `CandidateModel`; pi-ai catalog projection; hard filters + suitability ranking |
| Decision surface | `src/z0int/cognition/surface.py` | suitability, uncertainty, required quality/risk class, escalation, fail-closed |
| Replayable receipts | `src/z0int/cognition/receipts.py` | `z0int.cognition.receipt.v1`: state, eligible set, choice, quota, latency, tokens, cost, retries |
| Capability manifest | `manifests/local_cognition.v1.json` | versioned per-model metadata; source claims separated from local measurements |
| Adapters | `src/z0int/cognition/adapters/` | model-specific tool dialects behind one backend-neutral surface |
| Escalation policy | `src/z0int/cognition/escalation.py` | measured-signal tier choice; versioned thresholds |
| Cascade | `src/z0int/cognition/cascade.py` | tier ordering, abstention/escalation, shadow mode, receipts |
| Serving registry + probe | `src/z0int/cognition/registry.py`, `probe.py`, `serving.py` | "what is actually served here" and real machine measurements |
| OMP bridge op | `src/z0int/cognition/shadow.py`, `src/z0int/bridge/` | observe-only `cognition_shadow` op |
| OMP extension | `omp-extensions/local-cognition/` | shadow-only harness lane; never blocks a tool call |
| Evaluation | `scripts/local_tool_calling_eval.py` | schema vs trajectory axes, p50/p95/p99, dangerous-selection counter |
| CLI | `z0int cognition {manifest,candidates,roles,serving,probe,compile,decide}` | |

## Evidence discipline

`source_reported_benchmarks` and `measurements` are separate fields, and
`selectable()` reads only local measurements. A model with a better marketing
number is not selectable until it has been measured on this machine. This is
enforced by `assert_selection_is_evidence_based()` and covered by tests.

Verified upstream constraints (checked against the HF API, not assumed):

* `nvidia/Nemotron-Orchestrator-8B` — NVIDIA License, **non-commercial**.
* `MadeAgents/Hammer2.1-3b` — `qwen-research`, **non-commercial**.
* `MadeAgents/Hammer2.1-7b` — `cc-by-nc-4.0`, **non-commercial**.
* `google/functiongemma-270m-it` — gated; Gemma Terms of Use.
* `Qwen/Qwen3.5-9B` and `-4B` — apache-2.0.

Source claims are recorded but recorded *as claims*: the 4B card claims a higher
TAU2-Bench score (79.9) than the 9B (79.1) while claiming a much lower BFCL-V4
(50.3 vs 66.1). That is why nothing here is ranked by a vendor number.

## Local serving (RTX 3080 Ti 12 GB, measured 2026-09-21)

Runtime: `llama.cpp 0.4.1-dev` + CUDA 13.3, context 4096, one model resident at a
time, GGUFs as staged. Receipts: `z0int.serving_receipt.v1` under
`~/.z0int/benchmarks/local_cognition/`.

| model | quant | VRAM idle MiB | VRAM peak MiB | cold load ms | TTFT ms | decode tok/s | short decision ms | long decision ms |
|---|---|---|---|---|---|---|---|---|
| functiongemma_270m | Q8_0 | 2223 | 2241 | 46 | 8.4 | 360.0 | 124 | 100 |
| hammer2.1_3b | Q4_K_M | 3939 | 3957 | 66 | 5.6 | 281.6 | 137 | 214 |
| hammer2.1_7b | Q4_K_M | 5994 | 6014 | 67 | 9.3 | 216.9 | 211 | 333 |
| nemotron_orchestrator_8b | Q4_K_M | 7007 | 7023 | 122 | 9.7 | 113.7 | 733 | 855 |
| qwen3.5_9b | Q4_K_M | 7372 | 7406 | 306 | 62.6 | 83.9 | 1275 | 1185 |

All five fit at 4096 context; none were co-resident.

### Production path (llama.cpp supervisor, measured 2026-09-21 later the same day)

`z0int cognition serve` is now the production endpoint: one base URL, one resident
model, swap on request, unload when idle. Everything that produces a training
label goes through it.

| model | quant | cold load ms | TTFT ms | decode tok/s | short decision ms | long decision ms |
|---|---|---|---|---|---|---|
| functiongemma_270m | Q8_0 | 5319 | 8.6 | 351.1 | 139 | 83 |
| hammer2.1_3b | Q4_K_M | 5222 | 5.8 | 289.5 | 138 | 209 |
| hammer2.1_7b | Q4_K_M | 13284 | 13.2 | 124.7 | 244 | 357 |
| qwen3.5_4b | Q4_K_M | 11479 | 58.0 | 117.1 | 779 | 829 |
| nemotron_orchestrator_8b | Q4_K_M | 15349 | 10.1 | 108.0 | 741 | 888 |
| qwen3.5_9b | Q4_K_M | 17722 | 50.0 | 87.9 | 1083 | 1150 |

`cold load` here is supervisor wall-clock (process spawn + health poll), which is
why it is larger than the standalone table above — that one used llama.cpp's own
internal load timing. The supervisor number is the production one. **Qwen3.5-4B
fills the intended slot**: 117 tok/s and a 779 ms short decision, between
Hammer-7B (244 ms) and Qwen3.5-9B (1083 ms), with near-9B accuracy.



**Runtime choice must be measured.** The identical FunctionGemma Q8_0 checkpoint
decoded at **10.5 tok/s** through Ollama 0.33.2 and **360 tok/s** through
llama.cpp 0.4.1-dev on the same GPU — a ~34x difference for the same weights.

## Functional evaluation

28 deterministic fixtures (`benchmarks/fixtures/local-cognition-v1/examples.jsonl`)
covering the phase-8 list: one obvious tool, two similar tools, no relevant tool,
missing/wrong-typed argument, unknown tool, malformed/failed/timed-out/retried
tool, two independent parallel actions, hard dependency, diamond graph,
cheap/specialist/expensive routing, orchestrator abstention, JEV/SLM uncertainty,
stop/continue/retry/escalate/replan, and the four security cases.

**Zero dangerous selections by every backend in every run.** The four security
fixtures declare `dangerous_actions`, and no model ever named one — because the
compiler had already removed them before any model saw the list.

### Default completion budget (256 tokens)

| backend | schema valid | trajectory correct | abstained | invalid | dangerous | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|---|---|---|---|
| rules (first legal action) | 28/28 | 21/28 | 0 | 0 | 0 | 0 | 0 | 0 |
| functiongemma_270m | 28/28 | 17/28 | 0 | 0 | 0 | 58 | 81 | 111 |
| hammer2.1_3b | 27/28 | **25/28** | 1 | 0 | 0 | 177 | 344 | 474 |
| hammer2.1_7b | 28/28 | 24/28 | 0 | 0 | 0 | 319 | 370 | 382 |
| nemotron_orchestrator_8b | 17/28 | 17/28 | 11 | 0 | 0 | 2700 | 2977 | 3013 |
| qwen3.5_9b | 21/28 | 21/28 | 7 | 0 | 0 | 3573 | 4043 | 4099 |

### With an adequate budget for the thinking models (1024 tokens)

| backend | schema valid | trajectory correct | abstained | dangerous | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|---|---|---|
| nemotron_orchestrator_8b | 17/28 | 17/28 | 11 | 0 | 2768 | 3004 | 3006 |
| qwen3.5_9b | **28/28** | **26/28** | 0 | 0 | 3523 | 14446 | 14726 |

Two findings that changed the conclusions:

1. **Qwen3.5-9B was being mis-scored, not failing.** Its `<think>` block consumed
   the completion budget, so it never reached its call. Given budget it reaches
   28/28 schema-valid and 26/28 correct — the best accuracy measured — at ~18x
   Hammer-3B's median latency and a p95 above 14 s.
2. **Nemotron-Orchestrator-8B does not improve with budget.** Its abstentions are
   a capability mismatch, not a truncation artifact: it is trained for multi-turn
   heterogeneous tool/model orchestration, not single-shot choice over a small
   action set. Confirmed directly against the server: with enough budget it
   returns a clean `tool_calls` entry, but it frequently spends the whole budget
   deliberating and returns empty `content`.

   Its reasoning channel **must not be parsed as the decision** — the reasoning
   explicitly enumerates alternatives it then rejects ("fs.read … Alternatively,
   using shell.run …"). Parsing it would record a rejected option as the answer.
   `adapters/transport.py` ignores `reasoning_content` by design, and
   `test_reasoning_content_is_never_parsed_as_the_decision` locks that in.

## Served-model composition benchmark (A-F)

Same 28 fixtures, real served models, through the production supervisor. A/B have
**no compiler**: the model is handed the unfiltered action set, including the
security fixtures' dangerous actions.

| composition | compiler | correct | dangerous exposed | dangerous selected | p50 ms | p95 ms |
|---|---|---|---|---|---|---|
| A qwen3.5_9b alone | NO | 26/28 | 4 | 0 | 3340 | 14778 |
| B nemotron alone | NO | 18/28 | 4 | 0 | 2557 | 3041 |
| C compiler + nemotron | yes | 20/28 | 0 | 0 | 2577 | 2988 |
| D compiler + JEV + nemotron | yes | 19/28 | 0 | 0 | 1944 | 2701 |
| E compiler + JEV + nemotron + qwen9b | yes | 24/28 | 0 | 0 | 1943 | 22239 |
| F = E + tiny specialist | yes | 24/28 | 0 | 0 | 2096 | 27947 |
| SUB qwen3.5_4b alone | NO | 25/28 | 4 | 0 | 2261 | 4522 |
| SUB compiler + qwen3.5_4b | yes | 25/28 | 0 | 0 | 2156 | 4385 |
| **SUB compiler + hammer2.1_3b** | yes | **24/28** | 0 | 0 | **185** | **311** |
| SUB compiler + hammer2.1_7b | yes | 24/28 | 0 | 0 | 328 | 369 |
| SUB compiler + functiongemma | yes | 14/28 | 0 | 0 | 58 | 120 |

Three things this settles:

1. **The elaborate ladder is currently beaten by one small specialist.** Hammer2.1-3B
   behind the compiler scores 24/28 at p50 185 ms. Composition E scores the same
   24/28 at p50 1943 ms — **10.5x slower for identical accuracy**. Whatever the
   ladder is for, it is not yet earning its cost on bounded choice.
2. **The compiler improves Nemotron**: 18/28 alone → 20/28 with the compiler, and
   it removes all four dangerous exposures from the offered set.
3. **Safety was not discriminated by these fixtures.** Every served model declined
   the obviously-dangerous action even when handed it unfiltered (0 dangerous
   selections in A, B and SUB_qwen4b_alone). The compiler's safety value is proven
   against the scripted hostile backend in Evolution Lab, not against these models;
   a harder adversarial fixture set is needed before claiming otherwise.

### The ladder was inert until the answer itself was checked

First run of D/E/F produced identical 17/28 rows. The cause: NanoJev decided 25 of
28 fixtures and **never abstained**, so the orchestrator and the general fallback
were dead code. The cascade only escalated on explicit abstention, which a bounded
scorer returning a distribution almost never emits.

The cascade now re-checks a bounded scorer's confidence, top-1/top-2 margin and
entropy against the versioned thresholds and escalates when they disagree —
but only when a later tier is actually configured. Generative tiers are
deliberately not re-checked. Effect: D 17→19, E 17→24, F 17→24, with tiers
genuinely firing (JEV 6, orchestrator 16, general 4).

## Multi-turn orchestration benchmark

The bounded-choice suite cannot judge an orchestrator, so a separate 12-scenario
multi-turn harness was added: the model picks one callable per turn from a
heterogeneous set, the world answers deterministically, and the loop ends on
finish, budget exhaustion or `max_turns`. `optimal_greedy` is the
cheapest-covering-set control.

| backend | solved | correct stop | wasted calls | order viol | cost units | turns | precision | p50 ms |
|---|---|---|---|---|---|---|---|---|
| optimal_greedy (control) | 11/12 | 12/12 | 0 | 2 | 35 | 16 | 1.00 | 0 |
| **qwen3.5_9b** | **9/12** | 7/12 | 6 | 0 | 42 | 31 | 0.70 | 5063 |
| **qwen3.5_4b** | **9/12** | 5/12 | 8 | 0 | 53 | 33 | 0.57 | 4740 |
| nemotron_orchestrator_8b | 6/12 | 7/12 | 3 | 0 | 30 | 25 | 0.71 | 11475 |
| hammer2.1_3b | 0/12 | 1/12 | 0 | 0 | 0 | 12 | - | 314 |
| hammer2.1_7b | 0/12 | 1/12 | 0 | 0 | 0 | 12 | - | 511 |
| functiongemma_270m | 0/12 | 1/12 | 0 | 0 | 0 | 12 | - | 148 |

**This is the result that overturns the original role guess.**

* Nemotron *is* a real orchestrator — it is the only arm besides Qwen3.5 that
  engages in multi-turn dispatch (15 dispatch turns, precision 0.71). Hammer2.1
  and FunctionGemma never orchestrate at all: 12 premature stops each, zero
  dispatches. They are function-calling specialists, exactly as claimed.
* But **Nemotron is not the best orchestrator either.** Qwen3.5-4B and -9B both
  solve 9/12 to its 6/12, and the 4B is 2.4x faster. Nemotron is beaten on its own
  intended task by a general model, and it is non-commercial.
* Nobody is cost-disciplined: 3-8 wasted dispatches each, and no arm matches the
  control's 12/12 correct stops.

## Best observed policy by role

Not one global winner, and the evidence is now strong enough to demote a candidate
that the architecture expected to win.

| Role | Current best | Why |
|---|---|---|
| tiny action specialist | `hammer2.1_3b` | 24/28 at p50 185 ms behind the compiler. `functiongemma_270m` is 3x faster (58 ms) but 14/28 zero-shot and has not been fine-tuned for this task family |
| bounded scorer | JEV/NanoJev | 19/28 at ~30 ms when it owns the decision; it buys accuracy only when paired with a fallback, and it must be confidence-checked or it silences the rest of the ladder |
| general function caller | `qwen3.5_9b` | 26/28 alone; 28/28 schema validity with an adequate budget |
| **semantic orchestrator** | **`qwen3.5_4b` / `qwen3.5_9b` — NOT Nemotron** | both solve 9/12 multi-turn to Nemotron's 6/12, and the 4B is 2.4x faster. Nemotron is dominated on bounded choice *and* on its own task, and is non-commercial. It keeps a research role only |
| general local fallback | `qwen3.5_4b` | 25/28 alone at p50 2261 ms, and 25/28 with the compiler at 2156 ms — near-9B accuracy at two-thirds the latency |
| remote/frontier fallback | unchanged | resolved from the dynamically inventoried free provider paths |

The ladder ordering the architecture proposed (`tiny -> JEV -> Hammer -> Qwen ->
Nemotron`) is **wrong at two points** on this evidence: Hammer outranks JEV on
accuracy (24 vs 19) though not on latency, and Nemotron belongs last or not at all.
Both are exactly the kind of per-region decision Q-Route v0 now measures instead
of assuming.

## Q-Route v0 (Evolution Lab, `experiment/q-route-v0`)

Built on the real runs above: teacher table (38 states, 18 arms, 522 rows),
trajectory utility with an explicit CVaR tail term, ridge `Q(s,a)`, a
cost/quality Pareto frontier, and a **frozen non-inferiority gate** that walks the
ladder cheapest-first so a more expensive arm keeps a region only if the cheaper
one fails the gate there.

First real run: 19/38 states owned by their cheapest available arm (automatic
simplification), 24 escalation attempts refused because the candidate was itself
worse in utility, and routed latency of 1660 ms versus 221034 ms for
always-most-expensive. The distilled 32-parameter router reaches 0.579 fidelity
against a 0.658 feature-map ceiling — the gap is the frozen 4-feature v0
resolution and is reported as `feature_bucket_ceiling` rather than hidden.

## Shared capability model and the decision surface

Semantic selection is not per-provider code. One frozen candidate type
(`CandidateModel` in `src/z0int/cognition/candidates.py`) describes every
execution target — a locally served SLM, the JEV/OpenJev bounded scorer, and a
remote free-tier provider — by *capability*:

```text
candidate_id, provider, model_id
capabilities: tool_calling, parallel_tool_calls, structured_output,
              reasoning, multi_turn, modalities, context_window,
              supports_confidence, supports_abstention
quality_class: tiny | bounded | standard | strong | frontier
max_risk_class: read | write | destructive | credential | payment | publish
cost_class: local | free | unknown | metered
serves_tiers: which rungs of the ladder may execute here
```

**Groq and Cerebras are ordinary rows, not special cases.** They enter when DSH's
`llm-pi-ai` catalog is projected through `candidates_from_pi_ai_catalog()`;
z0intelligence keeps no second model registry, and no selection code branches on a
provider name. A third provider flows through the same adapter with zero code
change (asserted by `test_a_third_provider_needs_no_code_change`, and by an AST
guard that no non-docstring string literal in the selection modules names a
provider). Per-row capabilities come from the catalog (`reasoning`, `input`
modalities, `contextWindow`); anything the catalog cannot know (tool calling,
quality class, risk ceiling, cost class, served rungs) is supplied as a `z0int`
row annotation or a caller hint. Remote candidates default to the conservative
`quality_class=standard`, `max_risk_class=read`, so a provider is never trusted
for consequential work until someone says so.

`DecisionSurface` (`src/z0int/cognition/surface.py`) is the only place that
answers the four questions:

1. **Which candidates are semantically suitable?** — the shared hard filters
   (`filter_candidates`) in a fixed, replayable order: served tier, capabilities,
   required quality class, risk ceiling, cost ceiling; then `rank_candidates`
   orders the survivors by semantic suitability (quality headroom, execution cost
   class, latency headroom). Nothing consults a vendor benchmark.
2. **How uncertain is this decision?** — `uncertainty_of()`, a deterministic mean
   over *observed* signals: candidate entropy, top-1/top-2 margin, state novelty,
   novel tool combination, historical failure family, verification demand and
   capability demand. It never looks at the prompt.
3. **What quality/risk class is required?** — derived from risk class,
   independent-verification demand and capability demand, then raised by measured
   uncertainty, using versioned `SurfaceThresholds`.
4. **Should we escalate?** — yes when the required class sits above the tier the
   escalation policy proposed, when uncertainty crosses the threshold, or when the
   proposed rung has no eligible candidate but a later one does. If no rung has an
   eligible candidate the surface **fails closed**: the allowed set stays empty
   rather than lowering the bar to spend free capacity.

The effective entry tier is `max(escalation policy, quality-class floor)`, so the
surface can only ever raise the floor. The ladder rungs keep their meaning:

| Rung | Code |
|---|---|
| deterministic / compiled route | `actions.compile_actions()` + `LegalActionSet.deterministic_solution` |
| tiny specialist | rung `tiny_specialist`, `ROLE_TIERS["tiny_action_specialist"]` |
| JEV / OpenJev bounded scorer | rung `bounded_jev`, `ROLE_TIERS["bounded_scorer"]`, confidence re-checked by `EscalationPolicy.accept_or_escalate` |
| local / remote SLM router | rung `orchestrator_slm`, served by `CandidateRungBackend` over local **and** Groq/Cerebras candidates |
| general fallback | rungs `general_slm` / `remote_frontier` |

Legal-action filtering happens strictly before any learned selection:
`CognitionCascade.run()` compiles the `LegalActionSet` first and hands that set —
and nothing else — to every tier; `CandidateRungBackend` derives its capability
requirement from the already-legal set, and an out-of-set selection is rejected as
an illegal call even if a backend returns it.

### Replayable receipts

Every learned decision emits one `z0int.cognition.receipt.v1` row
(`src/z0int/cognition/receipts.py`) capturing: state, the eligible candidate set
(with capabilities), the chosen candidate/provider/model, observed quota state,
latency state (budget, observed, TTFT, decode tok/s), prediction and confidence,
execution outcome, verified outcome, tokens, GPU/provider cost, and retries. The
receipt round-trips through JSON and `receipt_from_dict()` reconstructs it for
replay without a model server; `mark_execution()` and `mark_verification()` join
the runtime and verifier facts later without collapsing them.

### Ownership boundary

| Concern | Owner |
|---|---|
| which models exist at a provider, price, wire API | DSH `llm-pi-ai` catalog (projected read-only) |
| live capacity, RPM/RPD/TPM/TPD, reset timers, placement | **Kerdoios** |
| semantic suitability, uncertainty, required quality/risk class, escalation | **z0intelligence** |

`QuotaState` is carried through receipts, never computed here: passing observed
quota state changes no decision. There is deliberately no quota arithmetic, rate
limit token bucket or reset timer in the cognition plane
(`test_no_quota_or_reset_accounting_parameters`).

## Safety properties (tested)

* A risk class outside granted authority is never legal; granting authority is
  the only way a destructive action becomes legal.
* A deterministic rule that names a filtered action cannot resurrect it.
* A backend that names a filtered action is rejected by the cascade even though
  adapters already validate (`test_cascade_rejects_a_backend_that_names_a_filtered_action`).
* Transport errors, unparseable output, missing models and empty legal sets all
  fail open; an empty legal set never contacts a model at all.
* Shadow runs record side by side and never execute; receipts keep
  `selected_action` and `executed_action` separate.
* `execution_completed` is never `verified_success`, and an ambient close cannot
  mint gold.

## Not yet done

* **The orchestration harness is single-model per turn.** It measures dispatch
  quality but not true model-as-tool composition where one model's output becomes
  another's input. Nemotron's card describes the latter.
* **Compiling routing cognition downward is unproven.** The hierarchy
  (Qwen/Nemotron -> Hammer -> FunctionGemma -> MLP/tree -> rules -> cache) is the
  intent, but Hammer and FunctionGemma have not been *trained* on the traces yet;
  their current numbers are zero-shot, and FunctionGemma is explicitly not meant
  to be judged that way.
* **The gating data is thin.** Many escalations in the first Q-Route run are
  `latency_ratio_exceeded` on n=1 samples under a frozen `max_latency_ratio=1.0`.
  That is an honest consequence of frozen thresholds plus single-sample data, not a
  tuned result.
* **The safety fixtures are too easy.** Every served model declined the dangerous
  action even unfiltered, so they no longer discriminate. The compiler's safety
  claim rests on the scripted hostile backend in Evolution Lab.
* **JEV co-residency** was not measured; Kerdoios#50 has the placement analysis
  and the never-co-reside constraints.
* **Ollama remains a second runtime.** It is demoted to `up-legacy` and documented
  as never a label source, but it is still installed and could silently be used.
