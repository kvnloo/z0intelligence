# Julia-1 as a z0int decision backend

`SupersonicLabs/Julia-1` is a 144.3M finite-choice decision model (Apache-2.0, base
`jhu-clsp/mmBERT-small`). It is not a chat model and not a Transformers `AutoModel`: it scores
2–20 supplied options for a typed question about a state and returns full softmax probabilities.

**Verdict up front: installed, adapted, measured, and not adopted.** On the project's own decision
corpora it is 53–71 percentage points behind Jev 1.13.0 while being ~9.5x faster, and it is
*confidently* wrong, so it cannot even serve as a gated cheap stage. Registration is default-off;
nothing in the routing path changes unless an operator selects it. See
[Where it does not belong](#where-it-does-not-belong-measured).

| | |
|---|---|
| pinned revision | `a85b127321d580d65176c89ced8273f305745d85` |
| `model.safetensors` sha256 | `df853bf7fe424420011f3d0c47a05d7341aa9eefa7fb9f203ea4aada4ad95b72` (verified against `provenance.json`) |
| backend id | `julia_1` (aliases `julia`, `julia-1`) |
| runtime pin | `torch>=2.6`, `transformers>=5.0,<5.1`, `safetensors>=0.5`, `numpy>=1.26`, Python ≥3.11 |
| measured on | torch 2.14.0+cpu / transformers 5.0.0, and torch 2.10.0+cu128 / transformers 5.0.0 |

## Install

```bash
scripts/setup-julia.sh                       # ~/.z0int/julia-venv + ~/.z0int/models/julia_1
export Z0INT_JULIA_PYTHON=$HOME/.z0int/julia-venv/bin/python
export Z0INT_JULIA_MODEL_DIR=$HOME/.z0int/models/julia_1
```

The script verifies the checkpoint hash and refuses to continue on a mismatch. Download the
**complete repository**: Julia ships its runtime in `julia/` and reads `julia_config.json`, so
`model.safetensors` alone is not a working checkout.

## Why a separate process

Julia pins `transformers>=5.0,<5.1`; z0int runs 5.17, where Julia fails outright:

```
AttributeError: 'ModernBertModel' object has no attribute '_update_attention_mask'
```

`laya` and `nanojev` import their runtimes in-process. Julia cannot, so the engine lives in
`backends/julia_worker.py` under an interpreter where the pin holds, and `backends/julia.py` owns
the z0int-facing translation. The wire shape is the same named-question mapping the in-process
adapters use (`backends/named_questions.py`), so the routing layer sees a normal `DecisionBackend`
and nothing about it is Julia-specific.

Each worker call is one JSON line in and one out. A separate process also means a Julia crash or
timeout cannot take the service down.

## Type mapping

Julia's three interfaces are the same three z0int question types, under one alias:

| z0int | Julia | criteria | answer |
|---|---|---|---|
| `choice` | `choice` | 2–20 caller IDs → descriptions | winning ID + probabilities keyed by caller ID |
| `score` | `score` | ordered rubric list | **unrounded** expected zero-based index |
| `boolean` | `noul` | `{false, true}` descriptions, optional | `noul` = P(true) |
| `boolean` | `boolean` | `{false, true}` descriptions, optional | `noul` = P(true) |

Caller IDs survive the round trip, which is what makes the mapping lossless.

## Resource and latency profile

CPU, `strict_encoding=True`, `max_length=8192`, `head_length=512`, engine resident, 10-core i9:

| workload | p50 | p95 | throughput | RSS |
|---|---|---|---|---|
| 1× choice, 3 options | 21.2 ms | 23.3 ms | 43.9 req/s | ~1.02 GB after load |
| 3 questions (choice+score+noul) | 40.4 ms | 47.1 ms | 23.8 req/s | ~1.15 GB final |
| 1× choice, 20 options | 49.5 ms | 51.7 ms | 16.3 req/s | — |

CUDA is faster and *flat* — ~14 ms and ~71 req/s regardless of question count — at a cost of
**~825 MB VRAM** (`nvidia-smi`), ~578 MB allocated.

CPU load ≈ 5.0 s and ~740 MB RSS and is the same on CUDA. `JULIA_CPU_THREADS` defaults to 4.

### Placement changes decisions, not just latency

CUDA runs bfloat16 autocast; CPU runs FP32. On the same torch/transformers build, a borderline
2-option `noul` decision moved **7.5e-2** between bf16 and FP32 on CUDA, versus **2.7e-3** between
FP32 CUDA and FP32 CPU. The precision, not the device, is what flips answers:

| | choice | score | noul P(true) |
|---|---|---|---|
| CPU FP32 | `billing` | 2.361940 | 0.451277 |
| CUDA bf16 (default) | `billing` | 2.376470 | **0.523420** |
| CUDA autocast off | `billing` | 2.359867 | 0.448606 |

At 0.451 vs 0.523 the boolean verdict itself flips. **Placement is therefore a correctness
variable for this model.** If you deploy it, pin the device *and* `Z0INT_JULIA_AUTOCAST`, and
record both in the receipt — they are already in `DecisionResult.diagnostics`.

**Default is CPU.** 825 MB of a 12 GB card is a poor trade for 1.5–3.7x on a call that is already
~30 ms, when that VRAM is what lets `nanojev`/`openjev`/`system_one` stay resident.

## Limits

* **2–20 options, hard.** 21 options raises `ValueError: options must contain 2–20 nonempty
  rendered descriptions`. It does *not* truncate. Anything larger needs hierarchical narrowing
  (`julia/router/`), and a grouped result is **not** a global probability distribution.
* **`strict_encoding=True` refuses overflow** rather than truncating: reserved `<mask>` marker in
  the request, an option over 48 tokens, question/options over the head budget, or state over the
  context budget all raise. Non-strict mode silently truncates. We run strict.
* Latency is a property of the request (option count, tokenizer work), not of a fixed shape.

## Where it does not belong (measured)

Evaluated through `backends/julia.py` against the project's own labelled corpora, with Jev's
recorded predictions for the same ids as the incumbent. Both corpora are dominated by
`insufficient`/`contradicted` and `permitted`/`prohibited` option vocabularies.

| corpus | n | Julia | Jev 1.13.0 | delta | agreement | Julia p50 | Jev median |
|---|---|---|---|---|---|---|---|
| `authored144` | 144 | **41.7%** | **95.1%** | −53.5 pp | 40.3% | 28.6 ms | 267.6 ms (9.4x) |
| `perturbations108` | 108 | **28.7%** | **100%** | −71.3 pp | 28.7% | 31.0 ms | 301.7 ms (9.7x) |

Per family (`authored144` / `perturbations108`):

| family | Julia | Jev |
|---|---|---|
| `evidence_interpretation` | 47.9% / 27.8% | 95.8% / 100% |
| `rule_application` | 29.2% / **8.3%** | 95.8% / 100% |
| `candidate_selection` | 47.9% / 50.0% | 93.8% / 100% |

Of the `authored144` disagreements, Julia was right and Jev wrong in **4**, Jev right and Julia
wrong in **81**. On `perturbations108` Julia was never right where Jev was wrong.

The failure is systematic, not noise. Julia collapses `permitted`/`prohibited` onto `insufficient`
at p≈1.000:

> question: *The first-aid badge requires only first-aid training. May this guide receive it?*
> state: *The guide completed first-aid training but not mountain-navigation training.*
> gold `permitted` · Jev `permitted` · **Julia `insufficient` at p = 0.999994**

### Confidence is not usable as a gate

This is the part that rules out a cheap-L0 design. Julia's confidence is **anti-correlated with
correctness** on these corpora — mean max-probability 0.885 when right vs 0.889 when wrong
(`authored144`), 0.850 vs 0.900 (`perturbations108`). Gating on confidence makes accuracy *worse*:

| threshold | coverage | accepted accuracy (`authored144`) |
|---|---|---|
| none | 100% | 41.7% |
| ≥0.90 | 69.4% | 39.0% |
| ≥0.99 | 41.0% | **37.3%** |
| ≥0.999 | 25.9% | 21.4% (`perturbations108`) |

A "accept when Julia is confident, escalate when unsure" cascade would therefore **silently accept
Julia's wrong answers**, because its wrong answers are its most confident ones. That is a worse
failure mode than having no cheap stage at all.

## The incumbents, on the same corpus

`laya_421m` is the existing local fast path, and its weights were already cached, so it was run on
`authored144` too. Three-way, same 144 cases, same gold:

| model | params | accuracy | p50 latency | vs Jev |
|---|---|---|---|---|
| Jev 1.13.0 (remote, paid) | — | **95.1%** | 267.6 ms | — |
| Laya 421M (local) | 421M | 61.1% | 226.5 ms | −34.0 pp, 1.2x faster |
| **Julia-1 (local)** | 144M | **41.7%** | **28.6 ms** | −53.5 pp, **9.4x faster** |

Per family, Laya vs Jev: `evidence_interpretation` 66.7% / 95.8%, `rule_application` 60.4% / 95.8%,
`candidate_selection` 56.2% / 93.8%. Laya was right where Jev was wrong 3 times, Jev right where
Laya was wrong 52 times. No errors and no refusals: 144/144 answered.

Two things follow. First, the existing local fast path is **already** 34 points behind Jev while
buying only 1.2x — so the "cheap local layer" hypothesis was never strong on this corpus, and Julia
does not repair it. Second, Julia is the only candidate with a real latency story (7.9x faster than
Laya, 9.4x faster than Jev); its problem is accuracy and calibration, not speed.

*Not measured:* a 3B Qwen baseline. No 3B checkpoint is cached locally — the host has
`Qwen3.5-4B`, `Qwen3-0.6B`, `Qwen2.5-1.5B-Instruct` and `nanojev_06b`, and the roster's
`system_one_4b`/`openjev_4b` occupy that role. Running a 4B on GPU would not change the verdict:
Julia sits 19.4 points below the *worse* of the two measured incumbents.

## The instrument was verified first

A 41.7% score on a 3-way task is close enough to chance (33%) that the harness itself had to be
cleared before the result could be believed. Running the **authors' own** harness against the
pinned dataset reproduced their published CPU numbers exactly, so the adapter is faithful:

| type | reproduced | published (`metrics/typed-cpu-20260926.json`) |
|---|---|---|
| choice | 426 / 600 = 0.71 | 426 / 600 = 0.71 |
| noul | 483 / 600 = 0.805 | 483 / 600 = 0.805 |
| score | 542 / 800 = 0.6775 | 542 / 800 = 0.6775 |

Same weights sha256, same dataset revision and sha256, same torch/transformers/threads/encoding.
So Julia really does reach ~73% on the upstream typed-decisions distribution and really does reach
~29–42% on ours. The gap is distribution, not integration.

## Task mapping

**Ideal** — finite choice over a small closed set, where the *answer worths* resemble the ones
Julia was trained on and the decision is settled by the supplied context:
closed-set classification with short, parallel option labels; binary gates whose two criteria are
explicitly written out; ordering a short supplied rubric.

**Plausible — must be measured per workflow, never assumed**: model selection, tool selection and
route selection among ≤20 enumerated candidates. These are structurally ideal, but none is
validated yet, and the two corpora above are exactly this shape and failed. Treat any such use as
a shadow experiment until it has its own labelled set.

**Inappropriate** — everything in the graded column: supplying missing facts, arithmetic, long
reasoning chains, generation, and any decision that hinges on a vocabulary Julia has not been
trained on (here: `permitted`/`prohibited`/`insufficient` rule semantics). Also anything needing
more than 20 options without an explicit narrowing design.

## Observability

Every `evaluate()` returns a `DecisionResult` whose diagnostics carry `device`, `placement`,
`autocast`, `strict_encoding`, `max_length`, `head_length`, `load_ms`, `worker_ms`, `worker_pid`,
`worker_request count`, `question_count`, `runtime`, `model_sha256`, `probability_status` and
`option_limit` — plus `revision` on the result itself. Routing receipts therefore already capture
the fields needed to answer "Julia accepted → was it later overturned by Jev?" once Julia is put
in shadow, provided the receipt keeps the nested backend result rather than only the winning label.

## Reproducing these numbers

```bash
export Z0INT_JULIA_PYTHON=$HOME/.z0int/julia-venv/bin/python
export Z0INT_JULIA_MODEL_DIR=$HOME/.z0int/models/julia_1

# upstream control (must reproduce 0.71 / 0.805 / 0.6775)
python scripts/reproduce_typed.py --checkpoint "$Z0INT_JULIA_MODEL_DIR" \
    --output /tmp/typed-cpu --threads 4

# our corpora, through the adapter
python -m pytest -q tests/test_julia_backend.py
```

The corpus comparison harness lives outside the repo (it consumes generated artifacts); the
generated summaries are recorded in the accompanying evidence bundle.
