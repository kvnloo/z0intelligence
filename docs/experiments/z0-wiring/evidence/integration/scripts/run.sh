#!/usr/bin/env bash
# Isolated runner for the integration phase: run.sh <tree> <home-tag> [--guard] -- cmd...
# env -i; HOME/Z0INT_HOME/TMPDIR under homes/integration/<tag>; <tree>/src first on PYTHONPATH; pyc cache in
# scratch; venv-integrate bin first on PATH. --guard adds the socket guard (refuse+log :11501 / non-loopback).
set -euo pipefail
tree=$1; tag=$2; shift 2
guard=""
if [ "${1:-}" = "--guard" ]; then guard=1; shift; fi
[ "${1:-}" = "--" ] && shift
S=/mnt/zer0models/z0-wt/wiring/scratch/integration
H=/mnt/zer0models/z0-wt/wiring/homes/integration/$tag
V=/mnt/zer0models/z0-wt/wiring/venv-integrate
mkdir -p "$H/home" "$H/z0" "$H/tmp"
pp="$tree/src"
if [ -n "$guard" ]; then pp="$S/guard:$pp"; fi
exec env -i PATH="$V/bin:/usr/local/bin:/usr/bin:/bin" LANG=C.UTF-8 TERM=dumb HOME="$H/home" Z0INT_HOME="$H/z0" \
  TMPDIR="$H/tmp" PYTHONPATH="$pp" PYTHONPYCACHEPREFIX="$S/pycache-$tag" VIRTUAL_ENV="$V" \
  Z0INT_SOCKET_GUARD_LOG="$H/socket-violations.log" ${EXTRA_ENV:-} "$@"
