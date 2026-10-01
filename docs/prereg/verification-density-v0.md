# Pre-registration: verification density v0

Committed **before** any measurement of the new verifiers on real transcripts. Only the v0 diagnosis
below (v0 signal set, `--no-density`) had been run. Parent: z0intelligence#54 (verified outcomes);
consumer: evolution-lab `exp/verified-loop-v0` (INSUFFICIENT_DATA: live 217 turns/wk, 28 resolved/wk,
binding constraint `not_success >= 30`, ~24 weeks to the sufficiency gate).

## Question

Can cheap, deterministic verifiers raise the share of live turns that carry an independently
verified outcome (medium/high-confidence signal, per `decide()`), without labels the loop cannot
trust?

## Diagnosis (v0 signal set, measured before this file was written)

`z0int outcomes density --since 7d --no-gh --no-density` over the local transcripts (span about one
day, 2026-09-30T04:06Z to 2026-10-01T03:44Z):

| population | turns | resolved | share |
|---|---:|---:|---:|
| live (interactive + agent cohorts) | 31 | 2 | 0.065 |
| harness (eval/probe arms) | 325 | 89 | 0.274 |

Live turns by type (unverified / total): orchestration 10/12, ops 9/9, qa 7/7, edit_untested 2/2,
research 1/1. Unverified reasons in the live cohort: no signal at all 20, tests passed but the turn
had edited tests (self-judged, low) 4, commit survival window still open 3, informational only 2.
Task-notification / agent-message ("hand-off") turns are excluded from the denominator, as in the
loop sweep: they have no opportunity record.

## Verifiers under test (code at the commit that adds this file)

`src/z0int/verification_density.py` v0.1.0, hooked into `outcome_verifier` 0.2.0:

| verifier | polarity | confidence as shipped | label class |
|---|---|---|---|
| `checked_later` (test run, turn did not edit existing tests) | +/- | medium | deterministic_gold / negative_gold |
| `checked_later` (lint/typecheck, or self-judged tests) | +/- | low | soft |
| `tests_suite_new_tests` | + | medium | deterministic_gold |
| `ended_on_error` | - | medium | negative_gold |
| `edit_reverted` | - | medium | negative_gold |
| `edit_rewritten` | - | low | soft |
| `answer_ungrounded` | - | medium | negative_gold |
| `answer_grounded` | + | low | soft |
| `user_correction_v2`, `user_approval` | -/+ | low | soft |

## Primary outcome

**T1 (verified share).** Share of live turns (interactive + agent cohorts, non-hand-off turns) with
`verification_state != unverified` in `z0int outcomes density --since 7d` (gh enabled), counting only
new medium/high verifiers that pass T2. Baseline 0.065 (v0). **Target: >= 0.20.**

## Precision protocol

**T2 (per verifier).** For each new verifier shipped at medium/high: draw up to 60 firings at random
(seed 20260930) from all local transcripts in the 7-day window, root sessions of every cohort plus
subagent transcripts read as standalone sessions. The live cohort alone is too small to sample. A
separate agent labels each firing from a local excerpt (prompt head, the turn's tool calls with exit
codes and short output tails, edits as paths plus short diffs, the final answer head, the next user
prompt head, and the evidence the verifier used). Excerpts and labels are stored under
`~/.z0int/research/verification-density/`, never in git. The labeller does not see the verifier's
confidence and answers:

- `holds`: the evidence shows the turn's work succeeded (for +) or failed / was not achieved (for -);
- `wrong`: the evidence contradicts the label;
- `unsure`.

Precision = holds / (holds + wrong). **Bar: precision >= 0.90 with n_labelled >= 50 and unsure <= 20%.**
The Wilson 95% lower bound is reported alongside. A verifier with fewer than 50 firings, or one that
misses the bar, is **demoted to low** in code. T1 is then recomputed without it, and the
recomputed value is the reported T1.

**User cue sets (v2).** These ship at low. The only available user prompts are the about 26 real
prompts in the live window, and the patterns were written after reading them, so any measured recall
is in-sample and optimistic. A hand-checked sample (every next-user-prompt in the window, labelled
correction / approval / neither by the separate agent) gives precision and recall for v0 versus v2.
Promotion to medium needs >= 50 labelled firings at >= 0.9. This sample cannot reach that.

## Secondary outcomes

- **T3.** Projected resolved labels per week for live = live turns/wk x T1 share, and not-success/wk.
  Weeks to the evolution-lab sufficiency gate (rows >= 300, not_success >= 30) at the current rate.
- **T4.** `z0int outcomes verify --since 7d --all-turns` with gh: cold-cache wall time <= 60 s, warm
  rerun <= 30 s. The baseline before this change was 99 s on this machine; the brief reported about 5 min.

## Fixed in advance

- No threshold, pattern or confidence is tuned after the labels are seen. The only post-label action
  is the demotion rule above.
- Population definitions are the loop sweep's (`loop_export.sweep_counts`, `claude_code_engagement.cohort`).
- Deviations are listed in the result doc under "Deviations".
