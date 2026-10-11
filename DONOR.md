# donor/cognition-shadow

Cut from `dd39238` (tag `archive/pre-reorg-dd39238`) as one commit that removes everything
outside this feature. File history and blame are intact for what is kept.

## What it is

An observe-only "shadow" lane for the OMP harness: on every tool call an extension asks the
resident bridge worker to compile the legal action set and record which tool a set of local
small models would have picked, then appends a receipt. It never blocks, changes or executes
a call; the package also carries the offline pieces built around it (action compiler, cascade,
model manifest, a one-model llama.cpp supervisor and three eval scripts).

## Entry points

Live path (all of these import on this branch):

- `omp-extensions/local-cognition/index.ts:673` `localCognition(pi)`; `:681` `tool_call`
  handler; `:697` `sendShadow` (fire and forget); `:707` always returns `undefined`.
- `omp-extensions/z0int-bridge/index.ts:510` `publishShadowTransport` (the transport the
  extension uses, read at `local-cognition/index.ts:372`); `:123` spawns
  `python -u -m z0int.bridge.worker`.
- `src/z0int/bridge/worker.py:173` `main`; `:115-121` op `cognition_shadow`.
- `src/z0int/bridge/runtime.py:319` `BridgeRuntime.cognition_shadow` -> `:327`
  `z0int.cognition.shadow.run_shadow`.
- `src/z0int/cognition/shadow.py:462` `run_shadow`; `:590` `summarize_shadow_receipts`
  (hand-run scorecard: `python -m z0int.cognition.shadow`).

Human / unit entry points:

- `src/z0int/cli.py:31`, `:539`, `:919` -> `src/z0int/cognition/cli.py:368` `cmd_cognition`:
  `z0int cognition manifest|candidates|roles|serving|serve|probe|compile|decide`.
- `deploy/systemd/z0-cognition.service` runs `python -m z0int cognition serve` ->
  `src/z0int/cognition/cli.py:141` -> `src/z0int/cognition/server.py:344` `serve_forever`
  (127.0.0.1:11500, one model resident).
- `deploy/systemd/z0-slm.service`: unit template for a host llama.cpp server (see defects).
- `scripts/probe_local_cognition.py`, `scripts/compiler_contract_eval.py`,
  `scripts/orchestration_eval.py`, `scripts/local_cognition_backend_eval.py`,
  `benchmarks/build_compiler_contract.py`, `benchmarks/build_orchestration_v2.py`.

Modules checked to import in the test environment: `z0int.cli`, `z0int.__main__` (module
load), `z0int.cognition`, `z0int.cognition.cli`, `z0int.cognition.shadow`,
`z0int.cognition.server`, `z0int.bridge.worker`, `z0int.bridge.runtime`, plus every module
left under `src/` (`make_donor.py --verify-imports`).

## Tests

    pip install -e '.[test]'        # also needs the tokenomics python package on PYTHONPATH
    pytest -m 'not live' -q
    (cd omp-extensions/local-cognition && bun test)

Result on this branch, isolated environment (empty HOME, no network use):
`882 passed, 2 skipped, 5 deselected` (full tree at `dd39238`: 1164 passed, 2 skipped, 5
deselected); bun: `27 pass, 1 skip, 0 fail`.
`.github/workflows/core-unit.yml` is trimmed by line removal only (the "Verify published
evidence" step is gone because `benchmarks/verify_published.py` and `results/raw/` are not
on this branch); the `omp-extension` bun job is kept.

The feature's own tests: `tests/test_cognition_*.py` (10 files), `tests/test_mutation_outcome.py`,
`tests/test_mutation_policy.py`, `tests/test_mutation_retry_bench.py`,
`tests/test_bridge_worker.py`, and the two bun test files.

## Why the branch is this large (284 files, about 129,000 lines)

The cognition package itself is 22 files / about 8,100 lines and imports only
`z0int.receipt` and `z0int.backends.base` from the rest of the tree. The size comes from
the path it runs through:

- The live lane only exists through the resident bridge worker, and the worker is shared.
  `bridge/runtime.py` and `bridge/worker.py` lazily import `preflight`, `context_resolve`
  (file search, memory), `autoresearch.queue`, `bridge/decision_cache.py` (all of
  `backends/`, `models_mgmt.py`, `openjev_phase1` core) and `automatic.py` ->
  `intelligence.py` -> `functions/`, `worker_routing.py`, `dispatch_authority.py`.
- `omp-extensions/z0int-bridge/index.ts:15` statically imports
  `omp-extensions/z0int-intelligence/index.ts`, which imports
  `harness-adapters/automatic-client.mjs`. Without them the bridge extension does not load.
- `scripts/local_cognition_backend_eval.py` imports `z0int.backends.bench`, whose tests read
  `results/decision-backends/20260930-mbp-dcv1/` (31 result files, about 72,000 of the kept
  lines).
- Tests of kept modules were kept rather than dropped, and they import more:
  `test_receipt.py` (counterfactual, ao_bridge), `test_provider_saturation.py`,
  `test_dispatch_authority.py`, `test_free_only.py` (intelligence_service, remote_executor,
  governed_worker, agentweb_* and outcome_* modules), `test_intelligence_canonical.py`
  (intelligence_mcp), `test_contrastive_evidence.py` (capabilities, data_recipe_race),
  `test_network_cutover_phase_a.py` (kerdoios_export).

None of that extra code is part of the claim; it is duplicated here so the branch runs.

## Cost on a turn's hot path

From the audit of the host that runs it (figures are the audit's, not re-measured here):

- One bridge round trip plus one receipt of about 2.1 KB per OMP tool call, on a serial
  worker. "It would hold the worker up to 3.2 s per tool call if the model server were ever
  started."
- `shadow.py:300` reloads the 30 KB manifest and `serving.json` on every call.
- `src/z0int/cli.py:31` imports the whole cognition package at module level: "every `z0int`
  CLI start and the routing service pay the eager package import (117-151 ms measured)".
  The routing service pays it because `worker_routing.py:13` imports
  `cognition/adapters/transport.py`.

## Evidence

Against (audit, quoted):

- "NONE. The handler always returns undefined (index.ts:707), so it cannot block, revise or
  replace a call; it only costs one bridge round trip and one ~2.1 KB receipt per tool call
  (29,870 rows, 62.7 MB ...)."
- "The result and receipt fix selected_action/executed_action to None (shadow.py:536-537,
  562-563); the only reader, summarize_shadow_receipts (shadow.py:590), is reachable solely
  via `python -m z0int.cognition.shadow`, which nothing invokes."
- "In the live call the extension marks every tool legal by construction (index.ts:342-357),
  so the compile filters nothing, and the cascade is built with no tiers (shadow.py:496)."
- "It only guesses the next tool name, so even a perfect answer saves no frontier call."
- "This is the server the shadow lane needs; with it absent the lane has produced no model
  selections." (nothing listened on 11500 on the audited host)

For: none measured. The audit kept one file on its own evidence:
`src/z0int/cognition/adapters/transport.py` (184 lines, standard library only) "carries the
actual worker dispatch call for the routing service"; that belongs to the router, not to
this lane.

## Did it run live on the audited host

Partly. The extension and the bridge op ran on every OMP tool call (the shadow log was
about 63 MB and written the day of the audit). The model server did not:
`z0-cognition.service` and `z0-slm.service` were not installed and nothing listened on
127.0.0.1:11500, so the rows record a compile and no model choice. Whether either unit is
installed on another host was not verifiable.

## Known defects

- No promotion path: nothing in the repo consumes a shadow result or executes a cascade
  outcome (`cascade.py:626` `mark_executed` has no non-test caller).
- Shadow is on by default (`local-cognition/index.ts:197-203`); the extension's default
  model `functiongemma_270m` (`index.ts:34`) is not a role default in the manifest.
- `cognition/mutation_policy.py` has no importer outside its test.
  `cognition/orchestration.py` and `cognition/adapters/bounded.py` are reached only from
  the eval scripts.
- `z0int cognition probe` shells out to `scripts/probe_local_cognition.py` by path
  (`cognition/cli.py:170-173`); it breaks on a non-checkout install.
- `deploy/systemd/z0-slm.service` is kept because it was assigned here, but the audit's
  second pass found it is the llama.cpp host server for the `llama_http` decision backend
  (port 11510), also referenced by `deploy/lab/up:70`, which is not on this branch. It is
  not what the shadow lane calls.
- `src/z0int/cli.py` is kept as a hub and still registers every subcommand. Subcommands
  backed by these removed modules fail at call time with ImportError: `abab`,
  `ao_experiment`, `ao_report`, `aodl`, `cascade`, `claude_code_launch`, `doctor`,
  `hermes_decisions`, `onboard`, `os_context`, `refinement`, `replay`, `routines`,
  `specialists`, `state_packet`, `status`, `task_loop`. `cli.py` also has a function-level
  import of `z0int.autoresearch.research.pipeline`, which did not exist at `dd39238`.
- `pyproject.toml`: the nine console scripts whose modules are gone were removed
  (`openjev-*`, `z0int-compile`, `z0int-train-next-action`, `z0int-image-decide`);
  dependencies are untouched. `README.md` and `zer0.repo.yaml` are the old whole-repo files
  and describe paths that are not here; `tests/test_architecture_manifest.py` was dropped
  for that reason.
- `omp-extensions/local-cognition/shadow.real-omp.test.ts` is skipped unless
  `Z0INT_COGNITION_LIVE_SLM` is set; the real-OMP path was not exercised here.
- The other bridge ops (`turn_open`, `turn_close`, `file_search`, `decision`) are present
  only because the worker is shared; the audit lists their own defects
  (`bridge/generation.py` mis-quarantines with two OMP sessions).

## Tests dropped

37 test files, each because the module it covers is not on this branch (alignbench, ao
experiment/report, aodl, claude_code*, decision_opportunity, free_fanout, hermes adapter and
decisions, image decision, openjev_phase1 jevlike/reranker/serial/shared/vllm_route,
memory_contract, optchat, os_context, state_packet, task_loop, worker_activity, abab,
battery, cascade, credit, families, onboard, refinement, routines, golden-canary wrapper,
aodl_observation, and `test_architecture_manifest.py` as noted). No test of a kept module
was dropped or edited.

## To earn a place on master

One measurement: on recorded OMP sessions, a local model's shadow choice, scored against the
tool the frontier model actually called, is good enough that acting on it would remove a
frontier call (or a retry) per task, at a latency below the call it replaces. That needs the
model server actually running and a consumer that acts on `selected_action`; neither exists
today. Guessing the next tool name alone does not save a frontier call, so the measurement
has to be of an acting decision, not of agreement.

Smallest subset worth taking if that is ever shown:

- `src/z0int/cognition/actions.py` (legal-action compiler), `cognition/manifest.py`,
  `cognition/registry.py`, `cognition/adapters/{local_slm,dialects,transport}.py`,
  `manifests/local_cognition.v1.json`, and `tests/test_cognition_{compiler,adapters,manifest}.py`.
- `cognition/adapters/transport.py` alone is worth taking now if the router is kept: move
  it next to `worker_routing.py` so the router stops importing the whole package.

Not worth taking as is: `shadow.py`, the extension, `server.py`/`serving.py`/`probe.py`,
the cascade/escalation/surface/candidates/receipts layer.
