#!/usr/bin/env bash
# Fix round: repeat the real `claude -p` e2e leg N times (runs 1..FG with a foreground subagent, the rest with a
# background subagent -> <task-notification> prompt), each in a fresh isolated home; check every run.
set -uo pipefail
WT=/mnt/zer0models/z0-wt/wiring/wt/C1-loop-core
PY=/mnt/zer0models/z0-wt/venv-claude-code/bin/python
S=/mnt/zer0models/z0-wt/wiring/scratch/C1-loop-core
HB=/mnt/zer0models/z0-wt/wiring/homes/C1-loop-core
N=${1:-8}; FG=${2:-3}; pass=0
for i in $(seq 1 "$N"); do
  bg=$([ "$i" -le "$FG" ] && echo 0 || echo 1)
  H=$HB/e2e-claude-fix-r$i
  mkdir -p "$HB/e2e-claude-outer"
  env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$HB/e2e-claude-outer" \
    flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock "$S/e2e_claude.sh" "$WT" "$H" "$PY" 11541 "$bg" < /dev/null > "$H.log" 2>&1
  if python3 "$S/e2e_claude_check.py" "$H" > "$H.check.json"; then r=PASS; pass=$((pass+1)); else r=FAIL; fi
  echo "run $i background=$bg: $r $(python3 -c "import json;d=json.load(open('$H.check.json'));print(json.dumps({k:d[k] for k in ('claude_result','prompts','joined_on_turn_key','outcome_cohorts','late_rows','failures','drops')}))")"
done
echo "PASSED $pass/$N"
