# donor/decision-backends

Cut from `dd39238` (tag `archive/pre-reorg-dd39238`) as one commit that removes everything
outside this feature. File history and blame are intact for what is kept.

## What it is

A registry of local "decision backends" (small models that answer a bounded question such as
"is a worker needed" or "is this evidence sufficient") behind one `DecisionBackend`
interface, with a benchmark harness that scored them, a model manifest/downloader, and the
`openjev_phase1` research package and OMP extensions the first backends came from. It also
carries the day-one prototype (`compile.py`, `train.py`, `families.py`) and the typed
verifier functions built on the Laya and Jev backends.

## Entry points

- `src/z0int/backends/registry.py:54` `create_backend(name)`; `:89`
  `register_builtin_backends` (decider_2b, julia_1, laya_421m, llama_http, nanojev,
  openjev_06b); interface in `src/z0int/backends/base.py`.
- `src/z0int/cli.py:513` `z0int backends list|eval|bench` (`:515`, `:524`, `:528`) ->
  `src/z0int/backends/bench/runner.py:301` `run_bench`.
- `src/z0int/cli.py:301` `z0int models plan|sync` -> `src/z0int/models_mgmt.py`.
- `src/z0int/functions/jev.py:214` `assess_claim` (paid remote call through the TypeSafe
  SDK client), `:131` `JevVerifier`; `src/z0int/functions/laya.py:86` `LayaVerifier`;
  `src/z0int/functions/verify_evidence_sufficiency.py:191` `verify_with_escalation`.
- Console scripts kept in `pyproject.toml`: `z0int`, `z0int-compile`
  (`src/z0int/compile.py:96`), `z0int-train-next-action` (`src/z0int/train.py:85`),
  `openjev-score` (`src/openjev_phase1/cli.py:33`), `openjev-data|train|eval|predict`
  and `openjev-bridge-score` (`src/openjev_phase1/jevlike/`).
- `omp-extensions/openjev/index.ts:141` tool `openjev_decide` -> `:67` spawns
  `omp-extensions/openjev/worker.py`; `omp-extensions/openjev-06b/index.ts:137`, `:188`
  -> `:64` spawns its `worker.py`.
- Scripts: `scripts/bench-host-roster.sh` (runs `z0int backends bench`),
  `scripts/bench_pareto_table.py`, `scripts/evidence-sufficiency-roster.sh`,
  `scripts/race-omp-backends.py` (imported by path from `functions/jev.py:34-48` to load
  the Jev credential), `scripts/setup-julia.sh`, `scripts/with-gguf-router.sh`,
  `scripts/get_wikispeedia.sh`, `benchmarks/julia_claims/*.py`,
  `benchmarks/local_evidence_sufficiency.py`.

Checked in the test environment: every module left under `src/` imports
(`make_donor.py --verify-imports`: 89 modules), including `z0int.cli`,
`z0int.backends.registry`, each backend module, `z0int.backends.bench.runner`,
`z0int.functions.*`, `z0int.models_mgmt`, `z0int.train`, `z0int.compile`,
`z0int.families`, `openjev_phase1.cli`, `openjev_phase1.jevlike.*`,
`openjev_phase1.vllm_route.*`. `python -m z0int backends list` and `z0int models plan` run
and report every backend as not ready (no weights, no server, no `laya` package in that
environment). No model was loaded and no backend produced a decision in this check.

Not on this branch, on purpose:

- `src/z0int/intelligence.py:110-117`, the one live call site (`create_backend('laya_421m')`
  behind `function=verification_needed, experimental=true`). It belongs to the router.
- `src/z0int/bridge/decision_cache.py` and the bridge `decision` / `decision_warm` ops (the
  resident decider). The assignment listed the file as shared; keeping it means keeping the
  whole bridge worker and its closure. The audit found no sender for op `decision`. Its
  test `tests/test_bridge_decision.py` is dropped with it. Both are on
  `donor/cognition-shadow`.

## Tests

    pip install -e '.[test]'        # also needs the tokenomics python package on PYTHONPATH
    pytest -m 'not live' -q

Result on this branch, isolated environment (empty HOME): `487 passed, 2 skipped, 5 deselected` (full tree at
`dd39238`: 1164 passed, 2 skipped, 5 deselected). No kept extension has bun tests.
`test_jevlike_smoke.py` needs torch; CI installs the CPU wheel for it.
`.github/workflows/core-unit.yml` is trimmed by line removal only (py_compile entries,
unittest ids and steps whose subject is gone, including the local-cognition bun job).

The feature's own tests: `test_backend_{bench,contract,device,health_offline}.py`,
`test_bootstrap.py`, `test_eligibility.py`, `test_tokenomics_bridge.py`,
`test_mutation_retry_bench.py`, `test_{decider,julia,laya,llama_http,openjev}_backend.py`,
`test_nanojev_{adapter,parity}.py`, `test_image_decision_backend.py`,
`test_functions_verify.py`, `test_typesafe_sdk_adapter.py`, `test_core.py`,
`test_reranker.py`, `test_serial.py`, `test_shared.py`, `test_vllm_route.py`,
`test_jevlike_smoke.py`, `test_z0int_families.py`.

## Extra code kept to stay green

- `src/z0int/cli.py`, `__main__.py`: the `backends` and `models` subcommands live there and
  `test_backend_contract.py` calls `z0int.cli.main`. Kept as a hub (module-level imports
  followed only).
- `src/z0int/cognition/` (all 22 files), `manifests/local_cognition.v1.json`,
  `scripts/probe_local_cognition.py` and the cognition tests: `cli.py:31` imports
  `cognition.cli` at module level, so the CLI does not import without the package.
- `src/z0int/context_resolve.py`, `file_search.py`, `memory/`: imported by
  `tests/test_functions_verify.py`, which covers `functions/verify_evidence_sufficiency.py`.
  Their own tests (`test_context_resolve.py`, `test_file_search.py`,
  `test_memory_*.py`) ride along.
- `src/z0int/counterfactual.py`: needed by `tests/test_receipt.py`, which covers the kept
  `receipt.py`.
- `src/z0int/receipt.py`, `paths.py`: shared helpers, duplicated across donors on purpose.
- `src/z0int/backends/image_decision.py` and its test stay with the package; its CLI
  (`image_decision_cli.py`, console script `z0int-image-decide`) belongs to another donor
  and is removed here.
- `results/decision-backends/` (31 files), `results/julia-claims/` (34),
  `results/evidence-sufficiency/` (9): the study outputs. One test reads two files in
  `results/decision-backends/20260930-mbp-dcv1/`; the rest are kept as the evidence.
- `examples/jevlike/` (33 files, including two `.pt` checkpoints): trained and played
  through `openjev_phase1.jevlike`.

## Cost on a turn's hot path

None by default: no hook or automatic path calls a backend. Where it can be reached:

- Laya through the router's opt-in branch: p50 5.4 s on CPU per call (study figure).
- The OMP extensions `openjev` and `openjev-06b` cost prompt tokens on every turn without
  being called: "two system-prompt sentences (about 90 tokens) plus three tool schemas on
  every OMP turn".
- A decider prewarm (not on this branch) "costs load time and about 5 GB RSS for nothing".
- Importing `z0int.backends.registry` imports all six backend modules; torch and
  transformers are imported lazily, on load.

## Evidence

All from the audit and the committed study under `results/decision-backends/`
(`20260930-mbp-dcv1`, 11 fixtures; `20260930-mbp-tfs-sealed77`, 77 fixtures).

Against:

- "The study these exist for found no eligible backend (pareto.md: empty; best arm 44/77 at
  p50 5.1 s)."
- laya_421m: "6/11 at p50 5.4 s CPU ... and 15/77 on sealed77"; the capability manifest
  records "n=2, eligible:false".
- decider_2b: "p50 17.4 s CPU, 7/11 on dcv1 and 31/77 on sealed77".
- nanojev: "4/11 at p50 9.3 s on dcv1, excluded as unsafe in pareto.md".
- openjev_direct: "5/11 at p50 7.9 s on dcv1, 32/77 on sealed77".
- julia: "5/11 on dcv1 and 8/77 on sealed77, excluded as unsafe on worker_needed".
- `openjev` / `openjev-06b` extensions: "NEGATIVE: the system-prompt line and essential tool
  schema are injected every turn, but route.log ... does not exist, so the tool has never
  been called on this host." (A second auditor found a 5.8 KB `route.log` for `openjev`;
  the two maps disagree on whether it was ever called.)
- `compile.py` / `train.py` / `families.py`: "No live module loads the trained ridge model."
- `functions/{__init__,laya,verify_evidence_sufficiency}.py`: "referenced only in
  tests/test_functions_verify.py".

For:

- llama_http is "the only torch-free backend and the one worth reviving if a local decision
  is ever wired in"; its best arm (qwen3_17b_q4) scored 44/77 at p50 5.1 s, "still below
  any gate".
- `functions/jev.py` `assess_claim` is "the only typed function with admission evidence
  (46/48 ...), but it is a paid remote call and no per-turn token or latency saving is
  recorded".
- The harness did its job: it "produced one decision: no backend is eligible".

## Did it run live on the audited host

Mostly no. The audit marks `backends/{__init__,base,registry}.py` and `functions/jev.py` as
loaded by the running routing service, and laya/decider as "unknown" (reachable only with an
opt-in flag or an environment variable nothing sets). nanojev, openjev_direct, julia,
llama_http, the bench harness, `compile.py`, `train.py` had no live caller. The two OMP
extensions were symlinked and loaded every session; their workers were not running.
`deploy/systemd/z0-slm.service` (the llama.cpp server `llama_http` expects on port 11510)
was not installed there and is not on this branch (it was assigned to
`donor/cognition-shadow`).

## Known defects

- `omp-extensions/openjev/index.ts:18` and `openjev-06b/index.ts:15` default to a
  host-specific interpreter path unless `OPENJEV_PYTHON` is set;
  `src/z0int/backends/julia.py:74` also hardcodes a host path;
  `scripts/race-omp-backends.py:18` points at an OMP extension that is not on this branch;
  `compile.py:21` defaults to a database path that exists on one machine.
- `omp-extensions/openjev-06b/worker.py` is byte-identical to `omp-extensions/openjev/worker.py`.
- `functions/jev.py` imports `scripts/race-omp-backends.py` by filesystem path; it breaks
  on any non-checkout install.
- `src/z0int/preflight.py` (not here) looks for `decide`/`predict` on the nanojev backend,
  which only has `evaluate`.
- `manifests/models.yaml` is a hand-kept twin of `models.z0int.json` with no sync test;
  `models.json` is an unreachable fallback (`models_mgmt.py:29-31`).
- `bench/runner.py:70` writes runs into `results/decision-backends/` inside the checkout
  by default.
- `src/z0int/cli.py` still registers every subcommand. Those backed by removed modules
  fail at call time with ImportError: `abab`, `ao_experiment`, `ao_report`, `aodl`,
  `artifacts`, `autoresearch`, `capabilities`, `cascade`, `claude_code_launch`,
  `contrastive_evidence`, `data_recipe_race`, `doctor`, `hermes_decisions`,
  `kerdoios_export`, `onboard`, `os_context`, `posture`, `preflight`, `refinement`,
  `replay`, `routines`, `specialists`, `state_packet`, `status`, `task_loop`. `cli.py`
  also has a function-level import of `z0int.autoresearch.research.pipeline`, which did not
  exist at `dd39238`. `z0int models` works, but `doctor`/`onboard`/`status`, which also
  use `models_mgmt.py`, are gone.
- `pyproject.toml` is unchanged except that the `z0int-image-decide` script entry is
  removed. `README.md` and `zer0.repo.yaml` are the old whole-repo files;
  `tests/test_architecture_manifest.py` was dropped for that reason.
- `docs/vllm-diffusion-route.md` describes a route whose extension (`omp-extensions/vllm-jev`)
  is not on this branch; `openjev_phase1/vllm_route/` (its client) is.

## Tests dropped

68 test files, each because the module it covers is not on this branch: the agentweb_*,
ao_*, aodl_*, outcome_*, claude_code_*, intelligence*, worker_routing*, posture*,
dispatch/free-only/quota/provider-saturation, bridge worker and bridge decision, governed
worker, hermes, memory_contract, optchat, os_context, state_packet, task_loop,
tokenomics_emit, worker_activity, abab/aodl/battery/cascade/credit/onboard/refinement/routines
groups, the golden-canary and OMP-hook wrappers, and `test_architecture_manifest.py`.
No test of a kept module was dropped or edited.

## To earn a place on master

One measurement: a local backend that passes the eligibility gate on the sealed 77-fixture
set (the best so far is 44/77) at a latency below the frontier call it would replace, plus
one consumer on a real turn path that acts on its answer. Today there is neither; the only
call site is opt-in and marked `eligible:false`.

Smallest subset worth taking if that happens: `src/z0int/backends/base.py`,
`backends/registry.py` cut to one entry, `backends/llama_http.py` (no torch),
`tests/test_llama_http_backend.py`, `tests/test_backend_contract.py`, and
`benchmarks/fixtures/decision-capability-v1/examples.jsonl` with
`results/decision-backends/fixtures-derived/` as the gate. The bench harness
(`backends/bench/`, 13 files) is worth keeping outside master as the measuring tool, with
its results. The torch backends, `openjev_phase1`, the two extensions, `compile.py`,
`train.py` and `families.py` have no evidence in their favour.
