# Donor: file-search

Cut from `dd39238` (tag `archive/pre-reorg-dd39238`) as one commit. Not on master.
`README.md` is the old whole-repo README; this file describes the branch.

## What it is

A model-callable OMP tool, `z0_file_search`, that answers a path or content query
from a warm, process-resident FFF (Rust) index and returns the hits as a
provenance "ContextPacket". The tool reaches Python through a resident
JSON-lines worker (`z0int.bridge.worker`), so this branch also carries that
worker and everything the worker needs to start and to serve the per-turn ops
the same OMP extension sends.

## Entry points

The file-search path, in call order:

- `omp-extensions/z0int-bridge/index.ts:537` `z0intBridge(pi)`: the extension.
  `:547` registers `z0_file_search`; `:571` sends op `file_search`; `:123` spawns
  `python -u -m z0int.bridge.worker`.
- `src/z0int/bridge/worker.py:173` `main`, `:51` `serve`, `:103` op `file_search`.
- `src/z0int/bridge/runtime.py:335` `BridgeRuntime.file_search`.
- `src/z0int/context_resolve.py:357` `resolve_context`, `:678`
  `append_context_packet_event`.
- `src/z0int/file_search.py:202` `search_repository`.

Also live on this branch because the same extension calls them every turn:

- `runtime.py:416` `turn_open` (`index.ts:657`), `runtime.py:551` `turn_close`
  (`index.ts:826`), ops `hello`, `self_check`, `status`, `drain`, `shutdown`,
  `decision`, `decision_warm`.
- `omp-extensions/z0int-intelligence/index.ts` and
  `harness-adapters/automatic-client.mjs`, `governed-client.mjs`: statically
  imported by the bridge extension (`index.ts:15`, `:538`); it cannot load without
  them. `automatic-client.mjs:14` spawns `python -m z0int.automatic`.

Nothing on this branch installs the extension into OMP.

## Tests

```
PYTHONPATH=src python -m pytest -m 'not live' -q
```

On the trimmed tree in a clean environment: 166 passed, 2 skipped (both
"NanoJev checkpoint not installed"), zero errors. All 47 modules under `src/`
import. The trimmed `core-unit` CI job (py_compile of five files and three
unittest classes, 21 tests) passes locally.

Directly about this feature: `tests/test_file_search.py`,
`tests/test_context_resolve.py`, `tests/test_bridge_worker.py`,
`tests/test_bridge_decision.py`, `tests/test_memory_event_log.py`,
`tests/test_omp_hook_context.py` (drives the real `z0int-intelligence/index.ts`
through `node`, so it needs Node with `--experimental-strip-types`).

The file-search tests and the smoke run below used a venv with the `fff` Python
binding installed. Without `fff`, `search_repository` reports the index as
unavailable; that path was not exercised here.

There are no `*.test.ts` files in the kept extensions, so no bun tests. The four
kept `.ts`/`.mjs` files parse with `bun build --no-bundle`.

Smoke run of the real worker on this branch (stdin JSON lines, no OMP):
`hello`, `self_check`, `file_search` x2, `turn_open`, `decision_warm` and
`shutdown` all returned `ok: true`. `cognition_shadow` returned `ok: false`
(see "Known defects").

## What was kept beyond the assignment, and why

The assignment lists 11 files. The branch has 102, because the worker is a hub:

- `src/z0int/bridge/generation.py`, `decision_cache.py`: `worker.py:17` and
  `runtime.py:20` import them at module level.
- `src/z0int/backends/` (`__init__`, `base`, `registry`, and the six concrete
  backends with `device.py`, `julia_worker.py`, `named_questions.py`,
  `nanojev_runtime.py`), `src/z0int/models_mgmt.py`, three `src/openjev_phase1/`
  files, `manifests/models*`: `decision_cache.py:15` imports `backends.base` at
  module level, and `registry.register_builtin_backends` imports all six backend
  modules the first time anything lists backends. Without them `turn_open`
  fails (`No module named 'z0int.backends.laya'`), and the extension sends
  `turn_open` on every turn. Their tests came with them, and
  `tests/test_julia_backend.py` reads `manifests/capabilities.v1.json` and
  `manifests/capability-evidence/`.
- `src/z0int/receipt.py`, `preflight.py`, `artifacts.py`: `turn_open` /
  `turn_close`.
- `src/z0int/memory/`: `append_context_packet_event` writes one event per search.
- `src/z0int/autoresearch/` (8 files), `contrastive_evidence.py`,
  `capabilities/`, `data_recipe_race.py`, `kerdoios_export.py`: `turn_close`
  enqueues verified traces (`runtime.py:672`), and the last three exist only so
  that `tests/test_network_cutover_phase_a.py` (covers `preflight.py`,
  `artifacts.py`, `autoresearch/`) and `tests/test_contrastive_evidence.py` stay
  on the branch instead of being dropped.
- `src/z0int/automatic.py`: `tests/test_omp_hook_context.py` runs it for real.
- `docs/bridge-hot-reload.md`: documents the worker transport.

Assignment note: `src/z0int/paths.py` is listed for this feature and is kept; it
is imported by nearly every kept module.

## Hot-path cost

- Per search: one worker round trip, an FFF query with a 120 ms budget
  (`file_search.py:26`), and one event-log append with fsyncs before the tool
  returns. The first search in a repository blocks up to 5 s while the index
  scans (`file_search.py:27`). If FFF misses and `allowQmd` is left at its
  default of true, a `qmd` subprocess may run (the audit puts it at "a 5 s + 8 s
  subprocess on a miss").
- Per turn, independent of search, as shipped in `index.ts`: `before_agent_start`
  awaits `turn_open` (up to 45 s timeout) and discards the reply; `agent_end`
  awaits `turn_close`. Both only write logs.
- Per session start: `compute_build_id` runs 6 git subprocesses (audit).
- Tool result size: the tool returns `JSON.stringify(packet)`, which carries
  each evidence row twice (`context_resolve.py:114` and `:346`) plus recipe and
  measurements.

## Evidence

From the audit of dd39238:

- "z0_file_search (OMP): the only bridge output a model consumes; no
  measurement. As wired it returns the full provenance packet with every
  evidence row twice (context_resolve.py:114 and :346) and does 3 fsyncs to an
  event log nobody reads."
- `file_search.py` (351 lines): verdict KEEP, confidence medium. "One tool call
  returns path + content hits with a 120 ms budget from a warm index ... No
  measured token or latency number in the repo." "Needs an A/B against the
  host's native grep before it keeps its `essential` slot."
- `context_resolve.py` (776 lines): FIX_THEN_KEEP. "Negative on tokens as wired."
  "Keep the ~120 lines that turn FFF hits into path:line + excerpt." The recipe
  cache "is write-only in effect ... 391 stale files on host".
- `bridge/runtime.py` (693 lines): "NONE for turn_open/turn_close: receipt is
  stamped execution=log_only (runtime.py:488) and no caller reads the reply."
  What remains after removing them "is ~150 lines".
- `omp-extensions/z0int-bridge/index.ts` (851 lines): "Net effect of the
  open/close half on tokens or latency is negative." Ruling: "keep the transport
  and the tool, cut the rest."
- The audit's skeptic pass: the event-log write and the QMD tier are live code
  used by this tool, and `context_resolve` is imported by other modules, so the
  trim cannot be done by deletion alone.

Measured on this branch during the smoke run (one run, this 102-file tree, so
not representative of a real repository): first `file_search` 79 ms with 5
hits, second 4.5 ms; the packet for 5 hits was 7.2 KB of JSON, the empty packet
1.9 KB. No token count and no comparison against grep was made.

## Did it run live on the audited host?

Yes. The bridge extension was loaded in OMP, the worker was running, and the
event log was being written on the audit day. Whether a model actually called
`z0_file_search`, how often, and whether it helped, was not measured. The live
host ran a different checkout with uncommitted changes, not this commit.

## Known defects

On this branch (caused by the cut):

- `cognition_shadow` op returns `{"ok": false, "reason": "ModuleNotFoundError:
  No module named 'z0int.cognition'"}`. The op is fail-open by design and its
  only caller (the `local-cognition` extension) is not here. `protocol.py` also
  names `cognition/shadow.py` as a build-id source; the file is absent.
- `automatic.dispatch_event` (`automatic.py:67`) imports `z0int.intelligence`,
  which is not here. Only the in-process dispatch is affected; the HTTP path
  that the hook test uses works.
- No `z0int` CLI: `src/z0int/cli.py` is not here, so `z0int context resolve` and
  every other subcommand is gone, and the `[project.scripts]` entries were
  removed.
- Tests of kept modules that were dropped because they also need modules that
  are not here: `test_receipt.py` and `test_backend_contract.py` (need
  `z0int.cli`), `test_llama_http_backend.py` (needs `backends/bench`),
  `test_hot_path.py` (needs `quota_budget`), `test_state_packet.py`,
  `test_task_loop.py`, `test_agentweb_context_packet.py`,
  `test_hermes_adapter.py` (other consumers of `context_resolve`),
  `test_claude_code_adapter.py`, `test_intelligence_canonical.py` (other users of
  `automatic.py`). `receipt.py`, `llama_http.py` and `automatic.py` therefore
  have less coverage here than at the source commit.
- `tests/test_architecture_manifest.py` was dropped: it asserts every path in
  `zer0.repo.yaml` exists, and that file is kept unmodified as the whole-repo map.

Inherited from the source commit:

- The tool result duplicates evidence rows and includes bookkeeping the model
  does not need.
- Every search pays event-log fsyncs for a log with no reader.
- The extension blocks each turn on `turn_open` and `turn_close`.
- `worker.py:17` imports `publish_current` and never calls it.
- `decision_warm` for `decider_2b` is fired at extension start (`index.ts:532`)
  and tries to load a local model in the background.
- `pyproject.toml` still declares the whole-repo dependency set and the name
  `openjev-phase1`.

## To earn a place on master

One measurement: a paired run on the same verified tasks with `z0_file_search`
available versus the host's native grep/glob tools only, reporting frontier
tokens per verified task and wall time. The audit asks for exactly this before
the tool keeps its `essential` slot.

Smallest subset worth taking if it wins (the audit's target, about 750 lines
instead of the 18,000 here):

- `src/z0int/file_search.py` (351 lines, imports nothing first-party)
- from `src/z0int/context_resolve.py`: only the part that turns FFF hits into
  `path:line` plus excerpt (about 120-150 lines), returned as compact text
- `src/z0int/bridge/worker.py` and `protocol.py`, with `runtime.py` reduced to
  `file_search` plus identity/self-check (about 150 lines), which removes the
  need for `backends/`, `receipt.py`, `preflight.py`, `autoresearch/` and
  `memory/`
- `omp-extensions/z0int-bridge/index.ts` reduced to worker transport plus the
  tool registration (about 250 lines), without the `z0int-intelligence` import
- `tests/test_file_search.py` and the FFF cases of `tests/test_context_resolve.py`

That reduction needs code edits, which a donor branch does not make.
