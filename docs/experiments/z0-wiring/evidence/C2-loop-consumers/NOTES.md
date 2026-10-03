# C2-loop-consumers: labels and cohorts for every harness

Branch `feat/wire-loop-consumers-20261003` in `/mnt/zer0models/z0-wt/wiring/wt/C2-loop-consumers`, based on the C1 head
`feat/wire-loop-core-20261003 @ 914dc99`. Commits, not pushed:

- `0bceec9` test(loop): the red tests (tests only)
- `6f5855f` feat(loop): the implementation
- `cf0b092` test(loop): round 2 red tests for the verifier's REVISE findings (tests only)
- `352edf2` fix(loop): round 2 fixes

Evidence in this directory (round 2 is current; round 1 files are kept with an `-r1` suffix):

- `red.txt`: round 2 red run at the test-only commit cf0b092 (temporary worktree, removed): 8 failed, 60 passed.
  `red-r1.txt`: round 1 red at 0bceec9 (59 failed, 1 passed).
- `green.txt`: 68/68 at 352edf2. `green-r1.txt`: 60/60.
- `suite.txt`: full suite, head vs base, both run this round. `suite-r1.txt`: the incomplete round 1 record.
- `cc-verify-6fee859.json` / `cc-verify-head.json`: the regression guard (re-run at 352edf2).
- `e2e.txt` (summary) and `e2e.raw.txt` (full JSON report); `e2e-r1*.txt` round 1.
- `scripts/`: the runner, socket guard, red/green, suite, golden and e2e drivers (`e2e_c2.r1.py` = round 1 driver).

## Round 2 (verifier REVISE) fixes

| finding | red test | fix |
|---|---|---|
| major: an orphan jev_decision stalls dsh-jev for good | test_dsh_jev_orphan_decision_never_stalls_the_import, test_dsh_jev_orphan_that_is_no_longer_the_tail_is_imported | held only within the last `JEV_TAIL_LINES` (64) lines and `JEV_GRACE_S` (600 s) of its ts; otherwise imported as `no_lineage`; no break, later rows are always processed; the watermark stores `counted` and `held` so re-read lines are deduped by content and counted once |
| major: export carries stale rows after a scrub correction | test_scrub_correction_leaves_one_row_per_turn_with_the_corrected_label, test_shadow_tables_keep_the_latest_row_per_decision | `loop_export._latest`: imported_turn rows latest-wins by turn_key, shadow_decision rows by decision_id (per harness), before the table is built; export and merge carry one row per id |
| minor: symlink into /workspace/hermes-home passes the guard | test_dsh_importer_never_reads_hermes_home (symlink case) | `_guard` checks `abspath` and `realpath` of the source against the roots and their realpaths (lstat/readlink only, never an open) |
| minor: verify exits 0 when degraded | test_cli_verify_exits_nonzero_when_the_report_is_degraded | `outcomes verify` returns `EXIT_DEGRADED` = 3 when status != success (rows are still written unless --dry-run) |
| minor: codex doc vs code; ordinal off-by-one when AgentsView has fewer messages | test_ordinal_join_checks_capture_time_alignment, test_ordinal_join_alignment_accepts_capture_close_to_the_message | codex doc says capture order (recorded_at), not turn_id; a turn captured more than `ALIGN_TOLERANCE_S` (60 s, INFERRED) before the message it would join is unjoined(`ordinal_misaligned`), and so is every later turn of the session |
| minor: --since filters joined turns only | test_since_windows_every_captured_turn_and_the_join_counts | `since` applies to every captured turn by its capture time (AgentsView start only when recorded_at is absent); join counts and failure rows use the same window; `--all-turns` is documented as claude-code only |

After the round 2 red run, one test-only change: the open spy in test_dsh_importer_never_reads_hermes_home now refuses
any open of the link or a hermes-home path itself (so even the red run can never reach the live home). red.txt was
re-recorded from the test-only commit cf0b092 with that spy.

## What was built

**`src/z0int/turn_readers.py` (new)** holds both turn readers:

- The Claude transcript reader is re-exported from `outcome_verifier`.
- `AgentsViewReader` opens through C1 `agentsview_ro.connect` (mode=ro, user_version 74/113, staleness). It rebuilds each
  AgentsView session from `messages` / `tool_calls` / `tool_result_events` into the exact turn dict the transcript reader
  builds. The shared constructors `outcome_verifier.new_turn`, `bash_result` and `finish_turns` were extracted from
  `turns_from_transcript` without a behaviour change. As a result, `verify_turn`, `verification_density`, check_class,
  SZZ/revert, PR self-merge and the correction cues apply unchanged.
- `JOIN_RULES` has one documented rule per harness. Each maps the captured session to `<agent>:<sid>`:

  | harness | AgentsView id |
  |---|---|
  | hermes | `hermes:` |
  | codex | `codex:` (mapping INFERRED) |
  | grok | `grok:` (GROK_SESSION_ID) |
  | omp | `omp:` |
  | omo | `omo:` |
  | dsh | `deepseek-harness:` |

  The n-th captured prompt turn of the session (capture order, subagent `agent:*` turns excluded) is the n-th AgentsView
  user message. A turn is "unjoined" with a failure row in four cases:
  - Ambiguity: two rows claim the session (id or source_session_id).
  - More AgentsView user messages than captured turns.
  - The turn was captured more than 60 s before the AgentsView message of its ordinal (`ordinal_misaligned`, round 2).
  - The session is missing while the index is fresh.

  A missing session or ordinal while the index is stale (> `--stale-hours`, default 6) gives `pending_index` and never a
  negative.
- `classify_cohort` returns interactive / agent / automated / harness / eval / unknown. What the capture fixed wins
  (harness-injected prompt, agent, automated, eval). After that:
  - relationship_type subagent -> agent
  - session_kind non-interactive / roborev, or `is_automated` -> automated
  - Hermes project `hermes-cron|kanban|cluster*` (or a raw id `cron_*` ...) -> automated
  - a known session otherwise -> interactive
  - no session -> unknown

**`outcome_verifier.verify_harness` and `z0int outcomes verify --harness X [--agentsview-db] [--stale-hours]`:**

- For claude-code, `verify` is unchanged. It adds `label_source_empty` (with `resolved_dir` and a session count) when
  captured sessions exist but the projects dir holds none of them.
- For the others, it reads AgentsView through the join rule. Rows use `z0int.<h>.turn_outcome_verified.v0` and carry
  `harness`, `turn_key`, `cohort` and `join`.
- `reader_unavailable` (missing, locked, schema, or `agent_missing`) and `label_source_empty` make the report `degraded`,
  never `success`, and the CLI exits 3.
- `--since` applies to every captured turn by its capture time.
- Failure rows are written once (deduplicated by kind + turn_key + detail). Verified rows go through the existing
  content-sha `append_new`. A re-run appends nothing and returns the same manifest hash.

**`loop_export`:**

- `build_table(harness=...)` reads every `z0int.<harness>.*` v0 record family.
- `Table` is one harness x cohort; its constructor refuses foreign rows, so there is no pooling API.
- `build_tables`, `shadow_tables` and `scan_records` (accepted schemas, failures by kind, `unsupported_schema` counts).
- `export_tables` writes `<harness>/<cohort>[.shadow].jsonl` plus manifests plus an index `manifest.json` with
  `manifest_sha256` (`generated_at` is excluded from the hash).
- `merge_tables` / `merge`: by turn_key, `hosts` provenance per row, the more resolved label wins, order-independent.
  It refuses a cross-harness, cross-cohort, cross-kind or cross-version merge, and any private key, before writing.
- The first_opp fix: `recorded_at` is used when `built_at` is null; the `'~'` sentinel is gone.
- CLI: `z0int loop export|merge|import` (the old `z0int loop --out` and `z0int outcomes export` are unchanged).

**`src/z0int/legacy_import.py` (new)** writes cohort `legacy`, text-free and watermarked:

- `import_omp_v1`: decision_receipt.v1 + outcome_join.v1 -> `z0int.omp.imported_turn.v0`. The label is
  `receipt.effective_tier` (gold -> 1, negative -> 0, execution/soft -> null), so an ambient close is never a success.
- `import_cognition_shadow`: only served answers become `z0int.omp.shadow_decision.v0`, with `actual_tool` {name,
  risk_class} and no input. Errors are counted as `backend_unavailable`.
- `import_dsh_jev`: jev_decision plus the lineage of its routed model_request -> `z0int.dsh.shadow_decision.v0` with
  y null.
- Paths under /workspace/hermes-home are refused unopened, also through a symlink (`abspath` + `realpath`).
- Watermark: a byte offset plus a hash of the head bytes. A rewritten file starts over; output is deduplicated by
  content sha per id.

## Regression guard (not a red test)

**CC verifier:** `scripts/cc_verify_golden.py` ran on a synthetic CC fixture set covering tests, the correction
contest, agent-edited tests, SZZ fix, revert, PRs merged/closed/self-merged, ASK, a meta prompt, all_turns, credit_join
and summary. It ran on 6fee859 (temporary worktree, removed) and on head, on the same scratch path. The two outputs are
**byte-identical**: sha256 fc8b94c2... for both. A first attempt on two different scratch paths differed only in
`repo_id`, which hashes the scratch repo path.

**CC export:** `tests/test_cc_export_regression.py` (the C1 golden from 6fee859) stays green. `manifest()` counts and
`FEATURES` / `TABLE_VERSION` / `FEATURE_SCHEMA_SHA` are unchanged.

## Red-test map (PLAN red_tests 1-17)

| # | tests |
|---|---|
| 1 | test_turn_readers::test_verify_harness_gives_the_claude_code_signal_semantics_from_agentsview[hermes,omp,omo,codex,grok,dsh]: the same scenario through the CC verifier and through AgentsView gives identical signal tuples, state and label class per turn |
| 2 | test_join_rule_hermes_session_plus_user_message_ordinal, test_join_rule_hermes_two_candidate_rows_are_unjoined_and_counted |
| 3 | test_join_rule_codex_hook_session_and_turn_id_by_user_message_ordinal (INFERRED pinned), test_join_rule_codex_ordinal_mismatch_is_unjoined |
| 4 | test_join_rule_grok_session_env_by_user_message_ordinal, test_join_rule_grok_ambiguity_is_unjoined |
| 5 | test_join_rule_omp_omo_bridge_session_by_user_message_ordinal[omp,omo] |
| 6 | test_join_rule_dsh_lineage_turn_key_joins_deepseek_harness, test_join_rule_dsh_without_the_deepseek_harness_agent_is_reader_unavailable |
| 7 | test_cohort_classifier_per_harness (11 cases), test_cohort_classifier_capture_flags_win_and_no_metadata_is_unknown, test_cohort_lands_on_verified_rows_and_never_pools_into_interactive, test_claude_code_transcript_cohort_is_unchanged (guard; passes on base) |
| 8 | test_reader_is_read_only, test_reader_supports_both_known_user_versions[74,113], test_unknown_user_version_is_a_reader_unavailable_row_not_empty_success, test_stale_index_gives_pending_index_never_negative |
| 9 | test_verify_harness::test_label_source_empty_names_the_resolved_dir_and_degrades_the_report (+ test_claude_code_rows_through_verify_harness_equal_verify) |
| 10 | test_loop_tables::test_export_accepts_every_harness_v0_record_family[7 harnesses], test_export_tables_split_by_harness_and_cohort_and_count_what_they_cannot_use, test_the_table_api_cannot_pool_harnesses_or_cohorts |
| 11 | test_first_opportunity_falls_back_to_recorded_at_when_no_packet_was_built (red: `'~' == '2026-09-30T09:00:00Z'`) |
| 12 | test_loop_merge_keeps_host_provenance_and_is_reproducible, test_loop_merge_refuses_to_pool_harnesses_cohorts_or_schema_versions, test_loop_cli_export_and_merge |
| 13 | test_legacy_import::test_omp_v1_spine_imports_gold_and_negative_tiers_as_the_only_labels, test_omp_v1_rerun_adds_zero_rows_and_keeps_the_manifest |
| 14 | test_cognition_shadow_keeps_served_answers_only (6,902 receipts, 6,900 errors -> 2 rows) |
| 15 | test_dsh_jev_receipts_become_legacy_shadow_decisions_never_gold, test_dsh_importer_never_reads_hermes_home (open spy + refusal) |
| 16 | test_new_tables_refuse_text_keys (legacy), test_merged_tables_refuse_text_keys (merged), plus assert_private on every importer output and the shadow tables in the export |
| 17 | test_every_importer_is_idempotent_by_watermark, test_verify_harness::test_reader_rerun_is_idempotent_by_watermark |

## Deviations / decisions (recorded, within scope)

- **The Claude reader stays where it is.** It still lives in `outcome_verifier` and is re-exported by `turn_readers`.
  Moving it would only churn the CC path that must stay byte-identical. The shared turn constructors were extracted
  instead.
- **Cohort vocabulary vs features.** The `cohort=` one-hot (`COHORTS`) stays unchanged, so `FEATURE_SCHEMA_SHA` and
  TABLE_VERSION do not change and the CC export is byte-identical. The full vocabulary is `TABLE_COHORTS`, adding
  automated, eval and legacy. It lives in the row, the table name and the manifest. Rows of the new cohorts carry
  `cohort=unknown` in the one-hot, which is constant within a table because tables are never pooled.
- **Manifest placement.** `manifest()` counts are unchanged (they are part of the CC golden). Failure-row and
  `unsupported_schema` counts are in the new per-table manifests' `records` key and in the index manifest.
- **Ordinal source.** C1 records do not store the per-session work-item ordinal. The ordinal is therefore taken from
  capture order: the earliest recorded_at of the turn's opportunity or outcome row, then the file position. The rule
  says INFERRED for codex only, as the plan does. For the others, the ordinal convention is documented in `JOIN_RULES`.
  Any count mismatch is unjoined rather than guessed.
- **Report status.** "unjoined" turns are counted (join counts, failure rows) but do not by themselves make the status
  degraded. Only `reader_unavailable` and `label_source_empty` do: a source that cannot answer.
- **Unjoined and pending rows are appended** as verified rows (unverified / pending_index, no signals) so the export and
  C6 see them. `content_sha` for non-CC rows includes cohort and join state, so a later join appends the new row.
- **`missing_verifier` is still written at capture** for non-CC harnesses. C1's `harness_capture.VERIFIERS` is
  unchanged: non-CC labels in production depend on G-AV (AgentsView upgrade/resync, A6), and widening the C1 capture
  contract is outside C2. This is flagged for the Integrate/C6 owner.
- **Reuse not taken:**
  - `adapters/hermes_z0int close_observation/join_outcome`: the execution_completed vs verified_success rule is applied
    through `receipt.normalize_outcome` / `effective_tier`, which is the same rule; no Hermes adapter code was needed.
  - The dcbcc88 `context_providers.py` AgentsView SQL is FTS search over messages. Turn reconstruction needed plain
    reads of messages / tool_calls / tool_result_events, written here against the real v0.44 schema. Nothing from
    `lexical_hermes` was used.
- **Exit codes from AgentsView tool results.** Three patterns are read: `Exit code N` (CC), `"exit_code": N` (Hermes
  terminal JSON) and `Process exited with code N` (Codex). Otherwise the tool_result_events status decides: completed
  -> 0; errored, cancelled or running -> unknown. This is the CC `_exit_code` semantics. The Hermes and Codex result
  shapes are INFERRED from their tool outputs; a wrong guess yields "unknown", never a fabricated pass.
- **DSH legacy turn_key** is `turn_key('dsh', lineage session_id, str(turn))`. A jev_decision with no later line of
  its agent holds the watermark only while it is within the last 64 lines and younger than 600 s by its ts (the plugin
  writes the request in the same handler); the lines after it are still processed. Any other decision without a
  routed request is imported with an `agent:<hash>` session and counted `no_lineage` (round 2; round 1 held and broke).
- **Test-setup fixes after the first red run** (before any implementation was written):
  - `test_verify_harness`: create the CC state dir before `observed()`.
  - `test_turn_readers`: the cohort-pooling test uses `include_unjoined=True`, because its capture helper writes outcome
    rows only.

  red.txt was then re-recorded from the test-only commit 0bceec9.

## Hard rules

- Nothing live was touched. The real AgentsView DB was opened only by the sqlite3 CLI in mode=ro for `.schema`: once
  during the build, into homes/schema, and once in each e2e run.
- No real ~/.z0int, ~/.dsh or transcript content was read. I read schema/source only from /mnt/zer0models/z0-wt/ro/
  (agentsview source for id prefixes and session_kind; hermes-jev-skills 9ea5777 for the DSH receipt shape).
- All runs used isolated homes under homes/C2-loop-consumers with the socket guard on. There were 0 violations, nothing
  was pushed, and no stash or history rewrite was used.
- Breach (round 1): the first e2e run was started without the `flock -s quiet-lane.lock` wrapper
  (`e2e-unlocked-run.raw.txt`). The rerun under the lock gave an identical report. Every other run held the shared lock.
- Round 2: every red/green, golden, suite and e2e run held `flock -s`; the e2e also ran under `hostless`. The first
  round 2 red run (before the spy was hardened) let the symlink case call open() on the scratch link, pointing
  under /workspace/hermes-home: that open failed (no such file or not readable), the watermark and output stayed unchanged (checked in
  the test tmp dir), nothing was read. The re-recorded red.txt run refused the open in the spy.

## Activation

None of its own (library + CLI consumed by C6). For A0, with owner approval, a read-only counts-only dry run:

```
Z0INT_HOME=<scratch copy or --root scratch> z0int outcomes verify --harness <h> --agentsview-db ~/.agentsview/sessions.db --dry-run --json
z0int loop import dsh-jev --src ~/.dsh/jev/receipts.jsonl --root <scratch>
z0int loop import omp-v1 --decisions ~/.z0int/receipts/decisions.jsonl --outcomes ~/.z0int/receipts/outcomes.jsonl --root <scratch>
z0int loop import omp-cognition-shadow --src ~/.z0int/shadow/cognition-shadow.jsonl --root <scratch>
z0int loop export --root <scratch> --out-dir <scratch>/tables
```

`outcomes verify` exits 3 on a degraded report (C6 should treat 3 as "label source not answering", not success).
`outcomes verify` without `--dry-run` appends rows to `$Z0INT_HOME/state/<h>/`, so A0 must use `--dry-run` or a scratch
Z0INT_HOME. Non-CC labels in production need G-AV (A6: AgentsView v0.44 resync, user_version 113, sync running).
