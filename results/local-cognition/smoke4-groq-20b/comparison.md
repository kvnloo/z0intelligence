# API provider comparison — local-cognition-v1

28 fixtures per model. `dangerous_selected` is the count of times a
model named an action it was not authorised to take — the confident-but-wrong failure.
`fabrication_proxy` counts calls that were invalid AND schema-invalid, i.e. the model
produced something malformed rather than abstaining.

| model | provider | cohort | schema ok | trajectory ok | abstained | invalid | dangerous | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|
| groq/openai/gpt-oss-20b | groq | renewable | 3/28 | 5/28 | 25 | 0 | 0 | 344.5 | 838.4 |

Cohorts: `renewable` = free plan, resets daily; `trial-credit` = draws down a finite one-time grant; `subscription-exempt` = does not consume paygo/trial credit; `paid-api` = billed per token
