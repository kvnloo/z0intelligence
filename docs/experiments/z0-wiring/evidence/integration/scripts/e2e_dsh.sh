#!/usr/bin/env bash
# Integration e2e, DSH leg (mock DSH host: the dsh binary/wrapper is never run): the C5 driver loads the integrate
# tree's dsh-z0intelligence/index.mjs with a fake cordis ctx, REAL detached spawns of
# `Z0INT_PYTHON -m z0int.hook_adapter --harness dsh` (venv-integrate), and a fake z0 shadow service on a private
# port. Arms on (capture, SHARED integration Z0INT_HOME) / off (capture:false, z0home-off); node + python socket
# guards on. usage: e2e_dsh.sh <worktree> <e2e-root> <port>
set -u
WT=$1; R=$2; PORT=$3
S=/mnt/zer0models/z0-wt/wiring/evidence/integration/scripts
H=$R/dsh; rm -rf "$H"; mkdir -p "$H"/{home,tmp,pycache} "$R/z0home" "$R/z0home-off"
for m in on off; do
  if [ $m = on ]; then Z="$R/z0home"; else Z="$R/z0home-off"; fi
  env -i PATH=/mnt/zer0models/z0-wt/wiring/venv-integrate/bin:/mnt/zer0models/home-offload/kvn/offload-2026-09-21/fnm/node-versions/v22.23.2/installation/bin:/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 TERM=dumb \
    HOME="$H/home" TMPDIR="$H/tmp" Z0INT_HOME="$Z" PYTHONPATH=/mnt/zer0models/z0-wt/wiring/scratch/integration/guard \
    PYTHONPYCACHEPREFIX="$H/pycache" Z0INT_PYTHON=/mnt/zer0models/z0-wt/wiring/venv-integrate/bin/python \
    NODE_OPTIONS="--import=$S/node-guard.mjs" Z0INT_SOCKET_GUARD_LOG="$H/socket-violations.log" \
    C5_PLUGIN="$WT/harness-adapters/dsh-z0intelligence/index.mjs" C5_PORT=$PORT \
    node --unhandled-rejections=strict "$S/e2e_dsh.mjs" $m > "$H/e2e-$m.json" 2> "$H/e2e-$m.err"
  echo "dsh $m exit=$?"
done
sleep 3  # detached hook_adapter children
