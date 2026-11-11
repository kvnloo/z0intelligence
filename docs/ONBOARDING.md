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

## Selected steps, preview and resume

Use repeated `--step NAME` arguments for approval-driven callers. Names are the
exact step IDs in the table above; they run in canonical table order, regardless
of argument order. Unknown, empty or repeated names fail before checkpoint
access. No dependency step is added implicitly. Omitting `--step` on a **new**
run retains the complete setup pipeline, including evolution-lab and OMP links.

```bash
# Preview the selected effects; no probes, operations, directories or files.
z0int onboard --step layout --step models.plan --step privacy --dry-run --json

# Apply only these selected steps after approval.
z0int onboard --step layout --step models.plan --step privacy --json

# Downloads require both selection and the existing explicit download opt-in.
z0int onboard --step models.sync --sync-models --json

# Inspect an interrupted plan, then explicitly retry its unfinished steps.
z0int onboard --resume --dry-run --json
z0int onboard --resume --json
```

Preview reads the existing checkpoint, if any, and describes each selected
step's effect, `would_run`, skip reason and potential network access. It does not
call step functions, perform hardware/data discovery, compute a model plan,
write checkpoints or mark steps complete. Obtain actual hardware/model
information separately with `z0int doctor --json` and
`z0int models plan --json`.

Execution writes bookkeeping under `$Z0INT_HOME/state/` (default
`~/.z0int/state/`). Each selected file-writing step creates only its needed
parent directories. Only the `layout` step creates the full private tree.
Selecting `models.sync` without `--sync-models` remains a skip. Omitting
`models.sync` and `evolution_lab` from the selection prevents their downloads
and installation.

The checkpoint records the exact selection, effective options, completed
prefix, in-flight step and necessary context before operations run. Any failed
step stops the plan. An unfinished plan requires explicit `--resume`; a bare
invocation, a changed selection or a newly enabled option cannot expand it.
Resume restores the original flags, including `--sync-models`,
`--skip-evolution-lab` (also when originally supplied through
`SKIP_EVOLUTION_LAB=1`) and `--force`. A changed environment skip value does
not rewrite that recorded decision; newly setting `SKIP_EVOLUTION_LAB=1` blocks
a saved plan that would install it. An explicit `--step` list on resume must
match the original selection. Completed steps are not repeated, including
doctor and runs originally started with `--force`.

The validated resident model plan, evolution-lab destination and OMP source
and destination paths survive interruption. Resume checks the effective model manifest for downloads and the
evolution-lab source/ref/environment overrides, repository/state/user-home paths
and Python executable before retrying. Restore the original inputs if they changed.
Malformed or unsupported checkpoints fail closed. To deliberately abandon a
plan or recover irreparable state, stop all onboarding invocations, archive
`state/onboard.json` yourself, then preview and approve a new explicit selection.
The CLI never silently resets consent or removes that evidence.

Checkpoint replacement is atomic. If a process stops after an external effect
but before recording completion, `--resume` may repeat the in-flight operation:
retries are **at least once**, not exactly once. Review the saved plan before
retrying. Run only one onboarding invocation at a time per `Z0INT_HOME`;
atomic file replacement does not provide a concurrent-run lock.

Both `z0int onboard … --json` and `z0int --json onboard …` emit
`z0int.onboard_report.v1` with `selected`, `options`, `completed`,
`pending`, `results`, `failed` and `state_path`. Validation/checkpoint errors
include `error_code` and `error`; unsuccessful invocations exit nonzero.
A normal keyboard interruption returns a failed step report. A killed process
cannot emit final JSON; its durable checkpoint is the recovery record.

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

## Scoped workstream capture (Draft integration, inactive)

The canonical continuity plan is [z0intelligence#22](https://github.com/kvnloo/z0intelligence/issues/22).
This capture-only branch reconciles identity-aware `EventLog` admission from
`integrate/wiring-20261003@05e50151ec04ea40892be46645fb208aff2a1f70`
with later native Hermes claim-attestation and correction from
`feat/hermes-multiharness-20261009@285d74f18c68aec233b0be1bce472d9085bd5817`.
It does **not** merge either aggregate branch, move original transcripts,
or activate capture, retrieval, injection, a host hook or a service.

The selected native adapter is `adapters/hermes_z0int/native_capture.py`.
A trusted host must bind the persisted Hermes session, absolute
`HERMES_HOME`, user/project/repo/task scope, message UID/row, and source-text
SHA-256. Only unmarked native `role=user` decisions in the strict
`z0int.hermes.native_decision.v1` grammar may be admitted with native
references and bounded facts. Explicit same-scope `correction_of` preserves
D1 history when D2 becomes current. Injected briefs, tool notifications and
assistant prose never gain independent user provenance.

Capture defaults **off**. This PR never changes a profile or environment.
The existing `Z0INT_MEMORY_SOURCE_INGEST=references` opt-in is for an
authorized host/operator only; disabled capture must create no ledger directory.
Original content stays in the native Hermes database.

**Stacked prerequisite:** this Draft PR depends on the minimal freshness/scope
branch from [PR #198](https://github.com/kvnloo/z0intelligence/pull/198),
pinned at `8aac6cf208d6be183332fffec68d64220d497fee`. That branch supplies
the canonical `memory_contract.py` and `context_resolve.py`; no aggregate
wiring branch is merged. Capture tests can run against this combined source.
A trusted Hermes host/session/workstream binding is still missing, so runtime
activation remains blocked independently of passing library tests.

After those prerequisites, OMP should run from repo root:

```bash
PYTHONPATH=src python -m pytest -q tests/test_memory_event_log.py tests/test_memory_event_identity.py
PYTHONPATH=src python -m pytest -q tests/test_memory_claims.py tests/test_memory_surface_capture.py
PYTHONPATH=src python -m pytest -q tests/test_hermes_native_capture.py
```

Fixtures are synthetic, not the frozen real-user D1/D2 cross-client experiment.
They do not qualify model-visible injection, independent verification, host
activation, or downstream actions.
