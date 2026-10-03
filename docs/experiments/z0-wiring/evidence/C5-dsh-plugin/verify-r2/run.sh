#!/usr/bin/env bash
# run.sh <tree> <tag> -- cmd...   isolated env, guards on
set -euo pipefail
tree=$1; tag=$2; shift 2; [ "${1:-}" = "--" ] && shift
S=/mnt/zer0models/z0-wt/wiring/scratch/C5-dsh-plugin-verify
H=/mnt/zer0models/z0-wt/wiring/homes/C5-dsh-plugin-verify/r2/$tag
mkdir -p "$H/home" "$H/z0" "$H/tmp"
cd "$tree"
exec env -i PATH=/mnt/zer0models/z0-wt/venv-claude-code/bin:/mnt/zer0models/home-offload/kvn/offload-2026-09-21/fnm/node-versions/v22.23.2/installation/bin:/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 TERM=dumb \
  HOME="$H/home" Z0INT_HOME="$H/z0" TMPDIR="$H/tmp" PYTHONPATH="$S/pyguard:$tree/src" PYTHONDONTWRITEBYTECODE=1 \
  Z0INT_PYTHON=/mnt/zer0models/z0-wt/venv-claude-code/bin/python NODE_OPTIONS="--import=$S/node-guard.mjs" \
  VERIFY_GUARD_LOG="$H/guard-violations.log" "$@"
