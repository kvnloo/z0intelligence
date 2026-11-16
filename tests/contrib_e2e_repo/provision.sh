#!/usr/bin/env bash
# Inside the image as user dev.
#   provision.sh          fresh clone of the z0int candidate + ./scripts/bootstrap.sh (repo-only
#                         onboarding, spec §1.3); records the bootstrap time.
#   provision.sh refresh  an image that is already provisioned: move the clone to the new
#                         candidate (same dependency pins) without a new 20-minute install.
set -euo pipefail
if [[ "${1:-}" == refresh ]]; then
  cd ~/z0intelligence && git fetch -q /mirror/z0int.git master && git checkout -q --detach FETCH_HEAD
  git branch -f master FETCH_HEAD && git checkout -q master
  .venv/bin/python -m pip install -q --no-deps -e . > /out/refresh.log 2>&1
  echo "refreshed $(git rev-parse HEAD)"; exit 0
fi
cd ~ && git clone -q /mirror/z0int.git z0intelligence && cd z0intelligence
t0=$(date +%s)
./scripts/bootstrap.sh > /out/bootstrap.log 2>&1 || { tail -20 /out/bootstrap.log; exit 1; }
echo "provisioned $(git rev-parse HEAD) bootstrap_secs=$(( $(date +%s)-t0 ))"
