#!/usr/bin/env bash
# Print (and optionally prepare) vLLM DiffusionGemma structured-read serving for Route C.
# Requires vLLM PR https://github.com/vllm-project/vllm/pull/57250 or equivalent merge.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VLLM_REF="${VLLM_REF:-pull/57250/head}"
VLLM_REPO="${VLLM_REPO:-https://github.com/vllm-project/vllm.git}"
VLLM_DIR="${VLLM_DIR:-$(dirname "$ROOT")/vllm}"
MODEL="${VLLM_MODEL:-nvidia/diffusiongemma-26B-A4B-it-NVFP4}"
CANVAS="${VLLM_CANVAS:-64}"
PORT="${VLLM_PORT:-8000}"
PYTHON="${PYTHON:-python3}"
VENV="${VENV:-$ROOT/.venv}"

banner() { printf '\n==> %s\n' "$*"; }

banner "OpenJev Route C — vLLM DiffusionGemma setup"
echo "  openjev:   $ROOT"
echo "  vllm dir:  $VLLM_DIR ($VLLM_REF)"
echo "  model:     $MODEL"
echo "  canvas:    $CANVAS"

if [[ ! -d "$VENV" ]]; then
  "$PYTHON" -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
pip install -q -e '.[test,ml]'

if [[ ! -d "$VLLM_DIR/.git" ]]; then
  banner "Clone vLLM"
  git clone --depth 1 "$VLLM_REPO" "$VLLM_DIR"
fi

banner "Fetch PR branch (best-effort)"
(
  cd "$VLLM_DIR"
  git fetch origin "$VLLM_REF" 2>/dev/null || git fetch origin pull/57250/head:refs/remotes/origin/pull/57250/head
  git checkout FETCH_HEAD 2>/dev/null || echo "warning: could not checkout $VLLM_REF; ensure structured diffusion reads are present"
)

banner "Install vLLM editable (may take several minutes)"
pip install -q -e "$VLLM_DIR"

banner "Serve command (run in a separate terminal / GPU session)"
cat <<EOF
vllm serve $MODEL \\
  --host 0.0.0.0 --port $PORT \\
  --diffusion-config '{"canvas_length": $CANVAS}' \\
  --max-logprobs 32 \\
  --enable-prefix-caching \\
  --async-scheduling

# Then score OpenJev JSONL:
openjev-score --mode vllm \\
  --upstream http://127.0.0.1:$PORT \\
  --model dgemma \\
  --tokenizer $MODEL \\
  --canvas $CANVAS \\
  --input examples/decisions.jsonl \\
  --output /tmp/results-vllm.jsonl
EOF
