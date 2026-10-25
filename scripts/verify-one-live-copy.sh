#!/usr/bin/env bash
# Run through fleet-build on MBP, from the shipped repository root; never on groot.
set -euo pipefail
VENV=$(mktemp -d /tmp/one-live-copy-venv.XXXXXXXX)
python3 -m venv "$VENV"
source "$VENV/bin/activate"
# Fleet's archive has no Git metadata; preserve the real tracked-snapshot contract
# used by no-tracked-weights tests rather than skipping that check.
if [[ ! -e .git ]]; then
  git init -q
  git add --force -A
fi
# The host toolchain Git cannot use the container's absent CA file. Reuse pip's
# trusted public bundle; do not disable TLS verification or trust a new authority.
GIT_SSL_CAINFO=$(python -c 'import pip._vendor.certifi; print(pip._vendor.certifi.where())')
export GIT_SSL_CAINFO
pip install -q pytest==8.4.2 PyYAML==6.0.3 requests httpx typesafe-sdk==0.7.2 fff-search==0.11.0 \
  'aodl-contract @ git+https://github.com/kvnloo/aodl.git@68231658f0ec0338464c0916a2329b9587444312' \
  'agent-tokenomics @ git+https://github.com/kvnloo/tokenomics.git@2ae90511a900fdb3dcc576c9634cd8d9ee1c8d6a'
export PYTHONPATH=src
export Z0INT_HOME
Z0INT_HOME=$(mktemp -d /tmp/one-live-copy-state.XXXXXXXX)
python -m pytest -q \
  tests/test_one_live_release.py tests/test_intelligence*.py \
  tests/test_shared_file_search.py tests/test_file_search.py tests/test_bridge*.py \
  tests/test_cognition*.py tests/test_shadow*.py tests/test_learning*.py \
  tests/test_choice_capacity.py tests/test_loop_tick.py tests/test_legacy_import.py \
  tests/test_optchat.py tests/test_hermes_optchat.py tests/test_omp_capture.py \
  tests/test_slm_methods.py tests/test_network_cutover_phase_a.py
bun test omp-extensions/local-cognition/index.test.ts \
  omp-extensions/z0int-bridge/index.test.ts omp-extensions/z0-usage/index.test.ts
