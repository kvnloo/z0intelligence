# PrismML Ternary Bonsai 2 on the RTX 3080 Ti (12 GiB)

Four **optional, unmeasured** local-cognition candidates are registered:
`bonsai2_2b`, `bonsai2_4b`, `bonsai2_9b`, and `bonsai2_27b`.
These are **general tool-calling LLMs**, not typed probability scorers and not
substitutes for the Laya/Jev decision contract. Existing defaults are unchanged.

Source: [PrismML Bonsai 2](https://prismml.com/news/bonsai-2-family),
[model collection](https://huggingface.co/collections/prism-ml).
Credit PrismML and the upstream Qwen authors.

| ID | PTQ1_0 file (GB) | Staged PQ2_0 file (GB) | Intended experiments |
|---|---:|---:|---|
| `bonsai2_2b` | 0.43 | 0.52 | Tiny action / fast tool choice |
| `bonsai2_4b` | 0.95 | 1.15 | Function calling / orchestration |
| `bonsai2_9b` | 2.00 | 2.42 | General local reasoning and vision |
| `bonsai2_27b` | 5.95 | 7.21 | Higher-quality local fallback |

**These are on-disk model footprints, not measured GPU allocation or decode rates.**
All four *weights* fit in 12 GiB, but KV/cache, compute buffers, optional
vision projectors, desktop GPU use, and context length consume more.
Start with **4096 context, batch=1, one resident GGUF**. On a busy GPU,
27B may still OOM. Do not extrapolate M4 Max or Bonsai 1 throughput to Bonsai 2.

## 1. Build a compatible CUDA backend

**Use the PrismML [llama.cpp fork](https://github.com/PrismML-Eng/llama.cpp)
on its `prism` branch**; stock llama.cpp may not implement the activation
transform and ternary formats. Record `git rev-parse HEAD` with each receipt.

```bash
git clone --branch prism https://github.com/PrismML-Eng/llama.cpp.git ~/src/prism-llama.cpp
cmake -S ~/src/prism-llama.cpp -B ~/src/prism-llama.cpp/build -DGGML_CUDA=ON
cmake --build ~/src/prism-llama.cpp/build -j 8
```

The 3080 Ti is NVIDIA Ampere, so use CUDA rather than a CPU/Metal/WebGPU
throughput claim. Test **PQ2_0** first, then **PTQ1_0** as a separate benchmark
if memory or decoding speed warrants. The shipped roster stages PQ2_0 only.

## 2. Download one pinned GGUF (4B example)

The following does **not** download the whole model repository or all four
candidates. The same IDs/revisions and allowlist are in
`manifests/models.yaml` and `manifests/models.z0int.json`.

```bash
hf download prism-ml/Ternary-Bonsai-2-4B-gguf \
  Ternary-Bonsai-2-4B-PQ2_0.gguf \
  --revision 8d6557168168f91ea6697a4a49ed2ef622b53adb \
  --local-dir "$HOME/.z0int/models/bonsai2_4b"
z0int models plan --json
```

Alternately `z0int models sync --which on_demand` downloads **all**
on-demand models, which can consume substantial disk space and bandwidth.
An optional model is never downloaded by normal `doctor`/`status`.

## 3. Serve via the existing z0int supervisor

```bash
z0int cognition serve \
  --llama-server "$HOME/src/prism-llama.cpp/build/bin/llama-server" \
  --gguf-dir "$HOME/.z0int/models" --context 4096 --port 11500
```

This is a localhost OpenAI-compatible endpoint at
`http://127.0.0.1:11500/v1/chat/completions` and swaps one GGUF at a time.
Choose the requested `model` field (e.g. `bonsai2_4b`) in the client.
The existing `z0int cognition serve --print-serving-map` produces the model
IDs to register in `~/.z0int/config/serving.json`; it does not start inference.
If other GGUF families are needed from this same endpoint, stage/link them
under the chosen `--gguf-dir` as well.

## 4. Bench and gate before promotion

```bash
python scripts/probe_local_cognition.py \
  --model bonsai2_4b --context 4096 --repeats 3 \
  --binary "$HOME/src/prism-llama.cpp/build/bin/llama-server" \
  --gguf-dir "$HOME/.z0int/models" --json
```

Run the existing tool-calling and orchestration fixtures against the live
OpenAI-compatible endpoint. Record device/driver, PrismML runtime commit,
GGUF repo revision and packing, peak VRAM, warm TTFT, decode tok/s, p50/p95,
schema validity, trajectory correctness, invalid/dangerous actions, and
accuracy vs cost. Include cold-load/eviction and 4096/8192-context probes.

**Promotion gate:** leave `measurements: []`, `promotion_state: candidate`,
and all `role_defaults` untouched until actual RTX 3080 Ti receipts and
verification evidence exist. A healthy server or a vendor speed claim is
not a verified task result. Keep user-specific telemetry under `~/.z0int/`.
