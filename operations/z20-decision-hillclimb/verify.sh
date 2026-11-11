#!/usr/bin/env bash
set -euo pipefail
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
printf 'Host: '; hostname
python3 --version
python3 -m venv .z20-venv
.z20-venv/bin/python -m pip install --disable-pip-version-check -r operations/z20-decision-hillclimb/evidence/requirements-audit.txt
.z20-venv/bin/python -m pytest -q operations/z20-decision-hillclimb/src
if [ -f z20-inputs.tar.gz ]; then
  mkdir z20-inputs
  tar -xzf z20-inputs.tar.gz -C z20-inputs
  .z20-venv/bin/python operations/z20-decision-hillclimb/src/verify_evidence.py --execution z20-inputs
  .z20-venv/bin/python operations/z20-decision-hillclimb/src/audit_round.py --data-root z20-inputs/sources/owner --out verification-audit --bootstrap 2000
  .z20-venv/bin/python operations/z20-decision-hillclimb/src/build_da_items.py --root z20-inputs/sources/owner --output-dir verification-sanitized
fi
