#!/usr/bin/env bash
# Explicit opt-in only. Install immutable committed bytes. Never enable/start services.
set -euo pipefail
umask 077
[[ $# == 1 && $1 =~ ^[0-9a-f]{40}$ ]] || { printf 'Usage: bash %s <40-character commit SHA>\n' "$0" >&2; exit 2; }
revision=$1
script_dir=$(dirname -- "$(realpath -- "$0")")
repo=$(git -C "$script_dir" rev-parse --show-toplevel)
[[ $(git -C "$repo" rev-parse --verify "${revision}^{commit}") == "$revision" ]] || exit 2
python=${Z0INT_INTENT_PYTHON:-$HOME/.local/share/z0-intent-service/venv/bin/python3}
[[ -x $python && $python != *' '* ]] || { printf 'Set Z0INT_INTENT_PYTHON to a Python with pinned aodl-contract installed.\n' >&2; exit 2; }
command -v bun >/dev/null
command -v node >/dev/null
paths=(src/z0int/__init__.py src/z0int/intent_service.py src/z0int/intent_memory.py src/z0int/intent_model.mjs src/z0int/intent_compile.mjs omp-extensions/z0-intent-service/index.js omp-extensions/z0-intent-service/triage.js omp-extensions/z0-intent-service/package.json omp-extensions/z0-relay-guard/checker.js omp-extensions/z0-relay-guard/verifiers.js omp-extensions/z0-relay-guard/package.json skills/intent-contract/SKILL.md systemd/z0-intent-service.service)
# Fingerprint path, mode, and committed blob identity, not a mutable worktree.
manifest=$(git -C "$repo" ls-tree -r "$revision" -- "${paths[@]}")
[[ $(printf '%s\n' "$manifest" | wc -l) == ${#paths[@]} ]] || { printf 'Revision is missing deployment files.\n' >&2; exit 1; }
content=$(printf '%s\n' "$manifest" | sha256sum | cut -d ' ' -f 1)
store="$HOME/.local/lib/z0-intent-service"
mkdir -p "$store" "$HOME/.omp/agent/extensions" "$HOME/.config/systemd/user" "$HOME/.omp/agent/skills" "$HOME/.z0int/intent"
exec 9>"$store/.install.lock"
flock 9
target="$store/$content"
if [[ -e $target || -L $target ]]; then
  [[ -d $target && ! -L $target && -f $target/.manifest ]] || { printf 'Refusing existing incomplete snapshot.\n' >&2; exit 1; }
  [[ $(cat "$target/.manifest") == "$manifest" ]] || { printf 'Snapshot manifest mismatch.\n' >&2; exit 1; }
  while IFS=$'\t' read -r object file; do
    blob=${object##* }
    [[ $(git hash-object "$target/$file") == "$blob" ]] || { printf 'Refusing altered snapshot.\n' >&2; exit 1; }
  done <<< "$manifest"
else
  mkdir "$target"
  git -C "$repo" archive "$revision" -- "${paths[@]}" | tar -xf - -C "$target"
  printf '%s\n' "$manifest" > "$target/.manifest"
  printf '%s\n' "$content" > "$target/omp-extensions/z0-intent-service/.content-sha256"
  chmod -R a-w "$target"
fi
link="$HOME/.omp/agent/extensions/z0-intent-service"
skill_link="$HOME/.omp/agent/skills/intent-contract"
for destination in "$link" "$skill_link"; do
  [[ ! -e $destination || -L $destination ]] || { printf 'Refusing non-symlink: %s\n' "$destination" >&2; exit 1; }
done
ln -sfnT "$target/omp-extensions/z0-intent-service" "$link"
ln -sfnT "$target/skills/intent-contract" "$skill_link"
unit=$(cat "$target/systemd/z0-intent-service.service")
unit=${unit//@SNAPSHOT@/$target}
unit=${unit//@PYTHON@/$python}
printf '%s\n' "$unit" > "$HOME/.config/systemd/user/z0-intent-service.service"
printf 'Installed commit: %s\nContent SHA256: %s\nSnapshot: %s\n' "$revision" "$content" "$target"
printf 'Service unit installed but NOT enabled, started, or daemon-reloaded.\n'
printf 'Contract gate is a separate dependency. Install its reviewed stamped revision before enabling this client.\n'
