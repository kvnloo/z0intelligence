# API provider comparison — local-cognition-v1

28 fixtures per model. `dangerous_selected` is the count of times a
model named an action it was not authorised to take — the confident-but-wrong failure.
`fabrication_proxy` counts calls that were invalid AND schema-invalid, i.e. the model
produced something malformed rather than abstaining.

`transport_failures` counts calls that never got a usable HTTP response after retries.
If it is non-zero the row is a LOWER BOUND on that model, not a measurement of it.

| model | provider | cohort | dialect | schema ok | trajectory ok | abstained | dangerous | transport fail | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|
| groq/openai/gpt-oss-120b | groq | renewable | hammer | 5/28 | 8/28 | 23 | 0 | 23 | 515.4 | 820.0 |
| groq/openai/gpt-oss-20b | groq | renewable | hammer | 27/28 | 25/28 | 1 | 0 | 0 | 454.2 | 636.4 |
| groq/qwen/qwen3.8-27b | groq | renewable | hammer | 23/28 | 19/28 | 5 | 0 | 5 | 307.9 | 819.3 |
| cerebras/gpt-oss-120b | cerebras | trial-credit | hammer | 6/28 | 9/28 | 22 | 0 | 22 | 181.3 | 524.1 |
| cerebras/qwen-3.8-27b | cerebras | trial-credit | hammer | 28/28 | 24/28 | 0 | 0 | 0 | 366.1 | 824.6 |
| deepseek/deepseek-chat | deepseek | paid-api | hammer | 6/28 | 9/28 | 22 | 0 | 22 | 405.7 | 872.7 |

Cohorts: `renewable` = free plan, resets daily; `trial-credit` = draws down a finite one-time grant; `subscription-exempt` = does not consume paygo/trial credit; `paid-api` = billed per token
