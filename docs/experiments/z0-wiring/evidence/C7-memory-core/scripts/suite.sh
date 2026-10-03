#!/usr/bin/env bash
# Full suite for one tree under the shared quiet-lane lock with the socket guard. Usage: suite.sh <tree> <home-tag>
set -uo pipefail
S=/mnt/zer0models/z0-wt/wiring/evidence/C7-memory-core/scripts
PY=/mnt/zer0models/z0-wt/venv-claude-code/bin/python
cd "$1" && flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock "$S/run.sh" "$1" "$2" --guard -- \
  "$PY" -m pytest -q -p no:cacheprovider -rfE --tb=no tests 2>&1
echo "# socket guard log: $(cat /mnt/zer0models/z0-wt/wiring/homes/C7-memory-core/$2/socket-violations.log 2>/dev/null | wc -l) violation(s)"
