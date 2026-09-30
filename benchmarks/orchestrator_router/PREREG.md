# Pre-registration: Nemotron-Orchestrator-8B as a shadow router for z0 (v0)

Issue: kvnloo/z0intelligence#20. Branch `exp/orchestrator-router-v0`, off `integrate/claude-code-z0-stack` @ 4fffc8c.
Written and committed **before** any labeled item was sent to any model. Shadow only: nothing here changes a live route.

## Question

When deterministic policy has already produced the set of legal candidates, does a learned
orchestrator (NVIDIA Nemotron-Orchestrator-8B) pick the right candidate more often or more cheaply
than (a) the current deterministic choice and (b) a generic 8B LLM given the same prompt?

Architecture under test: policy (privacy, authority, budget, $0 evidence, caps, availability) **filters** →
the router **proposes** one of the legal candidates → a gate **disposes**. The gate accepts a proposal only if
it names a legal candidate. Otherwise it falls back to the deterministic choice. Raw illegal proposals are counted before the gate.

## Model under test

- `nvidia/Nemotron-Orchestrator-8B` (base Qwen3-8B, NVIDIA License, research/dev use). Base repo revision `26df4b9aad5abdc5b7871ee4c71063ce888feb26`.
- GGUF: `bartowski/nvidia_Orchestrator-8B-GGUF` @ `b4bbbc08d2b475fe529428e6f358c932881aa0b5`, file `nvidia_Orchestrator-8B-Q4_K_M.gguf`,
  5027783840 bytes, sha256 `1cc7077e20b3339d1a46bc72e29959cdd4c7249ebbd73e6977e76f23625995c7`. The sha256 was recomputed on groot after download and matches the HF LFS oid.
- Served on the groot farm (`z0-farm-llama.service`, llama.cpp b11270, RTX 3080 Ti 12 GB) as router id `nemotron-orchestrator-8b-q4km`.
  It uses the same server flags as the other farm models (`-ngl 99 -c 8192 --parallel 2`).

## Routing sets (all built from existing z0 data; frozen inputs, sha256 below)

**S1 `evidence_route`: labeled, n=48.** These are the 48 `evidence_interpretation` test items of `benchmarks/data/authored144.jsonl`
(sha256 `8162d1c7…e079`), the frozen evidence_sufficiency set that Jev was admitted on. Each is routed as the OFFLOAD
worker task `"local-only: " + question + state + options`. The `local-only` rule is what z0 uses for private/local work.
- Legal candidates: every provider/model pair the *real* policy (`manifests/worker_routing.v1.json` sha256 `5a168aa2…a7c7` +
  host `~/.z0int/config/worker_routing.local.json` sha256 `c5005a80…f7ff`) accepts for the `local` category. That means cohort
  local, a validated $0 route whose evidence sha256 checks out, and cap > 0. There are 4 of them:
  `local/qwen3-0.6b-q8` (mbp Radeon, Vulkan), `groot/qwen3-0.6b-q8`, `groot/qwen3-4b-q4km`, `groot/qwen3-8b-q4km`.
  `groot/qwen3-1.7b-q4km` has outcomes but no validated $0 route, so policy drops it and it is never shown to a router.
  Jev is not local, so the `local-only` privacy rule drops it too.
- Per-candidate outcomes come from the existing bench runs in `~/.z0int/research/factory/groot-bench/`
  (`mbp-radeon-qwen3-0.6b-q8`, `groot_qwen3_06b_q8`, `groot_qwen3_4b_q4`, `groot_qwen3_8b_q4`.jsonl). A candidate *passes* an item if `pred_id == gold_id`.
- Cost of a candidate is the median `ms` per item in its outcome file (measured): 1236.3 / 41.0 / 63.1 / 90.2 ms.
- Deterministic arm: `worker_routing.plan_route(task, policy, providers, available_providers=all)` → `candidates[0]`
  (today that is `local/qwen3-0.6b-q8`).

**S2 `action_route`: labeled, n=11.** These are the pinned State Packet action-selector items (`benchmarks/state_packet/questions_pinned.json`,
sha256 `437412fc…95c4`; fixtures `~/.z0int/research/claude-code-overnight/pinned`). The router sees the rendered packet,
the user question, and the ACT/OBSERVE_MORE/ESCALATE question.
- Legal candidates: the decision backends that were *available* in the existing run
  `~/.z0int/research/claude-code-overnight/runs/action-selector-pinned.jsonl` (sha256 `f819f552…e2ed`). Those are `rule_always_act`, `julia_1`, and `laya_421m`.
  `nanojev`, `openjev_06b`, and `decider_2b` errored on every item because no CUDA device was available, so the availability filter drops them.
- Cost is the median `s` per backend in that file, with `rule_always_act` = 0.
- Deterministic arm: `rule_always_act`, the current rule baseline. No learned selector was promoted.

**S3 `worker_tasks`: unlabeled, excluded from accuracy.** These are the 4 distinct real `codex.delegated_text` subtask strings in
`~/.z0int/receipts/decisions.jsonl`, each sent as-is and with a `local-only: ` prefix, plus the 3 task strings used in the routing
tests (`Extract JSON`, `write python code`, `local-only: summarize this`). Legal candidates are the `plan_route` candidates from the real policy.
This set measures only illegal proposals, latency, and agreement with the deterministic choice.

## Gold

Gold is the cheapest candidate that passed. Every passing candidate whose cost is ≤ 1.05 × the cheapest passing cost counts as gold,
so near-ties accept either. An item where no legal candidate passed is **unlabeled** and excluded from accuracy and regret.
All items are test items. Nothing is tuned. The prompts below are frozen by this commit. The only calls made before this commit
were two smoke calls on the unlabeled string "Name three primary colors" with a toy 2-candidate tool. Their purpose was to check that
llama.cpp tool calling and per-request `enable_thinking` both work.

## Arms

| arm | what |
|---|---|
| `deterministic` | the current rule (see each set) |
| `orch_think` (**primary**) | Nemotron-Orchestrator-8B, native ToolOrchestra format, thinking on |
| `orch_nothink` | the same, `enable_thinking=false` (the farm default is `--reasoning off`) |
| `qwen3_8b_think` | generic-LLM control: `qwen3-8b-q4km` on the same farm, **identical** messages and tools, thinking on |
| `qwen3_8b_nothink` | the same, thinking off |
| `cheapest` | always the lowest-cost legal candidate |
| `strongest` | always the largest legal model by parameter count (S1: `groot/qwen3-8b-q4km`; S2: not defined, skipped) |
| `majority_loo` | the most frequent gold among the *other* labeled items of the same set (leave-one-out), with ties broken by cost |
| `oracle` | gold (upper-bound reference only) |

Native prompt format (ToolOrchestra `evaluation/eval_hle.py` + `tools.json`, NVlabs/ToolOrchestra@main):
- system: `You are good at using tools.`
- user: `Problem: {task}\n\nPreference: use the lowest-latency model that will still answer correctly.\n\nChoose an appropriate tool.'`
  The stray trailing quote is kept exactly as upstream.
- tools: one OpenAI function tool `answer` with a single string parameter `model`. Its description lists the legal candidates as
  opaque aliases `answer-1..k` (legal set in the order given above), a one-line **non-outcome** description of each (name, size, quant, host), and a
  `Model | price per million input tokens | price per million output tokens | average latency` table using the measured costs.
  No accuracy numbers from these sets are shown to any router.
- `temperature=0`, `seed=0`, `max_tokens=2048`. The model card uses temperature 1, but this run needs determinism, so that is a declared deviation.
- A proposal is **legal** iff the response has exactly one-or-more tool calls, the first call is `answer`, and its `model` is one of the
  shown aliases. Anything else counts as a raw illegal proposal: no tool call, another tool name, an unknown alias, unparsable JSON, or an HTTP error/timeout.
  The gate maps an illegal proposal to the deterministic choice.

## Metrics (reported per set and pooled over S1+S2 labeled items)

1. **Choice accuracy**: the gated choice is in the gold set.
2. **Pass rate**: the gated choice actually passed the item. This is the quality that would have been delivered.
3. **Cost-weighted regret**. Cost is normalized by the most expensive legal candidate in the set (c̃ ∈ [0,1]).
   If the choice passed, regret = c̃(choice) − c̃(gold). If it failed, regret = c̃(choice) + 1 − c̃(gold): the failed spend plus one re-run at max cost.
   Reported as the mean.
4. **Illegal proposals**: raw count and rate, and the post-gate count, which must be 0.
5. **Router latency** (mbp → groot wall clock, one sequential client): p50/p95 over warm calls. The first call of each arm is reported separately as cold.
   Server-reported prompt/decode timings are kept too.
6. **VRAM**: groot `nvidia-smi memory.used` before loading and after the first call, per model. The GPU is shared with Ollama, so this is reported as a delta and flagged as approximate.

Statistics: paired exact McNemar (two-sided) on per-item choice correctness, `orch_think` vs `deterministic` and
`orch_think` vs `qwen3_8b_think`, pooled over labeled S1+S2. No correction beyond this; only these two tests are confirmatory.

## Decision rule (fixed now)

The orchestrator is **worth continuing in shadow** only if all of the following hold on pooled labeled items:
(a) the raw illegal rate is ≤ 5% and the post-gate illegal count is 0;
(b) it beats `deterministic` on choice accuracy with McNemar p < 0.05, **or** it has lower mean regret with a pass rate no lower than deterministic;
(c) it beats the `qwen3_8b_think` control on choice accuracy or regret. If it does not, the "learned router" adds nothing over a generic LLM with the same prompt.
Otherwise the result is null or negative and is reported as such. It is never promoted to a live route from this experiment.

## Known limitations (declared up front)

- n is small (48 + 11 labeled), so only large effects are detectable.
- Outcomes are reused from single prior runs. A candidate's pass/fail is one sample, not a distribution.
- S1 costs are $0 for every candidate, so "cost" here is measured latency. Price is not part of it.
- S2 has 3 legal candidates and its gold is determined by the action label (ACT → rule, else a learned backend), so routing S2 amounts to solving the task.
- Orchestrator-8B was trained on multi-turn search/reason/answer orchestration with frontier experts. A one-shot pick among small $0 local models is off-distribution.

## Execution order

deterministic/baselines (no model) → `orch_nothink` → `orch_think` → `qwen3_8b_nothink` → `qwen3_8b_think`. Items run in fixed file order, one pass per arm.
Outputs go to `benchmarks/orchestrator_router/results_v0.json` (metrics + per-item rows), with each input file's sha256.

---

## Results addendum (written after the run; everything above is unchanged from commit 1302c3a)

Full numbers are in `results_v0.json`, per-call rows in `raw_v0.jsonl`, and VRAM in `vram_v0.json`. 280 router calls were made (4 LLM arms × 70 items).

Pooled labeled (S1+S2, n=59), gated choices:

| arm | choice acc | pass rate | mean regret | raw illegal | warm p50 / p95 |
|---|---|---|---|---|---|
| deterministic (plan_route / rule) | 8/59 (0.14) | 0.56 | 1.17 | 0 | 0 |
| orch_nothink | 8/59 (0.14) | 0.56 | 1.12 | 0 | 332 / 523 ms |
| **orch_think** (primary) | 23/59 (0.39) | 0.58 | 0.74 | 0 | 3597 / 6527 ms |
| qwen3_8b_nothink | 9/59 (0.15) | 0.56 | 1.14 | 0 | 253 / 469 ms |
| qwen3_8b_think | 23/59 (0.39) | 0.56 | 0.73 | 2 (no tool call) | 4721 / 10587 ms |
| cheapest | 35/59 (0.59) | 0.59 | 0.35 | n/a | n/a |
| majority_loo | 35/59 (0.59) | 0.59 | 0.35 | n/a | n/a |
| strongest (S1 only, n=48) | 4/48 | **0.90** | **0.13** | n/a | n/a |

- McNemar: orch_think vs deterministic, 16 vs 1 discordant, p = 0.0003. orch_think vs qwen3_8b_think, 9 vs 9, p = 1.0.
- Decision rule: (a) passes, (b) passes, (c) **fails**. **Do not continue in shadow.** In this setting the learned router is no better than a generic Qwen3-8B given the same prompt, and both are beaten by a trivial `cheapest` rule. A fixed `strongest` rule dominates them on delivered quality.
- The orchestrator never proposed the 4B or 8B candidate when thinking was on (S1: 27× groot-0.6B, 21× mbp-0.6B). It optimises the stated latency preference and does not predict difficulty.
- The deterministic arm's S1 accuracy is 0 because `plan_route` puts the host's own `local/qwen3-0.6b-q8` (mbp Vulkan, 1236 ms) first. That candidate is never the cheapest passing one, since the same weights run on groot at 41 ms. This is a finding about current host ordering, not about learned routing.
- VRAM (groot, shared GPU): about +6.0 GB for either 8B at `-c 8192 --parallel 2`. Orchestrator 935→6951 MiB; Qwen3-8B 901→6917 MiB. Cold first call: 3.9–7.6 s.
- Deviation: none in the protocol. One scoring bug was fixed before any metric was read: `oracle` crashed on unlabeled S3. The decision-rule block was added to the scorer after the run and mechanically encodes the rule above.
