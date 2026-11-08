# sr:free requirements (owner's words) and measured status

Gathered 2026-11-08 with the ABAB loop over the owner-resources index (18,926 owner messages, all harnesses,
refreshed first). 248 relevant owner messages read; agent briefs and test fixtures dropped. World state:
`~/.hermes/profiles/clean/.hermes/abab/srfree-requirements.json`. Status = measured on 2026-11-08 06:40 CST
from the router log (7 h, 371 requests) and `burn-pace`; re-measure before claiming any row.

| # | Requirement (owner, date) | Status 11-08 | Evidence |
|---|---|---|---|
| R1 | "SR use **all available free models in parallel** while respecting rate limits" (11-04); "literally use everything free in parallel and use the optimal orchestration pattern given each models ttft" (11-02) | **FAIL** | 371/371 requests went through one decision; 2 models answered 366. Groq used 3, 17 and 12 of 1000 requests/day while NIM ran 56/40 per minute and drew 429s. |
| R2 | "any time a model gets rate limited itll just go to the next priority… an **endless fallback of all free models from most capable to least**" (11-05) | PASS since 00:15 | 0 failed requests after fix `154cb01` (95 primary-ok or succeeded-after-fallback). |
| R3 | "use z0intelligence and z0evals to **choose the best model for each job**" (11-02); "ideally **dynamic based on a task**… semantic router alr has intelligent routing thru auto mode so if we just hand it all the free models itll figure it out" (11-05); "see hermes official bench… use it to prioritize" (11-05); "run many experiments to figure out which free models are good at which tasks" (11-03) | **FAIL** | No task classification: every request uses `free-quality`. The per-family eval lists (`free-reason` etc.) exist but get no traffic. `HERMES_INDEX` holds 4 models. |
| R4 | "as apart of burn pace **track ALL FREE MODELS** so we can respect all rate limits… u don't just switch to a different groq model" (11-05); track app limits too (ChatGPT, DeepSeek) (11-04) | PARTIAL | free-pace tracks router traffic per bucket and shows in `burn-pace`; NIM still exceeds 40 rpm (56/40) because nothing spreads load before the 429. |
| R5 | turbo "lets me **choose x lanes** and it auto scales" (11-05); "work **regardless of whats available**… even **local small models**" (11-05) | **FAIL** | No lane count; no local SLM backend in the pool. |
| R6 | turbo only "for hitl feedback loops when its realtime… or if i specifically ask", not benchmarks (11-03) | not enforced | No gate. |
| R7 | "**not recreate** functionality"; reuse z0intelligence/kerdoios routing already built; "keep everything in the same place" (10-28, 11-07) | PARTIAL | decide() (Z17, z0intelligence `feat/decide-shadow-z17`) is the agreed unified chooser and runs only in shadow; its report fires 11-08 17:00. |
| R8 | free models get "easy defined tasks", coding only after the coding-probe pass (10-20, memory 11-04) | policy only | Not enforced at the router. |
| R9 | free lanes carry public/T0 data; OSS work is fine (11-02, 11-03) | PASS | `privacy_lanes.py`, `no_google_guard.py`. |
| R10 | Cerebras/Groq prepaid balance may be used as backstop (09-26, 11-04) | blocked | Cerebras answered 402 (no balance) 11-05. |
| R11 | burn-rate aware everywhere; paid subs finish exactly at reset, free models absorb the rest (10-20, 11-05) | separate lane | burn-pace gate (paid side). |

Highest-value gap: R1 + R3 together. Both are the same missing piece: requests never get
spread or matched to a task. Done means the router log (not a test call) shows ≥ 4 task
decisions with traffic and Groq/NIM/OpenRouter/Cohere each carrying load under their limits.
