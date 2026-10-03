# C2-loop-consumers, build round 2 (fix of the verifier REVISE in ../../../round2/C2-last-verdict.json)

Worktree /mnt/zer0models/z0-wt/wiring/wt/C2-loop-consumers, branch feat/wire-loop-consumers-20261003 (existing branch,
new commits only, nothing rewritten, not pushed):

- 4a7df22 test(loop): red tests for the C2 round-2 verifier findings (tests + fixtures only)
- 0a73899 fix(loop): no status is never a pass, v0.39 sessions read, reader errors are failure rows

publish/SUMMARY.md did not exist at fix time; the findings come from C2-last-verdict.json (1 blocker, 1 major,
1 minor) and the integration E2E.md lines on C2 (non-CC verify/export missing: C2 itself, unchanged by this round).

| finding | red tests (4a7df22) | fix (0a73899) |
|---|---|---|
| blocker: exit_code() returned 0 with no result status; OMP 'Command exited with code N' not matched -> failing runs minted verified_success / deterministic_gold | test_omp_failing_test_run_without_result_event_is_a_verified_failure (omp/omo x pytest, make, cargo 101, npm), test_exit_code_reads_the_omp_notice_and_is_unknown_without_status_or_code, test_shell_result_without_status_or_exit_code_is_never_verified_success (omp, omo, hermes JSON without exit_code, dsh x2), test_hermes_terminal_json_exit_code_gives_the_label | EXIT_RE adds `(?:Process\|Command) exited with code N`; without an exit-code line, 0 only on an explicit `completed` status, else None (unknown). Fixture: `AVFixture.tool(status=None)` writes no tool_result_events row, as the pi, hermes and deepseek_harness parsers do |
| major: v0.39 (user_version 74) has no sessions.session_kind -> uncaught OperationalError; the 74 test relabelled the v0.44 schema | tests/fixtures/agentsview_core_schema_v039.sql (the four core tables from agentsview v0.39.0 internal/db/schema.sql, schema only); AVFixture(user_version=74) now builds from it; the signal-semantics test (all six harnesses), the hermes join test and the cohort test run at 74 and 113; test_v039_schema_without_session_kind_joins_and_classifies; test_reader_sqlite_error_is_a_reader_unavailable_row_never_an_exception (dropped table x2, dropped column) | AgentsViewReader.candidates selects session_kind only where `pragma table_info(sessions)` has it. _verify_agentsview catches sqlite3.Error: one reader_unavailable failure row (reason schema for 'no such ...', else error; exception type only), the run's partial rows/failures/join counts are discarded, nothing raised |
| minor: two turn_key derivations within one harness | test_non_cc_training_rows_carry_the_canonical_turn_key (omp, hermes, dsh) | build_table: non-CC rows use harness_id.turn_key (via hc.turn_key); claude-code keeps the 6fee859 key (byte-identical CC tables) |

Evidence (this directory):
- red.txt: at test-only commit 4a7df22, 30 failed / 68 passed; every new failure is the intended one (OperationalError
  'no such column: session_kind' on 74, uncaught sqlite errors, verified_success on failing OMP/DSH runs, exit_code 0,
  CC-derived turn_key on non-CC rows).
- green.txt: 98/98 at 0a73899.
- suite.txt (+ suite-base.raw.txt, suite-head.raw.txt, suite-isolated.raw.txt): no new deterministic failures.
- cc-verify-head.json: regression guard, byte-identical to ../cc-verify-6fee859.json.
- e2e.txt (+ e2e.raw.txt, e2e.report.json, e2e.stderr.txt): spec e2e unchanged + round-2 checks on host C.

Behaviour change to note for activation (A0 dry run): on harnesses whose AgentsView parser stores no result status
(OMP, OMO, Hermes, DSH), a shell run without an exit-code line no longer counts as a pass. Hermes terminal JSON carries
"exit_code" and OMP prints the notice only on failure, so OMP/OMO/DSH passing runs are now `unverified` (not labelled),
failing OMP runs are verified_failure. This supersedes the "completed -> 0" line in ../NOTES.md (which also said that
no status meant 0 in practice).

Rules: everything ran under flock -s; the e2e also under hostless; isolated homes under homes/C2-loop-consumers/
(r2-*, golden, e2e); the socket guard logged 0 violations; synthetic fixtures only; no live state, TencentDB or z0
service touched; the base worktree for the suite was created and removed.
