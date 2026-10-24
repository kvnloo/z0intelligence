#!/usr/bin/env bash
# Install committed bytes, never a symlink into a task checkout. Does not run OMP.
set -euo pipefail
umask 077

if [[ $# != 1 || ! $1 =~ ^[0-9a-f]{40}$ ]]; then
  printf 'Usage: bash %s <full 40-character commit SHA>\n' "$0" >&2
  exit 2
fi
revision=$1
script_dir=$(dirname -- "$(realpath -- "$0")")
repo=$(git -C "$script_dir" rev-parse --show-toplevel)
resolved=$(git -C "$repo" rev-parse --verify "${revision}^{commit}")
[[ $resolved == "$revision" ]] || { printf 'Exact commit SHA required.\n' >&2; exit 2; }

store="$HOME/.local/lib/z0-relay-guard"
link="$HOME/.omp/agent/extensions/z0-relay-guard"
mkdir -p -- "$store" "$(dirname -- "$link")"
# One deployment owner serializes snapshots and link replacements.
exec 9>"$store/.install.lock"
flock 9
stage=$(mktemp -d "$store/.stage.XXXXXXXX")
link_stage=
cleanup() {
  [[ -z $link_stage ]] || rm -f -- "$link_stage"
  if [[ -n $stage ]]; then chmod -R u+w -- "$stage"; rm -rf -- "$stage"; fi
}
trap cleanup EXIT

git -C "$repo" archive "$revision:omp-extensions/z0-relay-guard" | tar -xf - -C "$stage"
[[ -f $stage/index.js && -f $stage/README.md ]] || { printf 'Revision lacks the relay extension.\n' >&2; exit 1; }
# Relative paths and sorted file digests make this SHA independent of staging paths and mtimes.
content_sha() (
  cd -- "$1"
  find . -type f ! -name .content-sha256 -print0 | LC_ALL=C sort -z | xargs -0 sha256sum | sha256sum | cut -d ' ' -f 1
)
content=$(content_sha "$stage")
printf '%s\n' "$content" > "$stage/.content-sha256"
target="$store/$content"
if [[ -e $target || -L $target ]]; then
  [[ -d $target && ! -L $target && $(content_sha "$target") == "$content" ]] || {
    printf 'Refusing altered deployment: %s\n' "$target" >&2
    exit 1
  }
else
  chmod -R a-w -- "$stage"
  mv -T -- "$stage" "$target"
  stage=
fi
if [[ -e $link && ! -L $link ]]; then
  printf 'Refusing to replace a non-symlink: %s\n' "$link" >&2
  exit 1
fi
link_stage="$(dirname -- "$link")/.z0-relay-guard.install.$$"
ln -s -- "$target" "$link_stage"
mv -Tf -- "$link_stage" "$link"
link_stage=
printf 'Installed commit: %s\nContent SHA256: %s\nExtension link: %s -> %s\n' "$revision" "$content" "$link" "$target"
printf 'Enable command: bash %q %s\n' "$script_dir/install-z0-relay-guard.sh" "$revision"
printf 'Start a new OMP session. IntentService approval is required for CONTRACT-tier actions. Use omp --show for relay receipts.\n'
