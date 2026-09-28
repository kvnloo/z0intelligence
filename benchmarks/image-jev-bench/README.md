# Image JevBench entry

Tracking: https://github.com/kvnloo/z0intelligence/issues/39

This directory freezes the first reproducible z0intelligence image-decision
candidate for Benchmark Heaven's Image JevBench.

## Candidate

- system name: `z0int Image Decision 3B`
- backbone: `Qwen/Qwen2.5-VL-3B-Instruct`
- pinned model revision: `66285546d2b821cf421d4f5eb2576359d3770cd3`
- z0int readout: dynamic options are mapped to `A/B/C/...` and probabilities
  are computed from model logits restricted to those labels
- default runtime: local CUDA, BF16; intended first measurement target is a
  single 12 GB NVIDIA GPU
- generated answer tokens: zero in the normal single-token-label path

The backbone identity stays explicit. This is a z0int system configuration and
readout layer, not a claim that z0int trained Qwen2.5-VL.

## Install

```bash
pip install -e '.[image]'
```

## Public smoke test

```bash
z0int-image-decide run \
  --input benchmarks/image-jev-bench/red-square.json
```

Expected schema:

```json
{
  "schema": "z0int.image_decision.v1",
  "choice": "red",
  "probabilities": {
    "red": 0.0,
    "blue": 0.0
  }
}
```

The probabilities shown above are placeholders for shape only. Do not record a
benchmark claim until the command has actually run on the target hardware.

## Batch receipts

```bash
z0int-image-decide batch \
  --input benchmarks/image-jev-bench/smoke.jsonl \
  --output results/image-jev-bench/smoke.jsonl
```

The model is loaded once and reused across the JSONL rows. Each output records
the exact model revision, whole decision latency, option probabilities, label
mapping, readout method and raw restricted scores.

## Benchmark discipline

1. Use public/example data only for smoke testing.
2. Do not tune prompts, temperatures, calibration parameters or weights on
   sealed benchmark items.
3. Freeze the z0int commit, model revision, environment and command before
   requesting official evaluation.
4. Record GPU model, peak VRAM, p50/p95 whole-call latency and failures.
5. Never silently coerce malformed model output into a valid decision.
6. Submit the runnable code and exact revisions rather than locally computed
   benchmark claims.

Image JevBench: https://benchmarkheaven.com/image-jev-bench

## Local measurement checklist

```bash
nvidia-smi
python --version
python -c 'import torch, transformers; print(torch.__version__, transformers.__version__)'
git rev-parse HEAD
z0int-image-decide batch \
  --input benchmarks/image-jev-bench/smoke.jsonl \
  --output results/image-jev-bench/smoke.jsonl
```

After the smoke path passes, the next artifact should be a public-split runner
that consumes the benchmark's released task format without copying any sealed
items into this repository.
