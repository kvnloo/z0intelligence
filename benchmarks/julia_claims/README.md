# Julia-1 claim reproduction and distribution-shift investigation

Harness for answering: *can we independently reproduce the behaviour Julia-1 claims online,
identify the exact conditions under which it works, and demonstrate on sealed z0 data whether
any of those conditions transfer to a useful low-cost routing capability?*

Julia stays **default-off and non-authoritative** throughout. Nothing here changes routing.

## Frozen provenance

See `frozen_config.json` (machine-readable) and the module docstrings.

| | |
|---|---|
| model | `SupersonicLabs/Julia-1` @ `a85b127321d580d65176c89ced8273f305745d85` |
| weights sha256 | `df853bf7fe424420011f3d0c47a05d7341aa9eefa7fb9f203ea4aada4ad95b72` (verified) |
| historical encoding | `strict_encoding=True`, `max_length=1024`, `head_length=512`, CPU FP32 |
| typed-decisions | `LocalLLaMA/typed-decisions` @ `c76749ec…`, sha256 `4f294f21…` |
| BTZSC pilot | `btzsc/btzsc` @ `fef2a2ac…`, Jev protocol `0d610cc5…`, seed `20260917`, 100/dataset |

> The published accuracy came from a 1024-token protocol. The current 8192 default has only an
> inference smoke test and must not be used to claim benchmark reproduction.

## Runners

| file | purpose |
|---|---|
| `parity.py` | native runtime ↔ z0 adapter parity; state-rendering diff; full-suite adapter run |
| `published_claims.py` | BTZSC pilots (AG News, DAIR Emotion, Banking77) under the pinned protocol |
| `device_compare.py` | CPU FP32 vs CUDA BF16 vs CUDA FP32, item level |
| `representation_ablation.py` | factors A/B/D on dev, order sensitivity, batch invariance |
| `calibration.py` | AUROC, risk/coverage, ECE, temperature scaling |

The balanced sampler is **imported** from the pinned benchmark repo
(`jev_benchmarks.data._balanced_indices`), not reimplemented, so example selection matches the
published run.

## Results (see `../../results/julia-claims/`)

| experiment | published / expected | reproduced | adapter parity | conclusion |
|---|---:|---:|---:|---|
| typed CPU choice | 426/600 | **426/600** | exact | reproduced |
| typed CPU noul | 483/600 | **483/600** | exact | reproduced |
| typed CPU score | 542/800 | **542/800** | exact | reproduced |
| typed CUDA BF16 | historical 1463/2000 | 1460/2000 | n/a | within 3; precision, not device |
| AG News | 94/100 | **94/100** | exact | reproduced |
| Emotion (dair) | 86/100 | 84/100 | exact | −2, stable across CPU FP32 and CUDA BF16 |
| Banking77 official | 64/100 | — | — | **protocol not reproducible: no released shortlist** |
| MASSIVE en-US / pt-PT / all | 2580/2974 · 2565/2974 · 110573/154648 | — | — | not attempted: no released eval code/config located |

### The integration had a real fidelity defect

The adapter serialized object states with `json.dumps(sort_keys=True, separators=(",",":"))`,
while Julia's own `julia/data.py:sequence` uses `json.dumps(state, ensure_ascii=False)` — default
separators, insertion order. All 400 typed-decisions cases are dict states, so **400/400 rendered
differently** and the adapter scored **1300/2000** against the published **1451/2000**. After
matching the runtime's rendering the adapter scores **1451/2000 exactly**. Given identical text,
parity is 400/400 top-label with max probability delta 5.2e-07 (FP32 rounding only).

### Device vs precision

`cuda-fp32` reproduces `cpu-fp32` on 1999/2000 questions. `cuda-bf16` differs on 24/2000 (1.20%)
and lands within 3 of the historical H200 BF16 total, with `noul` matching exactly (484). The
published CPU/GPU difference is **bfloat16 autocast, not the device**.

### Representation does not rescue z0, and dev-tuning actively hurts

Splits are by `group_id` so paraphrases never straddle dev/sealed. Best-on-dev variants
(A2 state framing +0.068, B1 short labels +0.114) both **lost** on sealed. The dev-selected
combination is worse than the current representation on sealed:

| corpus | baseline sealed | dev-selected combo (A2/B1/D2) sealed | Jev on same rows |
|---|---:|---:|---:|
| authored144 | 0.5179 | **0.4464** | 0.9286 |
| perturbations108 | 0.3000 | **0.2000** | 1.0000 |

That is prompt overfitting, not signal.

### No usable escalation signal

All inference-time uncertainty measures are at or below chance on sealed (AUROC):
`p1` 0.470, `softmax_margin` 0.464, `logit_margin` 0.466, `entropy` 0.469 (authored144);
0.503/0.503/0.508/0.487 (perturbations108). Coverage at a ≤10% empirical error budget is **1.79%**
and **0.00%**. Accuracy by confidence decile is non-monotonic and the **most confident decile has
the lowest accuracy (0.17)**. Temperature scaling improves ECE (0.39→0.17, 0.60→0.32) but cannot
repair ranking, which is the only thing a cascade needs.

> **Circular measures.** NLL and Brier report AUROC 1.0000. They are proper scoring rules that take
> the gold label as input, so they encode correctness by construction and are unavailable at
> inference. They are reported only to make the trap explicit; they are never escalation signals.

## Reproducing

```bash
export Z0INT_JULIA_PYTHON=$HOME/.z0int/julia-venv/bin/python
export Z0INT_JULIA_MODEL_DIR=$HOME/.z0int/models/julia_1

python benchmarks/julia_claims/parity.py text          # 400/400 state renderings differ
python benchmarks/julia_claims/parity.py parity --n 400
python benchmarks/julia_claims/parity.py adapter        # must print 1451/2000
python benchmarks/julia_claims/published_claims.py --dataset agnews
python benchmarks/julia_claims/published_claims.py --dataset emotiondair
python benchmarks/julia_claims/device_compare.py --n 2000
python benchmarks/julia_claims/representation_ablation.py --corpus authored144 --mode ablate
python benchmarks/julia_claims/representation_ablation.py --corpus authored144 --mode combo --combo A=A2,B=B1,D=D2
python benchmarks/julia_claims/calibration.py
```

## Redistribution

Raw dataset text is **not** committed. `typed-decisions-test.jsonl` and `*-manifest.jsonl` are
regenerable caches and are gitignored; reconstruct them from the pinned revisions and hashes in
`frozen_config.json`. Prediction files carry example IDs and text SHA-256 only.
