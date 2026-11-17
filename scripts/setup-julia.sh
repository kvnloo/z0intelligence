#!/usr/bin/env bash
# Reproducible Julia-1 setup for z0intelligence.
#
# Julia pins transformers>=5.0,<5.1, which the z0int runtime (5.17) violates:
#
#     AttributeError: 'ModernBertModel' object has no attribute '_update_attention_mask'
#
# so it gets its own interpreter, and backends/julia.py reaches it through
# backends/julia_worker.py. This script builds that interpreter and downloads the
# complete repository — not just model.safetensors, because Julia ships its own
# runtime code and is not a Transformers AutoModel.
#
# Usage:  scripts/setup-julia.sh [venv_dir] [model_dir]
set -euo pipefail

VENV="${1:-$HOME/.z0int/julia-venv}"
MODEL="${2:-$HOME/.z0int/models/julia_1}"
PY="${PYTHON:-python3.11}"

REVISION="a85b127321d580d65176c89ced8273f305745d85"
WEIGHTS_SHA256="df853bf7fe424420011f3d0c47a05d7341aa9eefa7fb9f203ea4aada4ad95b72"

echo "== python =="
"$PY" -V

echo "== venv $VENV =="
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet huggingface_hub

echo "== downloading SupersonicLabs/Julia-1 @ $REVISION (complete repository) =="
"$VENV/bin/python" - "$MODEL" "$REVISION" <<'PY'
import sys
from huggingface_hub import snapshot_download
target, revision = sys.argv[1], sys.argv[2]
print(snapshot_download("SupersonicLabs/Julia-1", local_dir=target, revision=revision))
PY

echo "== verifying checkpoint provenance =="
actual="$(sha256sum "$MODEL/model.safetensors" | cut -d' ' -f1)"
if [ "$actual" != "$WEIGHTS_SHA256" ]; then
  echo "FATAL: model.safetensors sha256 $actual != $WEIGHTS_SHA256" >&2
  exit 1
fi
echo "   model.safetensors $actual OK"

echo "== installing the Julia package and its pinned runtime =="
# CPU torch by default: Julia is CPU-resident unless Z0INT_JULIA_DEVICE=cuda.
if [ "${Z0INT_JULIA_TORCH:-cpu}" = "cpu" ]; then
  "$VENV/bin/pip" install --quiet torch --index-url https://download.pytorch.org/whl/cpu
fi
"$VENV/bin/pip" install --quiet -e "$MODEL"

echo "== versions =="
"$VENV/bin/python" - <<'PY'
import sys, torch, transformers, safetensors, importlib.metadata as m
print("python       ", sys.version.split()[0])
print("torch        ", torch.__version__)
print("transformers ", transformers.__version__)
print("safetensors  ", safetensors.__version__)
print("supersonic-julia", m.version("supersonic-julia"))
PY

cat <<EOF

Done. Point z0int at it:

  export Z0INT_JULIA_PYTHON=$VENV/bin/python
  export Z0INT_JULIA_MODEL_DIR=$MODEL

Verify through the adapter:

  python -c "from z0int.backends.registry import create_backend; \\
             b=create_backend('julia_1'); print(b.health(load=True))"
EOF
