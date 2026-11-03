# Weight-space programming evaluations

Source: [Sparks of Weight-Space Programming](https://rikiyat.github.io/blog/weight-space-programming/),
Takehi, Teoh, Zhang & Khattab, November 2026. Upstream experiment logs:
`RikiyaT/lm_understand-logs` at `4d09ba6d6a7e891dd8063974dfe81b3d78e3494f`.
Upstream logs/networks are CC BY 4.0; MinAtar source is GPL-3.0 (see upstream LICENSE).
Downloaded source stays outside Git. New results, replies and small policy networks are under `results/`.

## Scope and status

Two separate studies, **not interchangeable**:

1. **Released-task replication**: raw-weight game identification (4 tasks), diffusion-shape identification
   (3 tasks), and writing MinAtar policy weights from scratch (3 games). Five editors: Astra, Argon,
   DeepSeek V4 Pro, Opus 5.5, Fable 5.1. These are small numerical networks, **not SLM weight improvement**.
2. **SLM editing pilot**: Qwen3.5-0.8B, Qwen3-0.6B and Gemma3-270M-it, each with those five editors.
   Four feedback passes, held-out evaluation on 32 MATH-500 + 32 GSM8K test problems, one sample each,
   max 4096 new tokens. This is **smaller and lower-powered than the paper**, whose Qwen run used
   50 passes and 32768-token generations. No full-paper math improvement claim is justified by this pilot.

The SLM runner is experimental. Generation and sandbox smoke tests have run, but full SLM-editing results
must exist under `results/slm/` before claiming a successful matrix. A provider reply or an edit file is
not evidence of improved math accuracy.

## Model routes

| editor | requested model | route |
|---|---|---|
| Astra | `gpt-6-astra` | native Codex CLI, paper's no-tools config |
| Argon | `gemini-4-argon-high` | agy, sandbox/plan mode; audit events for tools |
| DeepSeek | `deepseek-v4-pro` | raw DeepSeek API, no tools offered |
| Opus | `claude-opus-5-5` | Anthropic Messages API with existing Claude OAuth, no tools |
| Fable | `claude-fable-5-1` | same |

No provider/model substitution. Anthropic requires its Claude Code identity system line in addition to
the neutral paper instruction. agy has its own system/tool catalog even though the prompt forbids tool
use: this is not system-prompt parity. Its CLI can auto-continue after an output cap; those rows must be
labelled as multi-segment, not strictly one provider completion.

First runs used a 64k API output cap; some models spent all of it thinking without returning weights.
Those attempts were followed by a 128k run. The frozen grid contains the later artifact where retried;
upstream attempts are not all independently retained. **This is exploratory, not a preregistered equal-budget
ranking.** `20261103-high` is a separately labelled high-effort follow-up, not a replacement for xhigh.
An empty completed response is a no-answer failure; the all-zero policy's return is not credited to an editor.

## Running

From the repository root:

```sh
uv venv .venv-wsp -p 3.12
uv pip install -p .venv-wsp numpy minatar==1.0.15 anthropic huggingface_hub pyarrow math-verify pytest
# Inject ANTHROPIC_TOKEN / DEEPSEEK_API_KEY securely from the owner's secret broker; do not put values in argv.
.venv-wsp/bin/python benchmarks/weight_space/run.py --run-id NEW_ID --tasks exp1,exp2,exp5
.venv-wsp/bin/python benchmarks/weight_space/run.py --run-id NEW_ID --rescore
.venv-wsp/bin/python benchmarks/weight_space/report.py --run-id NEW_ID
.venv-wsp/bin/python -m pytest -q tests/test_weight_space.py
```

SLM runner (CUDA GPU; keep host memory headroom; never disable the memory guard):

```sh
uv venv .venv-wsp-gpu -p 3.12
uv pip install -p .venv-wsp-gpu torch transformers safetensors numpy scipy scikit-learn pandas \
  sympy tokenizers anthropic huggingface_hub pyarrow math-verify accelerate
.venv-wsp-gpu/bin/python benchmarks/weight_space/slm/matrix.py --run-id NEW_PILOT_ID --passes 4
```

vLLM was installed and tested but its host footprint triggered the host's memory guard. The current
GPU runner uses transformers BF16, batches of 2, an explicit presence-penalty processor and shared GPU
lock. It waits for >=6GB available RAM before starting a forward job, and fails visibly after 20 minutes
without capacity. CPU tensor analysis also waits for memory; unrelated GPU services are left untouched.

Paths:
- Source cache: `$Z0INT_HOME/cache/weight-space/lm_understand-logs`
- Raw provider streams / private credentials / SLM weights: `$Z0INT_HOME/state/weight-space/`
- Portable results: `benchmarks/weight_space/results/`
- Default `Z0INT_HOME`: `~/.z0int`

Gemma uses the ungated `unsloth/gemma-3-270m-it` mirror at a pinned revision because the owner's HF
credential could not read Google's gated repository. It is labelled as a mirror, not a byte-verified
copy of the paper's original checkpoint.

## Measurement and isolation limits

- Read scoring is open-answer keyword matching on the requested Game/Images field. Unicode dashes are
  normalized. Ambiguous/negated answers still warrant human inspection; automated totals are not a semantic judge.
- MinAtar scoring matches version, channel mapping, action sampling, sticky probability and episode cap.
  Seeds differ from the paper; original PPO checkpoints are not released, so PPO columns cite the paper,
  not independently rerun baselines. Per-model authored policies are one stochastic editor attempt each.
- The edits parser is non-executable: literals and slices only. All three published policy replies rebuild
  their released `.npz` tensors bit-for-bit in tests. Invalid statements are logged, not secretly repaired.
- SLM Python code runs in bubblewrap with no network/GPU, read-only model and practice data, and writable work.
  Model views contain **only the selected model**, not the HF cache (which also contains test datasets).
  Final norm, embeddings/output head, pure tensor rescales and shape/nonfinite edits are rejected by helpers.
- No-training/no-hidden-forward rules are instructions, **not a proven execution-security boundary**. The Python
  sandbox has numerical libraries and writable files; an adversarial editor could bypass a helper. Audit code
  and submitted tensors before accepting results. Do not label this an adversarially secure benchmark.
- HF generation uses fixed batch-level seeds, not per-sequence vLLM RNG parity. Paired original/edited runs use
  identical ordered batches and seeds, but RNG drift is possible after diverging generations.
- Holdout accuracy, truncation rate, token use and paired differences are reported separately. Lower truncation
  alone is not math improvement. Small pilot deltas are not established gains without uncertainty analysis.
