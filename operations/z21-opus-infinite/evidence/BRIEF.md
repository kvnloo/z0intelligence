# Z21: use Jev (and local d1) to make Opus feel infinite
Owner: hypothesis (owns Z17 decide() shadow). Kevin 2026-11-07 ~16:1x, verbatim:
"no need to cut jev calls tbh, if anything we can use both, jev is so cheap it doesn't even matter, i care wayyyyy more
about using jev more EFFECTIVELY. if theres a way to use it to reduce our opus usage thatd be awesome, i want opus 5.5
to be able to feel infinite"

## Why now (burn-pace 16:1x)
claude 56% weekly, pace 2.15x (hard-throttle); claude2 84%, 2.59x; claude3 100% (exhausted). Objective: Opus tokens
(and subscription %) per finished task at equal pass rate. Jev call count is NOT a cost to minimise.

## Starting state (read, don't redo)
- Where Opus time/tokens go: fly-mushroom-hillclimb/RESULTS.md (writing 44%, reasoning 25%; tool output into context
  terminal 200M + read_file 165M chars; skill loads 71M chars; polling turns 5.6k; dup calls 10k; 96% cache hits).
- Jev tools already installed: `jev route|pick-skill|compact-select|rerank|supervise|triage|ladder|spend`
  (~/zer0/oss/hermes-jev-skills); plugin hermes-jev (enabled in intake/chiefstaff only); Hermes catalog plugins
  hermes-switchyard (adaptive reasoning effort + skill routing) and hermes-jev-routing (shadow by default).
- Past results: A22 tiered skill-rank -19% tokens live; E6 card turns 0.645x / tokens 0.52x (clean); upstream Jev
  compaction 26x faster but lost recall per retained token (evals/compaction SCORECARD-2026-09-19-jev.md);
  clean jev routing fired on 0.65% of turns (pinned model / no_key). Z19 REPORT Q1. Z20 adds Liquid d1 (same Jev format, local).
- Prior-attempt pitfalls: ayo-runs/exec-Z20/PRIOR-ATTEMPTS.md (validation vs test, A/A, baselines, option order).

## Method
1. Measure 7 d Opus token budget by profile and by turn type from every profile's state.db (mode=ro): effort level,
   input vs output vs cache, tool-output share, skill-load share, polling/no-op turns, subagent turns.
2. Rank Jev levers by Opus tokens they can touch, e.g.: (a) per-turn reasoning effort (easy turns low effort);
   (b) route simple turns/subagents Opus -> Sonnet/Haiku (stays Claude; privacy-safe); (c) skill pick; (d) tool-output
   keep/trim before it enters context; (e) supervise delegated runs instead of Opus polling; (f) compaction keep/drop
   (re-test against the upstream recall loss); (g) no-op cron/goal ticks skipped by a Jev gate.
3. Shadow the top 3 for 24 h (log decision + counterfactual tokens, no behaviour change), then one A/B each on a frozen
   task set with A/A, pass non-inferior, Opus tokens as primary.
4. Privacy: Jev is external. Kevin's own turns: local d1 (or Claude) only, unless Kevin approves Jev on redacted text.
   Public/OSS work may go to Jev. State which arm saw what.

## Done-check
operations/z21-opus-infinite/REPORT.md: token budget table with sources; ranked levers with touchable %; shadow logs;
A/B results (Opus tokens/task, pass, A/A); a one-line recommendation per lever (adopt / reject / needs Kevin).
Shadow only until ayo OKs a live switch (backup + rollback line). No public posts, no spend beyond Jev credits.
