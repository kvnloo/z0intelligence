# Decision backends

z0int owns setup, typed decision contracts, evidence, and routing.
Backends implement local (or remote) judgment engines behind one contract.

## Contract

- `DecisionRequest` — state + boolean / choice / score questions
- `DecisionResult` — complete per-question probability distributions
- `DecisionBackend` — `capabilities`, `health(load=…)`, `evaluate(request)`

Importing `z0int.backends.base` is stdlib-only (no torch).

## Local vs remote

| Backend | Kind | Notes |
|---------|------|-------|
| `nanojev` | local semantic model | Pinned HF bundle `nanojev_06b`; CUDA V0 |
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
