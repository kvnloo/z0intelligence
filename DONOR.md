# DONOR: autoresearch-task-loop

Donor branch cut from `dd39238` (tag `archive/pre-reorg-dd39238`). One commit on
top of that tag removes everything outside this feature. `README.md` and
`zer0.repo.yaml` are the old whole-repo files, kept unmodified; this file
describes the branch.

## What it is

`autoresearch/` is a sqlite queue of verified turn traces plus a daemon that
replays context-policy mutations against them (ABAB and contrastive jobs) and
records champions; the OMP bridge enqueues a trace when a turn closes with
`verified_success=true`. With it come the three other offline loops of the
same period: `task_loop.py` (an authorized resolve -> worktree -> patch ->
verify task family with checkpoint resume), `counterfactual.py` (mines past
OMP turns into replay snapshots) and `replay/` (ingests paired-replay results
as usage events).

## Entry points

| Command or call | Code |
|---|---|
| `z0int autoresearch status\|pause\|resume\|run-once\|daemon\|report\|enqueue` | `src/z0int/cli.py:582` (parser), `:1077` (dispatch) -> `autoresearch/daemon.py:12,25,32,39,68`, `autoresearch/queue.py:47` |
| Bridge enqueue hook (producer) | `omp-extensions/z0int-bridge/index.ts:702` sends op `turn_close` -> `src/z0int/bridge/worker.py:144` -> `src/z0int/bridge/runtime.py:551` (`turn_close`) -> `:672` `enqueue_trace` |
| `z0int task authorize\|run\|status\|fixture` | `src/z0int/cli.py:490`, `:917` -> `task_loop.py:213` (`authorize_task`), `:439` (`run_until`), `:478` (`resume_task`) |
| `z0int counterfactual mine\|summary` | `src/z0int/cli.py:428,437`, `:994` -> `counterfactual.py:347,360` |
| `z0int counterfactual ingest-worker-needed` | `src/z0int/cli.py:439` -> `replay/worker_needed.py:26` |
| `python -m z0int.bridge.worker` | `src/z0int/bridge/worker.py` (spawned by the extension) |

Checked on this branch: all the modules above import; `z0int autoresearch
status|run-once|report`, `z0int task --help`, `z0int counterfactual summary`
exit 0. A direct call of `BridgeRuntime.turn_open` then `turn_close(...,
verified_success=True, verification_source=...)` left one `queued` row in the
autoresearch queue, so the hook works end to end on the Python side. The
TypeScript extension was not loaded in OMP here.

## Running the tests

```
pip install -e '.[test]'
pytest -m 'not live' -q
```

Result on this branch: 638 passed, 2 skipped, 5 deselected, 0 failed, 0
collection errors (full tree at `dd39238`: 1164 passed, 2 skipped, 5
deselected). Some kept bridge and usage tests import the external
`agent-tokenomics` Python package; put its `packages/python/src` on
`PYTHONPATH` as CI does. `Z0INT_HOME` should point at a scratch directory.

Tests of the feature itself: `tests/test_task_loop.py`,
`tests/test_network_cutover_phase_a.py` (enqueue gate, `run_once`, pause and
resume), `tests/test_contrastive_evidence.py` (contrastive replay job),
`tests/test_receipt.py` (counterfactual mining through the CLI),
`tests/test_bridge_worker.py` (turn open and close). No offline test asserts
the `turn_close` -> `enqueue_trace` step itself; the probe described above is
the only check of it. The other kept tests cover import closure.

No kept directory has bun tests. CI: `.github/workflows/core-unit.yml`
(trimmed by line removal to what still exists) and `receipt.yml`.

## Cost on a turn's hot path

- Autoresearch consumer, task loop, counterfactual, replay: none. They run only
  when a person types a command; no unit or timer runs the daemon.
- The enqueue hook rides on the bridge's `turn_close`, which the extension
  awaits at the end of every OMP turn. The enqueue itself is one sqlite insert
  inside a `try/except`, and only for turns closed with
  `verified_success=true`. The audit did not time it separately. The
  surrounding `turn_open`/`turn_close` path is what costs: the audit records
  `turn_open` as "awaited up to 45s and its reply is discarded" and the pair as
  net negative on latency. That path is kept here only because the hook lives
  inside it.

## Evidence (quoted from the audit of `dd39238`)

There is no measurement in favour.

- `autoresearch/` (878 lines): "Producer only ... The consumer
  (daemon.run_daemon / run_once) is reachable only from human-typed `z0int
  autoresearch ...`; no systemd unit or timer runs it." Effect: "NONE. [The
  autoresearch data directory] is an empty directory dated 2026-09-18: no
  queue.sqlite3 and no champions/ folder, so not even the enqueue has ever
  executed here." "A queue with no consumer process. The enqueue payload is
  hard-coded filler ('required_ops': ['exact_path'], 'baseline_wall_ms': 800
  at bridge/runtime.py:680-682)." "measurement_gaps.py (11 lines) has no
  importer at all."
- `task_loop.py` with `aodl.py` (1,446 lines): "NONE. task_loop writes
  state/tasks and state/worktrees; neither directory exists on this host, so
  it has never run here." "task_loop is imported only by cli.py."
- `counterfactual.py` (with ao_experiment/ao_report, 1,365 lines): "NONE.
  ... replay/task_snapshots.jsonl and counterfactual_mine.json were last
  written 2026-09-18."
- `replay/` (94 lines): "NONE. Only `z0int counterfactual
  ingest-worker-needed`"; its data directory "holds 2 files, both from
  2026-09-18."
- Plan: "Proof nobody types them: cli.py:1112-1114 imports
  `.autoresearch.research.pipeline`, which does not exist, and nobody
  noticed."
- Plan summary: "Nothing else in the repo has any evidence of saving tokens or
  latency: not ... posture, autoresearch, or the routine/cascade compilers."

## Did it run live on the audited host

The bridge worker and extension were live (audit: `runs_live_here: yes`), but
the autoresearch queue had never received a row, the daemon was never run, and
the task loop never ran (`runs_live_here: no` for all four pieces). Why the
enqueue never fired although the bridge was live is not established by the
audit; the likely reason is that no turn was closed with
`verified_success=true`, but that was not checked.

## Known defects

- `z0int autoresearch research-once` raises `ModuleNotFoundError`:
  `z0int.autoresearch.research.pipeline` did not exist at `dd39238` either.
- The enqueue payload is constant filler (`required_ops: ["exact_path"]`,
  `baseline_wall_ms: 800`), so replay jobs made from live turns do not reflect
  the turn. Only `frontier_tokens` is real.
- `autoresearch run-once` skips when its resource governor sees load (it did
  on the build machine: `reason: resource_governor`). `run_once(force=True)`
  exists as a function argument; the CLI call site is `src/z0int/cli.py`,
  check there whether a flag reaches it.
- `autoresearch/measurement_gaps.py` has no caller.
- `turn_open` tries to spawn a planner through a hardcoded interpreter path
  that does not exist on most machines; it records
  `kerdoios_plan: interpreter_missing` and continues.
- `z0int` is the whole-repo CLI kept shallow. Subcommands for this feature
  work, as do `receipt`, `context`, `cognition`, `routine`, `cascade`, `aodl`
  and the others whose modules arrived as closure. Subcommands whose modules
  are not on this branch fail at call time with `ModuleNotFoundError`:
  `doctor`, `status`, `onboard`, `state-packet`, `ao ...`, `repair`, `abab`,
  `os-context`, `claude-code`, `hermes`, `backend bench`.
- `tests/test_architecture_manifest.py` is dropped by design: it asserts that
  every path in `zer0.repo.yaml` exists.
- Tests dropped although they touch modules kept here as closure, because
  their main subject is not on this branch: `test_intelligence_canonical`
  (needs `intelligence_mcp`), `test_dispatch_authority`,
  `test_provider_saturation`, `test_free_only` (need `remote_executor`),
  `test_agentweb_integration`, `test_intelligence_service` (need
  `intelligence_service`), `test_claude_code_adapter` (needs `claude_code`),
  `test_llama_http_backend` (needs `backends.bench`), `test_ao_experiment`,
  `test_ao_report` (need `ao_bridge`), `test_z0int_refinement*`,
  `test_z0int_battery` (need `refinement`, `battery`), `test_hermes_adapter`.
  Those modules are covered on their own donor branches.

## Extra code kept so the branch works

About 1,850 lines are the feature (autoresearch 878, task_loop 509,
counterfactual 363, replay 94). The rest of the 183 files is closure:

- `src/z0int/bridge/` (all six files), `omp-extensions/z0int-bridge/`,
  `omp-extensions/z0int-intelligence/`, `harness-adapters/*.mjs`: the enqueue
  hook is inside `bridge/runtime.py:turn_close`, and the extension imports the
  intelligence extension and its clients statically.
- `automatic.py`, `intelligence.py`, `functions/`, `worker_routing.py`,
  `dispatch_authority.py`, `aodl_dispatch.py`, `aodl_admission.py`,
  `posture.py`, `provider_saturation.py`, `quota_budget.py`, `free_fanout.py`,
  `manifests/`: reached lazily from `bridge/worker.py` (op `automatic_event`)
  and `harness-adapters/automatic-client.mjs`. Not part of the claim; kept so
  the worker has no dangling ops.
- `preflight.py`, `receipt.py`, `tokenomics_emit.py`, `paths.py`,
  `context_resolve.py`, `file_search.py`, `memory/`: used by `turn_open`,
  `turn_close`, the bridge `file_search` op, `task_loop` and `replay`.
- `aodl.py`, `cascade.py`, `routines.py`, `credit.py`: `task_loop.py` imports
  the first two at module level.
- `contrastive_evidence.py`, `data_recipe_race.py`, `capabilities/`:
  `autoresearch/replay.py:177` imports contrastive evidence for the
  contrastive job, and its test needs the other two.
- `artifacts.py`, `kerdoios_export.py`: imported by
  `tests/test_network_cutover_phase_a.py`, the test that covers the enqueue
  gate and `run_once`.
- `cli.py`, `__main__.py` (shallow), `cognition/` (20 files),
  `scripts/probe_local_cognition.py`, `backends/`, `models_mgmt.py`,
  `src/openjev_phase1/{__init__,core,direct}.py`: `cli.py` imports
  `cognition.cli` at module level, and the cognition and decision ops of the
  bridge reach the backends.
- `docs/RESEARCH.md`: the only prose that describes autoresearch (old research
  notes, not a reference).

## To earn a place on master

One measurement: run the daemon against a queue of real verified traces whose
payload is the actual turn (not the constant filler) and show that a champion
context policy it promotes, when applied to new turns, lowers frontier tokens
or latency per verified task against the same tasks without it. Today nothing
reads a champion, so there is also no consumer; both are missing.

Smallest subset worth taking if that is attempted:
`src/z0int/autoresearch/{queue,store,schema}.py` (268 lines: the gated sqlite
queue that accepts only `verified_success=true` with a verifier identity) and
the enqueue cases of `tests/test_network_cutover_phase_a.py`. Take
`daemon.py`, `replay.py`, `mutations.py`, `resources.py` only once a consumer
for champions exists. `task_loop.py`, `counterfactual.py` and `replay/` have
no proposed consumer; whether they would help is unknown.
