Z21 REPORT: using Jev (and local d1) to cut Opus tokens. Step 1 (read-only 7 d budget) and lever ranking.
as of 2026-11-07 16:55 CST. Owner: hypothesis. Ledger Z21. Shadow only; nothing changed live.

Primary metric: Opus quota-weighted tokens per finished task at equal pass. Jev call count is not a cost.
Quota weight is a proxy for the subscription meter (API price ratios): input 1, output 5, cache read 0.1, cache write 1.25.

## 1. 7-day Opus budget (all profiles, state.db mode=ro)

Source: budget.py -> budget-7d.json (session_model_usage, last_seen in the last 7 d). 1 straddling session of 3,514.

| | value |
|---|---|
| Opus raw tokens | 12.36 B (cache read 11.74 B, cache write 573 M, output 42.3 M, input 3.4 M) |
| Opus weighted | 2.10 B |
| API calls / sessions | 65,614 / 3,514 |
| other families | other 2.57 B, haiku 0.05 B, sonnet 0.02 B |

Weighted share by component: cache read 55.8%, cache write 34.0%, output 10.0%, input 0.2%.
So about 90% of the Opus meter is context size x number of calls, and only 10% is what Opus writes.

By profile (weighted, turns.py): clean 74.0, ayo 6.7, reliability 5.6, ossfactory 4.4, z0i 3.2, product 3.0, analyst 1.4, others <1.
By source (weighted, budget-7d.json): interactive 73.0%, non-interactive 27.0% (oneshot 8.8, cron 6.5, kanban 5.1, background_review/child/api/subagent the rest).
Effort: 100% of Opus weight ran at the default effort; no session sets a per-turn effort (extra.py, sessions.model_config).

Average context per call (extra.py -> extra-7d.json), share of weighted tokens:
- under 50k: 5.3%
- 50-100k: 7.4%
- 100-200k: 41.3%
- 200-400k: 45.7%
- 400k and up: 0.3%

Turn types (turns.py -> turns-7d.json; one assistant message = one call; call weight = session weighted tokens / calls):
- exec (terminal, execute_code, browser): 63.7%
- final text: 10.1%
- read/search: 9.4%
- write/patch: 7.0%
- poll/wait: 3.2%
- other tools: 3.0%
- skill loads: 2.7%
- delegate/message: 0.9%
- 11,180 of 79,978 tool calls (14%) repeated identical name+args inside the same session.

What fills the context (chars, same sessions):
- terminal output: 40.2%
- user-role text (Kevin plus injected agent and notification messages): 26.9%
- skill_view output: 7.9%
- read_file: 7.4%
- assistant text: 4.4%
- execute_code: 2.4%
- session_search: 2.1%
- vision: 1.5%
- everything else: under 1.5% each

Cron ticks (cron-ticks-7d.json, all on clean; final message read by hand on a 14-row sample):
- [SILENT] ticks: 132 sessions, 0.95% of weight.
- "Nothing merged" ticks: 39 sessions, 0.77%.
- Real reports: 167 sessions, 5.0%.
- A first regex had called 312 of 338 cron sessions no-ops. The hand check showed it was wrong, so the numbers above use exact final-message matches only.

## 2. Levers ranked by Opus weight they can touch

touchable = upper bound on the weighted share the lever acts on, not a saving. Ease: S=1, M=3, L=8. score = touchable / ease.

| # | lever | touchable (query) | ease | score | Jev tool | privacy |
|---|---|---|---|---|---|---|
| 1 | (b) route non-interactive Opus sessions (oneshot, cron, kanban, background review, children) to Sonnet/Haiku when Jev says the task is easy | 27.0% (budget-7d.json by source) | S | 27 | jev route / ladder; hermes-jev-routing (shadow by default) | stays on Claude. The route decision reads the brief: agent-written briefs go to Jev, Kevin-authored turns go to d1 |
| 2 | (d) tool-output keep/trim before it enters context | ~58% of context chars are tool output, times 90% of weight that is context = ~52% | M | 17 | jev compact-select / rerank on the tool result | tool output can carry Kevin's data: outputs from owner paths go to d1, OSS repo outputs go to Jev |
| 3 | (f) compaction keep/drop on long sessions | 46.0% (sessions averaging 200k+ context) | M | 15 | jev compact-select | must re-test the upstream recall loss (SCORECARD-2026-09-19-jev). Overlaps with (d) |
| 4 | (a) per-turn reasoning effort (easy turns low) | 10.0% (output share; all turns run at default effort) | S | 10 | hermes-switchyard adaptive effort | turn text: Kevin's turns go to d1 |
| 5 | (c) skill pick | 7.9% of context + 2.7% of calls | S | 8 | jev pick-skill (A22 tiered rank already -19% live) | skill text is public, so Jev is fine |
| 6 | (e) supervise delegated runs instead of Opus polling | 3.2% | S | 3 | jev supervise | run logs: Jev for public, d1 for owner lanes |
| 7 | (g) skip no-op cron ticks with a Jev pre-check | 1.7% | S | 2 | jev triage on the tick inputs | tick inputs are PR/CI state, which is public |

Not a Jev lever, but measured: 14% duplicate tool calls. A cheap dedupe cache in Hermes would cut some exec context without any model.

## 3. Next: 24 h shadows on the top 3 (b, d, a)

I chose (a) over (f): (f) overlaps with (d) and carries a known recall risk, so it needs its own A/A first.

How each shadow works: log the Jev or d1 decision next to what ran, plus counterfactual Opus weighted tokens. No behaviour change. Written under ~/.z0int/shadow/z21/. Same pattern as the Z17 decide() shadow.
- (b): for each new non-interactive Opus session, log the `jev route` tier for its brief. Counterfactual = the session's weighted tokens at Sonnet/Haiku meter rates. Kevin-authored briefs are routed to d1 and are not scored until d1 is available.
- (d): for each tool result over 4k chars in an OSS/public cwd, log compact-select keep/trim and the chars that would be dropped. Counterfactual = dropped chars x remaining calls in the session (cache-read weight). Owner-path outputs are not sent to Jev.
- (a): for each Opus turn, log switchyard's effort pick. Counterfactual = output tokens at the picked effort (ratio taken from the A/B, not assumed).

Then one A/B per lever on a frozen task set: A/A first, pass non-inferior, Opus weighted tokens per finished task as primary.

Open:
- d1 (local Liquid, Z20) readiness for Kevin-turn arms.
- Whether Sonnet/Haiku draw on the same weekly Claude meter as Opus. That decides how much of (b)'s 27% is real relief.
- Shadows not built yet: the pools are at 2.14x and 2.59x pace and claude3 is exhausted, so I'm asking ayo before spending Opus on the build.
