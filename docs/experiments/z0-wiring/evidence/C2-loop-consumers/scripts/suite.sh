#!/usr/bin/env bash
# C2 full suite: suite.sh <tree> <tag> <raw-out>  (isolated runner, socket guard, shared quiet-lane lock)
set -uo pipefail
E=/mnt/zer0models/z0-wt/wiring/evidence/C2-loop-consumers
rm -rf /mnt/zer0models/z0-wt/wiring/homes/C2-loop-consumers/$2
out=$(realpath -m "$3")
cd $1 && flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock $E/scripts/run.sh $1 $2 --guard -- \
  /mnt/zer0models/z0-wt/venv-claude-code/bin/python -m pytest -q -p no:cacheprovider -rfE --tb=no tests > "$out" 2>&1
echo "exit=$?" >> "$out"
