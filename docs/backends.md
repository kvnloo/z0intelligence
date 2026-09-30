# Decision backends

z0int owns setup, typed decision contracts, evidence, and routing.
Backends implement local (or remote) judgment engines behind one contract.

## Contract

- `DecisionRequest` — state + boolean / choice / score questions
- `DecisionResult` — complete per-question probability distributions
- `DecisionBackend` — `capabilities`, `health(load=…)`, `evaluate(request)`

Importing `z0int.backends.base` is stdlib-only (no torch).

## Decision roster (canonical)

`manifests/models.yaml` → `decision_roster.candidates` lists **Jev/System-One-style
decision backends** — not general LLM roster entries (Kimi/Qwen/GLM belong elsewhere).
Kerdoios sees installed weights as secondary `ResourceOffer`s; z0int remains source of truth.

| ID | HF / source | Status | Notes |
|----|-------------|--------|-------|
| `laya_421m` | `convaiinnovations/laya` | pinned | ~421M calibrated; CPU/MPS-friendly |
| `decider_2b` | `Mapika/decider-2b` | pinned | Qwen3.5-2B one-pass typed probs |
| `nanojev_06b` | `C-Tianyu/NanoJev` | pinned + adapter | parallel decision heads |
| `reflex` | browser / GitHub | optional | WebGPU demo; no HF pin yet |
| `system_one_4b` | `pngwn/system-one-qwen3.5-4b-scorer` | pinned | **CC-BY-NC-4.0** (non-commercial) |
| `openjev_06b` / `openjev_4b` | Qwen base + direct logits | pinned | OpenJev substrate |

Next: benchmark all candidates on one capability contract (`rlm.worker_needed`, tool
select, retry/escalate, intent route, compression gate) and maintain a Pareto table
(p50, accuracy, calibration, VRAM, platform).

## Local vs remote

| Backend | Kind | Notes |
|---------|------|-------|
| `nanojev` | local semantic model | Pinned HF bundle `nanojev_06b`; CUDA V0 |
| `laya` / `decider` / `system_one_4b` | manifest candidates | Adapters TBD; weights via `z0int models sync` |
| `reflex` | browser / WebGPU | optional; no torch load path yet |
| OpenJev / vLLM / MB / fly | existing lanes | Not rewritten in the first backend PR; thin adapters later |

Probabilities that sum to one are **complete normalized distributions**.
Calibration is checkpoint/task dependent — do not treat “sums to 1” as calibrated.

## Observer semantics

A `DecisionBackend` is a transport/runtime contract, not an authority claim.

The same backend may be used in two different roles:

- **observer/reference** — read state and emit typed probabilistic features/weak labels for replay and evaluation;
- **runtime specialist** — make a bounded live decision only after that question family has independently passed its promotion gate.

JEV is the initial semantic observer/reference backend for cross-backend evals. JEV agreement alone is not verified truth. NanoJev/OpenJev/rules/statistical models should be compared against independent outcomes and verifiers, not promoted merely for matching JEV.

See [observer-evaluation.md](observer-evaluation.md).

## NanoJev install

```bash
z0int onboard --auto --sync-models   # or: z0int models sync
# managed path:
#   ~/.z0int/models/nanojev_06b/{best.safetensors,config.json,tokenizer/,backbone_config/}
# pin: C-Tianyu/NanoJev@4a19595eada0857133c0d2be024f879a4077054b
```

Override checkpoint: `Z0INT_NANOJEV_CHECKPOINT=/path/to/bundle`.

## CLI

```bash
z0int backends list --json          # no GPU load
z0int backends doctor --json        # filesystem/config only
z0int backends doctor --load        # explicit weight load
z0int backends eval --backend nanojev --input tests/fixtures/nanojev_request.json --json
```

## Ready means

- **configured** — backend registered / path known
- **ready** — checkpoint complete on disk
- **loaded** — weights resident in process

Ordinary `z0int doctor` never loads NanoJev weights.


## Pareto benchmark

Compare roster candidates on one contract (no OMP authority, no Kerdoios routing):

Pre-Pareto gates (v2 Pareto report):

- **unsafe:** `dangerous_false_rate > 0` → excluded
- **competence:** verified accuracy must exceed trivial random baseline + margin (and optional production floor)
- **evidence:** `PROVISIONAL` until ≥50 labeled examples per capability — not Pareto-eligible

```bash
z0int backends bench --contract decision-capability-v1
z0int backends bench --backend nanojev_06b --capability rlm.worker_needed --json
```

Artifacts land under `results/decision-backends/<timestamp>/`:

| File | Role |
|------|------|
| `tokenomics-events.jsonl` | **Canonical raw measurement** (Tokenomics traces) |
| `run-manifest.json` | Run identity: git SHAs, dataset hash, device env |
| `raw.jsonl` | Materialized compatibility view (derived from Tokenomics) |

Benchmark persistence is **event-sourced**: Tokenomics events are canonical;
`raw.jsonl` is the only compatibility projection. There is no parallel inline
row builder.
| `summary.json` | Aggregate metrics + eligibility + Pareto |
| `analytics.json` / `analytics.md` | Quality, safety, latency, paired comparisons |
| `coverage.json` / `coverage.md` | Measurement coverage gaps (Tokenomics) |
| `pareto.md` | Human-readable Pareto report |
| `bootstrap.json` / `bootstrap.md` | Pareto inclusion probability under fixture resampling |
| `uncertainty.json` | Accuracy CI + dangerous-false upper bounds |

Install analytics dependency: `pip install -e ".[analytics]"` (pins `agent-tokenomics`).

Unavailable backends report `status=unavailable` with a concrete reason — never silently skipped.

## Laya adapter (`laya_421m`)

Pinned checkpoint: `convaiinnovations/laya@7c76b622dfc5cac71b2dc1c29873efe2ce509a05`.

Architecture (from upstream `laya` runtime + HF bundle):

- ModernBERT-large encoder (~421M decision head bundle in `model.safetensors`)
- Non-autoregressive: one forward pass scores `[MASK]` markers per declared option
- Question types: `choice`, `score`, `noul` (boolean maps to `noul`)
- Emits calibrated option probabilities + confidence (RLCD-trained)
- CPU/MPS/CUDA supported; default bench device is CPU via `Z0INT_LAYA_DEVICE=cpu`
- License: Apache-2.0 (commercial use OK)
- No generative decode; `usage.input_tokens` is encoder token count only

```bash
Z0INT_LAYA_DEVICE=cpu z0int backends bench --backend laya_421m --contract decision-capability-v1
```

## Decider adapter (`decider_2b`)

Pinned checkpoint: `Mapika/decider-2b@1d96be0093133e194fe18105a521b3e69be931d2`.

Architecture (from upstream bundled `decider/` runtime + HF bundle):

- Base: `Qwen/Qwen3.5-2B-Base` (~1.9B), fully fine-tuned for typed decisions (v8)
- Single-pass: one forward pass reads option-letter logits at `Answer k: (` slots
- Jev-shaped API: `system_one(state, questions)` with choice/score/noul types
- Emits calibrated probabilities + confidence/certainty; no text generation
- CUDA/MPS preferred; default bench device CUDA via `Z0INT_DECIDER_DEVICE=cuda`
- Optional CUDA graphs via `Z0INT_DECIDER_USE_GRAPHS=1` (default on CUDA)
- License: Apache-2.0 (commercial use OK)
- Requires `flash-linear-attention` for Qwen3.5 linear-attention layers on GPU

```bash
Z0INT_DECIDER_DEVICE=cuda z0int backends bench --backend decider_2b --contract decision-capability-v1
Z0INT_LAYA_DEVICE=cpu z0int backends bench --backend decider_2b,nanojev_06b,laya_421m --contract decision-capability-v1
```

## OpenJev adapter (`openjev_06b`)

Pinned checkpoint: `Qwen/Qwen3-0.6B@c1899de289a04d12100db370d81485cdf75e47ca`.

Architecture (via bundled `openjev_phase1/direct.py` scorer):

- Base: `Qwen/Qwen3-0.6B` (~0.6B params), unpinned causal LM used as direct-logit substrate
- Single-pass: one forward pass reads uppercase option-letter logits at declared answer slots
- Prompt: chat template with JSON `{evidence, criterion, options}` user payload
- Emits softmax-normalized option probabilities; logits preserved in `DecisionResult.diagnostics`
- CUDA only at load time (`load_causal_model` requires exactly one visible GPU)
- No generative decode; `usage.input_tokens` is encoder token count only
- License: Apache-2.0 (Qwen3-0.6B base weights; commercial use OK)

Cache weights:

```bash
z0int models sync --which on_demand   # includes openjev_06b
# or: huggingface-cli download Qwen/Qwen3-0.6B --revision c1899de289a04d12100db370d81485cdf75e47ca
```

```bash
Z0INT_LAYA_DEVICE=cpu z0int backends bench --backend openjev_06b --contract decision-capability-v1
Z0INT_LAYA_DEVICE=cpu z0int backends bench \
  --backend openjev_06b,decider_2b,nanojev_06b,laya_421m --contract decision-capability-v1
```


## Running the roster on a CUDA-less host (CPU + Vulkan)

Every in-process backend has a CPU path; pick it per backend:

| Backend | Device switch | CPU numerics |
|---|---|---|
| `laya_421m` | `Z0INT_LAYA_DEVICE=cpu` (default) | FP32 |
| `decider_2b` | `Z0INT_DECIDER_DEVICE=cpu` | bf16-resident weights, FP32 compute |
| `nanojev` / `nanojev_06b` | `Z0INT_NANOJEV_DEVICE=cpu` (default `cuda:0`) | FP32 (bf16 autocast is CUDA-only) |
| `openjev_06b` | `Z0INT_OPENJEV_DEVICE=cpu` (default `cuda`) | FP32 weights + compute |
| `julia_1` | `Z0INT_JULIA_DEVICE=cpu` (default) | FP32, separate worker interpreter |
| `llama_http` | whatever the llama.cpp server runs on (Vulkan, Metal, CUDA, CPU) | GGUF quant |

Set `OMP_NUM_THREADS` to bound torch's CPU threads on shared hosts.

`julia_1` looks for its interpreter in `Z0INT_JULIA_PYTHON`, then `~/.z0int/julia-venv`
and for its checkout in `Z0INT_JULIA_MODEL_DIR`, then `~/.z0int/models/julia_1` (both
created by `scripts/setup-julia.sh`), then the HF cache.

`laya_421m` needs the upstream runtime pinned in `requirements.txt` (`laya==0.1.6`).

## `llama_http` server URL and GGUF arms

The URL resolves in this order:

1. `Z0INT_SLM_URL`
2. `backends.llama_http.url` in `~/.z0int/config/z0int.json` (honours `Z0INT_HOME`)
3. `http://127.0.0.1:11510`

Hosts whose server binds a bridge address set it once instead of exporting it in every
shell (the MBP lab host's `z0-slm.service` binds the kind gateway):

```json
{"backends": {"llama_http": {"url": "http://172.21.0.1:11510"}}}
```

`z0int backends doctor --json` reports where the URL came from (`diagnostics.url_source`).

**Arms.** `backends.llama_http.arms` names GGUFs so several can be benchmarked as separate
roster candidates (`llama_http:<arm>`). Each arm sends its `model` id in every request, which
is how a llama.cpp *router* server (`llama-server --models-dir DIR --models-max 1`) picks,
loads and swaps the GGUF — one model resident at a time, so a 2 GB GPU can serve every arm
in one bench run. `health(load=True)` forces the load, so the bench's cold-load column
includes the swap.

```json
{"backends": {"llama_http": {"url": "http://172.21.0.1:11510", "arms": {
  "hammer21_15b_q4": {"url": "http://127.0.0.1:11520", "model": "hammer2.1-1.5b-q4km",
                      "device": "vulkan0", "license": "cc-by-nc-4.0",
                      "sha256": "567555510e0c72d69a5fe17a6d3a391456f4471eac97ecaf840e701161703555",
                      "source": "mradermacher/Hammer2.1-1.5b-GGUF@d341431 Hammer2.1-1.5b.Q4_K_M.gguf"}}}}}
```

The router's model ids are the GGUF file stems in `--models-dir` (symlinks are fine).
`scripts/bench-host-roster.sh` stops the host's `z0-slm` service, starts a router on
`127.0.0.1:11520`, runs `z0int backends bench` over the CPU backends plus every arm, and
restarts `z0-slm` on exit.

The bench roster now also includes `julia_1`, `llama_http` and every configured
`llama_http:<arm>`. VRAM is read from `nvidia-smi` or, on amdgpu, from
`/sys/class/drm/card*/device/mem_info_vram_used` (device-level, like `nvidia-smi`).
RAM is the candidate's current RSS (plus declared out-of-process workers) sampled after
each example, not the process-lifetime `ru_maxrss` high-water mark, which made every later
candidate in a multi-backend run inherit the earlier ones' peak.
