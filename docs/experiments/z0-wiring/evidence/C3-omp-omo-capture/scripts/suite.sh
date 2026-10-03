#!/usr/bin/env bash
# Full suite on one tree: suite.sh <tree> <home-tag>. pytest (socket guard) + bun test over omp-extensions/.
set -uo pipefail
T=$1; tag=$2
S=/mnt/zer0models/z0-wt/wiring/evidence/C3-omp-omo-capture/scripts
PY=/mnt/zer0models/z0-wt/venv-claude-code/bin/python
LOCK=/mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock
echo "# tree: $T @ $(git -C $T rev-parse HEAD) (status: $(git -C $T status --porcelain | wc -l) changed paths)"
echo "# cmd: (cd <tree> && flock -s quiet-lane.lock run.sh <tree> $tag --guard -- python -m pytest -q -p no:cacheprovider -rfE --tb=no tests)"
cd "$T" && flock -s "$LOCK" "$S/run.sh" "$T" "$tag" --guard -- "$PY" -m pytest -q -p no:cacheprovider -rfE --tb=no tests 2>&1 | tail -40
echo "# socket guard log: $(cat /mnt/zer0models/z0-wt/wiring/homes/C3-omp-omo-capture/$tag/socket-violations.log 2>/dev/null | wc -l) violation(s)"
echo "# cmd: (cd <tree> && flock -s quiet-lane.lock run.sh <tree> $tag-bun -- env SENPI_TYPES=<installed senpi types.d.ts> bun test omp-extensions/)"
cd "$T" && flock -s "$LOCK" "$S/run.sh" "$T" "$tag-bun" -- env SENPI_TYPES=~/.bun/install/global/node_modules/@code-yeongyu/senpi/dist/core/extensions/types.d.ts bun test omp-extensions/ 2>&1 | grep -E "^\(fail\)|^ *[0-9]+ (pass|fail|skip)|^\(skip\)|^Ran " 
