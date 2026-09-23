# z0 kernel — backend race + resolver A/B (2026-09-23)

All numbers below were produced by commands in this session on this machine.
Raw row-level data (`raw.jsonl`, `tokenomics-events.jsonl`) is deliberately not
committed; the reports below are.

## 1. Backend race — `z0int backends bench` (decision-capability-v1)

Run: `results/decision-backends/20260922T-consolidate-kernel/`
`z0int_git_sha=008a3017` · `tokenomics_git_sha=c76f621d` · 8 candidates × 11 fixtures.

| | |
|---|---|
| rows | 88 |
| ok | 55 |
| **unavailable** | **33** |
| errors | 0 |
| fixtures per capability | 2–3 |
| harness `validated_min` | 50 |
| counterfactual coverage | **0.0%** |

### VERDICT: no winner — and the harness is right to refuse one

`pareto.md` states it directly:

> No universal winner declared. Dominance is per-capability only.

and for **every** capability:

> **Pareto-optimal (eligible only):** (empty — no eligible candidates)

The reason is measurement power, not model quality. Every row is `PROVISIONAL`
because `n_fixtures` is 2–3 against a `validated_min` of 50. `reflex`,
`system_one_4b` and `local_mb` have no adapter at all (33 unavailable rows).
**Do not promote any backend to the hot path on this evidence.**

### The one defensible per-capability observation

`tool_family_select` — equal accuracy, wildly unequal cost:

| backend | n | acc | dangerous | p50 latency |
|---|---|---|---|---|
| `laya_421m` | 2 | 1.000 | 0.000 | **274 ms** |
| `decider_2b` | 2 | 1.000 | 0.000 | **93,976 ms** |
| `openjev_4b` | 2 | 1.000 | 0.000 | 66 ms |
| `nanojev_06b` | 2 | 0.000 | 0.000 | 42 ms |
| `openjev_06b` | 2 | 0.000 | 0.000 | 31 ms |

`laya_421m` matches `decider_2b` on accuracy with 0 dangerous and is ~344× faster
on that capability. That is dominance *within one capability at n=2* — a
hypothesis to test with more fixtures, not a result.

**Safety signal worth keeping:** on `rlm.worker_needed`, `nanojev_06b` and
`openjev_06b` are `excluded_unsafe (dangerous_false)`. `openjev_4b` had the best
calibration there (Brier 0.294 vs decider 0.495, laya 0.635).

### What the race proves about this branch

The last complete race before this one measured **one** candidate
(`nanojev_06b`). This one measured **five with real adapters**. The reason is the
registry fix in this branch: `laya_421m` and `openjev_06b` shipped as working
adapters but were never registered, so every roster, Pareto plot and shadow lane
was blind to them.

## 2. Resolver A/B — `context_resolve` vs ordinary retrieval

Question set: the 17 `coverage_miss` rows. Ordinary retrieval = the AgentsView
HTTP search API given the raw question (what a harness does by default).
Resolver = `mcp__z0__resolve` over the provider registry.

| path | materialized 11 | all 17 | median latency | median evidence |
|---|---|---|---|---|
| ordinary @8 | 1/11 | 1/17 | 57 ms | 4 |
| ordinary @80 (10× K) | **1/11** | 1/17 | — | 60 |
| resolver (full packet) | **7/11** | **8/17** | 1,392 ms | 80 |

**resolver-only finds: [2, 4, 8, 10, 11, 16, 17] — ordinary-only finds: []**

The important part is the middle row: ordinary retrieval gains **nothing** from
10× the results, and returns **zero** hits for 5 of the 17 questions. So this is
not "the resolver returns more, therefore it wins" — more results do not help
the baseline at all. The gain comes from provider breadth, OR-term matching, and
the typed two-hop identifier hop.

### Caveats, stated plainly

- The set is **in-sample**: the 17 misses seeded the coverage ledger, and the
  token-harvest ordering fix was motivated by #8/#16. An out-of-sample set
  split by project/session/harness/task-family is still required.
- The resolver is **~24× slower** (1,392 vs 57 ms median) and returns ~20× the
  evidence. Packet size, not just correctness, is the cost.
- 4/11 materialized answers remain unreached, and 6 of 17 overall.
- `exact_symbol`-style typed kinds are routed but only lightly exercised.

## 3. Failure isolation — observed, not just tested

Mid-session the AgentsView daemon was down. The conversation provider did **not**
return a silent empty; every hit from the fallback carried
`bridge=http-unavailable:URLError`, latency rose 1,507 ms → 7,115 ms, and the
required answer dropped out of the top hits. That is the designed behaviour and
it was observed in production rather than in a fixture.

## 4. Decision: KEEP, narrowly

Keep:

- the provider registry + typed identifier hop — measured capability gain
  (7/11 vs 1/11), no test regressions (438 passed / 1 pre-existing CUDA failure);
- the `require_cached_snapshot` guard — `registry.backend_status()` could
  otherwise start a multi-gigabyte download;
- the backend registration fix — without it the race cannot see half the roster;
- `z0int/mcp_server.py` — one facade, stdio + streamable-http.

Do not treat as settled:

- any backend promotion. The race is underpowered and says so;
- the resolver's ranking quality. Its measured edge is coverage, not precision.

Rollback is cheap and total: nothing here is merged to `master`; the work lives
only on `consolidate/z0-kernel-20260922`. Reverting is a branch revert, not a
history rewrite.

Cost of keeping: **+1,736 net lines** on z0int against the "near-zero" bar, which
this change does not meet. The offset (recall.py adapters 314, recall_mcp.py 440,
statepack.py 254) is enumerated in `docs/consolidation-inventory-20260922.md`.
