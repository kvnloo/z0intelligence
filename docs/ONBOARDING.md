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
| `omp_plugins` | Skipped by default; `--omp-extension NAME` links only the named extension into `~/.omp/agent/extensions/` |
| `data.discover` | Find Hermes `state.db`, OMP history, Codex home, export zips |
| `privacy` | Fail if personalized weights sit inside the git repo |
| `env_hints` | Write suggested `EVOLUTION_LAB_*` / `Z0INT_HOME` for harnesses |

Repeat `--omp-extension NAME` to select multiple extensions. Existing correct
links are left in place; conflicting links and directories are reported, not
replaced. Default onboarding leaves all OMP extension paths unchanged.

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

Receipt reads stay within the selected `Z0INT_HOME` or explicit API root.
Canonical receipt updates override older raw and bridge snapshots. The summary
counts each trace once, using its latest version, so repeated joins do not add
tasks or costs. A later negative outcome removes earlier success credit.
Distinct attempt trace IDs remain distinct decisions.

The summary limit bounds current trace identities, not append-only versions.
It retains the last identities encountered in bridge-then-canonical append order.
Missing trace IDs cannot contribute to totals. Tokens per verified task require
measured tokens for every verified trace. Baselines and avoided-token estimates
do not substitute for missing measurements. Complete measurement flags alone
do not prove a task benefit or independent verification.

Close-turn usage arguments replace the previous usage snapshot. Without an
explicit total, both input and output counts are required. One missing count
keeps the total unknown and the measurement partial. It cannot retain earlier
savings or a stale count from a previous close.

Measured savings are signed baseline-minus-usage deltas. A negative value means
the turn used more tokens than its baseline. Summaries retain these regressions.
The `/usage` offload total includes only complete measurements, including negative
deltas. Character-based SoL-Pi and RLM estimates remain separate.

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

`python -m z0int.routing_table --json` reports the committed capability registry
through the existing evidence-integrity gate. Its `static_local_candidates` count
does not establish endpoint availability, measured quality or permission to route.
Measurement confidence and source metadata remain visible beside each entry.

`z0int.delegation_eligibility.classify` provides a pure preflight hint for bounded
text subtasks. It requires an explicit parent-latency estimate before returning
worker arguments; unknown latency stays with the parent. Unknown privacy keeps
remote transmission disabled. Neither diagnostic is connected to live dispatch,
and `ROUTE_WORKER` is not execution authorization.

## Scoped workstream capture (preview, inactive)

The canonical continuity plan is [z0intelligence#22](https://github.com/kvnloo/z0intelligence/issues/22).
The capture integration in [PR #197](https://github.com/kvnloo/z0intelligence/pull/197)
and its freshness/scope prerequisite in
[PR #198](https://github.com/kvnloo/z0intelligence/pull/198) are merged into
preview. They do **not** move original transcripts or activate capture,
retrieval, injection, a host hook or a service.

The selected native adapter is `adapters/hermes_z0int/native_capture.py`.
A trusted host must bind the persisted Hermes session, absolute
`HERMES_HOME`, user/project/repo/task scope, message UID/row, and source-text
SHA-256. Only unmarked native `role=user` decisions in the strict
`z0int.hermes.native_decision.v1` grammar may be admitted with native
references and bounded facts. Admission serializes source-reference replay and
claim creation under the canonical event-file lock; an interrupted
reference-only admission can be retried. Explicit same-scope `correction_of`
preserves D1 history when D2 becomes current. Unlinked disagreements remain
contradictions, not explicit supersessions. Injected briefs, tool notifications
and assistant prose never gain independent user provenance. Source-role
attestation does not authenticate human authorship.

Capture defaults **off**. The integration does not change a profile or environment.
The existing `Z0INT_MEMORY_SOURCE_INGEST=references` opt-in is for an
authorized host/operator only; disabled capture must create no ledger directory.
Original content stays in the native Hermes database.

The freshness/scope prerequisite supplies the canonical `memory_contract.py`
and `context_resolve.py`; no aggregate wiring branch is merged. A trusted Hermes
host/session/workstream binding is still missing, so runtime activation remains
blocked independently of passing library tests.

For the synthetic library checks, run from the repo root:

```bash
PYTHONPATH=src python -m pytest -q tests/test_memory_event_log.py tests/test_memory_event_identity.py
PYTHONPATH=src python -m pytest -q tests/test_memory_claims.py tests/test_memory_surface_capture.py
PYTHONPATH=src python -m pytest -q tests/test_hermes_native_capture.py
```

Fixtures are synthetic, not the frozen real-user D1/D2 cross-client experiment.
They do not qualify model-visible injection, independent verification, host
activation, or downstream actions.
