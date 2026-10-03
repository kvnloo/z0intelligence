#!/usr/bin/env bash
# Isolated runner for C4a-hermes-capture: run.sh <tree> <home-tag> [--guard] -- cmd...
# env -i with HOME/Z0INT_HOME/HERMES_HOME/TMPDIR under homes/C4a-hermes-capture/<tag>, the worktree src first on
# PYTHONPATH and the pyc cache redirected to scratch. --guard adds the socket guard (sitecustomize): any connect to
# port 11501 or a non-loopback address is refused and logged. Z0INT_TEST_HERMES_PYTHON points the opt-in host tests
# at the isolated Hermes venv built from the git-archive export of the fork.
set -euo pipefail
tree=$1; tag=$2; shift 2
guard=""
if [ "${1:-}" = "--guard" ]; then guard=1; shift; fi
[ "${1:-}" = "--" ] && shift
S=/mnt/zer0models/z0-wt/wiring/scratch/C4a
B=/mnt/zer0models/z0-wt/wiring/homes/C4a-hermes-capture
H=$B/$tag
mkdir -p "$H/home" "$H/z0" "$H/hermes-home" "$H/tmp"
pp="$tree/src"
if [ -n "$guard" ]; then pp="$S/guard:$pp"; fi
exec env -i PATH=/usr/local/bin:/usr/bin:/bin LANG=C.UTF-8 TERM=dumb HOME="$H/home" Z0INT_HOME="$H/z0" \
  HERMES_HOME="$H/hermes-home" TMPDIR="$H/tmp" PYTHONPATH="$pp" PYTHONPYCACHEPREFIX="$S/pycache-$tag" \
  Z0INT_SOCKET_GUARD_LOG="$H/socket-violations.log" \
  Z0INT_TEST_HERMES_PYTHON="${Z0INT_TEST_HERMES_PYTHON:-}" "$@"
