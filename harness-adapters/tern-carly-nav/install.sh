#!/usr/bin/env bash
# Install the carly-nav Tern plugin as a copy in Tern's plugin directory.
#
# Why a copy, not `tern plugin link`: Tern gives a window entry 50 ms to load and
# disables it on overrun. A cold read from a busy data disk can take that long,
# so the plugin lives on Tern's own config disk. The Python inlet stays in this
# checkout; its path is recorded in the plugin's data directory.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
src=$(cd "$here/../../src" && pwd -P)
test -f "$src/z0int/carly_nav/__main__.py" || { echo "missing $src/z0int/carly_nav" >&2; exit 1; }

if [ -e "$(tern plugin dir)/carly-nav.path" ]; then
	tern plugin unlink carly-nav
fi

data="${XDG_STATE_HOME:-$HOME/.local/state}/tern/plugin-data/carly-nav"
mkdir -p "$data"
printf '%s\n' "$src" > "$data/z0int-src"

tern plugin install "$here" --force
tern plugin list
