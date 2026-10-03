# ctx history bakeoff results (sanitized)

These are local real-history runs from 2026-10-03, made with `benchmarks/ctx_history/run.py`. The 26 questions,
their known answers and the evidence text stay private. These files contain only:
- question ids, topics and sha256 prefixes;
- metrics;
- opaque ctx / AgentsView ids;
- `h:` hashes for every other locator.

## Setup

**Shared**
- Question set sha `f2e288a018b49e5f`. It has 26 questions:
  - 4 `issue-open` (the questions listed in #116);
  - 4 `identifier` (commit, PR or branch);
  - 18 `fact`.
- Every question is drawn from this week's work: the z0 wiring rounds, the AgentsView v0.44 upgrade, TencentDB, the Bend RFCs, CUA calibration R10c, the DSH audit, and #116 itself.

**Control**
- z0 tree `integrate/wiring-20261003` @ `05e5015`.
- It calls `resolve_context` with qmd (lexical) plus the z0 memory surface. The memory surface has:
  - AgentsView v0.44 FTS5, opened with `mode=ro`;
  - the EventLog, empty in an isolated `Z0INT_HOME`;
  - TencentDB `not_configured`.
- `--cutoff 2026-10-03T22:50:00Z` drops control evidence written after the question set was authored.

**ctx**
- ctx 2.2.7 on an isolated, manually indexed root.
- Generation `f1404f74…` covers the `claude` and `hermes` providers: 2437 sources, 395,108 documents.
- Semantic is disabled, so `ctx_hybrid` did not run: there is no explicit opt-in and no local semantic backend.

**Repeats.** Each question ran twice per arm: the first run is cold, the second warm.

## Runs

| run | ctx caller env | meaning |
|---|---|---|
| `2026-10-03-local-caller-inherit` | as shipped | ctx automatically excludes the calling agent's session tree. The bakeoff ran inside the session tree that holds most of this week's work. |
| `2026-10-03-local-caller-neutral` | `--ctx-caller neutral` | Agent-session markers are removed from the ctx env, so ctx searches the full indexed history. This is equivalent to `--include-current-session` and is replayable. |

The first run predates the `--ctx-caller` flag, so its `summary.json` has no `params.ctx_caller` field. It
ran with the inherited caller environment.

## Headline (caller-neutral)

| arm | answered /26 | cold / warm median ms | bytes opened median | injected tokens median |
|---|---:|---:|---:|---:|
| control | 6 (5 freshness-matched) | 697 / 628 | 457,754 | 590 |
| ctx_lexical | 0 | 218 / 180 | 13,039 | 395 |
| ctx_exact_hydration | 3 | 1518 / 1015 | 1,088,834 | 1895 |
| ctx_hybrid | not run (`semantic_opt_in_absent`; semantic disabled) | | | |

- **ctx_exact_hydration vs control:** 3 wins (Q03, Q19, Q20), 6 losses (Q05, Q06, Q07, Q10, Q24, Q25), 17 ties.
- **Neither arm:** 15 of 26 questions were answered by no arm.
- **Union:** control or hydration answers 9 of 26.
- **Coverage:** every control answer came from Claude Code history, which ctx also indexes (`answered_covered` = 6), so ctx's narrower provider coverage does not explain the gap.
- **Read-only:** the ctx data root changed 0 files. The ctx arms issued only `search` (`--refresh off`) and `show event`.
