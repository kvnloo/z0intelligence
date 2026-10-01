# Verification density v0

Goal: raise the share of live Claude Code turns with an independently verified outcome. That share is
the bottleneck of the verified loop: evolution-lab `exp/verified-loop-v0` measured 217 live turns/wk,
28 resolved labels/wk and about 24 weeks to its sufficiency gate. Pre-registration:
[`docs/prereg/verification-density-v0.md`](prereg/verification-density-v0.md), committed before the
measurement. Parent: z0intelligence#54. Semantics of the base verifier: [verified-outcomes.md](verified-outcomes.md).

Module `z0int.verification_density`, hooked into `z0int.outcome_verifier` 0.2.0. `--no-density`
reproduces the v0 signal set.

```
z0int outcomes density --since 7d [--no-gh] [--no-density] [--out counts.json]   # counts only
z0int outcomes density --since 7d --label-sample ~/.z0int/research/verification-density/<date>
                     # PRIVATE excerpts for a separate labeller; refuses to write inside a git work tree
```

## 1. Why live turns are unverified (v0 signal set, local transcripts, about one day)

Live means the interactive and agent cohorts. Task-notification and agent-message hand-off turns are
excluded, as in the loop sweep, because they have no opportunity record. Of 31 live turns, 2 were
verified (no gh) or 4 (gh on).

| turn type | live turns | unverified (no gh) | main reason |
|---|---:|---:|---|
| orchestration (spawns agents / SendMessage / wakeups) | 12 | 10 | tests passed but the turn's subagents edited tests (self-judged, low) 4; commit window open 2; none 3 |
| ops (mutating shell, no edits) | 9 | 9 | no signal 8 |
| qa (no tools) | 7 | 7 | no signal |
| edit_untested | 2 | 2 | commit window open / none |
| research (read-only tools) | 1 | 1 | no signal |

The live population is one interactive session that orchestrates subagents (84 subagent transcripts,
147 hand-off turns), plus five single-turn SDK sessions. Most of the work happens in subagents and in
hand-off turns, not in the user turn the label attaches to.

## 2. Verifiers added

All verifiers emit `verifier_set: density`. A verifier ships at **low** until a pre-registered sample of
>= 50 labelled firings clears 0.90 precision (`PROMOTED` in the module). Demoted signals keep
`design_confidence`.

| signal | polarity | design conf. | shipped | label class | rule |
|---|---|---|---|---|---|
| `checked_later` | +/- | medium (test run) / low (lint, typecheck, self-judged) | low | soft | first test/lint/typecheck run after the turn's last code edit, anywhere later in the session, that covers the edited files (whole-repo run in the same repo, or path args naming the file, its directory or its test). Skipped when another turn edited the same files first. |
| `tests_suite_new_tests` | + | medium | low | soft | in-turn whole-suite pass where every test file the turn touched was newly created |
| `ended_on_error` | - | medium | low | soft | the turn's last non-probe tool call failed (Bash exit != 0, tool error), nothing later succeeded; user-gated tools and permission rejections excluded |
| `edit_reverted` | - | medium | low | soft | later `git checkout -- f` / `git restore f` of a file the turn edited |
| `edit_rewritten` | - | low | low | soft | a later edit deletes >= half (>= 3) of the lines this turn added |
| `answer_ungrounded` / `answer_grounded` | -/+ | medium / low | low | soft | `path:line` citations in the final answer of a turn without edits: path missing (cwd, repo, git ls-files suffix, files read in the turn) or line beyond EOF of a file unchanged since. Grounding, not truth. Skipped when the base directory is gone. |
| `user_correction_v2` / `user_approval` | -/+ | low | low | soft | cue sets on the next real user prompt (ids only): v0 strong cues + `listen`, `i_said`, `you_didnt`, `why_cant_you`, `frustration`, widened `didnt_work` / `still_broken`; approval strong (perfect, makes sense, thanks, ...) / weak (ok, ya, yes, ...) |

Turn type (`turn.type` on every verified row): qa / research / ops / edit_untested / edit_tested /
orchestration / handoff.

### Fixes to the v0 test signal (found by the labelling)

- **Piped exit codes.** `pytest ... | tail -5` reports the exit code of `tail`, so a failing suite
  looked like a pass. For test/lint/typecheck commands piped into a filter without `pipefail`, the
  verifier now reads the runner's summary line (failures win over passes), and treats the exit as
  unknown when there is none. The raw code is kept as `shell_exit`.
- **Installs are not test runs.** Segments that only install, locate or version-check a runner
  (`uv pip install pytest`, `which pytest`) no longer count.

Effect on the 7-day sweep, all cohorts: `tests_in_turn[+1]` went from 114 to 87,
`tests_in_turn[-1]` from 3 to 13, and unknown from 5 to 22. That means about 24% of v0's
test-success signals were not passes. v0 `verified_success` labels from piped runs should be treated
as suspect until re-verified. Re-running `z0int outcomes verify` appends corrected rows (append-only).

## 3. Results against the pre-registration

**T2 precision.** A separate agent labelled every medium firing (all < 60, so no subsampling) from
local excerpts under `~/.z0int/research/verification-density/`. The verifiers ran before the pipe fix.

| verifier | firings (n) | holds | wrong | unsure | precision | Wilson 95% LB | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| checked_later (+) | 33 | 12 | 20 | 1 | 0.38 | 0.23 | demoted (n < 50, precision < 0.9) |
| ended_on_error (-) | 6 | 1 | 4 | 1 | 0.20 | 0.04 | demoted |
| tests_suite_new_tests (+) | 4 | 4 | 0 | 0 | 1.00 | 0.51 | demoted (n < 50) |
| edit_reverted, answer_ungrounded | 0 | | | | | | demoted (no firings) |

Why `checked_later` failed:

- 15 of its 20 wrong labels were piped test runs whose output showed failures (now fixed, see above);
- 2 ran in another worktree or repo;
- 1 was a package install;
- 1 was a glob that matched nothing;
- 1 was a pre-merge worktree.

`ended_on_error` mostly fired on benign failures: probes, globs, bookkeeping writes. After the pipe
fix, `checked_later` has 27 firings and has not been re-labelled.

**User cues** (all 25 next-user prompts in the window, labelled blind). Labels: correction 3,
approval 4, neither 18.

| matcher | fired | tp | fp | fn |
|---|---:|---:|---:|---:|
| v0 strong correction cues | 0 | 0 | 0 | 3 |
| v2 correction cues | 2 | 2 | 0 | 1 |
| approval (strong only) | 2 | 2 | 0 | 2 |
| approval (strong or weak) | 7 | 3 | 4 | 1 |

These numbers are in-sample: the v2 patterns were written after reading these prompts, so recall is
optimistic. n is far below 50, so the cue sets stay low.

**T1 verified share (live, `--since 7d`, gh on).**

| | resolved / live turns | share | not-success |
|---|---:|---:|---:|
| v0 as shipped | 4 / 31 | 0.129 | 1 |
| **pre-registered T1** (new verifiers counted only if they pass T2: none) | 4 / 31 | **0.129** | 1 |
| exploratory: v0.2 code (pipe- and install-aware test exits; density signals all low) | 5 / 31 | 0.161 | 4 |
| hypothetical: density verifiers at design confidence (failed T2; not claimed) | 9 / 31 | 0.290 | 4 |

**Target T1 >= 0.20: not met.** The baseline in the prereg (0.065) was measured without gh. With gh,
v0 is 0.129.

**T3 weekly-labels projection (live, 217 turns/wk).**

- Pre-registered: unchanged at **28 resolved/wk**, so about 24 weeks to the gate (evolution-lab method).
- Exploratory v0.2 code: about **35 resolved/wk**, with observed not-success about 28/wk. The added
  not-success comes from piped test runs that v0 had labelled as passes.
  - At the pooled not-success rate the gate is bound by rows >= 300: about 8.6 weeks.
  - This rests on n = 5 resolved live turns. Treat it as an order of magnitude, not a forecast.
  - The new labels are mechanical exit-code corrections. They are not independently labelled.

**T4 gh runtime** (`z0int outcomes verify --since 7d --all-turns --dry-run`, 436 turns, 217 gh lookups).

| run | wall time | was |
|---|---:|---:|
| cold cache | 36 s | 118 s serial on this machine; about 5 min per the brief |
| warm cache | 20 s | 118 s serial |

Both meet the targets (cold <= 60 s, warm <= 30 s). The cache is `~/.cache/z0int/gh-lookups.json`:
merged/closed PRs and all-completed check-runs are final, everything else expires after 1 h, and
failures are not cached. A planning pass records the lookups and resolves them 8-way concurrently.
Commit outcomes and parsed sessions are memoised across the passes. `--no-gh-cache` turns the cache off.

## Deviations

- The pre-registered baseline (0.065) was measured without gh. T1 is measured with gh, where v0 is 0.129.
- The probe detector (quote- and redirect-aware) was fixed after the sample had been drawn, but
  before any label was seen. The pipe and install fixes were made **after** the labels were seen. They
  are reported only as exploratory and do not count toward T1.
- The live cohort was far too small for >= 50 firings per verifier. The sample used every firing across
  all cohorts and subagent transcripts, still < 50. The demotion rule therefore applied to every verifier.

## Next

[`prereg/verification-density-v1.md`](prereg/verification-density-v1.md): re-label the
pipe-aware verifiers once >= 50 firings per verifier have accrued (about 2 more days at the current rate for
`checked_later`). The episode attribution of hand-off work to the user turn that started it is the
largest remaining unverified class (orchestration), and it is not attempted here.
