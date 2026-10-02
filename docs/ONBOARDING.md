# z0int onboarding

**Canonical source.** Harness adapters (`AGENTS.md`, `CLAUDE.md`, …) point here.
Do **not** reproduce install steps from memory — run the CLI.

## Principle

> The LLM decides intent and handles genuinely semantic steps.
> Deterministic setup/training logic lives in code.

## One-command path

```bash
git clone https://github.com/kvnloo/z0intelligence
cd z0intelligence
./scripts/bootstrap.sh          # creates venv, installs package, runs doctor
z0int onboard --auto            # resumable; safe to re-run
z0int status
```

Or tell any coding agent:

> onboard this repo

The agent should:

1. Read **this file**.
2. Run `z0int doctor --json`.
3. Run `z0int onboard --auto` (add `--dry-run` first if unsure).
4. Report only **unresolved** user decisions from JSON `next` / `unresolved`.

## What `z0int onboard` does

Idempotent steps (state: `~/.z0int/state/onboard.json`):

| Step | Action |
| --- | --- |
| `layout` | Create `~/.z0int/{config,state,episodes,stream,specialists,…}` |
| `doctor` | Hardware / deps / discovery snapshot |
| `models.plan` | VRAM-aware resident vs on-demand plan → `~/.z0int/config/` |
| `models.sync` | Optional (`--sync-models`): download pinned HF revisions |
| `evolution_lab` | Clone/update [evolution-lab](https://github.com/kvnloo/evolution-lab) `@ nightly`, editable install |
| `omp_plugins` | Symlink `omp-extensions/*` → `~/.omp/agent/extensions/` if OMP exists |
| `data.discover` | Find Hermes `state.db`, OMP history, Codex home, export zips |
| `privacy` | Fail if personalized weights sit inside the git repo |
| `env_hints` | Write suggested `EVOLUTION_LAB_*` / `Z0INT_HOME` for harnesses |

Personal artifacts **never** default into the repo. Only `~/.z0int/`.

## Hard boundaries

```text
z0int        → cognition filter + user lifecycle (this CLI)
Evolution Lab → experiments / training / ABAB
Kerdoios      → residual compute allocation (optional)
Harness       → executes tools / models
```

- Kerdoios does **not** execute flies.
- z0int does **not** require Kerdoios.
- Do not commit `champion.npz`, raw histories, or vault material.

## After onboard

```bash
# if Hermes state.db was discovered
z0int-compile

# capability atlas (needs episodes + evolution-lab)
python -m evolution_lab capability-mine

# local cognition filter before residual LLM allocation
python -m evolution_lab preflight "your prompt"

z0int status
z0int doctor --json
# decision spine: emit → join world outcome → tokenomics
z0int receipt emit --capability-id coding.next_action --route local --avoided 1200 --prediction EDIT --confidence 0.9
z0int receipt join <trace_id> --test-pass true --success true
z0int receipt summary --json
```

## Models (12GB example)

```text
resident:   OpenJev 0.6B + local MB specialists
on-demand:  OpenJev 4B
never:      co-reside 0.6B + 4B on 12GB
```

Pins live in `manifests/models.yaml` and `manifests/models.z0int.json`.
`z0int models plan` / `z0int models sync` never invent unpinned IDs.

## Agent contract

| Do | Don't |
| --- | --- |
| `z0int doctor --json` then act | Paste multi-page bash from memory |
| `z0int onboard --auto` | Skip privacy checks |
| Prefer CLI over prose procedures | Invent unimplemented subcommands |
| Keep weights under `~/.z0int/` | Write specialists into `repo/data/` |

## Smoke (CI / fresh clone)

```bash
python -m z0int doctor --json
python -m z0int onboard --dry-run --skip-evolution-lab --json
python -m z0int models plan --json
python -m z0int status --json
```

Network, CUDA wheels, and HF downloads are **out of scope** for the default smoke.

## Current stack and optional activation

Read [the PR reconciliation audit](pr-reconciliation-2026-10-02.md) for current
master capabilities and reproducible offline verification. State Packets and
DecisionOpportunities are available through `z0int context packet` and the
existing harness shadow adapters. Memory modules do not migrate personal stores.

The local intelligence service retains canonical execution/receipt ownership.
AgentWeb paid shadow decisions, automatic routing, governed remote workers,
model downloads and model inference each require their existing explicit
configuration; installing the package is not a production activation.
Hermes can use the standalone [bend-native plugin](https://github.com/kvnloo/bend-native)
for native Bend verification and opt-in z0 shadow composition.
