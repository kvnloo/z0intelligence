# Evolution Lab integration

z0intelligence is the runtime/control plane: evidence-backed state, typed decisions, deterministic
legal-action compilation, receipts, harness adapters, and promoted mechanism execution. Historical
OpenJev code remains one backend/training substrate inside this repository.

[Evolution Lab](https://github.com/kvnloo/evolution-lab) is the **experiment/search engine**. It
compares candidate mechanisms against frozen evidence and outcome contracts, then exports credited
artifacts back to z0intelligence. Fly/mushroom-body specialists are one candidate family, not a
mandatory production layer.

```
z0intelligence runtime / receipts / replay
                    ↓
             Evolution Lab study
      rules · retrieval · linear · MB/fly
        local SLM · typed backend · ...
                    ↓
       promote / reject / deoptimize
                    ↓
      deploy credited mechanism back into z0int
```

## Who owns what

| Repo | Role | You use it when… |
| --- | --- | --- |
| **z0intelligence** (this repo) | Runtime/control plane: state, decisions, authority/receipts, compiler surfaces, backend experiments | You need runtime cognition, context, routing evidence, replay, or promoted mechanisms |
| **evolution-lab** | Evolution: genomes, locked splits, promotion ladder, DAgger, control tables | You want to search for a fly recovery specialist or JEV head that beats controls |
| **frontier-kb** | Claims vault / research notes | You need protocol, kill criteria, or receipts |
| **OMP extensions** | Live wiring (`typesafe-jev`, `flyforge-recovery`) | You want token-saving decisions inside Cursor / OMP today |

Evolution Lab may call the legacy OpenJev training backend for Track B genomes (`backend=openjev`). z0intelligence does **not** depend on Evolution Lab at runtime.

## Quick setup (recommended)

From the z0intelligence repo root:

```bash
bash scripts/setup-flyforge.sh
```

This will:

1. Create `.venv` (if missing) and `pip install -e '.[test]'` for z0intelligence
2. Clone [kvnloo/evolution-lab](https://github.com/kvnloo/evolution-lab) as a sibling (or use `EVOLUTION_LAB_DIR`)
3. Install evolution-lab editable (numpy-only core; OpenJev already satisfies `openjev_phase1`)
4. Lock P0 Hermes recovery splits and Jev Track B splits
5. Print smoke commands

Environment overrides:

| Variable | Default | Meaning |
| --- | --- | --- |
| `EVOLUTION_LAB_DIR` | `../evolution-lab` | Where to clone/install the lab |
| `EVOLUTION_LAB_REF` | `nightly` | Git branch to checkout |
| `HF_HOME` | unset | Hugging Face cache (set for Route B 4B models) |
| `SKIP_EVOLUTION_LAB=1` | — | z0intelligence-only install (no clone) |

## Manual setup

```bash
# 1. z0intelligence (this repo)
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[test]'

# 2. Evolution Lab (sibling)
git clone https://github.com/kvnloo/evolution-lab.git ../evolution-lab
cd ../evolution-lab && git checkout nightly
pip install -e .
cd -  # back to z0intelligence

# 3. Lock experiment splits (deterministic, in git)
python -m evolution_lab lock-splits
python -m evolution_lab lock-jev-splits
```

For GPU Route A Jev training, the legacy OpenJev substrate is already installed from this tree. You do **not** need
`pip install -e '.[openjev]'` on evolution-lab when this z0intelligence checkout is editable.

## Two evolution tracks

### Track A — Hermes recovery fly (`local_plasticity`)

Native NumPy in evolution-lab. 640-weight mushroom-body analogue for bounded recovery actions:

`{retry, restart_sandbox, escalate, noop, page_human}`

```bash
cd "$EVOLUTION_LAB_DIR"
python -m evolution_lab seed
python -m evolution_lab run --level 1
python -m evolution_lab table --level 1
python -m evolution_lab dagger-smoke --rounds 2
python -m evolution_lab recovery '{"action":"plan","event":{"kind":"tool_error","message":"timeout"}}'
```

Deploy path: OMP `flyforge-recovery` extension → cached `local_plasticity` student.

### Track B — JEV routing heads (`jev_tiny`, `jev_hf_head`)

Uses **this repo** as the training backend (`openjev_runner.py` in evolution-lab).

```bash
cd "$EVOLUTION_LAB_DIR"
python -m evolution_lab seed-jev
python -m evolution_lab jev-smoke --level 0   # needs CUDA + torch from OpenJev
```

Deploy path: train checkpoint → `openjev-predict` / OMP `typesafe-jev` at inference time.

## Export contract (lab → runtime)

| Artifact | Produced by | Consumed by |
| --- | --- | --- |
| P1 control table | `evolution_lab table` | Maintainers / receipts |
| Recovery advise JSON | `evolution_lab recovery` / `advise` | OMP `flyforge-recovery` |
| Jev checkpoint (`.pt`) | `openjev-train` via lab genome | `openjev-predict`, `openjev-eval` |
| Locked splits | `lock-splits`, `lock-jev-splits` | Reproducibility; do not re-sample |

Weights are not checked into either repo by default. Reproducibility = genome JSON + locked
splits + git SHA.

## Token-saving production stack

Use local specialists before subscription LLMs:

```
1. recovery_advise (fly, 640w)     → tool/harness failures
2. typesafe-jev / openjev-predict  → route, clarify, skill-pick
3. subscription LLM              → only on escalate / low confidence
```

## Smoke checklist

```bash
# OpenJev Route B zero-shot (needs GPU + HF cache)
CUDA_VISIBLE_DEVICES=0 openjev-score --mode direct \
  --model Qwen/Qwen3.5-4B \
  --revision 851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a \
  --input examples/decisions.jsonl --output /tmp/results.jsonl

# Evolution Lab unit tests (CPU)
cd "$EVOLUTION_LAB_DIR" && python -m unittest discover -s tests -p 'test_*.py'

# Jev Track B via lab (GPU, OpenJev installed)
python -m evolution_lab jev-smoke --level 0
```

## Sister links

- [Evolution Lab README](https://github.com/kvnloo/evolution-lab/blob/nightly/README.md)
- [Route A trainable scorers](jevlike-trainable-route.md)
- [FlyForge roadmap](https://github.com/kvnloo/evolution-lab/blob/nightly/ROADMAP.md)
- [frontier-kb](https://github.com/kvnloo/frontier-kb)
