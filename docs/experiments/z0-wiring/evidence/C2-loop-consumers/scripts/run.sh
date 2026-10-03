#!/usr/bin/env bash
# Isolated runner for C2-loop-consumers: run.sh <tree> <home-tag> [--guard] -- cmd...
# Clean env (env -i): isolated HOME/Z0INT_HOME/TMPDIR under homes/C2-loop-consumers/<tag>, worktree src first on
# PYTHONPATH, pyc cache redirected to scratch (nothing is written into the shared venv). --guard adds the
# socket guard (sitecustomize): any connect to port 11501 or a non-loopback address is refused and logged.
set -euo pipefail
tree=$1; tag=$2; shift 2
guard=""
if [ "${1:-}" = "--guard" ]; then guard=1; shift; fi
[ "${1:-}" = "--" ] && shift
S=/mnt/zer0models/z0-wt/wiring/scratch/C2-loop-consumers
H=/mnt/zer0models/z0-wt/wiring/homes/C2-loop-consumers/$tag
mkdir -p "$H/home" "$H/z0" "$H/tmp"
pp="$tree/src"
if [ -n "$guard" ]; then pp="$S/guard:$pp"; fi
exec env -i PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 TERM=dumb HOME="$H/home" Z0INT_HOME="$H/z0" \
  TMPDIR="$H/tmp" PYTHONPATH="$pp" PYTHONPYCACHEPREFIX="$S/pycache-$tag" \
  Z0INT_SOCKET_GUARD_LOG="$H/socket-violations.log" "$@"
