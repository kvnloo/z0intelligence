#!/usr/bin/env bash
# Repoint the common release reference and restart only the declared consumers.
set -euo pipefail
BASE=${Z0INT_RELEASE_BASE:-$HOME/.local/lib/z0int-shadow}
SHA=${1:?full staged commit required}
shift
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || { echo 'Expected full commit SHA' >&2; exit 1; }
[[ -f "$BASE/$SHA/DEPLOYED_SHA" && $(cat "$BASE/$SHA/DEPLOYED_SHA") == "$SHA" ]] || { echo 'Release not staged' >&2; exit 1; }
if [[ $# == 0 ]]; then
  set -- z0intelligence.service z0int-loop-tick.service
fi
PREVIOUS=$(readlink "$BASE/current" || true)
LINK="$BASE/.current.$$"
ln -s "$BASE/$SHA" "$LINK"
mv -Tf "$LINK" "$BASE/current"
printf 'previous=%s\ncurrent=%s\n' "$PREVIOUS" "$BASE/$SHA"
systemctl --user restart "$@"
# Existing OMP modules remain cached; reload at an explicit session boundary.
