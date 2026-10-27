#!/usr/bin/env bash
# Install committed, tested source without restarting the operator's OMP process.
set -euo pipefail
SOURCE=${SHADOW_SOURCE:-/mnt/zer0models/z0-wt/shadow-learning-loop}
REF=${1:-HEAD}
SHA=$(git -C "$SOURCE" rev-parse --verify "$REF^{commit}")
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
BASE="$HOME/.local/lib/z0int-shadow"
TARGET="$BASE/$SHA"
BACKUP="$HOME/.z0int/backups/shadow-$STAMP"
mkdir -p "$BASE" "$BACKUP" "$HOME/.omp/agent/extensions-backup"
git -C "$SOURCE" cat-file -e "$SHA^{commit}"
if [[ ! -d "$TARGET" ]]; then
  TMP=$(mktemp -d "$BASE/.install.XXXXXXXX")
  trap 'rm -rf -- "$TMP"' EXIT
  git -C "$SOURCE" archive "$SHA" | tar -x -C "$TMP"
  printf '%s\n' "$SHA" > "$TMP/DEPLOYED_SHA"
  chmod -R a-w "$TMP"
  mv "$TMP" "$TARGET"
  trap - EXIT
fi
python3 - "$HOME/.z0int" "$BACKUP" "$SHA" "$TARGET" <<'PY'
import json, os, shutil, sqlite3, sys, time
from pathlib import Path
home, backup = map(Path, sys.argv[1:3])
for relative in ('loop/shadow-learning-status.json', 'loop/strategy-ledger.json',
                 'loop/shadow-deployment.json', 'loop/shadow-census.json',
                 'config/serving.json', 'config/loop.json'):
    source = home/relative
    if source.exists():
        destination = backup/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
source = home/'loop/shadow-learning.sqlite'
if source.exists():
    with sqlite3.connect(source) as db, sqlite3.connect(backup/'shadow-learning.sqlite') as dest:
        db.backup(dest)
folder = home/'loop'
folder.mkdir(parents=True, exist_ok=True)
census = Path(os.environ.get('SHADOW_CENSUS', '/mnt/zer0models/workspace/zer0/data/operations/shadow-offload/opportunities.json'))
if not census.is_file():
    raise SystemExit('Exact-label census missing: ' + str(census))
shutil.copy2(census, folder/'shadow-census.json')
os.chmod(folder/'shadow-census.json', 0o600)
receipt = {'ts': time.time(), 'sha': sys.argv[3], 'source': sys.argv[4],
           'backup': str(backup), 'operator_session_restarted': False,
           'reload_note': 'Fresh OMP processes load this source; existing extension modules remain cached.'}
path = folder/'shadow-deployment.json'
path.write_text(json.dumps(receipt, indent=2)+'\n')
os.chmod(path, 0o600)
print(json.dumps(receipt))
PY
for EXTENSION in local-cognition z0int-bridge; do
  LINK="$HOME/.omp/agent/extensions/$EXTENSION"
  if [[ -e "$LINK" || -L "$LINK" ]]; then
    cp -a "$LINK" "$HOME/.omp/agent/extensions-backup/$EXTENSION.$STAMP"
  fi
  ln -sfn "$TARGET/omp-extensions/$EXTENSION" "$LINK"
done
printf 'DEPLOYED_ROOT=%s\n' "$TARGET"
printf '%s\n' 'No service unit or operator process was restarted. ServiceWatch owns the exact-SHA timer PYTHONPATH cutover.'
printf 'Fresh process: Z0INT_ROOT=%q Z0INT_PYTHON=/usr/bin/python3.11 OMP_Z0INT_COGNITION_MODELS=qwen3_06b omp -p <prompt>\n' "$TARGET"
