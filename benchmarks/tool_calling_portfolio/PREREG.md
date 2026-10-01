# Tool-calling portfolio v0: pre-registration (z0intelligence#20)

Written and committed before any candidate is scored. Branch `exp/tool-calling-portfolio-v0`,
based on `integrate/claude-code-z0-stack` at b072468.

## Question

Which local models on groot (RTX 3080 Ti 12 GB) form z0's own function calls correctly, and at what
latency and VRAM cost? How do they compare with the Qwen3-8B and Qwen3-14B models already on the farm?
Nemotron-Orchestrator-8B was already evaluated as a router (null, `exp/orchestrator-router-v0`). Here it
is a secondary arm only, because its GGUF is already on the farm.

## Arms (fixed execution order)

All arms use GGUFs whose sha256 was verified on groot against the HF LFS oid at the pinned revision.

| arm | GGUF repo @ revision | file | sha256 (prefix) | licence |
|---|---|---|---|---|
| functiongemma_270m | ggml-org/functiongemma-270m-it-GGUF @ 2566ce14 | functiongemma-270m-it-bf16.gguf | 44090e53 | gemma (gated upstream) |
| hammer2.1_3b | mradermacher/Hammer2.1-3b-GGUF @ 4b0e6fc4 | Hammer2.1-3b.Q4_K_M.gguf | 320b6114 | qwen-research (non-commercial) |
| qwen3.5_4b | bartowski/Qwen_Qwen3.5-4B-GGUF @ 4168f45a | Qwen_Qwen3.5-4B-Q4_K_M.gguf | 13c16f42 | apache-2.0 |
| hammer2.1_7b | mradermacher/Hammer2.1-7b-GGUF @ 5adddc4c | Hammer2.1-7b.Q4_K_M.gguf | 22f42540 | cc-by-nc-4.0 |
| qwen3_8b (farm baseline) | Qwen3-8B-Q4_K_M.gguf already on the farm | | | apache-2.0 |
| qwen3.5_9b | bartowski/Qwen_Qwen3.5-9B-GGUF @ 182be2fd | Qwen_Qwen3.5-9B-Q4_K_M.gguf | d784ce9e | apache-2.0 |
| qwen3_14b (farm baseline) | Qwen/Qwen3-14B-GGUF @ 530227a (farm) | Qwen3-14B-Q4_K_M.gguf | 500a8806 | apache-2.0 |
| nemotron_orchestrator_8b (secondary) | bartowski/nvidia_Orchestrator-8B-GGUF (farm) | | | nvidia (non-commercial) |

## Serving

- A bench-only llama.cpp router runs on groot as the transient unit `z0-toolcall-bench`, at
  `100.113.138.100:11541`. It uses the farm's binary (b11270) and the farm's serving flags
  (`-ngl 99 -c 8192 --parallel 2 --reasoning off -t 4`), with `--models-max 1`. Its models dir is
  `/workspace/z0-farm/models/toolcall/router`.
- The farm unit, `worker_routing` config and `local_order` are not touched. Each arm is unloaded after its
  run, and the bench unit is stopped at the end.
- Requests go to `/v1/chat/completions` with native `tools`, `tool_choice=auto`, `parallel_tool_calls=true`,
  `temperature=0`, `seed=0`, `max_tokens=1024` and `chat_template_kwargs.enable_thinking=false`. Thinking is
  off for every arm. Requests run sequentially.
- **Adapter, one parser chain for every arm:** use llama.cpp's native `message.tool_calls`, parsed by the
  GGUF's own jinja template. If that is empty, parse `message.content` with fixed fallbacks in this order:
  Hermes `<tool_call>` JSON, FunctionGemma `<start_function_call>call:…`, then a JSON array or object of
  `{name, arguments}` (Hammer's documented format). Each row records which path fired.
- **Smoke run:** one item per suite and category, per arm. It checks transport and parsing only, and its
  rows are discarded. If a parser bug is found in smoke, it may be fixed before the scored run, as long as
  the fix is in the adapter and not specific to an item. Any such fix is listed below under Deviations.

## Item set (frozen by `build.py`; sha256 in `items_public_v0.json`)

**(a) z0_route: 48 items.** The tools are the real z0intelligence MCP schemas (`list_models`,
`route_worker`, `delegate_worker`), imported unchanged from `src/z0int/intelligence_mcp.py`.

- 43 route_worker items: the distinct real Claude Code user turns in `~/.z0int/research/action_authority_{v0,v1_dev}/turns.json`.
  Three turns with no task content are excluded. The system prompt fixes `trace_id`, `parent_agent` and an
  8-entry function menu. The gold is the `route_worker` tool, exact ids, a faithful task (the request
  appears verbatim, or difflib ratio >= 0.9), `allow_remote` not true, and a `function` in an
  author-labelled acceptable set. The author labelled these sets before any model ran, keyed by
  sha256(request)[:12] in `build.py`. The function label is the softest part of the gold, so form accuracy
  without it (`form_exact`) is also reported.
- 5 delegate_worker items: every complete (untruncated) explicit delegation subtask in
  `~/.z0int/receipts/decisions.jsonl` (`codex.delegated_text`), each paired with its receipt's
  provider/model and phrased as "Use provider P with model M for this: …". The gold is `delegate_worker`
  with that exact provider and model, a non-empty reason, the ids and a faithful task.
- Request text stays in `~/.z0int`. Git receives hashes and labels only.

**(b) bfcl: 125 items.** These are BFCL v4 non-live categories `simple_python`, `multiple`, `parallel`,
`parallel_multiple` and `irrelevance`, 25 each, sampled with `random.Random(20)`. The source is
github.com/ShishirPatil/gorilla @ 6ea57973c7a6097fd7c5915698c54c17c5b1b6c8,
`berkeley-function-call-leaderboard/bfcl_eval/data` (Apache-2.0). Copies of the source files are in
`data/bfcl/src`. Function names are sanitised to `[A-Za-z0-9_-]` and mapped back, the same as BFCL's
OpenAI-FC handling. Types are mapped from BFCL's dialect to JSON Schema (dict→object, float→number,
tuple→array). No system prompt is used, as in BFCL FC mode. Scoring uses a re-implementation of the BFCL
AST checker subset in `common.py`:
- The call count must match.
- Names must match.
- Required parameters must be present, and no parameter may be unknown.
- Each value must be in the possible-answer list. Strings are compared lower-cased with BFCL's punctuation
  stripping. int and float compare as numbers. Nested lists and dicts are compared recursively.
- An omitted parameter is allowed only if `""` is in its possible-answer list.
- Parallel categories are matched in any order.
- An irrelevance item passes only if there are zero calls.
- A known contamination risk: BFCL is public, and Hammer's training data targets BFCL-style tasks.

**(c) action_selector: 11 items.** This is the pinned State Packet cohort (`benchmarks/state_packet/questions_pinned.json`),
rendered by the unchanged `action_selector.cohort()`. The next move is offered as three tools:
`act`, `observe_more` and `escalate`. The gold comes from the pinned key: answer→act, abstain→observe_more,
conflict→escalate. The class mix is 8/2/1, and the `rule_always_act` baseline gets 8/11.

## Metrics (per arm)

- **exact-call accuracy** per suite and category, and pooled over all 184 items. This is the primary metric.
- **argument validity:** among items where the arm emitted at least one call, the share where every call
  names an offered tool and its arguments validate against that tool's JSON schema. The check covers type,
  required fields, enum, min/max and `additionalProperties:false`.
- **irrelevant-tool rejection:** the BFCL irrelevance pass rate, which means no call was made.
- **latency:** client wall-clock p50 and p95 in ms (mbp→groot over the tailnet), plus llama.cpp
  server-side `prompt_ms + predicted_ms` p50 and p95.
- **cold start:** time from unloaded until the first warm-up reply.
- **VRAM:** memory held by the bench router's processes after load, and the sampled peak during the run.
  The total GPU memory used is also recorded, because the GPU is shared with Ollama and the farm.
- **parse path:** the shares of native, content-fallback and none.
- **Errors:** an HTTP error, timeout or unparseable reply counts as zero calls, and is never scored as a
  pass. In particular, an error is never an irrelevance pass.

## Decision rule (frozen)

The comparator is `qwen3_8b`, the farm's default local model. All comparisons are paired on the same items
with an exact McNemar test, two-sided, over the pooled 184 items and per suite.

1. A candidate is **reported better** than qwen3_8b on a suite only if its exact count is higher *and*
   McNemar p < 0.05. If it is higher without significance, it is reported as "not distinguishable".
2. A candidate is **eligible for a shadow tool-calling role** only if all of the following hold:
   - pooled exact >= qwen3_8b minus 5 items;
   - BFCL irrelevance rejection >= 0.80;
   - z0_route `form_exact` >= 0.90;
   - argument validity >= 0.95;
   - bench VRAM < qwen3_8b's;
   - client p50 <= qwen3_8b's.

   These are cheaper-or-equal substitutes that are not materially worse. Otherwise, a candidate is eligible
   if it is better than qwen3_8b under rule 1 on pooled accuracy.
3. Eligibility changes nothing at runtime. The manifest records the reproduced numbers and receipt ids;
   `promotion_state` stays `candidate`. No routing config, `local_order` or role default changes.
4. **functiongemma_270m** is run zero-shot only. Its card says it is meant to be fine-tuned before use, so a
   zero-shot null does not reject it from the tiny-specialist role. It only records that it is not usable
   off the shelf.
5. Nulls are reported as nulls. The action-selector suite has n=11 and only 3 non-ACT items, so it cannot
   separate arms statistically and is reported descriptively.

## Outputs

- `~/.z0int/research/tool_calling_portfolio/raw_v0.jsonl` holds the private raw rows, including model
  text, and `serving_v0.json` holds serving metadata.
- The committed files are `results_v0.json` (aggregates, serving metadata and McNemar tests) and
  `rows_v0.jsonl` (per arm and item: pass flags, parse path and latency, with no text).
- Manifest: `manifests/local_cognition.v1.json` gets `reproduced_results` entries (kept separate from
  `source_reported_benchmarks`) under one receipt id per arm, plus groot `measurements`.

## Deviations

Both deviations below are adapter fixes found during the smoke run (one item per category per arm). They
were committed before the scored run, and neither is item-specific:

1. **FunctionGemma.** llama.cpp b11270 returns no native `tool_calls` for this GGUF, and generation does not
   stop at `<end_function_call>`. The model goes on to role-play `<start_function_response>…` turns.
   `<start_function_response>` is the model's documented turn boundary, so the FunctionGemma fallback now
   parses only the text before the first `<start_function_response>`. The extra generation is left in the
   latency numbers, since that is how the stock template behaves on this runtime.
2. **Hammer 2.1.** Both sizes often emit a Python-literal list (single quotes) in a code fence instead of
   JSON. When no JSON value parses, the JSON-array fallback now tries `ast.literal_eval` on the
   fence-stripped body.

Smoke observations, not scored: all 8 arms loaded and answered. Qwen3.5-4B/9B, Qwen3-8B/14B and
Nemotron returned native `tool_calls`. Hammer and FunctionGemma used content fallbacks.

## Results (added after scoring; run 2026-09-30/10-01 on groot, 0 transport errors)

Scored by `score.py`. Aggregates are in `results_v0.json` and per-item flags in `rows_v0.jsonl`.
Comparator: qwen3_8b. McNemar is exact and two-sided, over the pooled 184 items.

| arm | pooled exact | z0_route exact (form) | BFCL exact | irrelevance rejection | action selector | args valid | client p50 / p95 ms | bench VRAM MiB | cold ms | vs qwen3_8b pooled |
|---|---|---|---|---|---|---|---|---|---|---|
| functiongemma_270m (bf16, zero-shot) | 63/184 | 0/48 (0) | 61/125 | 0.84 | 2/11 | 95/119 | 3274 / 9781 | 892 | 2291 | worse, p=2e-22 |
| hammer2.1_3b | 81/184 | 0/48 (0) | 78/125 | 0.88 | 3/11 | 85/96 | 619 / 2005 | 2504 | 3590 | worse, p=2e-17 |
| qwen3.5_4b | 137/184 | 22/48 (31) | 106/125 | 0.84 | 9/11 | 155/155 | 1454 / 4204 | 3486 | 5247 | worse, p=0.028 |
| hammer2.1_7b | 86/184 | 3/48 (6) | 81/125 | 0.84 | 2/11 | 104/138 | 658 / 1983 | 5052 | 7108 | worse, p=1e-16 |
| **qwen3_8b** (farm default) | **152/184** | 29/48 (38) | 113/125 | 0.76 | 10/11 | 163/163 | 1069 / 2832 | 6004 | 8875 | n/a |
| qwen3.5_9b | 121/184 | 9/48 (14) | 103/125 | 0.80 | 9/11 | 155/155 | 1697 / 5396 | 5850 | 13073 | worse, p=2e-6 |
| qwen3_14b (farm) | 150/184 | 26/48 (38) | 114/125 | 0.92 | 10/11 | 160/160 | 1601 / 4037 | 9872 | 18788 | not distinguishable (16 vs 18, p=0.86) |
| nemotron_orchestrator_8b (secondary) | 148/184 | 28/48 (38) | 110/125 | 0.72 | 10/11 | 164/164 | 953 / 2420 | 6004 | 14402 | not distinguishable (4 vs 8, p=0.39) |

Decision rule outcome: **no candidate is better than qwen3_8b, and none is eligible for a shadow
tool-calling role.**

- **Rule 1 (better).** No arm beats qwen3_8b on any suite. Qwen3-14B and Nemotron are statistically
  indistinguishable from it on every suite. Every new candidate (Qwen3.5-4B/9B, Hammer2.1-3B/7B,
  FunctionGemma) is significantly worse pooled.
- **Rule 2 (cheaper substitute).**
  - Hammer 3B/7B pass the VRAM and p50 gates, but fail accuracy (pooled 70 and 66 items behind), z0 form
    and argument validity.
  - Qwen3.5-4B uses less VRAM (3.5 vs 6.0 GB) but is slower at p50 (1454 vs 1069 ms) and 15 items behind.
  - FunctionGemma is slower than every other arm, because generation runs on to `max_tokens` in 120 of 184
    replies.
- **Gate defect, found while scoring.** The `z0_route form_exact >= 0.90` gate cannot be met by any arm.
  The frozen gold for the 5 delegate_worker items compares `task` with the whole "Use provider P with
  model M for this: …" line, and every arm put only the subtask there, which is arguably the right call. So
  form_exact is capped at 43/48 = 0.896.
  - A post-hoc sensitivity analysis compares those 5 items against the subtask instead. It is labelled
    `posthoc_sensitivity` in `results_v0.json` and is not part of the pre-registered score.
  - Under that analysis: qwen3_8b 157/184 (z0 34/48, form 43/48), qwen3_14b 155, nemotron 153,
    qwen3.5_4b 142, qwen3.5_9b 126, hammer2.1_7b 91, hammer2.1_3b 85, functiongemma 63.
  - The ranking and every eligibility verdict are unchanged.

Findings:
- **z0 route_worker formation is the discriminating suite.** The BFCL subset is close to saturated for the
  Qwen-family arms: 103 to 114 of 125.
- **Qwen3.5-9B ignores the routing instruction.** On 24/43 route_worker items it calls `delegate_worker` (route_worker 15, no call 4)
  without a named provider or model. Its ids and task copying are fine. This is why 9B scores below 4B on
  z0_route (9 vs 22 exact). It is a no-think result only.
- **Hammer 2.1** runs through its own GGUF chat template (Hammer's task/format instruction, JSON-list
  output) and the content fallback.
  - It is BFCL-competent on `multiple`: 3B 21/25, 7B 20/25.
  - It is weak on `parallel`: 9 and 11 of 25.
  - It almost never forms a valid z0 MCP call. Typical failures: it returns `[]`, invents a tool named after
    the function (for example `coding_implementation`), or paraphrases or shortens `task`.
  - Its argument validity is 0.75 to 0.89. BFCL is public, and Hammer is tuned for BFCL-style tasks, so its
    BFCL numbers are an upper bound.
- **FunctionGemma zero-shot is not usable off the shelf.** It made no valid z0 call. llama.cpp b11270 does
  not parse its native tool calls, and it role-plays tool responses. Per rule 4, this does not reject it
  from the fine-tuned tiny-specialist role.
- **Irrelevance rejection is the weak spot of the farm default.** qwen3_8b rejects 19/25 and nemotron
  18/25. qwen3_14b is best at 23/25.
- **Action selector (n=11, descriptive only).** Only qwen3_14b and nemotron chose `escalate` on the single
  conflict item. Everything else is within one item of `rule_always_act` (8/11) or below it.
- **Latency** for z0_route items is dominated by copying the request into `task`. Requests run up to 2110
  characters.
- **VRAM** is the bench-router process only, at `-c 8192 --parallel 2`. The GPU was shared with Blender and
  Ollama during the run: other processes held 0.6 to 4.3 GB. All arms, including the 14B at 9.9 GB, loaded
  without error.

The manifest records each arm's reproduced numbers as `reproduced_results`, under receipt
`tool-calling-portfolio-v0:<arm>:7fd542f9ebe2`, plus a groot `measurements` row. `promotion_state`,
`role_defaults`, routing config and `local_order` are unchanged. Note that the existing `role_defaults`
in `manifests/local_cognition.v1.json` name hammer2.1_3b as the `general_function_caller` and
`tiny_action_specialist`, qwen3.5_4b as the `semantic_orchestrator` and qwen3.5_9b as the
`general_local_fallback`. These reproduced results do not support any of those defaults over qwen3_8b.
Changing them is left as an explicit decision.
