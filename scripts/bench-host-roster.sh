#!/usr/bin/env bash
# Run the canonical DecisionBackend bench over every backend this host can run:
# in-process CPU backends (laya, julia worker, nanojev, openjev_06b, decider) plus
# GGUF arms on a llama.cpp *router* server that swaps one model at a time onto a
# small GPU. The host's resident z0-slm service is stopped for the duration
# (the GPU fits one small model) and restarted on exit.
#
# Usage: scripts/bench-host-roster.sh <out_dir> [fixtures.jsonl] [candidates]
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:?out dir}"
FIXTURES="${2:-$REPO/benchmarks/fixtures/decision-capability-v1/examples.jsonl}"
CANDS="${3:-laya_421m,julia_1,nanojev_06b,openjev_06b,decider_2b,llama_http:functiongemma_270m_q8,llama_http:qwen3_06b_q8,llama_http:qwen3_17b_q4,llama_http:hammer21_15b_q4}"
THREADS="${Z0INT_BENCH_THREADS:-4}"
LLAMA_SERVER="${LLAMA_SERVER:-$HOME/.local/opt/llama.cpp/b11270-vulkan/llama-server}"
MODELS_DIR="${Z0INT_ROUTER_MODELS_DIR:-$HOME/.local/share/z0int-models/router}"
ROUTER_PORT="${Z0INT_ROUTER_PORT:-11520}"
ROUTER_DEVICE="${Z0INT_ROUTER_DEVICE:-Vulkan0}"

export OMP_NUM_THREADS=$THREADS MKL_NUM_THREADS=$THREADS
export Z0INT_LAYA_DEVICE=cpu Z0INT_DECIDER_DEVICE=cpu Z0INT_NANOJEV_DEVICE=cpu Z0INT_OPENJEV_DEVICE=cpu Z0INT_JULIA_DEVICE=cpu

restore() {
  [ -n "${ROUTER_PID:-}" ] && kill "$ROUTER_PID" 2>/dev/null && wait "$ROUTER_PID" 2>/dev/null || true
  if [ "${SLM_WAS_ACTIVE:-0}" = 1 ]; then systemctl --user start z0-slm || true; fi
}
trap restore EXIT

SLM_WAS_ACTIVE=0
if [[ "$CANDS" == *llama_http:* ]]; then
  if systemctl --user is-active --quiet z0-slm; then SLM_WAS_ACTIVE=1; systemctl --user stop z0-slm; fi
  mkdir -p "$OUT"
  "$LLAMA_SERVER" --models-dir "$MODELS_DIR" --models-max 1 --device "$ROUTER_DEVICE" -ngl 99 -c 4096 \
     --parallel 1 --host 127.0.0.1 --port "$ROUTER_PORT" --no-webui --reasoning off -t "$THREADS" \
     > "$OUT/router.log" 2>&1 &
  ROUTER_PID=$!
  for _ in $(seq 60); do curl -sf "http://127.0.0.1:$ROUTER_PORT/health" >/dev/null && break; sleep 1; done
fi

"$REPO/.venv/bin/python" -m z0int backends bench --backend "$CANDS" --fixtures "$FIXTURES" --output "$OUT" --json \
  > "$OUT.stdout.json"
echo "done: $OUT"
