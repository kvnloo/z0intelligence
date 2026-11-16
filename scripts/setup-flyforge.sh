#!/usr/bin/env bash
# Bootstrap the FlyForge stack: OpenJev (runtime) + Evolution Lab (evolve).
# Prefer `z0int onboard` for new user-facing setup (docs/ONBOARDING.md).
# Run from the openjev / z0int repository root.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

OPENJEV_DIR="${OPENJEV_DIR:-$ROOT}"
EVOLUTION_LAB_DIR="${EVOLUTION_LAB_DIR:-$(dirname "$ROOT")/evolution-lab}"
EVOLUTION_LAB_REF="${EVOLUTION_LAB_REF:-nightly}"
EVOLUTION_LAB_REPO="${EVOLUTION_LAB_REPO:-https://github.com/kvnloo/evolution-lab.git}"
PYTHON="${PYTHON:-python3}"
VENV="${VENV:-$ROOT/.venv}"

banner() { printf '\n==> %s\n' "$*"; }

banner "OpenJev FlyForge setup"
echo "  openjev:        $OPENJEV_DIR"
echo "  evolution-lab:  $EVOLUTION_LAB_DIR ($EVOLUTION_LAB_REF)"

if [[ ! -f "$OPENJEV_DIR/pyproject.toml" ]] || ! grep -q 'openjev-phase1' "$OPENJEV_DIR/pyproject.toml"; then
  echo "error: run from the openjev repo root (expected pyproject.toml with openjev-phase1)" >&2
  exit 1
fi

if [[ ! -d "$VENV" ]]; then
  banner "Creating venv at $VENV"
  "$PYTHON" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

banner "Installing OpenJev editable (.[test,ml])"
pip install -q -U pip
pip install -q -e "$OPENJEV_DIR[test,ml]"

if [[ "${SKIP_EVOLUTION_LAB:-0}" == "1" ]]; then
  banner "SKIP_EVOLUTION_LAB=1 — OpenJev-only install complete"
  cat <<EOF

Next:
  openjev-score --help
  openjev-data synthetic --output data/synthetic
  See docs/evolution-lab.md when you are ready to evolve specialists.

EOF
  exit 0
fi

if [[ ! -d "$EVOLUTION_LAB_DIR/.git" ]]; then
  banner "Cloning evolution-lab → $EVOLUTION_LAB_DIR"
  git clone "$EVOLUTION_LAB_REPO" "$EVOLUTION_LAB_DIR"
fi

banner "Checking out evolution-lab @ $EVOLUTION_LAB_REF"
git -C "$EVOLUTION_LAB_DIR" fetch origin --quiet
git -C "$EVOLUTION_LAB_DIR" checkout "$EVOLUTION_LAB_REF"
git -C "$EVOLUTION_LAB_DIR" pull --ff-only origin "$EVOLUTION_LAB_REF" 2>/dev/null || true

banner "Installing evolution-lab editable"
pip install -q -e "$EVOLUTION_LAB_DIR"

banner "Locking experiment splits (idempotent)"
if ! python -c "from evolution_lab.splits import splits_exist; import sys; sys.exit(0 if splits_exist() else 1)"; then
  python -m evolution_lab lock-splits
else
  echo "  P0 splits already present"
fi
if ! python -c "from evolution_lab.jev_splits import splits_exist; import sys; sys.exit(0 if splits_exist() else 1)"; then
  python -m evolution_lab lock-jev-splits
else
  echo "  Jev splits already present"
fi

banner "Quick verification"
python -c "import openjev_phase1; print('  openjev_phase1:', openjev_phase1.__file__)"
python -c "import evolution_lab; print('  evolution_lab:', evolution_lab.__file__)"
python -m unittest discover -s "$EVOLUTION_LAB_DIR/tests" -p 'test_jev*.py' -p 'test_openjev*.py' -q 2>/dev/null || \
  echo "  (optional Jev tests skipped or failed — install CUDA torch for full Track B)"

banner "z0int doctor (user-facing lifecycle)"
python -m z0int doctor 2>/dev/null || true

cat <<EOF

FlyForge stack ready. Prefer \`z0int onboard\` / docs/ONBOARDING.md for product setup.

Run decisions (OpenJev):
  openjev-score --mode direct --model Qwen/Qwen3.5-4B \\
    --revision 851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a \\
    --input examples/decisions.jsonl --output /tmp/results.jsonl

Evolve recovery fly (Evolution Lab):
  cd "$EVOLUTION_LAB_DIR"
  python -m evolution_lab seed && python -m evolution_lab run --level 1
  python -m evolution_lab table --level 1

Evolve JEV heads (needs GPU):
  python -m evolution_lab jev-smoke --level 0

Docs: docs/evolution-lab.md
Set HF_HOME for large model downloads. Activate venv: source $VENV/bin/activate

EOF
