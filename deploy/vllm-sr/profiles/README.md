# Router profiles (virtual models)

Clients pick an objective by model name; the router picks the backend. Every profile is an `entrypoints` entry
mapped to a `recipes` entry in `../config.yaml`. Hermes aliases (`clean` profile): `turbo`, `free-quality`,
`free-reasoning`, `free-verify`, `free-longctx`, e.g. `hermes -m free-quality -z "..."`.

| Profile (model name) | For | Backends (random + fallback on 408/413/429/5xx) |
|---|---|---|
| `vllm-sr/turbo` (= `vllm-sr/auto`) | Many small parallel calls; lowest latency | Groq gpt-oss-20b/120b, qwen3.8-27b; Cerebras gpt-oss-120b, qwen-3.8-27b |
| `vllm-sr/free-quality` | Hardest free work: extraction from messy text, multi-constraint formatting | NIM nemotron-3.5-lightning, OpenRouter nemotron-3.5-super-vl:free, nemotron-3-ultra (OpenRouter free + NIM) |
| `vllm-sr/free-reasoning` | Arithmetic, dates, logic, table aggregation | nemotron-3.5-super-vl:free, nemotron-3-ultra (x2), dots-3-note:free |
| `vllm-sr/free-verify` | The *check* in draft->check; claim and answer verification | same as free-reasoning |
| `vllm-sr/free-longctx` | 8k-32k token inputs (needle and multi-needle) | same as free-reasoning (Groq excluded: 413 above 8K TPM) |

Owner rules that apply to every free profile: easy, well-defined, non-code tasks; verify the output.
Cerebras is trial credit, not free; it sits only in `turbo`.

## How the picks were made
`eval_free.py` runs `tasks.py`: 20 auto-graded, non-code tasks in 6 families (extract, classify, format, reason,
verify, longctx). 12 are easy and 8 are hard; the easy tier saturated at 100% for 8 models, so ranking uses the hard
tier. Each model ran every task twice, streaming, temperature 0, `max_tokens` 4096. A failed call counts as a wrong
answer, because that is what a router sees. `score.py` writes `scores.json` and `profiles.json`.
`catalog.py` lists what each provider serves now (`catalog.json`).

Run (2026-11-02, results.jsonl): hard-tier accuracy / reliability / total p50 latency
- nemotron-3.5-lightning (NIM) 1.00 / 1.00 / 10.6 s
- nemotron-3.5-super-vl:free (OpenRouter) 0.94 / 0.97 / 2.4 s
- nemotron-3-ultra-550b (NIM and OpenRouter free) 0.94 / 0.97 / ~6 s
- dots-3-note-preview:free 0.94 / 0.97 / 7.4 s
- Groq gpt-oss-120b and 20b 0.88 / 0.88 / 0.8 s (every failure is 413 on the 32k input, or 429)
- step-5-preview (NIM) 0.83 / 1.00 / 18 s; minimax-m3.1-flash 0.81 / 0.84
- Gemini 3.5 Flash Lite 0.69 / 1.00 / 0.8 s; Gemini 3.5 Flash 0.11 hard (429s)
- Unusable today: NIM nemotron-3-super and kimi-k2.6 (404), OpenRouter inkling (403), OpenRouter gemma-4-31b (429 on
  every call), Gemini 3.1 Pro (429), NIM deepseek-v4.1-flash, glm-5.3-flash, mistral-large-4, gemma-4-31b (timeouts
  over 3 minutes).
- Cerebras qwen-3.8-27b scored 1.00 at 0.5 s, but it is credit, so it stays in turbo only.

Caveats: two repeats per task; the hard tier has 8 tasks, so one task is 6 points. OpenRouter free and NIM capacity is
shared with other users and changes by the hour. Re-run before trusting the ranking: `dsh-env-exec ... -- python3
eval_free.py --repeats 2 && python3 score.py`.

## End-to-end through the router
`eval_free.py --only router/vllm-sr/<profile>` sends the same battery through the deployed router (`router.jsonl`).
Pitfalls found on 2026-11-02:
- Streaming through Groq returned `invalid_upstream_json` (non-canonical field). Groq now uses
  `provider: vllm, openai_compat_gateway: true`, like Cerebras.
- Global OOMs on groot killed the router pod (Burstable, oom_score_adj 958) and coredns (291 restarts). The router now
  runs Guaranteed QoS (512Mi/1 CPU requests == limits; it uses about 50 MiB). coredns is k3s-managed and needs a root
  change. While DNS is down, every backend returns `503 no healthy upstream`.
- Kueue's controller (512Mi limit) was OOM-killed 424 times; its fail-closed Deployment webhook then blocked
  `deploy.sh`. `.files` `setup-k3s.sh kueue` now sets 1Gi.
