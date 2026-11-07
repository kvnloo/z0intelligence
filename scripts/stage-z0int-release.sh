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
# The sealed tree cannot cache bytecode, so every hook/tick would recompile src (~0.7 s per hook at load).
# Ship it compiled; a source the runtime cannot compile is reported, never silently skipped.
if [[ -d "$TMP/src" ]]; then
  "$RUNTIME/.venv/bin/python" -m compileall -q -j 1 "$TMP/src" >/dev/null || echo 'compileall reported errors; those modules compile at import' >&2
fi
chmod -R a-w "$TMP"
mv -T "$TMP" "$TARGET"
printf '%s\n' "$TARGET"
