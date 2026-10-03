#!/usr/bin/env bash
# Full z0intelligence suite for one tree (pytest tests/ + node DSH tests + bun omp-extensions/), isolated, with
# the socket guard. usage: suite.sh <tree> <tag>   (run under flock -s quiet-lane.lock)
set -uo pipefail
tree=$1; tag=$2
E=/mnt/zer0models/z0-wt/wiring/evidence/C8-memory-wiring
R=$E/scripts/run.sh
cd "$tree"
out=$E/round2/suite-$tag.raw.txt
echo "tree=$tree head=$(git rev-parse HEAD) start=$(date -u +%FT%TZ)" > "$out"
nice -n 19 "$R" "$tree" "suite-$tag" --guard -- python -m pytest -q -p no:cacheprovider -rfE --tb=no tests >> "$out" 2>&1
echo "pytest_rc=$? end=$(date -u +%FT%TZ)" >> "$out"
node_files=$(ls tests/test-dsh-*.mjs 2>/dev/null | tr '\n' ' ')
nice -n 19 "$R" "$tree" "suite-$tag-node" -- node --unhandled-rejections=strict --test $node_files > "$E/round2/suite-$tag-node.raw.txt" 2>&1
echo "node_rc=$? files=$node_files" >> "$E/round2/suite-$tag-node.raw.txt"
nice -n 19 "$R" "$tree" "suite-$tag-bun" -- env SENPI_TYPES=/mnt/zer0models/home-offload/kvn/bun/install/global/node_modules/@code-yeongyu/senpi/dist/core/extensions/types.d.ts \
  bun test omp-extensions/ > "$E/round2/suite-$tag-bun.raw.txt" 2>&1
echo "bun_rc=$?" >> "$E/round2/suite-$tag-bun.raw.txt"
