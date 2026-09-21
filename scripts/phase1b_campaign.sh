#!/usr/bin/env bash
# Phase 1B measurement campaign, phases 2..6, run strictly in sequence.
#
# One RTX 3080 Ti and one production supervisor means exactly one measurement at
# a time: running two campaigns concurrently would make every latency label a
# measurement of the other campaign. Latency is a first-class output here, so the
# phases are serialised on purpose.
#
#   bash scripts/phase1b_campaign.sh <run-id> [phase...]
#
# Phases:
#   cold       deliberate cold-load / model-swap probe (evict, then measure)
#   contract   compiler-contract adversarial slice, behavioural pass
#   compose    true composition chains (compiler -> ... -> generalist), ablated
#   orch       expanded 40-scenario orchestration corpus (12 regression + 28 new)
#   ladder     the Phase 1 multi-model cascade arms, for comparability
set -uo pipefail

RUN_ID="${1:?usage: phase1b_campaign.sh <run-id> [phase...]}"
shift || true
PHASES=("$@")
[ ${#PHASES[@]} -eq 0 ] && PHASES=(cold contract compose orch ladder)

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${Z0INT_PYTHON:-/home/kvn/tmp/openjev/.venv/bin/python}"
RUN_DIR="$REPO/results/phase1b/$RUN_ID"
LOG_DIR="/mnt/zer0models/workspace/zer0/oss/.work"
export PYTHONPATH="$REPO/src${PYTHONPATH:+:$PYTHONPATH}"

if [ ! -f "$RUN_DIR/baseline/freeze.json" ]; then
  echo "no frozen run at $RUN_DIR -- run phase1b_freeze.py first" >&2
  exit 2
fi

phase_cold() {
  # 3 deliberate cold loads per model, each preceded by an eviction.
  "$PY" "$REPO/scripts/densify_measurements.py" \
    --run-id "$RUN_ID" --cold-probe --reps 3 \
    --cold-models qwen3.5_4b --cold-models qwen3.5_9b \
    --cold-models hammer2.1_3b --cold-models nemotron_orchestrator_8b \
    --cold-models hammer2.1_7b --cold-models functiongemma_270m
}

phase_contract() {
  "$PY" "$REPO/scripts/compiler_contract_eval.py" --served \
    --out "$RUN_DIR/compiler-contract"
}

phase_compose() {
  "$PY" "$REPO/scripts/true_composition_eval.py" --chains named --reps 1 \
    --out "$RUN_DIR/true-composition"
}

phase_orch() {
  "$PY" "$REPO/scripts/orchestration_eval.py" --greedy --include-expansion \
    --backend qwen3.5_4b --backend qwen3.5_9b --backend nemotron_orchestrator_8b \
    --backend hammer2.1_3b \
    --out "$RUN_DIR/orchestration-v2"
}

phase_ladder() {
  "$PY" "$REPO/scripts/densify_measurements.py" \
    --run-id "$RUN_ID" --arms ladder --reps 3
}

for phase in "${PHASES[@]}"; do
  log="$LOG_DIR/p1b-$phase.log"
  echo "=== $phase -> $log ($(date -u +%H:%M:%S)) ==="
  started=$(date +%s)
  "phase_$phase" >"$log" 2>&1
  rc=$?
  echo "=== $phase exit=$rc in $(( $(date +%s) - started ))s ==="
done
echo "=== campaign complete ($(date -u +%H:%M:%S)) ==="
