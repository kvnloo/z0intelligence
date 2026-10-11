# DONOR: claude-code-recording

Donor branch cut from `dd39238` (tag `archive/pre-reorg-dd39238`). One commit
removes everything outside this feature; history and blame of the kept files
are intact. This file describes the branch. `README.md` is the old whole-repo
README (kept because `pyproject.toml` names it), `AGENTS.md` and `ROADMAP.md`
are read by a kept test, and `zer0.repo.yaml` is the unmodified whole-repo
map; none of them describes this branch.

## What it is

The per-turn recording layer for harnesses other than the OMP bridge: the
Claude Code `UserPromptSubmit`, `Stop` and `SessionEnd` hooks, the shared
"automatic event" path that offers every prompt to the router, the
DecisionOpportunity record built for each prompt, and the Hermes and DSH
copies of the same shims. It also carries the resource-posture calculation,
the AgentWeb choice/noul shadow experiment lanes, and the CLI reports that read
these records (`z0int claude-code decisions`, `z0int hermes decisions`,
`z0int posture`, `z0int status`, `z0int doctor`).

## Entry points

| Entry point | Where |
| --- | --- |
| Claude Code hooks (`python -m z0int.claude_code prompt\|stop\|session-start`) | `harness-adapters/claude-code-z0intelligence/hooks/hooks.json:8`, `:19`, `:30`, `:41` |
| hook dispatcher `main` | `src/z0int/claude_code.py:212` |
| `on_prompt` (UserPromptSubmit) | `src/z0int/claude_code.py:89` |
| `emit_opportunity_async` / `on_opportunity` (detached child) | `src/z0int/claude_code.py:49`, `:73` |
| `on_stop` (Stop and SessionEnd) | `src/z0int/claude_code.py:160` |
| `on_session_start` (opt-in State Packet) | `src/z0int/claude_code.py:201` |
| automatic path: `handle_event`, `dispatch_event`, `consume`, `main` | `src/z0int/automatic.py:114`, `:66`, `:71`, `:129` |
| `build_decision_opportunity`, `deterministic_gate` | `src/z0int/decision_opportunity.py:144`, `:197` |
| `run_choice_experiment`, `run_noul_experiment` | `src/z0int/decision_experiment.py:262`, `:284` |
| Hermes recorder: `on_opportunity`, `decisions_report`, `_main` | `src/z0int/hermes_decisions.py:57`, `:99`, `:133` |
| Hermes shadow plugin `register` | `harness-adapters/hermes-z0int-decisions/__init__.py:282` |
| Hermes automatic plugin `before_turn`, `register` | `harness-adapters/hermes-z0intelligence/__init__.py:16`, `:28` |
| OMP / DSH client shim `call`, `route`, `scheduleOmpAutomatic` | `harness-adapters/automatic-client.mjs:12`, `:40`, `:76` |
| DSH plugin `apply` | `harness-adapters/dsh-z0intelligence/index.mjs:4` |
| posture: `current_posture`, `shadow_annotation`, `main` | `src/z0int/posture.py:533`, `:542`, `:617` |
| Claude Code decisions report | `src/z0int/claude_code_launch.py:88` |
| CLI `z0int doctor` / `status` / `posture` / `claude-code` / `hermes` | `src/z0int/cli.py:911`, `:913`, `:1125`, `:1187`, `:1189` |
| Hermes envelope helpers (no caller outside tests) | `adapters/hermes_z0int/adapter.py`, `src/z0int/harness_id.py:11` |

Python entry points that import on this branch (checked): `z0int.claude_code`,
`z0int.automatic`, `z0int.decision_opportunity`, `z0int.decision_experiment`,
`z0int.hermes_decisions`, `z0int.posture`, `z0int.harness_id`, `z0int.status`,
`z0int.doctor`, `z0int.claude_code_launch`, `z0int.intelligence_mcp`,
`z0int.cli`, `adapters.hermes_z0int`, and both Hermes plugin `__init__.py`
files loaded by path. `automatic-client.mjs` and `dsh-z0intelligence/index.mjs`
import under node.

## How to run its tests

```
pip install -e '.[test]'          # plus the tokenomics package and a kerdoios checkout, as in core-unit.yml
python -m pytest -q -m 'not live'
```

Result on this branch, in a clean environment (empty HOME, `Z0INT_HOME` in a
scratch directory): `676 passed, 2 skipped, 5 deselected`, no collection
errors. `make_donor.py --verify-imports`: 75 modules under `src/` import, 64
other `.py` files compile. The full tree at `dd39238` gives 1164 passed.

The feature's own tests are `tests/test_claude_code_adapter.py`,
`tests/test_decision_opportunity.py`, `tests/test_hermes_adapter.py`,
`tests/test_hermes_decisions.py`, `tests/test_posture.py`,
`tests/test_posture_routing.py`, `tests/test_agentweb_choice_experiment.py`,
`tests/test_agentweb_noul_experiment.py`, `tests/test_claude_code_launch.py`,
`tests/test_omp_hook_context.py` and `tests/test_z0int_onboard.py`. The other
test files ride along because their imports are all inside the kept closure.

No kept extension has `*.test.ts`, so there is no bun test to run.
`bun build omp-extensions/z0int-intelligence/index.ts --packages=external`
bundles. A manual smoke on this branch with a made-up hook payload and a
one-line transcript: `prompt`, `opportunity`, `stop` and `session-start` all
exited 0; `opportunity` wrote one 18.6 KB row to `opportunities.jsonl`, `stop`
wrote one usage event and one outcome row; `z0int claude-code decisions` then
reported `linked: 1`; `z0int posture --json`, `z0int hermes decisions`,
`z0int status` and `z0int doctor --json` printed their reports;
`python -m z0int.automatic event` returned `{"action": "native", "disabled": true}`
(the default when no config enables the harness). Neither plugin was loaded
into Claude Code or Hermes from this branch.

## What it costs on a turn's hot path

From the audit:

- Claude Code `UserPromptSubmit`: "about 50-70 ms interpreter start on the hot
  path, plus a detached process doing git calls and a transcript scan", to
  write a row of about 5 KB (576 KB for 112 rows on the audited host). The
  hook timeout is 30 s; the automatic POST has a 25 s timeout and runs only
  if the host config enables the harness.
- Claude Code `Stop` and `SessionEnd`: both run the same command and each
  re-parses the whole transcript; "live transcripts this week are median
  119 KB, p90 29 MB, max 135 MB, all re-parsed per Stop".
- Hermes automatic plugin: "up to two blocking Python process starts per
  Hermes turn (30 s timeout each)".
- DSH plugin: one Python spawn per turn.
- OMP `z0int-intelligence` `before_agent_start`: "a 5 s bridge op per turn
  that returns 'native, disabled' and appends hook_rtt.jsonl, which has no
  reader."
- `posture.py`, `decision_experiment.py`, `status.py`, `doctor.py`: nothing
  per turn (see "ran live" below).

The interpreter-start figure is the only timing the audit measured; the rest
are code paths, timeouts and file sizes.

## Measured evidence for or against

Against, from the audit of `dd39238`:

- `claude_code.py` prompt hook: "NONE. Nothing reaches the model
  (claude_code.py:101-103; docs/claude-code.md:16 says 'inert in shadow')."
  Verdict DELETE in one map, FIX_THEN_KEEP in another ("Drop the prompt hook
  and the opportunity/outcome recording; keep the Stop usage receipt only if
  the Tokenomics slice shows a consumer for it").
- `claude_code.py` Stop/SessionEnd: "Pure recording: tokenomics/events.jsonl
  723 rows, outcomes.jsonl 210 rows ... The only automated reader is the
  hourly loop tick, which reports 'degraded (label_source_empty,
  learner_missing)' on every run." Verdict DELETE.
- `automatic.py`: "NONE for normal prompts: normalize() (lines 56-62) labels
  them native_turn (no registry entry) or summarization (no eligible entry),
  so the result is always PARENT_ONLY; only a prompt literally shaped
  'Claim: ...\nEvidence: ...' executes anything, and that is a paid call."
  Verdict DELETE.
- `decision_opportunity.py`: "the gate result is only appended to
  state/claude-code/opportunities.jsonl ... The one real consumer is the
  host's hourly z0int-loop-tick timer, which runs unmerged code". Verdict
  UNSURE: "Either merge that loop and show a decision, or delete both."
- `hermes_decisions.py` and its plugin: "Shipped but never installed or run
  on the host; duplicates claude_code.on_opportunity almost line for line
  (hermes_decisions.py:59-72 vs claude_code.py:73-85)." Verdict DELETE.
- `harness-adapters/hermes-z0intelligence/`: DELETE in one map, UNSURE in
  another (could not confirm it fires).
- `harness-adapters/dsh-z0intelligence/`: "NONE for normal turns (same
  PARENT_ONLY result), at the cost of one Python spawn per turn." DELETE.
- `posture.py`: "NONE. Shadow-only by its own docstring"; its routing hook
  `worker_routing.route_worker:480` "has no caller". Verdict DELETE.
- `decision_experiment.py`: "NONE. Docstring: 'deliberately not production
  capability routes'; receipts are execution=shadow". Verdict DELETE.
- `harness_id.py` and `adapters/`: entry point chain "NONE". Verdict DELETE.
- `doctor.py`, `status.py`, `onboard.py`: "No token saving; install
  plumbing." FIX_THEN_KEEP / UNSURE.

For:

- The only number in the repository for this feature is an offline one, from
  `docs/decision-opportunity.md` (not re-run by the audit or by me): on 11
  pinned held-out questions the question-scoped DecisionOpportunity gate
  scored 8/11 strict with 0 false ACT, against 2/11 for the State Packet
  alone. The document's own caveat: "n=11, one snapshot; the vocabulary was
  written after seeing one pinned prompt". It measures gate agreement, not
  tokens or latency.
- The Stop hook's usage row is, per the audit, written by "the only place
  real provider token usage is recorded for Claude Code"
  (`tokenomics_emit.py`). That is a role, not a measured effect.
- `docs/resource-posture.md` defines an evaluation for posture and reports
  one host snapshot; it reports no outcome of that evaluation.

## Did it run live on the audited host

- Claude Code hooks: yes. The plugin was in the Claude Code plugin cache and
  `opportunities.jsonl` and `outcomes.jsonl` were written on the audit day.
- Automatic path: unknown. Whether any harness was enabled depends on a host
  config file the auditors did not read; they inferred "disabled" for OMP
  and Claude Code from side effects.
- Hermes automatic plugin: installed from a different, older checkout;
  whether it fires is unknown.
- Hermes decisions plugin: no, not installed.
- DSH plugin: unknown (named in three DSH profiles; DSH use not established).
- `posture.py`: no. No posture config or history existed.
- `decision_experiment.py`: unknown; gated off unless an environment flag is
  set, and no client was found.
- `doctor`/`status`/`onboard`: used once, on 2026-10-04.

## Known defects

From the audit:

- In shadow mode (the default) the automatic result is discarded
  (`claude_code.py:101-103`).
- `Stop` and `SessionEnd` run the same command, and each re-parses the full
  transcript (`claude_code.py:111`).
- Three shims do the same spawn-or-post into `z0int.automatic`:
  `claude_code.py:98-108`, `automatic-client.mjs:12-55`,
  `hermes-z0intelligence/__init__.py:10-26`.
- `harness-adapters/hermes-z0int-decisions/__init__.py:22` carries a
  hand-copied duplicate of `z0int.hermes_decisions` logic; a test exists to
  keep the two in sync.
- `adapters/hermes-z0int/adapter.py` is a re-export of
  `adapters/hermes_z0int/adapter.py`; nothing outside tests imports either.
- `status.py` is `run_doctor()` re-formatted.
- `doctor.py:129-166` probes hard-coded host-specific paths.
- `scripts/hermes-decisions-sandbox/` has zero inbound references.

Caused by the cut (this branch only):

- The automatic path posts to the intelligence service
  (`POST /v1/automatic`, `automatic.py:118`). The service module
  `intelligence_service.py` is not on this branch (it belongs to the router
  donor), so with a harness enabled `handle_event` gets a connection error
  and returns `action: native` unless a service from another checkout is
  running. `automatic.dispatch_event` (in-process) and `intelligence.py` are
  kept and tested.
- `decision_experiment.py` is kept and its two tests pass, but its only
  caller, `intelligence_service.py`, is not here: the
  `/v1/experimental/choice` and `/noul` routes do not exist on this branch.
- The OMP resident transport (`omp-extensions/z0int-bridge`, op
  `automatic_event`) is on the turn-receipts donor, not here. The OMP
  extension `z0int-intelligence` still loads; on the default (not enabled)
  path `scheduleOmpAutomatic` then has no `request` function and only
  appends a `hook_rtt.jsonl` row. The operator-enabled path spawns
  `python -m z0int.automatic` and is unaffected.
- `harness-adapters/claude-code-z0-obspack/`, `src/z0int/claude_code_obs.py`
  and `tests/test_claude_code_obs.py` are kept only so that both plugin
  sources named in `.claude-plugin/marketplace.json` exist. ObservationPack
  belongs to the claude-code-core donor; salvage it from there.
- `src/z0int/cli.py` is kept with module-level imports only. These
  subcommands parse and then raise `ImportError` when run: `task`,
  `backends eval` (bench), `artifacts`, `context-state`, `autoresearch`,
  `kerdoios`, `os-context`, `preflight`, `ao`, `counterfactual
  worker-needed-ingest`, `contrastive`, `routine`, `cascade`, `aodl`,
  `repair`, `abab`. Working: `doctor`, `status`, `onboard`, `models`,
  `receipt`, `counterfactual mine|summary`, `context`, `backends
  list|doctor`, `cognition`, `posture`, `claude-code`, `hermes`.
  `z0int claude-code launch|warm|profile` were not exercised here.
- `tests/test_architecture_manifest.py` is dropped: it asserts every path in
  `zer0.repo.yaml` exists, and most do not here.

## Why the branch is 200 files and not 30

The feature's own code is about 30 files. The rest is import closure:

- `claude_code.py` and `decision_opportunity.py` import `state_packet.py`,
  which imports `context_resolve` -> `file_search`, `memory`.
- `automatic.py` lazily imports `intelligence.py` -> `worker_routing.py`,
  `functions/`, `dispatch_authority`, `provider_saturation`, `quota_budget`,
  `aodl_dispatch`, `aodl_admission` and the manifests they read.
- `worker_routing.py:13` imports one HTTP client from `cognition.adapters`,
  which loads the whole `cognition` package and, through it, `backends/`.
  `cli.py` also imports `cognition.cli` at module level.
- `decision_experiment.py` imports `functions/jev.py` at module level.
- The Claude Code plugin directory is kept whole, so its MCP launcher keeps
  `intelligence_mcp.py`.
- `status.py` imports `doctor`, `models_mgmt`, `onboard`.
- `tests/test_receipt.py` is kept because `receipt.py` is here, and it needs
  `counterfactual.py`.

Those modules are owned by other donors and are duplicated here on purpose.

Assignment notes: the assignment listed `provider_saturation.py`,
`functions/` and `tokenomics_emit.py` as shared, and they arrive by closure
as expected. I added `claude_code_launch.py` (it holds the only reader of
the Claude Code records), `omp-extensions/z0int-intelligence` with
`governed-client.mjs` (the OMP consumer of `automatic-client.mjs`, covered
by `tests/test_omp_hook_context.py`), `src/z0int/__main__.py` (so
`python -m z0int` works) and the obspack plugin (above).
`tests/fixtures/decision_request.json` was flagged in the assignment and is
not kept: nothing references it.

Tests dropped: 61 files. 60 import a module, or read a script, that is not on
this branch (the tool's report names the first missing one for each); the
other is `test_architecture_manifest.py`. No test of the feature's own files
was dropped. Some dropped tests are the dedicated tests of closure modules
that are kept here but owned elsewhere, and they need more removed code to
import: `test_dispatch_authority.py`, `test_provider_saturation.py`,
`test_free_only.py` (need `remote_executor.py`),
`test_llama_http_backend.py`, `test_backend_bench.py`,
`test_tokenomics_bridge.py`, `test_eligibility.py` (need `backends/bench/`),
`test_cognition_orchestration.py`, `test_mutation_policy.py` (need
`cognition/orchestration.py`, `cognition/mutation_policy.py`). Those modules
are therefore less tested here than on the donor that owns them; salvage
them from that donor, not this one.

## To earn a place on master

The one thing that would justify it: a consumer that turns the recorded rows
into a decision with a measured effect. Concretely, for DecisionOpportunity:
on live recorded prompts, the deterministic gate (or a learner trained on
the rows) changes what a turn does (asks, abstains or routes) and a paired
comparison shows fewer frontier tokens or lower latency per verified task.
For the Stop hook alone: a named reader of the usage events that reports
tokens per verified task for Claude Code sessions. Today the only automated
reader is unmerged and reports itself degraded.

Smallest subset worth taking:

- `on_stop`, `messages`, `transcripts` from `src/z0int/claude_code.py`
  (about 90 lines) with `tokenomics_emit.py` and `paths.py`, changed to read
  the transcript incrementally and bound to one of `Stop` / `SessionEnd`, not
  both. Take the matching cases from `tests/test_claude_code_adapter.py`.
- Only if a consumer exists: `src/z0int/decision_opportunity.py` (221 lines)
  with `tests/test_decision_opportunity.py` and
  `benchmarks/decision_opportunity/`; it needs `state_packet.py` from the
  claude-code-core donor.
- `evidenced()` from `automatic.py:20-28` if `intelligence.py` survives (the
  audit notes `intelligence.py:44` uses it).

Not worth taking as they stand, per the audit: the prompt hook, the rest of
`automatic.py` and its three shims, `hermes_decisions.py` and its plugin,
`posture.py`, `decision_experiment.py`, `harness_id.py`, `adapters/`.

Uncertain: whether any harness had the automatic path enabled on the audited
host; whether Hermes or DSH load their plugins; whether the 8/11 offline
result reproduces; whether the unmerged loop that reads these records is
worth merging (it is not in this repository at `dd39238`).
