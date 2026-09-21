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
| Capability manifest | `manifests/local_cognition.v1.json` | versioned per-model metadata; source claims separated from local measurements |
| Adapters | `src/z0int/cognition/adapters/` | model-specific tool dialects behind one backend-neutral surface |
| Escalation policy | `src/z0int/cognition/escalation.py` | measured-signal tier choice; versioned thresholds |
| Cascade | `src/z0int/cognition/cascade.py` | tier ordering, abstention/escalation, shadow mode, receipts |
| Serving registry + probe | `src/z0int/cognition/registry.py`, `probe.py`, `serving.py` | "what is actually served here" and real machine measurements |
| OMP bridge op | `src/z0int/cognition/shadow.py`, `src/z0int/bridge/` | observe-only `cognition_shadow` op |
| OMP extension | `omp-extensions/local-cognition/` | shadow-only harness lane; never blocks a tool call |
| Evaluation | `scripts/local_tool_calling_eval.py` | schema vs trajectory axes, p50/p95/p99, dangerous-selection counter |
| CLI | `z0int cognition {manifest,roles,serving,probe,compile,decide}` | |

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

All five fit at 4096 context; none were co-resident. `qwen3.5_4b` was not served
locally in this pass (only bfloat16 weights were staged).

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

## Best observed policy by role

Not one global winner — the roles are genuinely different:

| Role | Current best | Why |
|---|---|---|
| tiny action specialist | `hammer2.1_3b` | 25/28 at 177 ms p50; `functiongemma_270m` is faster (58 ms) but 17/28 and has not yet been fine-tuned for this task family |
| bounded scorer | JEV/OpenJev/NanoJev (unchanged) | the measured SLMs are 100–1000x slower on bounded choice; JEV still owns that lane |
| general function caller | `qwen3.5_9b` | 28/28 schema, 26/28 correct once budgeted correctly |
| semantic orchestrator | `nemotron_orchestrator_8b` (provisional) | **not validated by this suite.** It is the only candidate trained for model-as-tool orchestration, but it is non-commercial and it underperformed every specialist on bounded choice. It needs a multi-turn orchestration benchmark, which this suite is not |
| general local fallback | `qwen3.5_9b` | best accuracy; `hammer2.1_3b` is the cheap alternative when the decision is structurally bounded |

`functiongemma_270m` is **not** judged here as a specialist. Its role per
z0intelligence#20 is to be fine-tuned for one high-volume action-family problem
(phase 10) and promoted only if it improves the Pareto frontier — a 270M model
that is merely interesting but slower or worse than a deterministic classifier
should not survive.

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

* **Local serving of `qwen3.5_4b`**: only bfloat16 weights were staged, and an
  8–9B model already occupies most of the card. Needs a GGUF.
* **Nemotron as orchestrator is unvalidated** — the suite here measures bounded
  choice; a multi-turn orchestration benchmark is required before it earns that
  role.
* **FunctionGemma fine-tuning (phase 10)** and the **Agent0 self-evolution arm**
  (phase 11) are scoped, not run. The Agent0 arm belongs in Evolution Lab with
  frozen held-out work-item groups and independent verifiers.
* **JEV co-residency** was not measured; Kerdoios#50 has the placement analysis
  and the never-co-reside constraints.
