#!/usr/bin/env bash
# Linux x86_64 only; installs solely in a new caller-owned directory.
set -euo pipefail
root=$(realpath -m "${1:?usage: bootstrap.sh NEW_OWNED_DESTINATION}")
mkdir "$root"
mkdir "$root/downloads"
bend=${BEND_ARCHIVE:-$root/downloads/bend.tar.gz}
lean=${LEAN_ARCHIVE:-$root/downloads/lean.tar.zst}
if [ ! -f "$bend" ]; then
  curl --fail --location --max-time 180 --max-filesize 40000000 --output "$bend" https://github.com/bendlang/bend/releases/download/v2.0.34/bend-2.0.34-linux-x64.tar.gz
fi
if [ ! -f "$lean" ]; then
  curl --fail --location --max-time 600 --max-filesize 600000000 --output "$lean" https://github.com/leanprover/lean4/releases/download/v4.34.0/lean-4.34.0-linux.tar.zst
fi
printf '%s  %s\n' 78106a97af242429dcc057258eb8d10f69cddebcd5e263022185a52d003e09bf "$bend" | sha256sum --check --strict -
printf '%s  %s\n' caaa98356098c85dc0fcbbd28e1ec66f39eb6551829972b752ff20e1286b646b "$lean" | sha256sum --check --strict -
tar -xzf "$bend" -C "$root"
tar --zstd -xf "$lean" -C "$root"
printf 'BEND2_BIN=%s/bend/bin/bend\nLEAN_BIN=%s/lean-4.34.0-linux/bin\n' "$root" "$root"
