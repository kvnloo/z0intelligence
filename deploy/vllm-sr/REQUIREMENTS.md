# sr:free requirements (owner's words) and measured status

Gathered 2026-11-08 with the ABAB loop over the owner-resources index (18,926 owner messages, all harnesses,
refreshed first). 248 relevant owner messages read; agent briefs and test fixtures dropped. World state:
`~/.hermes/profiles/clean/.hermes/abab/srfree-requirements.json`. Baseline measured 06:40 CST (371 requests: one decision, 2 models).
Current status measured 08:00 CST from the router log; re-measure before claiming any row.

| # | Requirement (owner, date) | Status 11-08 08:00 | Evidence / enforcement |
|---|---|---|---|
| R1 | "SR use **all available free models in parallel** while respecting rate limits" (11-04); "use everything free in parallel… given each models ttft" (11-02) | PASS | Patched random selector draws the first backend by weight = bucket per-minute limit / models in bucket x eval score. Live: 66 requests, first draws NIM 48, Groq 18 (OpenRouter/Cohere out on daily caps). Enforced: `test_weights_spread_*`, `audit()` R1 every minute in free-pace. |
| R2 | "an **endless fallback of all free models from most capable to least**" (11-05) | PASS | 0 failed requests since 00:15. Enforced: `NEXT_ON`, patched fallback for tool turns, deploy.sh sha check, `audit()` R2. |
| R3 | "**choose the best model for each job**" with z0evals / Hermes bench (11-02, 11-05); "dynamic based on a task" (11-05) | PASS (mechanism); eval data weak | vllm-sr/free routes classify/extract/format/verify keywords to `auto-<family>` decisions ranked and weighted by family eval x reliability; Hermes Index outranks. Live: `auto-classify`, `auto-verify`… carry traffic. Limit: profiles/results.jsonl has 16 hard items per model and most score 1.0, so it barely separates models. |
| R4 | "**track ALL FREE MODELS** so we can respect all rate limits… switch to a different groq model" (11-05) | PASS | free-pace tracks every bucket; weights keep each bucket's share at its rpm; circuit breakers are per quota bucket (Groq per model), so one capped Groq model no longer benches the others. |
| R5 | "choose x lanes and it auto scales"; "regardless of whats available… even local small models" (11-05) | PARTIAL | Local z0-farm qwen3-8b is wired as the last fallback in every cascade, but the farm cannot load models right now (NVIDIA driver/library mismatch: needs a reboot). "Lanes": the router now spreads any client concurrency; there is no lane knob. |
| R6 | turbo only for HITL / when asked, not benchmarks (11-03) | not enforced | turbo and free share the same quota buckets and pace, so turbo no longer steals capacity; no consumer gate. |
| R7 | reuse z0intelligence/kerdoios; one unified chooser (10-28, 11-07) | PARTIAL | All logic stays in free_pool.py + router.patch. decide() (Z17) still shadow; report 11-08 17:00. |
| R8 | free models: defined tasks; code only after the coding-probe pass (10-20, 11-04) | not enforced | No coding-probe result set exists to gate on. |
| R9 | free lanes carry public/T0 data only (11-02, 11-03) | PASS | privacy_lanes.py, no_google_guard.py. |
| R10 | Cerebras/Groq prepaid balance as backstop (09-26, 11-04) | blocked | Cerebras returns 402 (no balance). |
| R11 | burn-rate aware everywhere (10-20) | separate lane | burn-pace gate (paid side). |

Run `free_pool.py audit [hours]` for the live R1/R2 verdict (exit 1 on FAIL); free-pace logs `REQUIREMENTS FAIL:` and stores `audit` in free-pace.json every minute.
