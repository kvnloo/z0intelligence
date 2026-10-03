#!/usr/bin/env bash
# Isolated runner for C8: run.sh <tree> <home-tag> [--guard] -- cmd...
# env -i; HOME/Z0INT_HOME/TMPDIR/AGENTSVIEW_DATA_DIR under homes/C8-memory-wiring/<tag>; <tree>/src on PYTHONPATH;
# venv-integrate first on PATH (Z0INT_PYTHON too); --guard adds the socket guard sitecustomize (refuses :11501 and
# non-loopback connects, logs them).
set -euo pipefail
tree=$1; tag=$2; shift 2
guard=""
if [ "${1:-}" = "--guard" ]; then guard=1; shift; fi
[ "${1:-}" = "--" ] && shift
W=/mnt/zer0models/z0-wt/wiring
H=$W/homes/C8-memory-wiring/$tag
V=$W/venv-integrate
rm -rf "$H"; mkdir -p "$H/home" "$H/z0" "$H/tmp" "$H/av"
pp="$tree/src"
if [ -n "$guard" ]; then pp="$W/scratch/integration/guard:$pp"; fi
exec env -i PATH="$V/bin:~/.bun/bin:/usr/local/bin:/usr/bin:/bin" LANG=C.UTF-8 TERM=dumb HOME="$H/home" \
  Z0INT_HOME="$H/z0" TMPDIR="$H/tmp" AGENTSVIEW_DATA_DIR="$H/av" PYTHONPATH="$pp" Z0INT_PYTHON="$V/bin/python" \
  PYTHONPYCACHEPREFIX="$W/scratch/c8-pycache-$tag" VIRTUAL_ENV="$V" Z0INT_SOCKET_GUARD_LOG="$H/socket-violations.log" \
  ${EXTRA_ENV:-} "$@"
