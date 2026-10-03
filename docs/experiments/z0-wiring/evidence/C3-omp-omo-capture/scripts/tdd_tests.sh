#!/usr/bin/env bash
# C3 red/green set. Usage: tdd_tests.sh <home-tag> <header line>
# Python: pytest under the isolated runner with the socket guard. Bun: bun test per file, same isolated env.
set -uo pipefail
W=${W:-/mnt/zer0models/z0-wt/wiring/wt/C3-omp-omo-capture}
S=/mnt/zer0models/z0-wt/wiring/evidence/C3-omp-omo-capture/scripts
PY=/mnt/zer0models/z0-wt/venv-claude-code/bin/python
LOCK=/mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock
PYTESTS=(tests/test_omp_capture.py
  tests/test_cognition_shadow.py::test_no_models_served_still_returns_the_compiled_legal_set)
BUNTESTS=(omp-extensions/z0int-bridge/index.test.ts omp-extensions/z0int-bridge/omo.test.ts
  omp-extensions/local-cognition/index.test.ts)
echo "# $2"
echo "# python: flock -s quiet-lane.lock run.sh <wt> $1-py --guard -- python -m pytest -v -p no:cacheprovider -rfE --tb=short ${PYTESTS[*]} (cwd=<wt>)"
cd "$W" && flock -s "$LOCK" "$S/run.sh" "$W" "$1-py" --guard -- "$PY" -m pytest -v -p no:cacheprovider -rfE --tb=short "${PYTESTS[@]}" 2>&1
echo "# socket guard log: $(cat /mnt/zer0models/z0-wt/wiring/homes/C3-omp-omo-capture/$1-py/socket-violations.log 2>/dev/null | wc -l) violation(s)"
for t in "${BUNTESTS[@]}"; do
  echo "# bun: flock -s quiet-lane.lock run.sh <wt> $1-bun -- env SENPI_TYPES=<installed senpi types.d.ts> bun test $t (cwd=<wt>)"
  (cd "$W" && flock -s "$LOCK" "$S/run.sh" "$W" "$1-bun" -- env SENPI_TYPES=~/.bun/install/global/node_modules/@code-yeongyu/senpi/dist/core/extensions/types.d.ts bun test "$t" 2>&1)
  echo "# exit: $?"
done
