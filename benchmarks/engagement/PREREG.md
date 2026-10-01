# Engagement measurements M2 / M4 / M5: pre-registration

Committed before any measured run (smoke runs only exercised the harness and are not counted).
Design follows `docs/engagement.md`; where that doc does not fix n or the analysis, this file does.
Runner: `run_engagement.py`; analysis: `analyze.py`. Outputs: `results_m{2,4,5}.json` (aggregates),
`runs/*.jsonl` (per-run derived numbers; no prompts other than the task texts in `tasks_*.json`).

## Common

- Claude Code 2.1.286, headless `claude -p --output-format stream-json --verbose`, `--model sonnet
  --effort low`, `--permission-mode bypassPermissions`. Inherited `CLAUDE_*`/`CLAUDECODE` env vars
  stripped; `TMPDIR` and all scratch under `~/.cache/z0-savings-engagement` (harness cohort by
  `claude_code_engagement.cohort`). Per-run `Z0INT_HOME`.
- Isolation: every arm except launcher-lean passes its own `--settings` file with
  `enabledPlugins: {z0intelligence@z0intelligence: false, z0-obspack@z0intelligence: false}`;
  user/project/local setting sources stay on (that is "stock"). Mechanisms under test are registered
  only as hooks in that per-run settings file, running this branch's code
  (`PYTHONPATH=<branch>/src`, stack venv interpreter). The user's settings, plugins, z0 config and
  shell are never modified.
- Billed input tokens of a request = `input_tokens + cache_creation_input_tokens +
  cache_read_input_tokens`, one count per API request (dedup by `message.id`), root and subagent
  transcripts. Cost = Claude Code's `total_cost_usd`. Secondary "weighted" = input + 1.25 cc + 0.1 cr.
- Statistics: paired differences (arm − baseline); median with percentile bootstrap 95% CI (10,000
  resamples, seed 0); exact sign test and exact Wilcoxon signed-rank, two-sided. Pass = verifier.
- At most 3 concurrent claude processes.

## M2 workspace packet (F1)

- cwd: `~/.cache/z0-savings-engagement/ws`, not a git repo, containing 27 `git clone --shared`
  checkouts of the user's z0 repos/worktrees (z0intelligence ×17 branches, z0evals ×4,
  evolution-lab ×3, z0, z0-board, frontier-kb) at their current HEAD and branch, remotes removed, with
  the source checkouts' recency mtimes copied so the packet ranks them as the real workspace does.
  The packet shows 12 of 27.
- Arms: `stock` vs `ws-packet` = stock + SessionStart hook (`Z0INT_CLAUDE_CODE_PACKET=1`,
  `Z0INT_CLAUDE_CODE_OFFLOAD_HINT=0`, so only F1 differs).
- Tasks: `tasks_m2.json`, 8 read-only cross-repo questions with exact answers; 5 target repos the
  packet lists, 3 target repos it does not (`packet_shown`). n = 8 tasks × 3 reps = 24 pairs (48
  runs); arm order alternates by (rep + task index) parity.
- Primary: billed input tokens summed over all requests (root + subagents, by timestamp) up to and
  including the first request that issues a read of the target file in the target repo: a `Read`,
  `Grep`, `Bash` or `NotebookRead` tool input that matches the task's file pattern and names the target
  repo directory in its input or in the tracked Bash cwd (`cd` targets accumulate per agent). If never
  reached, the run's total billed input tokens (censored; counts reported).
- Secondary: pass rate (final `ANSWER:` line, normalized: whitespace/backticks/quotes removed,
  lowercased); weighted tokens to first relevant read; billed cost.
- Decision: claim a saving only if the paired median CI is entirely below 0 **and** ws-packet passes
  ≥ stock. CI entirely above 0 is reported as a cost. Exploratory: shown vs not-shown split.

## M4 subagent packet (F2)

- cwd: fresh `git clone --shared` of this branch at `24a8321` (branch `feat/engagement-v0`, remote
  removed) per run: an agent-cohort-like session in a real repo.
- Arms: `m4-off` = stock + SessionStart packet hook (`PACKET=1`, offload hint default on), as agent
  sessions run today; `m4-on` = same + `SubagentStart` hook (`SUBAGENT_PACKET=1`). Only the
  SubagentStart registration differs.
- Tasks: `tasks_m4.json`, 8 read-only tasks that instruct two parallel general-purpose subagents.
  n = 8 tasks × 3 reps = 24 blocks (48 runs); within each block arm order is randomized
  (`random.Random(4)`), i.e. blocked randomized per-session assignment.
- Primary: subagent billed input tokens per run. Guard: pass rate. Secondary: total cost, total
  billed input, weighted subagent tokens, subagents spawned / packet delivered.
- Decision: recommend default-on only if the primary CI is entirely below 0, pass rate not lower, and
  the median paired cost difference ≤ 0. Otherwise the default stays off.

## M5 settings-lean (F5)

- Tasks: the claude-code-savings-v0 warm/cold set (`add-feature`, `debug-suite`, `fix-bug`,
  `grep-answer`, `nav-harness`, fixtures from `benchmarks/claude_code`).
- Arms: `stock`; `settings-lean` = stock + `lean_settings()['settings']`
  (`skillListingMaxDescChars: 80`, `skillListingBudgetFraction: 0.005`, `disableClaudeAiConnectors:
  true`) via `--settings`, plus the proposal's removal of the user's `lavish-axi` SessionStart hook,
  done by shadowing that binary with a silent no-op on this arm's PATH (a settings overlay cannot
  remove a user hook); `launcher-lean` = `z0int claude-code launch --profile lean --no-plugin -- …`
  (`--no-plugin` so no arm carries the z0 plugin, as in savings-v0's `lean` arm).
- n = 5 tasks × 3 arms × 4 reps (60 runs). One persistent dir per (task, arm), reset between reps;
  rep 0 cold, reps 1–3 warm, reps of one (task, arm) run back to back; the three arms of a task run
  concurrently.
- Primary: billed cost per arm. Paired by (task, rep) against stock: median % change with CI,
  cold (n = 5) and warm (n = 15) reported separately; also launcher vs settings-lean. Guard: verified.
- Decision: "settings-lean carries over" only if its warm median % CI vs stock is entirely below 0 and
  it verifies ≥ stock; report the fraction of launcher-lean's saving it recovers (ratio of medians).
