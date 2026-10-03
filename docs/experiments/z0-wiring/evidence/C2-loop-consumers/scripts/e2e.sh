#!/usr/bin/env bash
# C2 isolated e2e: schema dump (read-only, schema only) -> scratch DBs; everything else synthetic, under homes/.
set -euo pipefail
WT=/mnt/zer0models/z0-wt/wiring/wt/C2-loop-consumers
E=/mnt/zer0models/z0-wt/wiring/evidence/C2-loop-consumers
H=/mnt/zer0models/z0-wt/wiring/homes/C2-loop-consumers/e2e
PY=/mnt/zer0models/z0-wt/venv-claude-code/bin/python
rm -rf "$H"; mkdir -p "$H"
echo "# 1. schema only, read-only: sqlite3 \"file:/mnt/zer0models/sft-svlm/data/agentsview/sessions.db?mode=ro\" .schema"
sqlite3 "file:/mnt/zer0models/sft-svlm/data/agentsview/sessions.db?mode=ro" .schema > "$H/sessions.schema.sql"
echo "#    $(wc -l < "$H/sessions.schema.sql") schema lines, $(grep -c 'CREATE TABLE' "$H/sessions.schema.sql") CREATE TABLE statements"
echo "# 2-5. cd $WT && run.sh $WT e2e --guard -- python e2e_c2.py $WT $H python $H/sessions.schema.sql"
cd "$WT" && "$E/scripts/run.sh" "$WT" e2e --guard -- $PY "$E/scripts/e2e_c2.py" "$WT" "$H" "$PY" "$H/sessions.schema.sql"
echo "# socket guard violations: $(cat "$H/socket-violations.log" 2>/dev/null | wc -l)"
