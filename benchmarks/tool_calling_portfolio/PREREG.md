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

(None at registration.)
