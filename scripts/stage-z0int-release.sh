#!/usr/bin/env bash
# Materialize one committed source tree; never change the live release pointer.
set -euo pipefail
SOURCE=${Z0INT_RELEASE_SOURCE:-$(git rev-parse --show-toplevel)}
BASE=${Z0INT_RELEASE_BASE:-$HOME/.local/lib/z0int-shadow}
SHA=$(git -C "$SOURCE" rev-parse --verify "${1:?full commit required}^{commit}")
RUNTIME=${2:?path to an immutable, self-contained Python runtime required}
[[ -x "$RUNTIME/.venv/bin/python" && -f "$RUNTIME/RUNTIME.json" ]] || { echo 'Missing stamped runtime' >&2; exit 1; }
mkdir -p "$BASE"
TARGET="$BASE/$SHA"
if [[ -e "$TARGET" ]]; then
  [[ $(cat "$TARGET/DEPLOYED_SHA") == "$SHA" && $(readlink -f "$TARGET/.venv") == "$RUNTIME/.venv" ]] || exit 1
  printf '%s\n' "$TARGET"
  exit 0
fi
TMP=$(mktemp -d "$BASE/.stage.XXXXXXXX")
# Preserve incomplete evidence on failure; never delete an existing installation.
trap 'printf "Incomplete staging retained at %s\n" "$TMP" >&2' ERR
git -C "$SOURCE" archive "$SHA" | tar -x -C "$TMP"
printf '%s\n' "$SHA" > "$TMP/DEPLOYED_SHA"
ln -s "$RUNTIME/.venv" "$TMP/.venv"
printf '%s\n' "$RUNTIME" > "$TMP/RUNTIME_ROOT"
chmod -R a-w "$TMP"
mv -T "$TMP" "$TARGET"
printf '%s\n' "$TARGET"
