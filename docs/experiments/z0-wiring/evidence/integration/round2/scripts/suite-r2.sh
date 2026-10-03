#!/usr/bin/env bash
# Full z0intelligence suite (pytest tests/ + DSH node contract tests + OMP bun tests) for one tree, isolated.
# usage: suite.sh <tree> <tag>   (run under flock -s quiet-lane.lock)
set -uo pipefail
tree=$1; tag=$2
S=/mnt/zer0models/z0-wt/wiring/evidence/integration/scripts
E=/mnt/zer0models/z0-wt/wiring/evidence/integration/round2
cd "$tree"
echo "tree=$tree head=$(git rev-parse HEAD) start=$(date -u +%FT%TZ)" > "$E/suite-$tag.raw.txt"
nice -n 19 "$E/scripts/run-r2.sh" "$tree" "suite-$tag" --guard -- python -m pytest -q -p no:cacheprovider -rfE --tb=no tests >> "$E/suite-$tag.raw.txt" 2>&1
echo "pytest_rc=$? end=$(date -u +%FT%TZ)" >> "$E/suite-$tag.raw.txt"
if ls tests/*.mjs >/dev/null 2>&1; then
  H=/mnt/zer0models/z0-wt/wiring/homes/integration-r2/suite-$tag-node; mkdir -p $H/home $H/z0 $H/tmp
  env -i PATH="$(dirname "$(command -v node)"):/usr/bin:/bin" HOME=$H/home Z0INT_HOME=$H/z0 TMPDIR=$H/tmp \
    Z0INT_PYTHON=/mnt/zer0models/z0-wt/wiring/venv-integrate/bin/python PYTHONPATH="$tree/src" \
    nice -n 19 node --unhandled-rejections=strict --test tests/*.mjs > "$E/suite-$tag-node.raw.txt" 2>&1
  echo "node_rc=$?" >> "$E/suite-$tag-node.raw.txt"
fi
H=/mnt/zer0models/z0-wt/wiring/homes/integration-r2/suite-$tag-bun; mkdir -p $H/home $H/z0 $H/tmp
( env -i PATH="~/.bun/bin:/mnt/zer0models/z0-wt/wiring/venv-integrate/bin:/usr/bin:/bin" HOME=$H/home \
    Z0INT_HOME=$H/z0 TMPDIR=$H/tmp Z0INT_PYTHON=/mnt/zer0models/z0-wt/wiring/venv-integrate/bin/python \
    PYTHONPATH="$tree/src" PYTHONPYCACHEPREFIX=/mnt/zer0models/z0-wt/wiring/scratch/integration/pycache-bun-$tag \
    nice -n 19 bun test omp-extensions/ > "$E/suite-$tag-bun.raw.txt" 2>&1; echo "bun_rc=$?" >> "$E/suite-$tag-bun.raw.txt" )
