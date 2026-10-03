#!/usr/bin/env bash
# Isolated runner for C5-dsh-plugin: run.sh <tree> <home-tag> [--guard] -- cmd...
# env -i; HOME/Z0INT_HOME/TMPDIR under homes/C5-dsh-plugin/<tag>; worktree src first on PYTHONPATH; pyc cache in
# scratch. --guard adds the Python socket guard (sitecustomize) and the Node guard (NODE_OPTIONS --import):
# any connect to port 11501 or a non-loopback address is refused and logged to <home>/socket-violations.log.
set -euo pipefail
tree=$1; tag=$2; shift 2
guard=""
if [ "${1:-}" = "--guard" ]; then guard=1; shift; fi
[ "${1:-}" = "--" ] && shift
S=/mnt/zer0models/z0-wt/wiring/scratch/C5-dsh-plugin
E=/mnt/zer0models/z0-wt/wiring/evidence/C5-dsh-plugin/scripts
H=/mnt/zer0models/z0-wt/wiring/homes/C5-dsh-plugin/$tag
mkdir -p "$H/home" "$H/z0" "$H/tmp"
pp="$tree/src"; nodeopts=""
if [ -n "$guard" ]; then pp="$S/guard:$pp"; nodeopts="--import=$E/node-guard.mjs"; fi
PY=/mnt/zer0models/z0-wt/venv-claude-code/bin/python
exec env -i PATH=/mnt/zer0models/z0-wt/venv-claude-code/bin:/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 TERM=dumb \
  HOME="$H/home" Z0INT_HOME="$H/z0" TMPDIR="$H/tmp" PYTHONPATH="$pp" PYTHONPYCACHEPREFIX="$S/pycache-$tag" \
  Z0INT_PYTHON="$PY" NODE_OPTIONS="$nodeopts" Z0INT_SOCKET_GUARD_LOG="$H/socket-violations.log" "$@"
