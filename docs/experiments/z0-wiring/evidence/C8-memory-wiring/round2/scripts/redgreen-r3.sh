#!/usr/bin/env bash
# Round-3 red/green runner: the round-3 tests only. usage: redgreen-r3.sh <tree> <tag>  (under flock -s)
set -uo pipefail
tree=$1; tag=$2
E=/mnt/zer0models/z0-wt/wiring/evidence/C8-memory-wiring; R=$E/scripts/run.sh; O=$E/round2
cd "$tree"
echo "tree=$tree head=$(git rev-parse HEAD) dirty=$(git status --porcelain | wc -l)" > "$O/$tag-pytest.raw.txt"
nice -n 19 "$R" "$tree" "r3-$tag" --guard -- python -m pytest -q -p no:cacheprovider -rfE --tb=line \
  tests/test_memory_capture_join.py >> "$O/$tag-pytest.raw.txt" 2>&1; echo "pytest_rc=$?" >> "$O/$tag-pytest.raw.txt"
nice -n 19 "$R" "$tree" "r3-$tag-node" -- node --unhandled-rejections=strict --test \
  --test-name-pattern='round 3|one root turn' tests/test-dsh-memory.mjs > "$O/$tag-node.raw.txt" 2>&1; echo "node_rc=$?" >> "$O/$tag-node.raw.txt"
nice -n 19 "$R" "$tree" "r3-$tag-bun" -- bun test omp-extensions/z0-memory -t 'with the capture bridge loaded' > "$O/$tag-bun.raw.txt" 2>&1; echo "bun_rc=$?" >> "$O/$tag-bun.raw.txt"
