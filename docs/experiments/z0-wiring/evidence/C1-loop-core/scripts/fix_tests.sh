#!/usr/bin/env bash
# Fix round (verifier REVISE): run the red/green test set. Usage: fix_tests.sh <home-tag> <header line>
set -uo pipefail
W=/mnt/zer0models/z0-wt/wiring/wt/C1-loop-core
S=/mnt/zer0models/z0-wt/wiring/evidence/C1-loop-core/scripts
PY=/mnt/zer0models/z0-wt/venv-claude-code/bin/python
TESTS=(
  tests/test_hook_adapter.py::test_claude_code_stop_measures_its_own_prompt_and_closes_a_lagging_turn_explicitly
  tests/test_hook_adapter.py::test_claude_code_stop_measurement_errors_are_failure_rows_not_silent
  tests/test_hook_adapter.py::test_subagent_turns_are_agent_cohort_and_join_on_turn_key
  tests/test_harness_capture.py::test_append_and_the_stale_check_read_no_record_history
  tests/test_harness_capture.py::test_spool_put_racing_close_is_written_or_counted_never_lost
  tests/test_loop_export.py::test_legacy_rows_without_capture_flags_are_never_user_rows
  tests/test_cc_export_regression.py
)
echo "# $2"
echo "# cmd: flock -s quiet-lane.lock run.sh <wt> $1 --guard -- python -m pytest -v -p no:cacheprovider -rfE --tb=short <tests> (cwd=<wt>)"
echo "# tests: ${TESTS[*]}"
cd "$W" && flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock "$S/run.sh" "$W" "$1" --guard -- \
  "$PY" -m pytest -v -p no:cacheprovider -rfE --tb=short "${TESTS[@]}" 2>&1
echo "# socket guard log: $(cat /mnt/zer0models/z0-wt/wiring/homes/C1-loop-core/$1/socket-violations.log 2>/dev/null | wc -l) violation(s)"
