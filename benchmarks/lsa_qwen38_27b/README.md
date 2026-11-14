# Latent Sparse Attention on Qwen3.8-27B (arXiv:2610.42147)

Reproduces the Qwen3.8-27B cells of *Latent Sparse Attention: Towards Subquadratic Ultra-Long Context
Attention* (Kubicek et al., 2026) with the authors' own released harness, image, maps and configs, on one H200.

| What | Pin |
|---|---|
| Paper | arXiv:2610.42147 |
| Code | Prefrontal-AI/latent-sparse-attention @ `97b82d695b74ca341965a16bbc2a951c8b0c567a` |
| Serving image | `ghcr.io/prefrontal-ai/sglang:lsa-v0.1.0` (SGLang fork tag `lsa-v0.1.0`) |
| Model | `Qwen/Qwen3.8-27b` @ `1d4bf0f2ff6012fd82039f2fa52739d0dd7c60c0` (the repo's asset card pin), bf16 |
| D-LSA map | release `d-lsa-weights-v1`: `qwen38_27b__long-16k-1M-heavy-key-and-query-d2048.pt` (+ the PCA step-0 map) |
| Hardware | 1x NVIDIA H200 (Modal), the paper's `tp1` mesh |

## Experiments

| ID | Paper section | Config (repo-relative) | Cells |
|---|---|---|---|
| A1 | 4.2 accuracy, Full Attention | `configs/evaluation/accuracy/qwen38_27b/full_tp1.yaml` | RULER 13 tasks x 100 rows at 4k and 32k |
| A2 | 4.2 accuracy, D-LSA (Flat) | `configs/evaluation/accuracy/qwen38_27b/lsa_flat_tp1.yaml` | same |
| K1 | 4.3 one-layer timing, Full Attention | `configs/evaluation/kernels/qwen38_27b/full.yaml` | prefill + decode ladder |
| K2 | 4.3 one-layer timing, D-LSA (Flat) | `configs/evaluation/kernels/qwen38_27b/lsa_flat.yaml` | prefill + decode ladder |

A1/A2 are the README's "Reproducing the paper" accuracy cell (expected D-LSA Flat: 0.974 / 0.968 from a clean
checkout; paper 0.975 / 0.966). They are run with `data.suite=ruler` and the 26 RULER cell names, exactly as the
repo README's command.

## What differs from the paper, and why

- **Launch layer.** Slurm + enroot is replaced by Modal: the container *is* the serving image, `run.runner=docker`
  is answered by [`docker_shim.py`](docker_shim.py) (identity mounts only; anything else is refused), and
  [`driver.py`](driver.py) calls the harness's own `run_experiment` in-process instead of submitting it.
  Server flags, suite, scoring and records are the harness's own.
- **Scope.** The 27B battery's other cells (MMLU-Pro, GPQA-Diamond, AIME25, MATH500 with 163,840-token reasoning,
  RULER 128k–10M, LongBench v2, PaperTrail, DCP8/TP8 production serving, Block Sparse baselines, Tree and
  Training-Free arms) need 8-GPU nodes or tens of GPU-hours each and are not run here. Section 4.4 (N-LSA native
  pre-training on 100B tokens) and the 48k-step distillation are not re-run: the released map is used.
- **Kernel prompt.** The paper's `gutenberg_300k.txt` is not released; War and Peace (Project Gutenberg #2600,
  public domain) cut to 300,000 Qwen tokens stands in for it. Prefill and decode timings depend on the
  context length, not the text, for full attention; D-LSA's selection reads the keys, so its timings may shift.
- **Kernel ladder.** Contexts are capped by budget (see run status); every reported speedup names its context.

## Reproduce

```bash
pip install modal            # >= 1.6
modal run benchmarks/lsa_qwen38_27b/modal_lsa.py::main --step probe
modal run benchmarks/lsa_qwen38_27b/modal_lsa.py::main --step fetch
modal run benchmarks/lsa_qwen38_27b/modal_lsa.py::main --step ruler
modal run --detach benchmarks/lsa_qwen38_27b/modal_lsa.py::main --step evaluate --kind accuracy \
  --config configs/evaluation/accuracy/qwen38_27b/lsa_flat_tp1.yaml \
  --overrides "data.suite=ruler data.tasks=[<26 cells>]"
modal run benchmarks/lsa_qwen38_27b/modal_lsa.py::main --step kernel_prep
modal run --detach benchmarks/lsa_qwen38_27b/modal_lsa.py::main --step evaluate --kind kernels \
  --config configs/evaluation/kernels/qwen38_27b/lsa_flat.yaml
```

Results, records and the published study live in kvnloo/z0evals `studies/lsa-qwen38-27b-v0/`.
