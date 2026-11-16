#!/usr/bin/env bash
# Bootstrap z0int user-facing CLI. All real logic lives in `z0int` (Python).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-python3}"
VENV="${VENV:-$ROOT/.venv}"

banner() { printf '\n==> %s\n' "$*"; }

banner "z0int bootstrap"
echo "  root: $ROOT"
echo "  python: $PYTHON"
echo "  venv: $VENV"

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
pip install -q -e ".[test,ml]"

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
