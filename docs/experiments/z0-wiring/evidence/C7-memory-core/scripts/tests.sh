#!/usr/bin/env bash
# Run the C7 red/green test set. Usage: tests.sh <home-tag> <header line> [tree]
set -uo pipefail
W=${3:-/mnt/zer0models/z0-wt/wiring/wt/C7-memory-core}
S=/mnt/zer0models/z0-wt/wiring/evidence/C7-memory-core/scripts
PY=/mnt/zer0models/z0-wt/venv-claude-code/bin/python
TESTS=(
  tests/test_memory_event_identity.py
  tests/test_memory_surface.py
  tests/test_memory_scrub.py
  tests/test_memory_receipt_binding.py
  tests/test_context_resolve_memory.py
  tests/test_memory_mcp_profile.py
  tests/test_memory_doctor.py
  tests/test_memory_bench.py
)
echo "# $2"
echo "# cmd: flock -s quiet-lane.lock run.sh <wt> $1 --guard -- python -m pytest -v -p no:cacheprovider -rfE --tb=line <tests> (cwd=<wt>)"
echo "# tree: $W @ $(git -C "$W" rev-parse --short HEAD) (+ working tree changes: $(git -C "$W" status --porcelain | wc -l) paths)"
cd "$W" && flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock "$S/run.sh" "$W" "$1" --guard -- \
  "$PY" -m pytest -v -p no:cacheprovider -rfE --tb=line "${TESTS[@]}" 2>&1
echo "# socket guard log: $(cat /mnt/zer0models/z0-wt/wiring/homes/C7-memory-core/$1/socket-violations.log 2>/dev/null | wc -l) violation(s)"
