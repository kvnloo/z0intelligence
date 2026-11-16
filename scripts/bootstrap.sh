#!/usr/bin/env bash
# Bootstrap z0int user-facing CLI. All real logic lives in `z0int` (Python).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# pyproject requires >=3.10,<3.14; pick a supported interpreter when python3 is newer.
if [[ -z "${PYTHON:-}" ]]; then
  for cand in python3 python3.13 python3.12 python3.11 python3.10; do
    if command -v "$cand" >/dev/null && "$cand" -c 'import sys; sys.exit(not ((3, 10) <= sys.version_info[:2] < (3, 14)))'; then
      PYTHON=$cand; break
    fi
  done
  [[ -n "${PYTHON:-}" ]] || { echo "error: need Python 3.10-3.13 (set PYTHON=...); uv python install 3.12 is one way" >&2; exit 1; }
fi
VENV="${VENV:-$ROOT/.venv}"
# Light by default (no torch) so contributors on any machine finish fast.
# Models/backends work: Z0INT_EXTRAS=test,ml ./scripts/bootstrap.sh
EXTRAS="${Z0INT_EXTRAS:-test}"

banner() { printf '\n==> %s\n' "$*"; }

banner "z0int bootstrap"
echo "  root: $ROOT"
echo "  python: $PYTHON"
echo "  venv: $VENV"
echo "  extras: $EXTRAS"

if [[ ! -f "$ROOT/pyproject.toml" ]]; then
  echo "error: run from z0int repo root" >&2
  exit 1
fi

if [[ ! -d "$VENV" ]]; then
  banner "Creating venv"
  "$PYTHON" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install -q -U pip
banner "Installing z0int / openjev-phase1 editable"
pip install -q -e ".[$EXTRAS]"

banner "doctor"
python -m z0int doctor || true

cat <<EOF

z0int installed.

Next:
  z0int onboard --auto
  z0int status

Agents: read docs/ONBOARDING.md — prefer CLI over manual steps.
Legacy stack script (still valid): bash scripts/setup-flyforge.sh
EOF
