# Verified outcomes v0 -- live run

Counts only. No prompt, response or command text. Semantics: z0intelligence docs/verified-outcomes.md (branch feat/verified-outcomes-v0).

- generated_at: 2026-10-03T12:30:31Z
- since: all
- all_turns: False
- fix_days: 7
- gh: disabled
- verifier: z0int.outcome_verifier 0.2.0
- rows_appended: 4

## Verification state

| state | turns |
|---|---:|
| verified_success | 0 |
| verified_failure | 0 |
| contested | 0 |
| unverified | 4 |

## Label class (z0int#54)

| class | turns |
|---|---:|
| deterministic_gold | 0 |
| negative_gold | 0 |
| execution_only | 0 |
| soft | 0 |
| unknown | 4 |

## Signals (kind[polarity])

| signal | count |
|---|---:|

## Measurement completeness

- turns: 4 (with observed turn_outcome.v0 row: 4; with any signal: 0)
- transcript_missing: 2
- commits_produced: 0
- commits_resolved: 0
- turns_with_unexamined_subagents: 0

## Credit join (opportunity -> gate -> observed -> verified)

- observed_rows: 4
- with_opportunity: 4
- with_verified: 4
- full_chain: 4

| gate -> verified state | turns |
|---|---:|
| ACT->unverified | 2 |
| ASK->unverified | 2 |

| observed action -> verified state | turns |
|---|---:|
| ACT->unverified | 4 |
