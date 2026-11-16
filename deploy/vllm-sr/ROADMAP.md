# Turbo router roadmap

Goal: turbo's parallel small calls finish as fast as the free and burn-paced backends allow.
Placement follows O14 (Kerdoios `plan_routing`, z0intelligence #222). The router (vLLM Semantic Router) executes the choice.
Main agent turns stay on Opus. Free models get easy, well-defined, non-code tasks.
Each step is done only when measured: peak in-flight calls, wall time, success rate, 429s, and quota spent per bucket.

## Facts this plan rests on (2026-11-02)

- **Per-model buckets** (`limits.json`, from provider headers plus Groq docs). Each model's limit is independent of the others.

  | Bucket | RPM | TPM | RPD | TPD |
  |---|---|---|---|---|
  | Groq gpt-oss-20b / gpt-oss-120b / qwen3.8-27b (each) | 30 | 8K | 1K | 200K |
  | Groq allam-2-7b | unpublished | 6K | 7K | unpublished |
  | Cerebras gpt-oss-120b | 1000 | 500K | 1.44M | — |
  | Cerebras qwen-3.8-27b | 450 | 150K | 648K | — |

- **Where the capacity is:** Cerebras holds about 94% of pool RPM and about 96% of TPM. Cerebras is trial/paid **credit**, not a free tier; Groq is the truly free part.
- **Uniform random is not capacity-aware.** It sends 60% of calls to Groq. In the 200-call run at c=50, about 40 calls hit each Groq model within one second against a 30 RPM limit, which produced the 39 fallbacks.
- **The router rejects duplicate `modelRefs`,** so the pool can't be weighted by listing a model twice.
- **The OpenCode free DeepSeek lane is CLI-only** (`opencode run`). It is an agentic lane beside the router, not a router backend.
- **NIM:** the preferred model is nemotron-3-ultra-550b, at 15–45 s. That is too slow for fanout; NIM is a scan lane.

## Phases

| # | Phase | Done when |
|---|---|---|
| R0 | Deploy, pin, fallback, fanout bench (PR #289) | Done. 60 calls go from 13–18 s serial to 1.2–1.3 s at c=20 |
| R1 | Rate-limit-aware pool. Fill free Groq buckets first, then spill to Cerebras credit. Keep requests over ~6K tokens off Groq. The bench preflight checks the planned load against `limits.json` | Equal or better wall time than random at c=20/50, with Groq 429s ≈ 0 while it stays under limit; Cerebras tokens reported per run |
| R2 | O14 drives the router. z0int compiles the O14 ranking into router config. Router receipts (TTFT, tok/s, status) feed Kerdoios observations, so O14's unknown inputs become scored | Paired experiment H1 (O14 order + fallback) vs H2 (random) at c=20/50; `audit-routing` passes on router receipts |
| R3 | Turbo runtime (#176) calls the router. Patterns: fanout, race (client-side first-good), draft→check (`confidence` looper) | 10-19 A/B rerun shows peak overlap > 1 and the wall-time delta |
| R4 | Quota truth. Groq RPD/TPD headroom and Cerebras credit balance are visible to `kerdoios capacity` and burn-pace. The router refuses a spent bucket instead of retrying it | Exhausted bucket gets 0 calls; Cerebras spend shows up before credit runs out |
| R5 | Upstream to vllm-project/semantic-router (via fork): Cerebras `time_info` tolerance, k8s `kubectl wait` latching onto the outgoing pod, weighted/capacity-aware random | Issues or PRs with repro, filed per the upstream-contribution rules |
| R6 | Later. Laya/Jev as a shadow classifier signal; router decisions logged as DecisionOpportunity episodes; OMH `/dag-ulw` pairing trial (RESEARCH-PROMPT.md) | Shadow-only first, under z0int promotion gates |

Trackers: one GitHub issue per phase under the epic linked from RFC #175.
