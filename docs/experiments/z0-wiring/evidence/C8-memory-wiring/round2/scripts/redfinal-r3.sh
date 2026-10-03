#!/usr/bin/env bash
# Round-3 red on the committed red tests (7673829, no implementation): the new file plus the changed Grok contract.
set -uo pipefail
tree=$1
E=/mnt/zer0models/z0-wt/wiring/evidence/C8-memory-wiring; R=$E/scripts/run.sh; O=$E/round2
"$E/round2/scripts/redgreen-r3.sh" "$tree" red
cd "$tree"
echo "tree=$tree head=$(git rev-parse HEAD)" > "$O/red-grok-contract.raw.txt"
nice -n 19 "$R" "$tree" r3-red-grok --guard -- python -m pytest -q -p no:cacheprovider -rfE --tb=line \
  "tests/test_memory_seam.py::test_grok_has_no_push_seam" >> "$O/red-grok-contract.raw.txt" 2>&1; echo "pytest_rc=$?" >> "$O/red-grok-contract.raw.txt"
