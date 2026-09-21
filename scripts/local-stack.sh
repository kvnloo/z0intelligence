#!/usr/bin/env bash
# One entry point for the local cognition stack on this machine.
#
#   bash scripts/local-stack.sh up        # start the local model server
#   bash scripts/local-stack.sh status    # what is served + role/evidence view
#   bash scripts/local-stack.sh shadow    # tail the OMP shadow receipts
#   bash scripts/local-stack.sh decide    # run the compiled cascade on a sample
#   bash scripts/local-stack.sh eval      # 28-fixture functional evaluation
#   bash scripts/local-stack.sh down
#
# The stack is observe-only by construction: nothing here executes a tool. In
# OMP the extension records candidate decisions; the harness keeps its own
# permissions, approvals and retries.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${Z0INT_PYTHON:-/home/kvn/tmp/openjev/.venv/bin/python}"
OL_BIN="${OL_BIN:-/mnt/zer0models/zer0-models/runtime/ollama-0.33.2/bin/ollama}"
GGUF_DIR="${GGUF_DIR:-/mnt/zer0models/zer0-models/gguf}"
MODELS_DIR="${MODELS_DIR:-/mnt/zer0models/workspace/zer0/oss/.work/ollama-models}"
LOG="${LOG:-/mnt/zer0models/workspace/zer0/oss/.work/ollama-11440.log}"
PORT="${PORT:-11440}"
HOST="127.0.0.1"

export OLLAMA_HOST="$HOST:$PORT"
export OLLAMA_MODELS="$MODELS_DIR"
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-15m}"
export PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}"

# tag:gguf-relative-path
REGISTRY=(
  "z0/functiongemma-270m-it:q8_0:functiongemma-270m/functiongemma-270m-it-q8_0.gguf"
  "z0/hammer2.1-3b:q4_k_m:hammer2.1-3b/Hammer2.1-3b.Q4_K_M.gguf"
  "z0/hammer2.1-7b:q4_k_m:hammer2.1-7b/Hammer2.1-7b-Q4_K_M.gguf"
  "z0/nemotron-orchestrator-8b:q4_k_m:nvidia-orchestrator-8b/nvidia_Orchestrator-8B-Q4_K_M.gguf"
  "z0/qwen3.5-9b:q4_k_m:qwen3.5-9b/Qwen_Qwen3.5-9B-Q4_K_M.gguf"
)

register_models() {
  mkdir -p "$MODELS_DIR"
  for row in "${REGISTRY[@]}"; do
    tag="${row%%:*}"; rest="${row#*:}"; quant="${rest%%:*}"; rel="${rest##*:}"
    f="$GGUF_DIR/$rel"
    [ -f "$f" ] || { echo "  missing GGUF: $f" >&2; continue; }
    if "$OL_BIN" show "$tag:$quant" >/dev/null 2>&1; then continue; fi
    mf="$(mktemp)"; printf 'FROM %s\n' "$f" >"$mf"
    echo "  registering $tag:$quant"
    "$OL_BIN" create "$tag:$quant" -f "$mf" >/dev/null 2>&1 || echo "  FAILED $tag" >&2
    rm -f "$mf"
  done
}

up() {
  if curl -sS -m 2 "http://$HOST:$PORT/api/version" >/dev/null 2>&1; then
    echo "already serving on http://$HOST:$PORT"
  else
    mkdir -p "$MODELS_DIR"
    setsid nohup "$OL_BIN" serve >"$LOG" 2>&1 </dev/null &
    for _ in $(seq 1 30); do
      curl -sS -m 2 "http://$HOST:$PORT/api/version" >/dev/null 2>&1 && break
      sleep 1
    done
    echo "serving on http://$HOST:$PORT"
  fi
  register_models
  echo
  status
}

down() {
  python3 - "$MODELS_DIR" <<'PY'
import os, signal, sys
mark, me = sys.argv[1], os.getpid()
for pid in os.listdir('/proc'):
    if not pid.isdigit() or int(pid) == me:
        continue
    try:
        env = open(f'/proc/{pid}/environ', 'rb').read().decode('utf-8', 'replace')
        cmd = open(f'/proc/{pid}/cmdline', 'rb').read().decode('utf-8', 'replace')
    except OSError:
        continue
    if (mark in env or mark in cmd) and ('serve' in cmd or 'llama-server' in cmd):
        try:
            os.kill(int(pid), signal.SIGTERM)
        except OSError:
            pass
PY
  echo "stopped local model server"
}

status() {
  "$PY" -m z0int cognition serving || true
  echo
  "$PY" -m z0int cognition roles
}

shadow() {
  f="$HOME/.z0int/shadow/cognition-shadow.jsonl"
  [ -f "$f" ] || { echo "no shadow receipts yet at $f"; return 1; }
  tail -n "${1:-5}" "$f" | "$PY" -c 'import json,sys
for line in sys.stdin:
    d = json.loads(line)
    print(d.get("trace_id"), "legal=", d.get("legal_ids"), "executed=", d.get("executed_action"))
    for s in d.get("shadow", []):
        print("   ", s.get("label"), "->", s.get("selected_action"),
              "abstained=" + str(s.get("abstained")), str(s.get("latency_ms")) + "ms")'
}

decide() {
  tmp="$(mktemp)"
  cat >"$tmp" <<'JSON'
{
  "state": "Read /etc/hostname and report what it contains.",
  "authority": ["read"],
  "granted_capabilities": ["fs", "shell"],
  "budget_units": 8,
  "actions": [
    {"action_id": "fs.read", "kind": "tool", "description": "Read a file from disk",
     "family": "fs", "risk_class": "read", "cost_units": 1,
     "arguments_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                          "required": ["path"], "additionalProperties": false}},
    {"action_id": "fs.write", "kind": "tool", "description": "Write a file to disk",
     "family": "fs", "risk_class": "write", "cost_units": 1,
     "arguments_schema": {"type": "object", "properties": {"path": {"type": "string"}},
                          "additionalProperties": false}},
    {"action_id": "shell.run", "kind": "tool", "description": "Run a shell command",
     "family": "shell", "risk_class": "write", "cost_units": 2,
     "arguments_schema": {"type": "object", "properties": {}}},
    {"action_id": "fs.rm_rf", "kind": "tool",
     "description": "Recursively delete the working tree", "family": "fs",
     "risk_class": "destructive", "cost_units": 2,
     "arguments_schema": {"type": "object", "properties": {}}}
  ]
}
JSON
  echo "== deterministic compile =="
  "$PY" -m z0int cognition compile --input "$tmp" | "$PY" -c 'import json,sys;d=json.load(sys.stdin);print("legal:",d["legal"] and [a["action_id"] for a in d["legal"]]);print("eliminated:",[(e["action_id"],e["stage"],e["reason"]) for e in d["eliminated"]])'
  echo
  echo "== cascade with shadow candidates (nothing is executed) =="
  "$PY" -m z0int cognition decide --input "$tmp" --use-models \
      --shadow nemotron_orchestrator_8b --shadow hammer2.1_3b \
    | "$PY" -c 'import json,sys;d=json.load(sys.stdin);print("tier:",d["tier"],"selected:",d["selected_action"],"executed:",d["executed_action"]);[print("  shadow",s.get("label"),"->",s.get("selected_action"),s.get("error","")) for s in d["shadow"]]'
  rm -f "$tmp"
}

eval_all() {
  "$PY" "$REPO/scripts/local_tool_calling_eval.py" \
      --backend rules --backend functiongemma_270m --backend hammer2.1_3b \
      --backend hammer2.1_7b --backend nemotron_orchestrator_8b --backend qwen3.5_9b \
      --llamacpp --context 4096 --max-tokens 1024
}

case "${1:-status}" in
  up) up ;;
  down) down ;;
  status) status ;;
  shadow) shift || true; shadow "${1:-5}" ;;
  decide) decide ;;
  eval) eval_all ;;
  *) echo "usage: $0 {up|down|status|shadow [n]|decide|eval}" >&2; exit 2 ;;
esac
