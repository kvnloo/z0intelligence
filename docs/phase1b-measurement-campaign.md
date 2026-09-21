# Phase 1B measurement campaign

Phase 1B exists to find out which Phase 1 findings survive repeated,
production-path measurement. Nothing in it trains a router, tunes a threshold,
promotes an artifact, or changes runtime authority.

## The problem it addresses

The Phase 1 teacher table held **522 `(state, arm)` cells, of which 460 had a
single observation**. The frozen non-inferiority gate cannot distinguish "this
arm is worse here" from "we measured this arm once", so most Phase 1 ownership
decisions were, arithmetically, sample noise. Everything below exists to fix
that before anything is trained on it.

## What is measured, and through which path

Every model call goes through the **production supervisor** — `z0int cognition
serve` on `http://127.0.0.1:11500` — which holds exactly one resident
`llama-server` and swaps on request. Nothing is measured against a second
runtime: Phase 1 already showed a ~34x latency gap between runtimes on identical
weights, so a label taken from anywhere else would describe the runtime rather
than the model.

Ollama remains only as `up-legacy` and is never a label source.

## Run identity and freezing

`scripts/phase1b_freeze.py --run-id <id>` pins, before any measurement:

| pinned | where it is recorded |
|---|---|
| model manifest + HF revisions | `baseline/freeze.json` → `models` |
| GGUF sha256 + byte size per model | `baseline/freeze.json` → `models[].gguf` |
| fixture revisions (path + sha256 + row count, per owning repo) | `baseline/freeze.json` → `fixtures` |
| frozen gate + utility + ladder config | `baseline/freeze.json` → `frozen_config` |
| escalation thresholds | `baseline/freeze.json` → `frozen_config.escalation` |
| production serving map | `baseline/freeze.json` → `serving` |
| git SHA, branch and dirty-file list, per participating repo | `baseline/freeze.json` → `repos` |
| every harness file that produced the numbers | `baseline/freeze.json` → `harness_files` |
| the raw Phase 1 `raw.jsonl` receipts | copied into `sources/` and hashed |

A measurement run refuses to start if the fixture hash has drifted from the
freeze, so a fixture edit cannot silently reinterpret an existing run.

Fixtures are **referenced, never forked**: the freeze records the owning repo and
path. AODL keeps the legality/authority contract, Tokenomics the physical
accounting fixtures, z0intelligence the benchmark manifests and this harness,
Evolution Lab the experiment artifacts and splits. Raw *run receipts* are
different — they live in scratch space that the next run overwrites — so those
are copied into the immutable run directory.

## One receipt per call

`scripts/densify_measurements.py` writes one JSON object per model call to
`observations.jsonl`, append-only and resumable. It never aggregates: the
distribution is computed later, from the receipts.

Each receipt carries the full observation record: state identity and features,
the legal action set and every elimination, arm identity, model id / revision /
quant, compiler and router revision, residency class, `load_ms`, `ttft_ms`,
`decision_ms`, `total_ms`, tokens in/out, `tok_s`, resident VRAM, the selected
action and its distribution, confidence / margin / entropy, abstention, the
verification and correctness read, dangerous exposure and selection, retry and
failure state, raw utility components, the source fixture revision and a trace
id.

Physical accounting is recorded as observed and **not** reinterpreted: cost is
`null` and every receipt names `tokenomics` as the accounting authority. A second
opinion about cost inside Q-Route would be exactly the duplication the ownership
boundaries forbid.

## Cold, swap, resident and warm are different measurements

The supervisor reports `load_ms` and `cold` per response, so residency is read
from the serving stack rather than inferred from wall clock. Four classes are
kept apart:

| class | meaning |
|---|---|
| `cold_load` | nothing was resident; this call paid a full model load |
| `model_swap` | a *different* model was resident and was evicted for this one |
| `already_resident` | the requested model was already resident when the batch began |
| `warm_invocation` | a later call inside the same residency |
| `no_model_call` | deterministic arm; no model was involved |

This matters more than it sounds. Measured on the first pass: a cold load is
**~7-11 s** and a model swap is a **~10 s** penalty, against a warm decision of
**1.5-3.7 s**. A cascade that alternates models pays a swap per tier change, so
the residency class is part of the cost of choosing a ladder — not an
implementation detail.

## Sampling

`--reps 3` is the floor for every arm in a measured set. Adaptive top-up to
`--reps 10` is reserved for cells that can actually change a decision: on or near
the Pareto frontier, cells that move a Q-Route ownership call, cells that
trigger or refuse a gate escalation, high-variance cells, cells where arms
disagree on correctness, and cells within measurement uncertainty of a gate.
Obviously dominated arms are not exhaustively sampled.

## Arm naming

Densified arms carry an `arm_alias` that maps them onto the arm names the Phase 1
teacher table already uses (`SUB_compiler_hammer3b`, `A_qwen9b_alone`, …). Without
it, downstream analysis silently sees zero overlapping cells and reports
"undetermined" for every comparison.

Every arm is also marked `compiler_first` or not. The `unfiltered+*` arms are the
honest control: they are handed the unfiltered action set, including the declared
dangerous actions, so "dangerous selected" is a measurement rather than an
untested claim.

## What each script produces

| script | output |
|---|---|
| `phase1b_freeze.py` | the immutable baseline snapshot + `SHA256SUMS` |
| `densify_measurements.py` | `observations.jsonl` (one receipt per call) |
| `compiler_contract_eval.py` | compiler-conformance report + behavioural pass |
| `true_composition_eval.py` | per-stage receipts, ablation, composition report |
| `orchestration_eval.py` | 40-scenario orchestration report (12 regression + 28 new) |
| `phase1b_report.py` | the decision artifact: coverage, intervals, frontier, niches |
| `phase1b_campaign.sh` | runs the phases in sequence on the one GPU |

## Known measurement limits

* One 12 GB card and one supervisor, so phases are serialised: two campaigns at
  once would each measure the other's load.
* `max_tokens` is part of the measurement contract. Phase 1 measured the same
  fixtures at 256 and at 1024 and got materially different answers for the
  reasoning models, so the budget is frozen per run and recorded on every
  receipt.
* Generative arms return a one-hot distribution, so `confidence`, `margin` and
  `entropy` are only meaningful for scorer arms; `distribution_source` says
  which.
* Latency labels taken while the machine is doing other work are noisier than
  ones taken on an idle machine. The `cold_or_warm` split and the interval
  reporting are there so this cannot hide.
