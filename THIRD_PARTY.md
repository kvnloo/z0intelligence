# Third-party material

No model weights or third-party raw evaluation records are distributed here.

| NanoJev (legacy runtime port) | https://github.com/TianyuCodings/NanoJev (fork baseline kvnloo/NanoJev) | model `C-Tianyu/NanoJev@4a19595eada0857133c0d2be024f879a4077054b`; code port in `src/z0int/backends/nanojev_runtime.py` | MIT; retained for benchmark/reproducibility only, not as the production verification default. |
| jevlike trainable scorers | https://github.com/vinnylarouge/jevlike | `main` at port time | Route A code in `src/openjev_phase1/jevlike/`; MIT. |
| vLLM structured diffusion reads | https://github.com/vllm-project/vllm/pull/57250 | open at port time | Route C client only; Apache-2.0 upstream. |
| Item | Upstream | Pinned revision | Note |
|---|---|---|---|
| Qwen3.5-4B | https://huggingface.co/Qwen/Qwen3.5-4B | `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` | Direct-logit baseline; check upstream model license. |
| Qwen3-Reranker-4B | https://huggingface.co/Qwen/Qwen3-Reranker-4B | `22e683669bc0f0bd69640a1354a6d0aebcfeede5` | Native reranker baseline; Apache-2.0 on its model card. |
| TypeSafe public evaluations | https://evals.typesafe.ai/ | Exact case URLs and snapshot hashes are in `benchmarks/fetch_sources.py`; no redistribution grant inferred. |
| Every parallel judgment lab | https://typesafe-parallel-judgment-lab.every-4573.chatgpt.site/ | [Experiment JSON](https://typesafe-parallel-judgment-lab.every-4573.chatgpt.site/downloads/experiments.json) and [source archive](https://typesafe-parallel-judgment-lab.every-4573.chatgpt.site/downloads/typesafe-lab-source.zip) are public downloads. |
| WANLI | https://huggingface.co/datasets/alisawuffles/WANLI | Pinned at `61c95318fd71c55b6ba355d76253254615f387ec`; CC-BY-4.0. |
| WebLLM | https://github.com/mlc-ai/web-llm | `0.2.85` | Browser inference runtime; Apache-2.0. |
| Vue | https://github.com/vuejs/core | `3.5.21` | Browser demo UI runtime; MIT. |
| Material Symbols | https://fonts.google.com/icons | Google Fonts CDN | Browser demo icons; Apache-2.0. |
| MLC Qwen3-0.6B build | https://huggingface.co/mlc-ai/Qwen3-0.6B-q4f16_1-MLC | Catalog entry from WebLLM `0.2.85` | Quantized browser model; upstream terms apply. |
| MLC Qwen3.5-0.8B build | https://huggingface.co/mlc-ai/Qwen3.5-0.8B-q4f16_1-MLC | Catalog entry from WebLLM `0.2.85` | Quantized browser model; upstream terms apply. |

These URLs returned HTTP 200 on 2026-09-16. `benchmarks/fetch_sources.py` refuses content whose SHA-256 differs from the evaluated snapshot.

TypeSafe and Jev are marks of their respective owner. OpenJev is unaffiliated. OpenJev code is provided under the repository's MIT License; third-party models and material retain their upstream terms.
