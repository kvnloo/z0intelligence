#!/usr/bin/env bash
# Run the canonical DecisionBackend bench over every backend this host can run:
# in-process CPU backends (laya, julia worker, nanojev, openjev_06b, decider) plus
# GGUF arms (`llama_http:<arm>`) on a llama.cpp router (scripts/with-gguf-router.sh).
#
# Usage: scripts/bench-host-roster.sh <out_dir> [fixtures.jsonl] [candidates]
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
INNER=0
if [ "${1:-}" = "--inner" ]; then INNER=1; shift; fi
OUT="${1:?out dir}"
FIXTURES="${2:-$REPO/benchmarks/fixtures/decision-capability-v1/examples.jsonl}"
CANDS="${3:-laya_421m,julia_1,nanojev_06b,openjev_06b,decider_2b,llama_http:functiongemma_270m_q8,llama_http:qwen3_06b_q8,llama_http:qwen3_17b_q4,llama_http:hammer21_15b_q4}"
THREADS="${Z0INT_BENCH_THREADS:-4}"
export OMP_NUM_THREADS=$THREADS MKL_NUM_THREADS=$THREADS
export Z0INT_LAYA_DEVICE=cpu Z0INT_DECIDER_DEVICE=cpu Z0INT_NANOJEV_DEVICE=cpu Z0INT_OPENJEV_DEVICE=cpu Z0INT_JULIA_DEVICE=cpu
mkdir -p "$OUT"
if [ "$INNER" = 0 ] && [[ "$CANDS" == *llama_http:* ]]; then
  exec "$REPO/scripts/with-gguf-router.sh" "$OUT/router.log" "$0" --inner "$OUT" "$FIXTURES" "$CANDS"
fi
"$REPO/.venv/bin/python" -m z0int backends bench --backend "$CANDS" --fixtures "$FIXTURES" --output "$OUT" --json \
  > "$OUT.stdout.json"
echo "done: $OUT"
