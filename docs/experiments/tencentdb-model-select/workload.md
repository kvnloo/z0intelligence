# TencentDB Agent Memory: LLM workload and candidate endpoints

Date: 2026-10-03. Sources: `/mnt/zer0models/oss/TencentDB-Agent-Memory` @0aff21a
(MemoryCore), the live config `../tdai-gateway.yaml`, the journal of
`tencentdb-memory.service`, Hermes `chiefstaff` `state.db` (counts and lengths only),
and read-only probes of local endpoints. No message content was read or recorded. No
inference was run against any model, local or cloud. No paid calls were made.

## TL;DR

1. **Intelligence matters more than speed.** No LLM call is on a user-facing path.
   Search, read and add answer in milliseconds without an LLM (p95 under 11 ms). L1, L2
   and L3 run in a background pipeline that already waits on purpose: L1 runs every 5
   turns or after 10 minutes idle, and L2 waits at least 90 s after L1. A model that
   takes 3 s and one that takes 30 s give almost the same memory freshness. Wrong JSON,
   invented memories or broken tool edits, on the other hand, are lost or stored for
   good.
2. **Latency budget.** The code's hard timeouts are L1 180 s, dedup 180 s, L2 300 s and
   L3 180 s. A practical target is L1 p95 under 60 s and L2 under 120 s. Speed still
   matters in two ways: (a) when GPU time is shared, a slow local model keeps the
   Quackles/quiet-lane GPU busy longer; (b) on peak days (about 400 L1 calls) a very
   slow, reasoning-heavy model still has to keep up.
3. **A local SLM is plausible for L1 and dedup only, and only with a dedicated context
   of at least 16k (better 32k).** It cannot run on the z0-farm-llama router as it is
   configured today: `-c 8192 --parallel 2` gives about 4k tokens per slot, but a
   typical L1 prompt is 3k to 6k tokens and the heavy end is 15k or more. L2 and L3 are
   multi-step tool-calling agents (read/write/edit with exact `oldText` matching) driven
   by a system prompt of about 8k tokens. That is weak ground for 3B to 8B models. The
   benchmark must test this, not assume it.
4. **Prior decision.** The 2026-09-19/20 Hermes session designed a shootout but **never
   ran it**. Its "recommendations" were desk estimates, and some of the file paths it
   cited do not exist (details below). The only things ever actually run were
   `grok-4-1-fast-non-reasoning` (picked because the xAI OAuth token happened to be
   available, not from research) and now `nemotron-3-super-120b-a12b:free`, which
   worked in the synthetic e2e.

## 1. Every LLM call the gateway makes

All calls go through `StandaloneLLMRunner` (`src/adapters/standalone/llm-runner.ts`),
which uses Vercel AI SDK `generateText` with `createOpenAI({compatibility:"compatible"})`
→ **`POST {baseUrl}/chat/completions`**. Behaviour that matters when choosing an
endpoint:

- It sends **no `temperature`, no `response_format`/JSON schema, and no reasoning
  parameter**, so the provider's defaults apply. llama-server defaults to temp 0.8 and
  OpenRouter to 1.0. JSON validity depends only on prompt following. Grammar or schema
  enforcement needs a gateway patch or a server-side default.
- `maxOutputTokens` = `llm.maxTokens` = **32000** (from our yaml) on every call that does
  not override it. Some endpoints reject `input + 32000 > context` with a 400 error.
  Small local contexts clamp it. Lower this when testing small-context models.
- There is one `baseUrl` + `model` for everything. `extraction.model` (L1) and
  `persona.model` (L2+L3) can override only the *model name*: the runner strips
  everything up to the first `/`. They cannot override the endpoint. A hybrid such as
  local L1 + cloud L2/L3 therefore needs an OpenAI-compatible router in front, or a
  patch.
- The gateway requires a non-empty `apiKey` to enable the LLM. A local endpoint needs a
  dummy key.
- All prompts are **Chinese system prompts**. They tell the model to write free-text
  output in the language of the user's messages and to keep JSON keys and enums in
  English.

| Call (taskId) | Where | Tools | Output contract | Timeout | Trigger / volume |
|---|---|---|---|---|---|
| `l1-extraction` | `core/record/l1-extractor.ts` + `core/prompts/l1-extraction.ts` | none | **Bare JSON array** `[{scene_name, message_ids[], memories:[{content, type: persona\|episodic\|instruction, priority:int, source_message_ids[], metadata:{activity_start_time?, activity_end_time?}}]}]`. Parser strips ```` ``` ```` fences, regex-grabs the first `[...]`, repairs only known scalar fields, and drops everything on failure (`NO_JSON`). Max 20 memories per session (yaml). | 180 s | Per session after `everyNConversations=5` turns or `l1IdleTimeoutSeconds=600`. Warm-up 1→2→4→5. Input = last **10 new + 5 background** L0 messages, **not truncated** (the 5000-char cap is commented out in `utils/sanitize.ts`). |
| `l1-conflict-detection` (dedup) | `core/record/l1-dedup.ts` + `core/prompts/l1-dedup.ts` | none | Bare JSON array `[{record_id, action: store\|update\|skip\|merge, target_ids[], merged_content, merged_type, merged_priority:int, merged_timestamps[]}]` | 180 s | After an L1 run, only if FTS5 finds candidates (embedding=none, top 5 per new memory). One batched call per L1 run. |
| `scene-extract-*` (L2) | `core/scene/scene-extractor.ts` + `core/prompts/scene-extraction.ts` | **read/write/edit**, sandboxed to `scene_blocks/`, ≤20 steps | Edits scene Markdown files through tool calls. The final text is mostly ignored. | 300 s | `l2DelayAfterL1Seconds=90`, at least 900 s and at most 3600 s apart per session, max 15 scenes. Uses `persona.model`. |
| `persona-generation` (L3) | `core/persona/persona-generator.ts` + `core/prompts/persona-generation.ts` | **read/write/edit**, ≤20 steps | Writes the persona Markdown file through tools. | 180 s | First run, then every **50 new memories** (`persona.triggerEveryN`). Uses `persona.model`. |
| `skill-extract-*` | `core/skill/skill-extractor.ts` | caller tools, ≤16 iterations | SKILL.md through tool calls | runner default | Only via an explicit `/skill/extract` API call. **The Hermes plugin never calls it**, so 0 per day. |
| Search-time | `/v3/atomic/search`, `/v3/core/read`, `/v3/scenario/ls\|read`, `/v3/conversation/search` | none | FTS5/BM25 | 5 s recall budget | **No LLM call** on any read path (checked in `gateway/`). |

User-facing path (Hermes plugin `memory_tencentdb`): it runs search + core read +
scenario ls in parallel before the turn, then `conversation/add` in a background thread
(at most 4 in flight). `conversation/add` only queues L0. Extraction runs later in the
pipeline worker. **The LLM is never awaited by a Hermes turn.**

## 2. Observed calls (journal, counts and sizes only)

Journal window: 2026-10-03 03:03 to 11:32 (the unit has run since 03:07:51).
**All 12 LLM calls came from the synthetic e2e run (tenant `z0-e2e`) between 03:04 and
03:07.** After the restart there were 2026 `GET /health` requests and **zero** add or
search requests. No real Hermes traffic has reached the gateway yet.

Endpoint: OpenRouter `nvidia/nemotron-3-super-120b-a12b:free`. 0 failures, 0 `NO_JSON`,
0 JSON repairs, 0 HTTP 429s.

| Call | n | Latency (ms) | Output chars | Input |
|---|---|---|---|---|
| l1-extraction | 7 | 5.6k–22.5k | 510–1497 | sys 2369 chars; user 514–1481 chars (2–10 new msgs, 0 bg); synthetic msgs are short |
| l1-conflict-detection | 3 | (within above) | | |
| L2 scene | 1 | ≈10.2k, 2 steps | | |
| L3 persona | 1 | ≈13.5k, 2 steps | | |
| **All 12** | 12 | p50 ≈ 13.4 s, max 22.5 s | 73–1497 | |

The free 120B endpoint sits at about 13 s per call. That is well inside every timeout,
and it produced a correct persona memory with the canary in about 15 s end to end.

## 3. Production-shape estimate (from Hermes chiefstaff history, counts only)

Real turns are much larger than the synthetic e2e ones. Over the last 30 days:

| Message | n | p50 chars | p90 | p99 | max |
|---|---|---|---|---|---|
| user | 4539 | 1340 | 7142 | 24579 | 47908 |
| final assistant | 4125 | 225 | 1012 | 3249 | 14262 |

Token estimate: a CJK-weighted heuristic. No tokenizer is installed, so treat it as
±30%.

- L1 system prompt is about 1.2k tokens.
- L1 user prompt for a full 15-message window: p50 about 12k chars ≈ **3–4k tokens**,
  p90 about 50k chars ≈ **13–15k tokens**. The tail can reach 80k+ tokens because input
  is untruncated.
- **L1 total ≈ 5k tokens typical, 15k heavy. Output 150–600 tokens.**
- Dedup: system prompt about 1k tokens + new memories + ≤5 candidates each ≈ 2–4k in,
  ≤600 out.
- L2: about 8k-token system prompt (the scene prompt file holds about 17k chars across
  both modes) + memories + scene files read by tools. Each step re-sends context, so
  about 10–20k per step × 2–6 steps.
- L3: about 4k-token system prompt + scene summaries, 2+ steps.

Volume over 60 days: 8830 user turns on 31 active days. Very bursty: 0 to 1941 per day.
Mid-September and mid-August bursts were 800–1900 per day. Since 2026-09-21 it has been
under 70 per day.

| Day type | Turns | L1 | Dedup | L2 | L3 | ≈ Input tokens/day |
|---|---|---|---|---|---|---|
| Quiet (now) | 0–30 | ≤8 | ≤5 | ≤3 | ~0 | ≤0.2M |
| Mean active | ~285 | ~60 | ~40 | ~15 | ~2 | ~1M |
| Peak | ~1900 | ~400 | ~300 | ~50 | ~10 | ~5M |

These are estimates. L1 runs about once per 5 turns per session after warm-up. L2 is
capped by the 900 s per-session minimum interval. The first real day on the gateway
should be used to replace them with measured numbers.

## 4. Latency budget and speed vs intelligence

- **User-facing budget for the LLM: none.** Recall p95 is 1.9 ms and add p95 is 10.5 ms
  (e2e). Neither depends on the model.
- **Freshness budget.** A memory becomes searchable after L0 → (wait for 5 turns or
  10 min idle) → L1. That trigger delay is minutes long, so LLM latency is a small part
  of it.
- **Hard ceilings.** Calls that hit the 180 s (L1/dedup/L3) or 300 s (L2) timeout are
  lost. With reasoning-heavy settings such as "xhigh", reasoning tokens and slow decode
  add up against these, so 15k-token heavy L1 windows must be tested at p95.
- **Throughput.** A peak day of about 400 L1 + 300 dedup calls averages one call every
  2 minutes. Even 30 s per call is a 25% duty cycle, which is fine for cloud. Locally it
  means GPU hours that other GPU users (Quackles arbiter, quiet-lane) lose.
- **Intelligence is what actually fails.** The contract is strict free-form JSON with
  no schema enforcement. The memories must be grounded: no invented facts, the correct
  type and priority, and correct `source_message_ids`. The model must follow long
  Chinese instructions while writing English content. L2/L3 also need dependable
  multi-step function calling with exact-match `edit` text. Each failure mode either
  silently drops data (NO_JSON → `[]`) or stores wrong memories permanently.

**Verdict:** use quality as the objective, subject to two limits: p95 latency inside
the timeouts, and cost and GPU duty cycle acceptable. Owner-named paid candidates: test
`grok 4.7 low` as the "fast and cheap enough" arm and `luna xhigh` as the "maximum
intelligence" arm. Luna xhigh must show its p95 on 15k-token L1 windows stays under
180 s and that its per-day cost at the peak volume above is acceptable.

## 5. Local candidates reachable now (inventory only, no inference run)

GPU: **RTX 3080 Ti, 12 GB**. At probe time 855 MiB was used, about 11 GB free, and
utilisation was 34%. No compute processes were listed and router models were unloaded.
The quiet-lane lock and ledger were active (ledger updated 11:33), so other lanes are
using the GPU.

### z0-farm-llama router `100.113.138.100:11530` (GET /v1/models only)

Process: llama.cpp b11270, `--models-max 2 -c 8192 --parallel 2 --reasoning off
--sleep-idle-seconds 900 -ngl 99`. The unit describes it as the "OFFLOAD tier for mbp
z0int", so **other tenants share it**.

| Model | Status | Per-slot ctx |
|---|---|---|
| qwen3-0.6b-q8 | unloaded | ~4k |
| qwen3-1.7b-q4km | unloaded | ~4k |
| qwen3-4b-q4km | unloaded | ~4k |
| qwen3-8b-q4km | unloaded | ~4k |
| qwen3-8b-q6k | unloaded | ~4k |
| qwen3-14b-q4km | unloaded | ~4k |
| nemotron-orchestrator-8b-q4km | unloaded | ~4k |

- It is OpenAI-compatible (`/v1/chat/completions`). Recent llama.cpp applies the jinja
  template, which tool calls need, by default. Tool calling has not been probed on
  these models.
- **As configured it is unusable for L1.** 8192 context split over 2 slots is smaller
  than a typical L1 prompt (5k) and far smaller than heavy L1 (15k) or L2.
- Raising `-c` changes behaviour for z0int, so it needs the router owner's agreement.
  It was not touched.

### Ollama

Two daemons:

- System `127.0.0.1:11434`, v0.16.1.
- User `127.0.0.1:11435`, v0.33.2.

Both list the same store. Neither has a model loaded.

| Model | Params / quant | Note |
|---|---|---|
| qwen2.5:3b | 3.1B Q4_K_M | Fits easily. Weakest JSON/grounding. Tool calls are supported in Ollama. |
| qwen2.5-coder:7b | 7.6B Q4_K_M | Code-tuned. Candidate for L2/L3 tool use. |
| qwen2.5vl:3b | 3.8B Q4_K_M | Vision model, not relevant. |
| nomic-embed-text | 137M F16 | Embedding model. Side option: switch `embedding.provider` from none to vector dedup. Not an LLM candidate. |

- Both expose `/v1/chat/completions`, and the gateway container uses `--network host`,
  so loopback is reachable.
- Risk: through `/v1`, Ollama applies its default `num_ctx` unless
  `OLLAMA_CONTEXT_LENGTH` or a Modelfile sets it. Long L1 windows would then be
  **silently truncated**, which looks like a quality failure but is not.

### VRAM fit (arithmetic estimate; f16 KV ≈ 144 KiB/token for qwen3-4b/8b)

| Option | Weights | KV @32k | Total | Fits 12 GB? |
|---|---|---|---|---|
| qwen3-4b q4km, 32k | ~2.5 GB | ~4.5 GB (q8 KV ~2.3) | ~5–7 GB | yes |
| qwen3-8b q4km, 32k | ~5 GB | ~4.5 GB (q8 KV ~2.3) | ~7.5–10 GB | yes, only when router/Quackles are idle |
| qwen3-8b q4km, 16k | ~5 GB | ~2.3 GB | ~7.5 GB | yes |
| qwen3-14b q4km, 16k+ | ~9 GB | ~3+ GB | >12 GB | no |

### Local SLM plausibility

- **L1 + dedup:** plausible with qwen3-8b (or 4b) on a dedicated llama-server instance
  with ≥16k context, `--reasoning off`, and a low server-side default temperature (the
  gateway sends none). Run it under the quiet-lane lock with idle sleep. It is unproven
  on grounding, type and priority accuracy, and JSON validity over real-length windows,
  and that is what the benchmark must measure.
- **L2 + L3:** doubtful. The ~8k-token Chinese tool-agent prompt, multi-step
  read/write/edit, and exact `oldText` matching are the known weak spot of 3–8B models.
  L2/L3 are low-volume (tens per day), so a cloud model here costs little.
- **Router as-is:** no (≈4k context per slot, shared tenant).
- **Hybrid** (local L1/dedup + cloud L2/L3): needs a small OpenAI-compatible router
  between the gateway and the providers, or a gateway patch, because the gateway has
  one `baseUrl`.
- **Privacy:** local is the only option that keeps real conversations on the machine.
  Every cloud candidate, including the current OpenRouter free endpoint, sees real
  turns in production. That is the owner's call, separate from the benchmark, which
  uses synthetic data only.

## 6. What was decided before (prior model research for TencentDB)

| When | What | Status |
|---|---|---|
| 2026-08-04 (hermes:20260803_215944_bcaf13) | `api.x.ai` `grok-4-1-fast-non-reasoning` using an xAI OAuth token | **Convenience, not research.** The notes say "OpenRouter key wasn't set. Used xAI OAuth". The token expired, and 0 L1 records were ever produced. |
| 2026-09-19/20 (hermes:20260919_201004_ec2c4f) | Brief for an empirical **shootout** across quality, reliability, latency, tokens, cost, rate limits and privacy. Rules: fixed model identities, test reasoning off first for L1, compare decoding modes (prompt-JSON vs json_object vs json_schema), add matched-control arms, synthetic corpus. | **Designed, never executed.** The final summary listed L1 `nemotron-3-super-120b-a12b:free`, L2 `deepseek-v4-flash-0731:free`, L3 `nemotron-3-ultra-550b-a55b:free`, all-local `qwen2.5:3b`/`qwen2.5-coder:7b`, and a "Jev" gate. Its latency and quality-loss numbers ("Local ~20–30% F1 loss", "L1 local p50 12 s") are **estimates with no runs behind them**. It cited patch targets that do not exist in this tree (`src/gateway/llm-resolver.ts`; `generate_memories()` in the plugin `client.py`) and a chiefstaff `memory.l1/l2/l3` yaml schema that the plugin does not read. `../README.md` confirms: "Model shootout was designed but never run against a live gateway." |
| 2026-10-03 | Interim `nemotron-3-super-120b-a12b:free` via OpenRouter (key has a $0 credit limit) | **Running.** It passed the synthetic e2e (§2). It is the only model with real gateway evidence. |

Parts of the earlier design to reuse: the methodology (fixed identities,
reasoning-off-first, decoding mode recorded separately, matched controls, synthetic
corpus). Discard its recommendations; they are not evidence.

## 7. Implications for the benchmark (next step)

- The corpus must include **real-length** synthetic windows: 10+5 messages with long
  user turns (p50 1.3k, p90 7k, p99 25k chars) and not only short e2e lines, plus
  English content with the Chinese system prompts.
- Score L1 on: JSON validity with the gateway's own parser (`parseExtractionResult`
  semantics), grounding/false-memory rate, type and priority accuracy,
  `source_message_ids` correctness, and p50/p95 latency on 5k and 15k windows. Score
  dedup on action accuracy. Score L2 on tool-call validity and edit success.
- Send requests exactly as the gateway does: no temperature, no response_format, and
  `max_tokens=32000` or the lowered value. A separate arm can measure server-side
  low-temp/grammar as a deployable option.
- Arms:
  - interim nemotron-super free (control)
  - grok 4.7 low
  - luna xhigh
  - local qwen3-8b q4km @32k on a dedicated port
  - local qwen3-4b
  - qwen2.5-coder:7b (L2 only)
- Paid cap is $5 total, so size the paid arms from the token estimates above.
- Run local arms under `flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock`,
  on a separate llama-server instance (not the shared router), and stop it afterwards.
