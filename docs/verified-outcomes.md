# Verified outcomes v0

First slice of [z0intelligence#54](https://github.com/kvnloo/z0intelligence/issues/54)
(experience -> outcome -> counterfactual credit contract; parent kvnloo/z0#15). Related:
#55 (selective autonomy: ASK/ABSTAIN need their own outcome), #56 (promotion needs a verifier /
outcome contract).

The Claude Code plugin already writes, per turn:

| record | file (under `~/.z0int/state/claude-code/`) | what it is |
|---|---|---|
| `z0int.claude_code.opportunity_record.v0` | `opportunities.jsonl` | DecisionOpportunity + deterministic gate decision, written at prompt time |
| `z0int.claude_code.turn_outcome.v0` | `outcomes.jsonl` | **observed behaviour** at Stop time (`label_kind: observed_behaviour_not_optimal`) |
| `tokenomics.event.v0` | `~/.z0int/tokenomics/events.jsonl` | cost / mechanism attribution |

Observed behaviour says what the agent did. It is not a label of whether that was right. This
module adds a separate, append-only verification record, written after the fact by
`z0int outcomes verify`:

| record | file | written by |
|---|---|---|
| `z0int.claude_code.turn_outcome_verified.v0` | `outcomes_verified.jsonl` | `z0int outcomes verify [--since 7d] [--all-turns] [--fix-days 7] [--no-gh] [--no-gh-cache] [--no-density]` |
| `z0int.claude_code.credit_join.v0` | wherever `--out` says | `z0int outcomes join [--out PATH]` |

Observed rows are never rewritten. A verified row is appended only when its content (signals +
state) differs from the latest verified row for the same turn, so re-running is idempotent. A
re-run is also how a label matures: a commit's survival window closes, or a PR gets merged.

## Identity (stable across restart / replay)

`(session_id, trace_id)` is the turn key. `trace_id` is Claude Code's `prompt_id`. The observed
row, the opportunity record (`opportunity.trace.trace_id`) and the transcript's user row
(`promptId`) all carry it. The join chain is:

```
opportunity_id / semantic_id   (DecisionOpportunity, prompt time)
  -> gate_decision             (deterministic_gate: ACT / ESCALATE / ASK / OBSERVE / ABSTAIN)
  -> observed                  (turn_outcome.v0: asked_user / tool_calls / ...; observed_ref.row_sha)
  -> verified                  (turn_outcome_verified.v0: verification_id, state, label_class, oracles)
```

`verification_id` hashes (turn key, content, verifier id+version), so the same evidence always gets
the same id.

## Signals

Each signal has a `kind`, a `polarity` (+1 success evidence, -1 failure evidence, 0 informational),
a `confidence` (low / medium / high), a #54 `label_class` and an `oracle` (who did the judging).

| signal | polarity | confidence | label_class | oracle | how it is computed |
|---|---|---|---|---|---|
| `tests_in_turn` | + if the **last** test run in the turn exited 0, - otherwise | medium | deterministic_gold / negative_gold | test_runner | Bash tool calls matching a test-runner pattern (pytest, cargo test, go test, npm test, ...) and their exit code from the tool result. Runs in subagents the turn spawned count too. |
| `tests_in_turn` (tests edited) | + | **low** | soft | test_runner | same, but the turn also edited a test file. The agent wrote its own judge, so this is not independent (#54: optimizer labels must not become their own judge). |
| `ci_watch_in_turn` | +/- | medium | gold / negative_gold | ci | `gh run watch` / `gh pr checks` exit code (exit 8 means pending and is ignored) |
| `commit_reverted` | - | high | negative_gold | vcs_history | a later commit on any branch whose subject starts with `Revert` and names our SHA or subject (`reverted_by`, ported from feat/promotion-sim-v0) |
| `commit_szz_fixed` | - | high | negative_gold | vcs_history | SZZ: within `--fix-days` (default 7) a later commit whose subject says fix/bug/regress/... deletes or rewrites lines that `git blame` at its parent attributes to our commit (`szz_origins`, ported from feat/promotion-sim-v0) |
| `ci_on_commit` | +/- | medium | gold / negative_gold | ci | check-runs on the pushed commit (`gh api .../check-runs`, GET), ignoring non-code workflows |
| `commit_survived` | + | **low** | soft | vcs_history | window elapsed, no revert, no SZZ fix. Absence of evidence, so it never decides a state on its own. |
| `commit_created` | 0 | low | execution_only | vcs_history | survival window still open |
| `pr_merged` | + | medium | deterministic_gold | pr_review | `gh pr view` state MERGED, for a PR the turn opened (`gh pr create` output or a transcript `pr-link` row) |
| `pr_merged` (merged by agent) | + | **low** | execution_only | pr_review | the session itself ran `gh pr merge` on it. This is self-acceptance, not review. |
| `pr_closed_unmerged` | - | medium | negative_gold | pr_review | `gh pr view` state CLOSED |
| `user_correction` | - | medium | soft | user_cue | next **user** prompt (harness/peer messages skipped), first 300 chars, matches a strong cue: `undo`, `revert that`, `that's wrong`, `not what I asked`, `you broke`, `didn't work`, `still broken`. Only the cue ids are stored. |
| `user_weak_correction` | - | low | soft | user_cue | weak cues (leading `no`, `wrong`, leading `stop`) or a **re-ask** (word-set Jaccard >= 0.6 with this turn's prompt) |
| `user_interrupt` | - | low | soft | user_cue | `[Request interrupted by user]` during the turn |
| `ask_answered` / `ask_ignored` / `ask_pending` | 0 | low | soft | user_cue | the turn asked (AskUserQuestion, or the final text ends in `?`). Answered means a later user prompt exists, or the AskUserQuestion result was not an error. Ignored means no later user prompt and the session has been idle for over an hour. This is the outcome of the ASK action (#55), not task success. |

Test/lint exit codes are **pipe-aware** (verifier 0.2.0): when the runner's output is piped into a filter
(`| tail`, `| grep`, ...) without `pipefail`, the shell exit belongs to the filter, so the runner's summary
line decides (failures win) and the exit is unknown without one (`shell_exit` keeps the raw code). Segments that
only install or locate a runner (`pip install pytest`, `which pytest`) are not test runs. Additional
session-level verifiers (`verifier_set: density`, all shipped at low until a pre-registered precision sample
clears 0.9) and the turn-type diagnosis live in [verification-density.md](verification-density.md).

Commits produced by a turn are found from successful `git commit|revert|cherry-pick` tool calls.
The SHA comes from the `[branch sha]` line. For `git commit -q` there is no such line, so the
fallback is any commit whose committer time falls inside that tool call's start..result window, in
the repo the command ran in (`cd X`, `git -C X`, or the cwd). `measurement.commits.matched_by_time_window`
counts these fallback matches.

## verification_state

Only **medium/high** signals decide. Low signals stay attached as annotations.

| state | rule |
|---|---|
| `verified_success` | at least one decisive positive signal, no decisive negative |
| `verified_failure` | at least one decisive negative signal, no decisive positive |
| `contested` | decisive signals of both polarities (e.g. tests passed, then the user said "that's wrong") |
| `unverified` | no decisive signal. `label_class` is still `execution_only` (work happened, nobody judged it), `soft` (only low-confidence evidence) or `unknown` (nothing observed). |

The row's `label_class` and `label_confidence` come from the strongest decisive signal.

## Mapping to #54

| #54 contract field | where it lives |
|---|---|
| DecisionOpportunity identity | join: `opportunity.opportunity_id`, `semantic_id`, `intent_revision` |
| trace / attempt lineage | `session_id`, `trace_id` (= prompt_id). Subagent work is folded into its spawning turn (`turn.bash_calls_via_subagents`). |
| candidate action set | join: `opportunity.legal_actions` |
| action actually taken | join: `observed.action` (ASK if the turn asked, else ACT) |
| deterministic gate decision | join: `gate_decision`, `gate_agrees_with_observed` |
| tool / execution result | `signals[*]` with oracle test_runner / ci / vcs_history |
| independent verifier / outcome | `verification_state` + `signals[*].oracle` |
| retries / corrections / escalations | `tests_in_turn.failed_runs`, `user_correction`, `user_weak_correction.re_ask`, `user_interrupt` |
| measurement completeness | `measurement` (transcript found / turn_not_found / missing, subagents inspected vs not, commits produced vs resolved, gh enabled, gh failures, survival window pending) |
| label source + confidence | `label_class` (deterministic_gold / negative_gold / execution_only / soft / unknown) + `label_confidence` |
| counterfactual availability | `credit.counterfactual_available: false`, `counterfactual_claimed: false` |
| credit level | `credit.level: observational`, always, in v0 |
| privacy / provenance class | `privacy: counts_ids_and_shas_only`, `verifier.{id,version,params}` |

Invariants the code enforces:

- **Execution completion is not verified success.** A commit, an open PR, a self-merged PR or a
  test run with no exit code is `execution_only` and leaves the state `unverified`.
- **The agent cannot be its own judge.** Tests the turn edited, and PRs the session merged itself,
  are downgraded to low confidence and never decide a state.
- **Missing is not negative.** A missing transcript, an unresolved SHA, `gh` being off or failing,
  or uninspected subagents all show up in `measurement` and never become a failure.
- **Negative and inconclusive outcomes are first-class.** `verified_failure`, `contested` and
  `unverified` rows are written just like successes.
- **The observed action is not the optimal label.** The join says this in `credit.note`.

## What is NOT claimed

- **No counterfactual.** A `verified_success` means this turn's own outputs passed an
  independent check. It does not mean another action (ASK instead of ACT, a different
  model/tool/route) would have done worse. No alternative action was executed, so the credit level
  is `observational`, never `ablation`, `paired counterfactual` or `causal`.
- **No causal credit to a component.** A verified turn does not show that the router, packet,
  skill or model choice caused the success.
- **The cues are not semantic judgement.** `user_correction` is regex on the next prompt.
  "no, also do X" can look like a correction (that one is only a weak cue), and real dissatisfaction
  phrased differently is missed.
- **Survival is not proof.** A commit nobody reverted or fixed within N days could still be wrong.
  It is the same caveat promotion_sim states for `fix_Nd`.
- **Time-window commit matching is heuristic.** Another process committing to the same repo in
  the same few seconds would be mis-attributed. Such matches are counted separately.
- **No text is labelled.** Prompts, responses and commands are read transiently and never stored.
  Rows hold counts, cue ids, SHAs, PR numbers and hashed repo ids.

## Training table export (verified loop v0)

`z0int outcomes export --out PATH [--include-unjoined] [--json]` (module `z0int.loop_export`) writes
one `z0int.loop.training_row.v0` row per turn that has an opportunity record, plus
`PATH.manifest.json` (`z0int.loop.training_table_manifest.v0`: `table_version`, ordered `features`,
`feature_schema_sha`, source file row counts + sha256 prefixes, label/state counts).

- **features** (prompt-time only, ints over a fixed vocabulary): scope mode, required fact
  families, claim/superseded/unknown counts (by status and family), contradiction count and
  families, effects, authority source/grants/fingerprinted, missing authority, legal actions,
  deterministic gate, posture factory mode, cohort (interactive / agent / harness).
- **observed** (`post_decision: true`): the action the agent took. Evaluators use it to restrict to
  turns that actually acted; learners must not use it as an input.
- **label**: `state`, `label_class`, `y_success` (1 = verified_success, 0 = verified_failure or
  contested, null = unverified: missing, not negative). The latest verified row per turn wins.
- **ids**: `turn_key` = sha(session, trace), `group` = sha(session) for grouped cross-validation.
  Harness messages are dropped. No prompt, response, command, path or claim value is exported;
  `assert_private` fails closed on text-bearing keys.

Changing the feature vocabulary is a `table_version` bump. The offline learner lives in
evolution-lab (`exp/verified-loop-v0`, evolution-lab#24).

## Not yet covered (next slices)

- outcomes of ABSTAIN / ESCALATE / OBSERVE (the observed row records only ASK vs not-ASK);
- per-hunk attribution when one turn's commits are partly fixed;
- CI regressions relative to the target branch before the merge (promotion_sim `ci_broke`
  compares to the previous green run; v0 reads only the commit's own check-runs);
- verified rows for Hermes / OMP harnesses (the record is harness-neutral; only the transcript
  reader is Claude Code-specific).
