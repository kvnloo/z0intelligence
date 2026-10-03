#!/usr/bin/env bash
# C2 red/green runner: red_green.sh <tag> <out-file> <header>  (runs the C2 red-test files under the shared lock)
set -euo pipefail
WT=${WT:-/mnt/zer0models/z0-wt/wiring/wt/C2-loop-consumers}
E=/mnt/zer0models/z0-wt/wiring/evidence/C2-loop-consumers
PY=/mnt/zer0models/z0-wt/venv-claude-code/bin/python
TESTS="tests/test_turn_readers.py tests/test_verify_harness.py tests/test_loop_tables.py tests/test_legacy_import.py"
tag=$1; out=$2; shift 2
{
  echo "# C2-loop-consumers: $* ($(date -u +%Y-%m-%dT%H:%M:%SZ)) HEAD $(git -C $WT rev-parse --short HEAD) + worktree changes"
  echo "# cmd: flock -s quiet-lane.lock run.sh <wt> $tag --guard -- python -m pytest -v -p no:cacheprovider -rfE --tb=line $TESTS (cwd=<wt>)"
  cd $WT && flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock $E/scripts/run.sh $WT $tag --guard -- \
    $PY -m pytest -v -p no:cacheprovider -rfE --tb=line $TESTS 2>&1 || true
  echo "# socket guard violations: $(cat /mnt/zer0models/z0-wt/wiring/homes/C2-loop-consumers/$tag/socket-violations.log 2>/dev/null | wc -l)"
} > $out
