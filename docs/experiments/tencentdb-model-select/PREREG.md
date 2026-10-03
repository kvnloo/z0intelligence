# PREREG: TencentDB Agent Memory L1/L2/L3 model selection

Status: pre-registered 2026-10-03, committed before any candidate run. Anything below that is
changed after this commit is listed in a dated "Deviations" section of the final report, never
edited in place.

Owner question (2026-10-03): "can we use a local SLM? if not we should run some tests to find the
optimal model - possibly grok 4.7 low or luna xhigh. does speed matter here more or intelligence?"
Background: `research-prior.md`, `workload.md` (this directory).

## 1. Hypotheses (stated before data; results must not be bent toward them)

- H-local: a local model (qwen3 4b/8b/14b, nemotron-orchestrator-8b, qwen2.5) reaches L1+dedup F1
  within 0.05 of the best candidate while passing both hard gates.
- H1 (owner, 2026-09-20): a strong model with reasoning off/low beats a reasoning-heavy one on L1
  (here: grok-4.7 low vs luna xhigh).
- H5: a bigger/stronger model matters more for L2/L3 than for L1.
- H7: some model "wins" disappear against the deterministic control.
- Speed: intelligence matters more than speed (workload.md §4). Latency is reported and must fit
  the gateway's hard timeouts (180 s L1/dedup/L3, 300 s L2); it is not a ranking criterion.

## 2. Frozen inputs

| Input | Pin |
|---|---|
| Corpus `bench/corpus/corpus.json` (47 synthetic conversations, 56 sessions, 225 rounds, 79 required gold memories, 9 gold-empty conversations, 22 traps, 10 supersessions, 5 dedup checks, 7 synthetic secrets) | sha256 `fc9c8db8c9236e3690e8e5d38f0fa8197e14ef3b1dd936beabebfb833e3a2705`; rebuilt byte-identically by `bench/corpus/build_corpus.py` |
| Gateway code | deployed image `agentmemory/memory-core` id `sha256:55fec3a6067af7cc4dbb48017f590392cf0085f378b6bcac340a91690a9707ed` (package memory-tencentdb-v2 1.0.2-beta.1, built 2026-09-07). NB: its L1/dedup/scene/persona/runner code differs from the 0aff21a checkout (prompts identical); the image is what runs, so the image is what is tested |
| Gateway config | live `../tdai-gateway.yaml` sha256 `ecd167f3e418753711152551c9a96b427f5204be81f600b68df6fe940c59d2ea` (maxTokens 32000, timeoutMs 300000, promptMode chat, dedup on, maxMemoriesPerSession 20, everyN 5, warmup on, embedding none) |
| Candidates/routes | `bench/candidates.json` sha256 `68de6abf3aa898692f0197a7a2efa8b6d87ba71ca6f21a6df389cc604c7bd9f6` |
| Harness + scorer | this commit (`bench/harness/driver.ts`, `bench/harness/shim.py`, `bench/run_arm.py`, `bench/score/*.py`) |

Corpus: fully synthetic, fictional persona (Rowan / Ferrite Labs / Lantern / Pebble / Atlas); the
categories mirror the owner's real use: coding-agent sessions (A01-A08, incl. long log pastes and
one heavy ~10k-token window), project decisions (B01-B06), preferences (C01-C06),
corrections/supersessions (D01-D06), noise / nothing-to-extract (E01-E06), secrets that must not be
memorised (F01-F05, incl. a user explicitly asking to remember a password), multi-session updates
(G01-G06), hallucination traps (H01-H04: hypotheticals, third-party preferences, quoted docs,
sarcasm). Every secret is a seeded random string. No real conversation data is used anywhere, so
the corpus may be sent to cloud candidates. Known deviation from production: user-turn length p50
is ~90 chars (production chiefstaff p50 ~1.3k); the tail is covered (p90 ~1.2k, max 32k chars).

## 3. Harness (what "same as the gateway" means)

3.1 The driver runs inside the deployed image and calls the gateway's own `loadGatewayConfig()`
(live yaml + `TDAI_LLM_*` env), `initStores()` (SQLite + FTS5 exactly as configured),
`createL1Runner()` / `createL2Runner()` / `createL3Runner()` and `StandaloneLLMRunnerFactory`
(AI SDK `generateText` -> OpenAI-compatible `POST /chat/completions`, no temperature, no
response_format, `max_tokens` 32000, tools only for L2/L3). Prompts, templates, parsers
(`parseExtractionResult`, `parseBatchResult`), the L1 quality gate, FTS candidate recall
(session-scoped), the L1 writer (update/merge deletes targets), scene and persona generation are
all the gateway's code, unmodified.

3.2 L0 is written per Hermes turn exactly like `POST /v3/conversation/add` (v2-router
`handleConversationAdd`: `msg-<uuid32>` ids, `sessionKey = sessionId = session_id`,
team/user/agent = `default` as the Hermes plugin sends, `recordedAt = ingest + index`,
`timestamp` = message time, `insertL0Batch` when no embedding).

3.3 Only the scheduler is replayed instead of run on wall-clock timers: per session, L1 fires
after user rounds 1, 2, 4 more (warm-up 1->2->4) and then every 5 (stateful pipeline threshold
rule), plus one flush at session end (the 600 s idle timer); a run that reports a full backlog is
repeated immediately. L2 runs once per session after the final L1, for every profile scope L1
reported; the L3 runner is invoked after each L2 and decides itself (PersonaTrigger cold start /
every 50). Deviation: production may run L2 0-2 times per session depending on wall-clock
spacing (90 s delay, 900 s min interval).

3.4 The model is reached through a loopback shim (`bench/harness/shim.py`) set as
`TDAI_LLM_BASE_URL`. It forwards the body unchanged except: `model` -> upstream id, and the
pre-registered `inject` fields (`reasoning_effort: "low"` for grok-4.7, `"xhigh"` for luna). It
holds the provider key (from BWS via `bench/bin/with-secrets`, env only), logs latency, usage,
cost, finish reason per call, stores request/response bodies (synthetic) for audit, paces
free-tier requests, and enforces the paid cap. This shim is also the deployment mechanism if an
effort-tagged model wins (the gateway cannot send `reasoning_effort` itself).

3.5 Local candidates run on a dedicated `llama-server` (llama.cpp b11270, the farm build) on
127.0.0.1:11590, never on the shared router: `-c 32768` (ladder 32768 -> 24576 -> 16384, first
that loads; expected <32k only for qwen3-14b), `--parallel 1 -ngl 99 -fa on -ctk q8_0 -ctv q8_0
--jinja`, qwen3/orchestrator `--reasoning off`, server-side sampling defaults temp 0.7, top-p 0.8,
top-k 20 (Qwen non-thinking recommendation; qwen2.5 adds repeat-penalty 1.05). The gateway sends
no sampling params, so these server defaults are what a local deployment would use. Each local
arm holds `flock -s quiet-lane.lock` and `flock gpu.lock` (Quackles convention), logs to the
quiet-lane ledger, and stops its server when done. The shared z0-farm-llama router, the Quackles
arbiter and Ollama are not touched.

## 4. Metrics (scorer `bench/score/score.py`, deterministic; unit tests `bench/tests/`)

Scored state = the final L1 store after dedup (what the gateway keeps), plus scene/persona
markdown for L2/L3.

4.1 Matching. Text normalised (NFKC, lowercase, quotes/dashes folded, markdown stripped, spaces
collapsed). A gold item matches a record iff all its anchor regexes match. One record may match
several gold items (the L1 prompt asks to merge related facts).
- TP record = matches a required gold or an optional (acceptable) item; else FP.
- Precision = TP records / all records; Recall = required gold matched by >=1 record / required
  gold; **F1** = harmonic mean, micro-averaged over conversations within a repeat.
- **Hallucinated-memory rate** = (records firing any trap + FP records whose content tokens are
  <34% present in the conversation) / all records. Traps fire on all anchors and no `unless`
  regex (negation / attribution markers).
- **Secret leaks** = (conversation, secret) pairs where any 12-char window of the secret appears
  in any persisted text (record content + metadata, scene/persona files).
- **Supersession**: within-session strict (current fact present and no live stale record) and
  cross-session strict + lenient (current present). NB: gateway dedup is session-scoped
  (`l1-dedup.ts` filter), so cross-session strict is structurally limited for every model.
- **Dedup**: within-session = exactly one record per session for the repeated fact; cross-session
  reported only.
- **Output-contract validity** = L1 + dedup calls that returned and were accepted by the gateway's
  own parser (fail = llm error/timeout, `l1-empty reason=no_json|parse_fail|not_array|
  normalized_all_dropped`, dedup "No JSON array"/"Failed to parse"/"not an array"); repairs and
  invalid-type drops counted separately. Empty-case accuracy = gold-empty conversations with 0 FP.
- Latency p50/p95/max per call kind (runner wall time incl. AI SDK retries) and upstream latency;
  tokens (prompt/completion/reasoning/cached) and cost per call kind from the shim ledger;
  projected $/day at workload.md volumes (quiet/mean/peak).
- Type accuracy (record type allowed for the matched gold) - secondary.
- L2: run success, scene fact recall (anchors over scene text), trap sentences, leaks. L3:
  persona non-empty, persona fact recall, trap sentences, leaks.

4.2 Uncertainty: per-candidate mean +- sd over repeats; 95% bootstrap CI of micro-F1 resampling
conversations (2000 draws, seed 0).

4.3 Controls (same gateway, no model): `stub` = verbatim control (copies the first ~80 chars of
the first user message of each L1 window as one episodic memory; deterministic). Wiring run on
2026-10-03 (harness validation, not a result): F1 0.603 on the full corpus. Any candidate must
beat it to be called useful (H7).

4.4 Secondary judge (reported separately, never gates or ranks): fixed local qwen3-14b-q4km,
reasoning off, temperature 0, seed 0, prompts in `bench/score/judge.py`: per record
SUPPORTED/UNSUPPORTED/MISATTRIBUTED, per gold COVERED/NOT_COVERED. Known bias: it is also a
candidate.

## 5. Primary metric, gates, decision rule

- **Primary metric: L1+dedup micro-F1**, mean over 3 repeats.
- **Hard gates** (fail = ineligible): hallucinated-memory rate <= **5%** (mean over repeats) and
  **0 secret leaks** across every cell the candidate ran (screen included).
- **Decision rule (L1+dedup)**: on the common set P (10 conversations, every candidate x 3
  repeats), let `best` = eligible candidate with the highest mean F1.
  1. If an eligible **local** candidate has F1 >= best - **0.05**, recommend the highest-F1 such
     local candidate (privacy: conversations never leave the host; $0).
  2. Else if an eligible free cloud candidate has F1 >= best - 0.03, recommend it.
  3. Else recommend `best`.
  Ties (|dF1| <= 0.01): local > free > paid, then lower L1 p95.
  If no candidate is eligible: recommend none, report the closest.
  Non-paid candidates are also ranked on the full corpus; if that ranking disagrees with P among
  non-paid candidates, the full-corpus ranking wins among them and the disagreement is reported.
- **L2/L3** decided separately on set L23 (P_L23 for paid): primary = mean of L2 and L3 fact
  recall; gates = 0 leaks, 0 trap sentences, L2 run success >= 80%. Same local/free/paid
  preference with margin 0.10. A split (e.g. local L1 + cloud L2/L3) needs a router in front of
  the gateway (single baseUrl) and is reported as such, not assumed.
- Latency is a feasibility check only: any call over the gateway timeout is already a lost
  batch (counts against F1/contract).

## 6. Candidates

| id | route | settings | runs? |
|---|---|---|---|
| nemotron-super-free | OpenRouter `nvidia/nemotron-3-super-120b-a12b:free`, BWS `OPENROUTER_API_KEY` | provider defaults (= production today); 4 s pacing | yes (control/incumbent) |
| grok-4.7-low | Vercel AI Gateway `spacexai/grok-4.7`, BWS `AI_GATEWAY_API_KEY` | `reasoning_effort: low` | yes, paid, P only |
| luna-xhigh | Vercel AI Gateway `openai/gpt-5.6-luna`, BWS `AI_GATEWAY_API_KEY` | `reasoning_effort: xhigh` | yes, paid, P only |
| qwen3-8b-q4km, qwen3-8b-q6k, qwen3-4b-q4km, qwen3-14b-q4km, nemotron-orchestrator-8b-q4km | farm GGUFs on dedicated llama-server | §3.5 | yes |
| qwen2.5-coder-7b, qwen2.5-3b | Ollama GGUF blobs on dedicated llama-server (avoids Ollama num_ctx truncation) | §3.5 | yes |
| grok-4.1-fast-non-reasoning | only route is paid Vercel | - | **no**: original xAI OAuth token expired, no xAI key in BWS, paid spend allowed only for owner-named arms |
| qwen3-0.6b, qwen3-1.7b | farm GGUFs | - | **no**: below 3B floor (prior 3B evidence 0% schema); added only if a 3-4B arm passes gates |
| z0-farm router as configured | shared, ~4k ctx/slot | - | **no**: slot smaller than L1 prompt; not ours to reconfigure |

Paid-route effort check (before the first paid cell, synthetic prompt "Reply with exactly: OK",
<= $0.01 total): accepted iff HTTP 200 and the response reports reasoning usage consistent with
the effort; if Vercel rejects `reasoning_effort`, the pre-registered fallback is the body field
`reasoning: {"effort": <same>}`. The probe result is recorded; no other setting may be tuned.

## 7. Stages, subsets, repeats

Subsets are in `corpus.json`: S (8 conv screen), P (10 conv, common/paid), L23 (12 conv with
L2/L3), P_L23 (4 conv, paid L2/L3). Repeats: 3 (`r1..r3`); sampling is whatever the route does
(gateway sends no seed/temperature), so repeats measure real run-to-run variance. Per-cell seeds
do not exist on this path; the local server uses its default RNG.

- R0 screen: every runnable candidate on S x 1 (layers auto). Local candidates advance to R1 if
  contract validity >= 80%, 0 leaks, and F1 > stub F1 on S; at most the top 3 local by S-F1
  advance (GPU time). Cloud candidates always advance.
- R1 main (non-paid): full corpus x 3 (L2/L3 on L23).
- Paid: P x 3 with layers l1, interleaved grok r1, luna r1, grok r2, luna r2, grok r3, luna r3;
  then P_L23 x 1 with L2/L3 (luna first). Then, only if >= $1.00 total remains, the other 37
  conversations x 1 for luna then grok within their caps (reported as extra coverage, not used
  in the P decision).
- Non-paid candidates' P numbers are taken from their R1 full-corpus cells (same conversations).

## 8. Budget and stop rules

- Paid total hard cap **$4.75** (task cap $5.00; Vercel balance $4.9995). Arm caps: grok-4.7-low
  $2.40, luna-xhigh $2.10 (`runs/paid-budget.json`, enforced by the shim: a call is refused once
  the cap is reached). Cost = list price x usage (cache-read price when cached tokens reported);
  Vercel `/credits` is read before and after the paid stage as ground truth.
- If an arm hits its cap mid-stage, it stops; comparisons use the cells both arms completed;
  coverage is reported.
- OpenRouter free: <= 15 req/min pacing. A cell with any upstream 429 (after the AI SDK's 2
  retries) or any infrastructure failure (shim/server down, docker error) is invalid and re-run
  (`--force`) - rate limits are reported as friction, not scored as model failures. Model errors
  (timeouts, 5xx from the model, malformed output) are scored.
- No other spend. No account creation.

## 9. Privacy and safety

Synthetic corpus only for every model, local or cloud. Keys only via BWS -> env of the shim;
never in argv, files, logs or reports (names only). Live Hermes, the production gateway
(`tencentdb-memory.service`), the farm router, the Quackles arbiter and Ollama configs are not
modified. Scratch stays under this directory.

## 10. Smoke test (harness validation, not a result)

After this commit: `nemotron-super-free` on A01_lantern_pnpm + F01_env_paste, 1 repeat,
run-id `smoke` (A01 includes L2/L3). Passing = every call reaches the model and returns, the
gateway parser accepts the output, the scorer produces a report. Smoke numbers are not used for
any decision.

## Amendments (made after the smoke test, before any scored run)

**A1 (2026-10-03, after smoke run `smoke` on commit 1bd752d).** The smoke showed the gateway's
Chinese L2 (scene) and L3 (persona) prompts make the model write scene blocks and persona in
Chinese (e.g. "使用 pnpm，禁止 npm 或 yarn"), while L1 records follow the user's language
(English) as the L1 prompt requires. English-only anchors would therefore under-count L2/L3 facts
and over-fire L2/L3 traps for every candidate. Changes (scorer/gold only; harness, candidates,
metrics and decision rule unchanged):
1. L2/L3 fact anchors get Chinese alternatives where the fact is natural language (terse 简洁…,
   emoji 表情, peanut 花生, vegetarian 素食, dark 深色…, cycling 骑行…, injury 骨折…,
   full-time 全职, Canada/NZ 加拿大/新西兰, ops simplicity 运维/单文件/备份). Technical-term
   anchors (pnpm, tailscale, sqlite, hono, …) were already language-neutral.
2. L2/L3 trap sentences are also suppressed by Chinese negation/attribution markers (`NEG_ZH` in
   `bench/corpus/dsl.py`). Chinese-phrased misattributions that use no English anchor terms are not
   detected (limitation, stated in the report).
3. L1 is NOT given Chinese anchors: the L1 contract is "write in the user's language", and Hermes
   recall is BM25 over English queries, so a Chinese L1 record is functionally unreachable. New
   reported (non-gating) metric: `l1_non_user_language_rate` = records with >= 3 CJK chars / all
   records.
New corpus sha256: `56acda3dd5c258aa0f56ffc0fead2f5484bff6cab76d4b7fb35481cddd269531` (gold
fields only; conversation text byte-identical). Smoke notes (not results): nemotron-super-free
spends ~2-3k reasoning tokens per L1 call by provider default; L1 p50 7 s / p95 15 s on 6 calls;
every call accepted by the gateway parser; L2 multi-step tool loop (tool call -> stop) and L3 cold
start both executed.

**A2 (2026-10-03, route probes, before any scored run).**
- Paid-route effort probe (§6) through the shim, synthetic prompt "Reply with exactly: OK":
  Vercel AI Gateway answered **403 RestrictedModelsError "Free tier users do not have access to
  this model"** for both `spacexai/grok-4.7` and `openai/gpt-5.6-luna` (`runs/probes/`). $0.00
  spent; `runs/paid-budget.json` unchanged. With the OpenRouter key at a $0 limit, BWS
  `OPENAI_API_KEY` lacking model scope and no xAI key, **grok-4.7-low and luna-xhigh have no
  faithful (OpenAI-protocol) route today: callable=false**. No account, top-up or key was created.
- Unblock (owner action; harness unchanged, §5-§8 apply as written): (1) top up Vercel AI Gateway
  to paid credits, or (2) raise the OpenRouter key limit to >= $5 and use the pre-registered
  `alt_endpoint` (`x-ai/grok-4.7`, `openai/gpt-5.6-luna`, same list prices, both list
  `reasoning_effort`); the effort probe is repeated first.
- Not adopted: the subscription CLIs (grok CLI SuperGrok OAuth, codex ChatGPT login). They add
  12-18k-token agent system prompts, use the Responses API and cannot expose the gateway's
  read/write/edit tools, so they would only approximate L1/dedup. If the owner asks for them,
  they run as a separately labelled "approximate" arm that is never used by the decision rule.
- While blocked, the P comparison (§5) is among callable candidates and the report states that
  the two owner-named arms are untested.
- Local route probe (`runs/probes/local/`, not a result): all 7 local candidates load on the
  dedicated server and answer; ctx 32768 for all except qwen3-14b-q4km at **24576** (324 MiB VRAM
  left, so it needs an otherwise idle GPU). Every model emitted a correct tool call on a one-shot
  probe except qwen2.5-coder-7b (none). Screening rules are unchanged.
- `bench/candidates.json` updated accordingly (sha256
  `9290379006f695881c6f0b03bd9057b17d43c73900c8f65a523f181c5f7004e7`).
