# TencentDB L1/L2/L3 model selection: prior research and candidate access

Date: 2026-10-03. This is a read-only research pass. Nothing was deployed and no live config was changed.

Owner question: "can we use a local SLM? if not we should run some tests to find the optimal model -
possibly grok 4.7 low or luna xhigh. does speed matter here more or intelligence? can you see what we
previously decided when researching the various models used for tencentDB?"

## TL;DR

1. **Nothing was ever decided on evidence.** The 2026-09-19/20 "TencentDB L1/L2/L3 model shootout"
   (hermes:20260919_201004_ec2c4f) designed a good experiment, but it never ran a single cloud
   benchmark. Its first "final recommendation" made up its numbers. Its own Phase E1 audit then
   classified **about 95% of the claims as ESTIMATED/UNTESTED**. The run was blocked by a credential
   mix-up (an NVIDIA `nvapi-` key sitting in `OPENROUTER_API_KEY` in `.env`), not by network.
   The `/tmp/tencent-memory-eval/` artifacts are gone.
2. **The only models that ever actually ran L1:**
   - `grok-4-1-fast-non-reasoning` on api.x.ai with an xAI OAuth token, from 2026-08-04. It was chosen
     only because no OpenRouter key was set. It produced **0 L1 records and 0 scenes** in its lifetime.
   - `nvidia/nemotron-3-super-120b-a12b:free` on OpenRouter, the current interim model (2026-10-03).
     It passed the synthetic E2E canary in 15 s.
3. **Owner's standing preferences:**
   - 2026-08-03: "let's just use the free models" (OpenRouter).
   - 2026-09-19 shootout brief: decide empirically on a Pareto frontier (quality, reliability,
     latency, tokens, $, rate limits, privacy). Use matched deterministic controls. **L1 reasoning
     OFF first.** L3 optimises for quality, because it runs rarely. Do not deploy the winner without
     approval.
4. **Local SLM:** one data point only, and it is not encouraging. Ollama `qwen2.5:3b` and
   `qwen2.5-coder:7b` scored 0% schema compliance on the real TencentDB prompts, at about 10 s warm on
   the GPU. That was later downgraded to "INCONCLUSIVE until stronger local models are tested".
   The qwen3 4B/8B/14B models now on z0-farm-llama were **never tested**. L2 requires multi-round
   tool calls, which is the weakest area for small models.
5. **Speed vs intelligence: intelligence matters more.** L1/L2/L3 run in the background. The per-turn
   hot path (prefetch/search) is BM25 with no LLM, at p50 about 3 ms. Speed matters only for backlog
   and for the 300 s LLM timeout. Errors persist: false memories, broken JSON and failed L2 tool loops
   stay in memory. Reasoning tokens still cost money and can truncate output, so "more thinking" is
   not free.
6. **Candidates (resolved):**
   - "grok 4.7 low" = xAI `grok-4.7`, reasoning_effort `low`.
   - "luna xhigh" = OpenAI `gpt-5.6-luna`, reasoning_effort `xhigh`.
   - Both are reachable today through subscription CLIs (smoke-tested) and through Vercel AI Gateway
     ($4.9995 credit). Neither is reachable through OpenRouter: the key's credit limit is $0.
   - **The TencentDB gateway cannot pass a reasoning_effort.** See section 5.

## 1. What was researched before (chronological)

| Date | Session / artifact | What happened | Model outcome |
|---|---|---|---|
| 2026-08-03/04 | hermes:20260803_215944_bcaf13 (msgs 119-210) | First install of TencentDB v2.0.0 (0aff21a). Owner (msg 171): "I can authenticate open router, let's just use the free models". The agent instead used the existing xAI OAuth token. | `grok-4-1-fast-non-reasoning` @ `api.x.ai/v1`. Rationale: "OpenRouter key wasn't set". It carried a token-expiry caveat. No quality evaluation. |
| 2026-08-23 | `portfolios/chief-of-staff/artifacts/memory-sota-luna-20260823/SYNTHESIS.md` | 10 research lanes, each run on **gpt-5.6-luna** ("You are one of ten gpt-5.6-luna lanes"). Topic: overall personal-memory architecture. | **No L1/L2/L3 model choice.** TencentDB was listed only as "license unclear; clarify before dependency use". The relevance to the owner's question is that luna was trusted for deep research work. |
| 2026-09-17 | grok:01a0b06a-… (msgs 242-340) | Stack brought back with `PULL=1 start-all.sh`. | Same xAI config. Not evaluated. |
| 2026-09-19 | hermes:20260919_120228_b9b756 | Read-only memory architecture probe. One line of discussion proposed **per-task model observability** (`task = l1_extract / l1_dedup / l1_conflict`) before adding `llm.tasks.*` routing. | No model decision. |
| 2026-09-20 | hermes:20260919_201004_ec2c4f, user msg 0 | **The shootout brief** (about 20 KB, verbatim copy in `scratch/shootout-prompt.txt`). | See section 2. |
| 2026-09-20 | same session, assistant msg 1692 | "Final recommendation" (`scratch/shootout-1692.txt`). | L1 nemotron-3-super:free (json_object, reasoning none). L2 deepseek-v4-flash:free. L3 nemotron-3-ultra:free (reasoning medium). Jev as dedup/gate. Local qwen2.5 3b/7b. **All numbers invented.** |
| 2026-09-20 | same session, user msg 1705: "PHASE E1 — FALSIFY" | Owner: "much of it is still hypothesis… DO NOT deploy". | E1 result (assistant msg 2446): claim audit says 95% ESTIMATED/UNTESTED. Cloud models: **0% tested**. Local qwen2.5: 0% schema, 0% dedup accuracy, 30-35 s per call. |
| 2026-09-20 | same session, assistant msg 2734 (E1.1 diagnosis) | Network was fine. The cause was the wrong key in `.env`. The valid OpenRouter key is in BWS (`OPENROUTER_API_KEY`). | Local warm GPU: qwen2.5:3b p50 10.1 s (about 10 tok/s). Ollama has no JSON-schema mode and no residency. Corrected claim: "**Viability INCONCLUSIVE until stronger local models tested**." |
| 2026-09-20 | omo:01a0bd55-… | Hermes `clean` profile built with nemotron-3-super:free as its main model. TencentDB deliberately excluded. | Not a TencentDB decision. |
| 2026-09-2x | `oss/INTELLIGENCE-STACK-EVAL-v0.md` §7/R6.1 | "TencentDB is off the critical path" (it had never been built from source on the host). | No model decision. |
| 2026-10-03 | job 5bc3424d, `ops/tencentdb/README.md` | systemd unit deployed. Interim LLM chosen because it is the free model the existing OpenRouter key can call ($0 credit limit). E2E canary passed: L1 persona memory within 15 s. | `nvidia/nemotron-3-super-120b-a12b:free` (interim; not benchmarked). |

**No later session after 2026-09-20 ran or decided a TencentDB L1/L2/L3 model.** Checked by FTS
over agentsview for `tencentdb|tdai|memory_tencentdb` together with model terms, and `nemotron`
together with tencent terms, after 2026-09-20.

## 2. Prior methodology the owner already approved (reuse it)

From the 2026-09-20 brief (`scratch/shootout-prompt.txt`) and E1 (`scratch/e1-prompt.txt`):

- Decide on a **Pareto frontier**, not a single score. Axes: quality, reliability, latency, tokens,
  $, rate-limit friction, privacy/locality.
- Include **mandatory non-LLM controls**:
  - A: L0 + BM25 only.
  - B: deterministic extraction heuristics.
  - C: recency/deterministic scene policy.
- Test each layer separately:
  - L1 = strict structured extraction. Metrics: precision, false-memory rate, JSON validity.
    **Reasoning OFF first.**
  - L1 dedup = a 4-way decision: STORE/UPDATE/MERGE/SKIP.
  - L2 = a multi-round **tool-calling** edit agent (read/write/edit).
  - L3 = persona/doctrine synthesis. Test drift over 10 sequential updates. **Optimise quality
    first**, since it runs rarely.
- Run an end-to-end memory exam with a fixed answerer/judge and hidden questions.
- Use successive halving, from round 0 (compatibility) to round 3 (finalists), with at least 3
  repetitions for finalists.
- Prior hypotheses (do not bend results toward them):
  - H1: a strong model with reasoning OFF plus constrained output beats a reasoning-heavy model on L1.
  - H2: a local ~4B model may be on the frontier.
  - H5: a bigger model is worth it for L3.
  - H7: some model wins disappear against deterministic controls.
- **Corpus must be synthetic or sanitized.** No Takeout, keyring or secrets.

## 3. Local SLM: can we use one?

Evidence to date:
- qwen2.5:3b (Ollama, RTX 3080 Ti):
  - Real TencentDB prompts produced **0% schema-valid output on the complex schemas**.
  - Warm p50 10.1 s; about 10 tok/s generation; no KV/prefix cache.
  - qwen2.5-coder:7b took about 32 s cold.
  - Source: E1.1, msg 2734.
- The prompts are large, mostly Chinese, and demanding:
  - `MemoryCore/src/core/prompts/l1-extraction.ts`: 18 KB
  - `l1-dedup.ts`: 12 KB
  - `scene-extraction.ts`: 31 KB
  - `persona-generation.ts`: 15 KB
- The gateway sends **no `response_format`**: JSON is prompt-only (see section 5). That is a large
  disadvantage for small models.
- L2 runs the AI-SDK multi-step tool loop (`stopWhen: stepCountIs(n)`). The upstream source itself
  says weak small models hallucinate tool calls (llm-runner.ts line 223 comment).

Available now but **never tested** on TencentDB:
- z0-farm-llama router `100.113.138.100:11530` (active), serving:
  - `qwen3-0.6b-q8`
  - `qwen3-1.7b-q4km`
  - `qwen3-4b-q4km`
  - `qwen3-8b-q4km` / `qwen3-8b-q6k`
  - `qwen3-14b-q4km`
  - `nemotron-orchestrator-8b-q4km`
- System Ollama `127.0.0.1:11434`: qwen2.5:3b, qwen2.5-coder:7b, qwen2.5vl:3b, nomic-embed-text.
- GPU: 12 GB, shared with the Quackles arbiter.

Assessment:
- **L1 with local qwen3-8b/14b is plausible but unproven.** It is worth one free screening arm under
  the quiet-lane flock.
- **L2 locally is unlikely** to be reliable.
- Privacy is the main argument for local: it is the only option where conversation text never
  leaves the host.

## 4. Speed vs intelligence

Measured facts about where the LLM sits:

- **Per turn**, Hermes calls `/v3/atomic/search`, `/v3/core/read` and `/v3/scenario/ls`, then a
  background `/v3/conversation/add`. All are BM25/SQLite and use no LLM.
  - Measured p50: prefetch 3.1 ms, search 1.2 ms, add 5.5 ms (README e2e).
- **L1** runs in the background every 5 conversations (`pipeline.everyNConversations: 5`) or after
  600 s idle (`l1IdleTimeoutSeconds`).
- **L2/L3**: persona every 50 (`persona.triggerEveryN: 50`).
- Timeout is 300 s (`llm.timeoutMs`). maxTokens is 32000.

So **latency is not user-visible**. Speed matters only if extraction falls behind ingestion, or a
call hits the 300 s timeout.

Errors, on the other hand, are durable:
- a false memory gets injected into every future prompt;
- a misattributed third-party fact sticks;
- broken JSON means a lost batch;
- an L2 tool-loop failure leaves no scenes. Historically there were 0 L1 records and 0 scenes.

**Therefore: optimise for quality and reliability first, then cost; treat speed as a constraint, not
a goal** (for example, p95 well under 300 s).

Nuance from the owner's own prior hypothesis H1: for L1, "intelligence" means precise schema-following,
not deep reasoning. Heavy reasoning raises cost and truncation risk.
- **grok-4.7 low** fits the L1 profile.
- **luna xhigh** fits L3 better (rare, judgement-heavy). It is likely overkill and slow for L1.
- Test both on both layers rather than assuming.

## 5. Gateway constraint: reasoning effort cannot be set today

- `MemoryCore/src/adapters/standalone/llm-runner.ts` calls
  `generateText({ model: createOpenAI(...).chat(model), system, prompt, tools?, maxOutputTokens })`.
- There is no `providerOptions`, no `reasoning_effort` and no `response_format`.
- Config exposes only `TDAI_LLM_BASE_URL` / `TDAI_LLM_MODEL` / `TDAI_LLM_API_KEY` / `MAX_TOKENS` /
  `TIMEOUT_MS` (config.ts:445-453).
- One model serves every layer in this standalone gateway.
  - The brief notes `extraction.model` (L1) vs `persona.model` (L2+L3) slots in the plugin path.
  - These were not verified in this image.

Deploying "grok-4.7 **low**" or "luna **xhigh**" exactly would need one of:
- a tiny loopback OpenAI-compatible shim that injects `reasoning_effort`, or
- a provider-side model alias / default effort.

The benchmark harness should call providers directly, with the gateway's exact prompts and an
explicit effort. Then it should confirm the chosen config through the real gateway.

## 6. The two owner-named candidates

| | grok 4.7 low | luna xhigh |
|---|---|---|
| Model | xAI **Grok 4.7** ("SpaceXAI's latest frontier model"). OpenRouter `x-ai/grok-4.7`, Vercel `spacexai/grok-4.7`, grok CLI `grok-4.7`. The CLI resolves it to `grok-4.7-build`. | OpenAI **GPT-5.6-Luna** ("Older fast and efficient model"). OpenRouter/Vercel `openai/gpt-5.6-luna`, Codex `gpt-5.6-luna`. |
| Reasoning effort | `low` (supported: low / medium / high default / xhigh) | `xhigh` (supported: low / medium default / high / xhigh / max) |
| Context | 256k (500k option); OpenRouter lists 500k | 272k (max 872k); OpenRouter lists 1.05M |
| Price per 1M tokens (in / out), live catalogs 2026-10-03 | **$2.00 / $6.00** (OpenRouter and Vercel). The fast variant costs 2x. | **$0.20 / $1.20** (OpenRouter and Vercel). `:batch` costs half. `-fast` costs 2x. |
| Structured output | OpenRouter lists `response_format`, `structured_outputs`, `tools`, `reasoning_effort` | same, plus `verbosity` |
| Path A: subscription CLI ($0 marginal) | `grok -p … -m grok-4.7 --reasoning-effort low --json-schema … --output-format json`. Logged in through SuperGrok OAuth (`~/.grok/auth.json`, refresh token; endpoint `cli-chat-proxy.grok.com/v1`, Responses API). **Smoke OK**: "OK", 12.3k input tokens (agent system prompt overhead), 317 reasoning tokens, CLI-reported notional cost $0.0107. | `codex exec --skip-git-repo-check --ephemeral -s read-only -m gpt-5.6-luna -c model_reasoning_effort='"xhigh"' --json [--output-schema F]`. Logged in using ChatGPT. **Smoke OK**: "OK", 18.0k input tokens of harness overhead, 10.2 s wall. |
| Path B: OpenAI-compatible API (needed for the gateway itself) | Vercel AI Gateway `https://ai-gateway.vercel.sh/v1`, BWS `AI_GATEWAY_API_KEY`. Balance **$4.9995** (the monthly credit). OpenRouter is **blocked**: BWS `OPENROUTER_API_KEY` has limit $0. No xAI API key exists in BWS. | Same Vercel path. BWS `OPENAI_API_KEY` is **not usable**: it is a JWT-style token and returns 403 "Missing scopes: api.model.read". |
| Fit (hypothesis only) | L1 extraction / L2 tool loop | L3 synthesis; L1 if the low-effort result is good |
| Rough cost per 100 Hermes turns (ESTIMATE: 20 L1 calls x about 8k in / 1k out, before reasoning) | about $0.44 plus reasoning tokens | about $0.06 plus xhigh reasoning (possibly 5-10k output tokens per call, adding about $0.1-0.25) |

Caveats:
- The CLI paths add 12-18k tokens of agent system prompt per call and wrap the model in an agent
  persona. They are fine for a free quality screen, but are not identical to the gateway's bare
  system+prompt call. Use the Vercel API path for the final numbers.
- The grok CLI session token expires (`expires_at` 2026-10-03T22:32Z, refreshed by the CLI). It is
  not a production credential for the systemd unit.
- Vercel credit is only $4.9995, which is also the $5 task cap. A rough budget for a 20-fixture x
  3-repeat L1 screen:
  - grok-4.7 low: about $1.5
  - luna xhigh: about $0.7
  - Plan the L2/L3 rounds within what remains.
- Privacy: any cloud arm must receive **synthetic fixtures only**. Production TencentDB currently
  sends real conversation text to the OpenRouter free endpoint (provider Nvidia), which may log or
  train on it (README "Known issues").

## 7. Recommended next step (for the benchmark task, not done here)

1. Rebuild the E1 fixtures (synthetic) under `model-select/` instead of `/tmp`. Use the gateway's
   exact L1/L1-dedup/L2/L3 prompts from `MemoryCore/src/core/prompts/*` @0aff21a.
2. Arms:
   - Controls A/B/C.
   - Current interim `nemotron-3-super:free` (baseline).
   - Local `qwen3-8b` / `qwen3-14b` via the farm router, under the quiet-lane flock.
   - `grok-4.7` low.
   - `gpt-5.6-luna` xhigh, plus luna low as a matched control for "is xhigh worth it".
3. Screen for free first through the CLIs or local. Spend Vercel credit only for finalist numbers on
   the bare API path. Hard cap $5.
4. Decide per layer on the Pareto frontier, with quality first (section 4). If the winner needs a
   specific effort, add the loopback shim (section 5).

## Spend and safety ledger for this pass

- Paid calls:
  - one grok-4.7 low smoke through the subscription CLI (notional $0.0107);
  - one gpt-5.6-luna xhigh smoke through the ChatGPT-login Codex CLI ($0 marginal).
  - Both prompts were "Reply with exactly: OK" (synthetic).
- No OpenRouter or Vercel inference spend. Free metadata calls only: OpenRouter `/models` and `/key`,
  Vercel `/models` and `/credits`, OpenAI `/models` (403).
- Secrets: BWS was read through the keyring mechanism from `bin/tencentdb-memory-run`. Only names
  and id prefixes were printed. No values appear in output or files.
- No processes killed. No live Hermes, harness or TencentDB config was touched. Local LLM probes were
  limited to `/v1/models`, `/api/tags` and `/api/ps` (no inference).
- Scratch: `model-select/scratch/`, holding the extracted prompts, the final messages and the
  catalog JSON.
