# Turbo router (vLLM Semantic Router)

Part of turbo mode (RFC #175). Turbo sends independent, tool-free small calls in parallel to the fastest free backends.
The router is the executor: one OpenAI-compatible endpoint, `model: vllm-sr/auto`.
Main agent turns stay on the session's frontier model.

## Files

| File | Role |
|---|---|
| `config.yaml` | Router config: backend pool, `turbo-fanout` decision, and cross-model fallback |
| `chart-local.patch` | Changes to the upstream Helm chart at the pinned commit: image digest pin and memory caps that leave room for the desktop |
| `deploy.sh` | Idempotent deploy to local k3s, namespace `vllm-sr`. Fetches and patches the chart once, gets keys through `dsh-env-exec`, and gates on the Deployment rollout |
| `free_pool.py` | The free pool, all in one file: `probe` (which backends work, how big a prompt) -> `pool.json`; `render` -> `config.yaml`; `pace` (free-pace.timer, every minute) tracks rate limits from router traffic and pulls capped backends |
| `limits.json` | The one rate-limit table: per-model rows (Groq) and per-account buckets (OpenRouter, Kilo, NIM) |
| `bench_fanout.py` | Runs N small tasks through the router, first serially, then at concurrency C |

## Backend pool (probed 2026-11-01)

| Model (router name) | TTFT | In pool |
|---|---|---|
| groq-gpt-oss-20b / -120b / qwen3.8-27b | ~0.21 s | yes |
| cerebras-gpt-oss-120b / qwen-3.8-27b | 0.18–0.25 s | yes |
| NVIDIA NIM gpt-oss-20b | 18–29 s | no (too slow); NIM gpt-oss-120b is end-of-life (410) |
| OpenRouter nemotron-3-super:free | — | no: no longer free (404) |
| OpenRouter other `:free` | flaky (403/429/503) | no |
| OpenCode Zen deepseek-v4.1-flash-free | — | no: the API refuses ("free models work in OpenCode only") |

`decision: turbo-fanout` picks a model uniformly at random. On 413, 429 or 5xx it falls back across the other models
(up to 4 attempts), with a per-backend circuit breaker.

## Measured (2026-11-02, small sentiment-classification calls)

| Run | Wall | OK | Peak in flight | Agg. completion tok/s |
|---|---|---|---|---|
| 60 serial | 13.1–18.6 s | 60/60 | 1 | 130–165 |
| 60 at c=20 | 1.19–1.30 s | 60/60 | 20 | ~1,700 |
| 200 at c=50 | 1.18 s | 200/200 | 50 | ~7,600 |

The c=50 run used fallback on 39 calls (Groq returned 429; the calls finished on Cerebras), and no calls failed.
Turbo v1 (PR #176) never ran more than one call at a time; the router fixes that.

## Known router quirks (upstream candidates)

- Cerebras returns a non-OpenAI `time_info` field, and the strict decoder answers 502.
  Workaround: `provider: vllm` with `openai_compat_gateway: true`.
- `vllm-sr serve --target kubernetes` waits with `kubectl wait pod -l ...`. That command can latch onto the outgoing pod
  and block for 600 s. `deploy.sh` cuts it at 180 s and gates on `kubectl rollout status` instead.
- The chart keeps the live ConfigMap unless `configMap.applyValuesRevision` changes.
  `deploy.sh` sets the revision to the config file's hash.

## Free cascade (2026-11-05)

`free_pool.py probe` tests every zero-cost backend (tool call, then 6K/30K/100K-token prompts) into `pool.json`;
`free_pool.py render` writes `config.yaml`. `vllm-sr/free` (= `auto`) tries them most capable first,
`vllm-sr/turbo` fastest first; on 408/413/429/5xx the router moves to the next one (up to the whole pool), and the
circuit breaker skips a backend for 120 s after two failures. Auth/billing failures (401/402/403) are left out of
the pool; rate-limited ones stay in. Re-run probe + render + `deploy.sh` to refresh the pool.

## Not yet

- Pool generated from z0int `/v1/providers` and Kerdoios placement (PLAN P1).
- Turbo runtime (#176) calling this endpoint instead of its per-provider adapters (PLAN P2).

## Endpoints by workflow (2026-11-15)

One router, base URL `http://100.113.138.100:30899/v1` (groot tailnet NodePort). `free_pool.py inventory` prints all of
this live, with per-model rpm headroom from free-pace's counts; nothing below is probed.

| Workflow | Endpoint | Behaviour | Typical caller |
|---|---|---|---|
| free | `vllm-sr/free` (= `auto`, `free-quality`) | every free model, best first, endless fallback; task keywords pick `auto-<family>` | Hermes `delegation`, z0live smol lanes |
| free by task | `vllm-sr/free-{reason,verify,longctx,extract,classify,format}` | same pool, ranked by that family's eval | callers that know the task |
| turbo | `vllm-sr/turbo` | fastest first | burst small units |
| one model | `vllm-sr/m/<model>` (e.g. `m/gpt-oss-120b`, `m/nemotron-3-ultra`) | that model on any free host, falls over only to its other hosts | GEPA arms, diversity lanes |
| one host | `vllm-sr/b/<backend>` (e.g. `b/groq-gpt-oss-120b`) | that host only, fallback off | evals, A/B arms that must not be scored on a substitute |
| decision | `http://127.0.0.1:11532/v1` (outside the router) | local d1, native `/v1/systemone` | sr:decision |
| paid | outside the router | Claude via `burn-gate --pick` / `t3pace.py pick`, Sol via fx | sr:paid |

Lane plans: `free_pool.py lanes <x> [--models y] [--family f] [--pinned]` picks the y best models (by family eval)
that have headroom now and splits x lanes across them in proportion to headroom. Each lane gets an endpoint and an
`rpm_budget`; the plan warns when lanes would get under 1 request/min. `--pinned` hands out `b/` hosts instead of `m/`.
