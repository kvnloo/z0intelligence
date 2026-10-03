#!/usr/bin/env bash
# Integration e2e master: one fully isolated environment under homes/integration/e2e (fresh), every harness leg into
# ONE shared Z0INT_HOME (<root>/z0home), then the learning stage, the memory probe and the verdict.
# Run once, under the shared quiet-lane lock:  flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock e2e_all.sh
set -u
WT=/mnt/zer0models/z0-wt/wiring/wt/integrate
R=/mnt/zer0models/z0-wt/wiring/homes/integration/e2e
S=/mnt/zer0models/z0-wt/wiring/evidence/integration/scripts
PY=/mnt/zer0models/z0-wt/wiring/venv-integrate/bin/python
rm -rf "$R"; mkdir -p "$R"
echo "integrate HEAD $(git -C $WT rev-parse HEAD) clean=$(git -C $WT status --porcelain | wc -l) start $(date -u +%FT%TZ)"
echo "### claude-code";  "$S/e2e_claude.sh" "$WT" "$R" "$PY" 11540 2
echo "### codex";        "$S/e2e_codex.sh" "$WT" "$R" "$PY" 11541 2
echo "### grok";         "$PY" "$S/e2e_grok_replay.py" "$WT" "$R" "$PY" 3
echo "### omp";          "$S/e2e_omp.sh" omp "$WT" "$R" "$PY" 11542 11549 2
echo "### omo";          "$S/e2e_omp.sh" omo "$WT" "$R" "$PY" 11544 11549 2
echo "### hermes";       "$S/e2e_hermes.sh" "$WT" "$R" 3 11543
echo "### dsh";          "$S/e2e_dsh.sh" "$WT" "$R" 11545
sleep 5   # last detached builds
echo "### learn";        "$S/e2e_learn.sh" "$R"
echo "### memory probe"
env -i PATH=/usr/bin:/bin HOME="$R/learn/home" PYTHONPATH=/mnt/zer0models/z0-wt/wiring/scratch/integration/guard \
  PYTHONPYCACHEPREFIX="$R/learn/pycache" Z0INT_HOME="$R/z0home" Z0INT_SOCKET_GUARD_LOG="$R/memory-socket-violations.log" \
  "$PY" "$S/e2e_memory_probe.py" "$WT" "$R" > "$R/memory-probe.json"; echo "probe exit=$? $(grep -o '"memory_e2e": "[A-Z]*"' "$R/memory-probe.json")"
echo "### check"
"$PY" "$S/e2e_check.py" "$R" "$WT" > "$R/e2e-check.json"; echo "check exit=$?"
grep -m1 '"verdict"' "$R/e2e-check.json"; "$PY" -c "import json;d=json.load(open('$R/e2e-check.json'));print('failed:',d['failed'])"
echo "end $(date -u +%FT%TZ)"
