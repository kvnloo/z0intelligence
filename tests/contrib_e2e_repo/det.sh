#!/usr/bin/env bash
# Deterministic cells (spec §4.3) in the clean container, as dev. Writes /out/det.json.
set -uo pipefail
cd ~ && git clone -q /mirror/z0.git z0 && cd ~/z0intelligence
PY=~/z0intelligence/.venv/bin/python
export Z0INT_HOME=~/.z0int-det
declare -A RES DET
run() { # cell, label, cmd...
  local cell="$1" label="$2"; shift 2
  local log="/out/det-$cell-$label.log"
  "$@" > "$log" 2>&1; local rc=$?
  grep -qE "[0-9]+ skipped|skipped=" "$log" && rc=7   # a skipped test is not a pass
  DET[$cell]+="$label:rc=$rc;"
  [[ $rc -eq 0 ]] || RES[$cell]=fail
  [[ -n "${RES[$cell]:-}" ]] || RES[$cell]=pass
  tail -1 "$log" | cut -c1-200
}
T="$PY -m pytest -q -p no:cacheprovider"
run smoke cli bash -c "$PY -m z0int contribute smoke --json > /out/smoke.json && $PY -c 'import json;d=json.load(open(\"/out/smoke.json\"));p=d[\"data\"][\"parts\"];assert all(x[\"exit\"]==0 for x in p);assert [x for x in p if x[\"name\"]==\"eval_fixture\"][0][\"golden_match\"] is True'"
run E1 align $T tests/test_contribute_align.py -k "e1 or reasons_are_closed or fallback or engine_has_no_llm"
run P1 check env PYTHONPATH=src $PY -m z0int.contribute.priorities --check
run P1 tests $T tests/test_contribute_priorities.py tests/test_contribute_schemas.py
run P2 tests $T tests/test_contribute_align.py tests/test_contribute_priorities.py -k "allowed_newcomer or acceptance_profile or oracle_profiles"
run C1 race $T tests/test_contribute_claims.py
run W1 loop $T tests/test_contribute_cli.py
# z0 G-cells with the venv python (PyYAML); a skipped test is not a pass.
for g in 1 2 3 4 5; do run G$g z0 bash -c "cd ~/z0 && $PY -m unittest -v tests.onboard.test_profile -k G$g > /tmp/g.log 2>&1; rc=\$?; cat /tmp/g.log; grep -q 'skipped' /tmp/g.log && exit 7; exit \$rc"; done
run G1 z0int $T tests/test_contribute_privacy.py -k g1_g3
run G3 z0int $T tests/test_contribute_privacy.py -k g1_g3
run G4 z0int $T tests/test_contribute_privacy.py -k g4_g5
run G5 z0int $T tests/test_contribute_privacy.py -k g4_g5
{ echo "{"; first=1; for c in "${!RES[@]}"; do [[ $first -eq 1 ]] || echo ","; first=0; printf '"%s": {"status": "%s", "detail": "%s"}' "$c" "${RES[$c]}" "${DET[$c]}"; done; echo "}"; } > /out/det.json
cat /out/det.json
