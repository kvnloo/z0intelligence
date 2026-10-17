#!/usr/bin/env bash
# Run a harness from inside one worktree so its session tokens attribute to that branch.
#
#   scripts/unit-session.sh <worktree> hermes|codex|claude [harness args...]
#
# Hermes and Claude record the git branch of the directory they start in; Codex records
# its cwd, which scripts/scoreboard.py resolves to a branch. Hermes also gets a bounded
# default budget so one session cannot run to a thousand API calls.
set -euo pipefail
[ $# -ge 2 ] || { sed -n '2,9p' "$0" >&2; exit 2; }
wt=$(cd "$1" && pwd) harness=$2; shift 2
branch=$(git -C "$wt" rev-parse --abbrev-ref HEAD)
case $branch in
  HEAD|main|master|dev|preview|nightly) echo "unit-session: $wt is on '$branch'; check out the unit branch first" >&2; exit 2;;
esac
cd "$wt"
case $harness in
  hermes) exec hermes chat --in "$wt" --max-turns "${UNIT_MAX_TURNS:-150}" --run-budget "${UNIT_RUN_BUDGET:-5400}" "$@";;
  codex)  exec codex -C "$wt" "$@";;
  claude) exec claude "$@";;
  *) echo "unit-session: unknown harness '$harness'" >&2; exit 2;;
esac
