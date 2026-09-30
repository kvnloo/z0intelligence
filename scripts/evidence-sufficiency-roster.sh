#!/usr/bin/env bash
# Frozen evidence_sufficiency admission eval (48 items Jev was admitted on, 46/48) for
# every backend this host runs; GGUF arms go through the llama.cpp router.
# Usage: scripts/evidence-sufficiency-roster.sh <out_dir> [backends]
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
INNER=0; if [ "${1:-}" = "--inner" ]; then INNER=1; shift; fi
OUT="${1:?out dir}"
CANDS="${2:-laya_421m,julia_1,nanojev,openjev_06b,llama_http:functiongemma_270m_q8,llama_http:qwen3_06b_q8,llama_http:qwen3_17b_q4,llama_http:hammer21_15b_q4}"
THREADS="${Z0INT_BENCH_THREADS:-4}"
export OMP_NUM_THREADS=$THREADS MKL_NUM_THREADS=$THREADS
export Z0INT_LAYA_DEVICE=cpu Z0INT_DECIDER_DEVICE=cpu Z0INT_NANOJEV_DEVICE=cpu Z0INT_OPENJEV_DEVICE=cpu Z0INT_JULIA_DEVICE=cpu
mkdir -p "$OUT"
if [ "$INNER" = 0 ] && [[ "$CANDS" == *llama_http:* ]]; then
  exec "$REPO/scripts/with-gguf-router.sh" "$OUT/router.log" "$0" --inner "$OUT" "$CANDS"
fi
IFS=, read -ra LIST <<< "$CANDS"
for b in "${LIST[@]}"; do
  f="$OUT/${b//:/__}.jsonl"
  "$REPO/.venv/bin/python" "$REPO/benchmarks/local_evidence_sufficiency.py" --backend "$b" --out "$f" \
     > "$OUT/${b//:/__}.log" 2>&1 || echo "FAILED $b (see log)"
  tail -1 "$OUT/${b//:/__}.log"
done
