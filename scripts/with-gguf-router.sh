#!/usr/bin/env bash
# Run a command with a llama.cpp *router* server serving every GGUF in a models dir,
# one resident at a time (--models-max 1), for `llama_http:<arm>` candidates.
# The host's z0-slm service is stopped for the duration (a small GPU fits one small
# model) and restarted on exit, even on failure.
#
# Usage: scripts/with-gguf-router.sh <log_file> <command...>
set -euo pipefail
LOG="${1:?log file}"; shift
THREADS="${Z0INT_BENCH_THREADS:-4}"
LLAMA_SERVER="${LLAMA_SERVER:-$HOME/.local/opt/llama.cpp/b11270-vulkan/llama-server}"
MODELS_DIR="${Z0INT_ROUTER_MODELS_DIR:-$HOME/.local/share/z0int-models/router}"
ROUTER_PORT="${Z0INT_ROUTER_PORT:-11520}"
ROUTER_DEVICE="${Z0INT_ROUTER_DEVICE:-Vulkan0}"

SLM_WAS_ACTIVE=0
restore() {
  [ -n "${ROUTER_PID:-}" ] && kill "$ROUTER_PID" 2>/dev/null && wait "$ROUTER_PID" 2>/dev/null || true
  if [ "$SLM_WAS_ACTIVE" = 1 ]; then systemctl --user start z0-slm || true; fi
}
trap restore EXIT
if systemctl --user is-active --quiet z0-slm; then SLM_WAS_ACTIVE=1; systemctl --user stop z0-slm; fi
mkdir -p "$(dirname "$LOG")"
"$LLAMA_SERVER" --models-dir "$MODELS_DIR" --models-max 1 --device "$ROUTER_DEVICE" -ngl 99 -c 4096 \
   --parallel 1 --host 127.0.0.1 --port "$ROUTER_PORT" --no-webui --reasoning off -t "$THREADS" \
   > "$LOG" 2>&1 &
ROUTER_PID=$!
for _ in $(seq 60); do curl -sf "http://127.0.0.1:$ROUTER_PORT/health" >/dev/null && break; sleep 1; done
"$@"
