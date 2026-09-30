# claude-code-savings-v1 — pre-registration

Committed before any measured run. Follow-up to z0evals `claude-code-savings-v0`
(lean −27% warm / −64% cold; lean × State Packet −61% on the pinned held-out set; ObservationPack
−30% on long sessions, composing with lean at −38%). v0 samples were small (6–22 per cell) and
the plugin has since gained DecisionOpportunity shadow emission, a resource-posture fact in the
packet, and worker (groot) routing. v1 re-measures the **current integration**
(`integrate/claude-code-z0-stack` @ `b072468`) with real headless Claude Code runs.

## Arms

All arms: `claude -p` (Claude Code 2.1.286), `--model sonnet`, `--output-format stream-json`,
`--no-session-persistence`, `--max-budget-usd 2`, and `--settings` that set the user-installed
`z0intelligence` / `z0-obspack` plugins to `enabledPlugins: false`, so z0 enters only through
`--plugin-dir`. Plugins run from this branch's venv (`Z0INT_PYTHON`), per-unit `Z0INT_HOME`.

| arm | flags |
| --- | --- |
| `stock` | user settings, hooks, MCP servers, skills as installed (z0 plugins disabled) |
| `lean` | `--setting-sources project --strict-mcp-config --disable-slash-commands` |
| `lean+packet` | lean + `--plugin-dir harness-adapters/claude-code-z0intelligence`, `Z0INT_CLAUDE_CODE_PACKET=1` |
| `lean+packet+obspack` | lean+packet + `--plugin-dir harness-adapters/claude-code-z0-obspack` |

`lean+packet` is the plugin as shipped by `z0int claude-code launch --profile lean`: SessionStart
State Packet (with posture fact), UserPromptSubmit shadow routing + async DecisionOpportunity
emission (never injected), Stop/SessionEnd usage receipts. Known before running (smoke check):
under `--strict-mcp-config` the plugin's `route_worker` MCP server is **not** loaded, so worker
routing is not exercised by any lean arm; this is measured as shipped, not patched.

## Suites and tasks (frozen)

**qa** — the v0 pinned held-out set, unchanged: `benchmarks/state_packet/questions_pinned.json`
(11 questions, keys frozen in v0) run in place in the pinned fixture repos
(`~/.z0int/research/claude-code-overnight/pinned`, transcripts from its frozen `projects/`), with
the v0 read-only tool allowlist and history hint in every arm. Scored with v0's `score()`.
Effort low. **4 reps** (v0 used 2).

**repo** — 19 fresh tasks written for v1 across four z0 repos plus one replicated v0 long task,
pinned by SHA and verified programmatically by `check.py` (bugfix: restored tests must pass;
feature: hidden tests; nav: exact JSON/text key). Every task was validated before any run:
as-given fails the check, oracle (pristine / oracle patch / key) passes (`run.py validate`).
Effort low except the long task (medium, as in v0). **4 reps**: each (task, arm) has one
persistent directory; rep 0 is a cold session, reps 1–3 are warm (tree reset to the exact fixture
commit between reps).

| repo | sha | tasks |
| --- | --- | --- |
| z0intelligence | `b072468` | zint-fix-posture-burn, zint-fix-warm-ttl (bugfix); zint-feat-stale-dirs (feature); zint-nav-posture-importers, zint-nav-plugin-hooks (nav) |
| z0intelligence | `c4a4554` | zint-long-backend-review (v0 `backend-review`, long-horizon recall; replication, not fresh) |
| tokenomics | `65e8f2d` | tok-fix-offline-replay-rollup, tok-fix-trace-aggregate-doublecount (bugfix); tok-feat-range-weeks (feature); tok-nav-env-vars, tok-nav-cli-surface (nav) |
| kerdoios | `fd34a48` | ker-bugfix-context, ker-bugfix-merge (bugfix); ker-feature-exclude (feature); ker-nav-defaults (nav) |
| evolution-lab | `4cf52bb` | evo-bugfix-capacity-budget, evo-bugfix-table-tiebreak (bugfix); evo-feature-capacity-max-calls (feature); evo-nav-capacity-plan, evo-nav-tool-tournament (nav) — frozen in a second commit before the repo suite started; the qa suite had already started |

Planned n: qa 11 × 4 = 44 paired units per contrast; repo 20 × 4 = 80; pooled 124
(≥ 30 per arm on every contrast). 4 arms × 124 = 496 runs. ≤ 4 concurrent `claude` processes.
Order: tasks shuffled (seed 20260930), arm order rotated per task (Latin square), reps sequential
within a (task, arm) unit.

## Metrics

- **Primary:** billed tokens per trial = input + cache_read + cache_creation + output, summed over
  Claude Code's `modelUsage` (all models, incl. subagents) from the run's own result event
  (fallback `usage`). Plugin receipts and tool-reported "savings" are never used.
- **Secondary:** `total_cost_usd` (Claude Code's own estimate; cache reads are ~10× cheaper than
  input, so cost and tokens can diverge); task success (`verified`); tool calls; turns.

## Contrasts and tests

| id | A → B | role |
| --- | --- | --- |
| C1 | stock → lean+packet+obspack | **primary** (full current integration) |
| C2 | stock → lean | confirmatory |
| C3 | lean → lean+packet | confirmatory |
| C4 | lean+packet → lean+packet+obspack | confirmatory |
| S1 | stock → lean+packet | secondary (v0 headline comparator) |

Unit = (suite, task, rep) pair. Effect size: ratio of total billed tokens (B/A − 1), 95% CI by
task-cluster bootstrap (10k resamples, seed 20260930); also median paired % change and geometric
mean ratio. Test: two-sided Wilcoxon signed-rank on per-pair log(B/A); Holm across C1–C4.
Reported for the pooled set (headline) and strata: qa, repo, repo cold (rep 0), repo warm
(reps 1–3), repo short (excl. long task), long task.

**Quality guard (savings don't count if success drops).** For each contrast, success is compared
on the same pairs. Guard PASS iff success(B) − success(A) ≥ −5 percentage points AND NOT
(exact McNemar p < 0.05 with more A-only than B-only successes). A token saving is claimed only
if the guard passes, Holm-adjusted p < 0.05, and the bootstrap CI excludes 0. We also report
tokens per verified success and the saving restricted to pairs where both arms verified.

## Handling

- Fixed n; no extension after looking. Smoke runs (tag `smoke`: zint-fix-warm-ttl × 2 reps,
  qa p02/p10 × 1 rep) checked plumbing only and are excluded.
- A trial with no result event (infrastructure failure) is re-run once; budget/turn-limit
  endings count as runs (with their billed tokens) and as failures if unverified.
- Raw JSONL (session ids, local paths, model text) stays outside git
  (`~/.z0int/research/claude-code-savings-v1/`); git gets aggregates (`results/`) only.

## Known limitations (before running)

One host, one model (Sonnet), one night; Claude Code version drift is recorded per run.
qa questions share one pinned cwd per repo across arms, so their cache state is not controlled
per arm (as in v0). The repo-task packet sees no session history (fresh fixture dirs).
`cost_usd` is Claude Code's estimate on a subscription, not an invoice.
