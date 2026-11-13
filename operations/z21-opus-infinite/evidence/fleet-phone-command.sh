#!/bin/sh
set -eu
cd /mnt/zer0models/z0-wt/ayo-Z21
/home/kvn/.local/bin/fleet-build --node phones --local-data --class test --wait 1800 \
  --src-include src/z0int/__init__.py \
  --src-include src/z0int/z21_shadow.py \
  --src-include tests/test_z21_shadow.py \
  --src-include operations/z21-opus-infinite \
  --fetch operations/z21-opus-infinite/evidence \
  --out /mnt/zer0models/workspace/zer0/data/operations/intent-analyzer-20261104/ayo-runs/exec-Z21/evidence/recheck-20261113/fetched \
  -- sh operations/z21-opus-infinite/verify.sh
