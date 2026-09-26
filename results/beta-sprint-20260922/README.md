# Beta sprint receipts — 2026-09-22

Speed-first beta exploration (ABAB mode). **Every result here is
`evidence_class = exploratory_beta`.** It is enough to reject obvious losers and
to choose what to shadow next. It is **not** promotion evidence, and nothing here
may be promoted on its own.

| file | what it is |
|---|---|
| `trace-census.json` | where tokens actually go, from 93 DSH sessions / 4,644 model calls |
| `tournament-nanojev.json` | NanoJev + baselines over `decision-capability-v1` + `local-cognition-v1`, full threshold sweep |
| `prov-*.json` | free-tier Groq/Cerebras models over `decision-capability-v1` (11 rows) |
| `prov28-*.json` | same models over `local-cognition-v1` (28 rows, harder) |
| `role-evidence.json` | the deduped evidence folded into the kerdoios runtime inventory |
| `k8s-placement-exp6.json` | host container vs kind pod cold start and warm latency |

Reproduce:

```
python -m z0int.trace_census --json
python -m z0int.beta_tournament --backend nanojev --out out.json
python -m z0int.provider_tournament --provider groq --model openai/gpt-oss-20b --out out.json
```

Environment notes that cost real time and are worth not rediscovering:

* Groq's edge returns **403 to plain `urllib`** even with a valid key; send a
  `User-Agent`.
* A 24-token `max_tokens` yields **empty content** on gpt-oss reasoning models —
  the reasoning channel eats the whole budget first.
* Groq's free tier **429s** `qwen/qwen3.8-27b` after a handful of calls
  (7 of 11 failed); Cerebras did not rate-limit at this n.
* `zstandard` is required to read the session traces and is not in the repo venv.
