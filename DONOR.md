# Donor: evals-artefacts

Cut from `dd39238` (tag `archive/pre-reorg-dd39238`) as one commit. This branch is a
parts shelf, not a product: nothing here is on master unless it is salvaged later.

## What it is

The repo's evidence and research record: committed benchmark results
(`results/`), the benchmark builders, runners and fixtures (`benchmarks/`), the
demos and examples (`demo/`, `assets/`, `webgpu-demo/`, `examples/jevlike`,
`examples/aodl`), the dated reports and historical docs, and the scripts that
read them. It also carries the `src/` code those scripts import, so they run.

About 161,000 lines, of which `results/` is about 103,000. It is data, not a
feature: nothing here runs in an agent turn.

## Entry points

| What | Where | Status on this branch |
|---|---|---|
| Published-claims check | `benchmarks/verify_published.py:30`; CI `.github/workflows/core-unit.yml:68-69` | ran: `{"verified_summary_claims": 69, "status": "ok"}` |
| Raw result checksums | `results/raw/SHA256SUMS` (`sha256sum -c`) | ran: all OK |
| Phase 1 builders / evaluators | `benchmarks/build_*.py`, `evaluate.py`, `evaluate_external.py:174`, `evaluate_perturbations.py:30`, `shape777.py:19`, `shape777_reranker.py`, `decision_vs_generation.py`, `fetch_sources.py` | compile; `evaluate.py --help` runs; the model-loading ones need GPU weights and were not run |
| Paired Claude Code harness | `benchmarks/claude_code/run_pair.py:84`, `attribute.py:42` | `--help` runs; a real run calls the paid Claude CLI and was not run |
| Skill-selection eval | `benchmarks/claude_code/skill_select/evaluate.py`, `evaluate_semantic.py` | imports; needs installed Claude Code skills under the home directory, fails with a ZeroDivisionError when there are none |
| State Packet bench | `benchmarks/state_packet/run.py:224`, `action_selector.py`, `pin_fixtures.py` | `--help` runs; a real run calls the paid Claude CLI and was not run |
| Decision-opportunity scripts | `benchmarks/decision_opportunity/falsification.py:30`, `selective_autonomy.py:43` | `--help` runs; need pinned fixtures outside the repo |
| Julia claims | `benchmarks/julia_claims/*.py` (`published_claims.py:171`) | `--help` runs; need the Julia model |
| Evidence-sufficiency roster | `benchmarks/local_evidence_sufficiency.py:27`, `scripts/evidence-sufficiency-roster.sh` | imports; needs local model backends |
| Backend bench | `z0int backends bench` (`src/z0int/backends/bench/runner.py`), `scripts/bench-host-roster.sh`, `scripts/with-gguf-router.sh` | `--help` runs; needs local model backends |
| Pareto table from a run dir | `scripts/bench_pareto_table.py:95` | ran on `results/decision-backends/20260930-mbp-dcv1` and printed the table |
| Cognition eval drivers | `scripts/orchestration_eval.py:362`, `compiler_contract_eval.py:392`, `local_cognition_backend_eval.py:208` | `--help` runs; need a local model server for a real run |
| Browser demo | `webgpu-demo/index.html` (static, no build) | covered by `webgpu-demo/test_demo.py` |
| Replay page | `demo/index.html` | static; not opened |
| Chess / Doom trainable scorers | `examples/jevlike/{chess,doom}/*.py`, `examples/jevlike/film/` | compile; need `chess`, `vizdoom`, GPU; not run |

All 73 modules under `src/` import (74 files; `backends/julia_worker.py` is run as a script); 120 other
`.py` files compile. Importable real entry modules: `z0int.cli`,
`z0int.backends.bench`, `z0int.state_packet`, `z0int.decision_opportunity`,
`z0int.claude_code_skills`, `z0int.claude_code_obs`,
`openjev_phase1.{core,direct,serial,shared,reranker}`,
`openjev_phase1.jevlike.{data,vision}`.

## Tests

```
python -m pytest -m 'not live' -q                    # 508 passed, 2 skipped
(cd results/raw && sha256sum -c SHA256SUMS)          # all OK
python benchmarks/verify_published.py                # 69 claims, status ok
```

Tests that exercise files in this donor directly: `webgpu-demo/test_demo.py`,
`tests/test_backend_bench.py`, `test_mutation_retry_bench.py`,
`test_tokenomics_bridge.py`, `test_cognition_orchestration.py`,
`test_cognition_manifest.py` (they read `benchmarks/fixtures/` and
`results/decision-backends/`). The other 37 kept test files cover the `src/`
modules the benchmark scripts import; they come along so that no kept module
is on the branch without its tests. No bun tests.
`examples/jevlike/*/test_smoke.py` are outside pytest `testpaths`, as at
`dd39238`.

Dropped tests: all have a subject module that is not on this branch (router,
service, bridge, AgentWeb, AO, onboard, launch, compilers). Several of them
also import `z0int.receipt`, `context_resolve` or `models_mgmt`, which are
here; those modules keep their own test files (`test_receipt.py`,
`test_context_resolve.py`, the backend tests). `tests/test_posture_routing.py`
is dropped with the `worker_routing` seam below.
`tests/test_architecture_manifest.py` is dropped: it asserts every path in
`zer0.repo.yaml`, which is kept unmodified as the whole-repo map.

`core-unit.yml` was trimmed by line removals; the "Verify published evidence"
step is kept and passes locally.

## Cost on a turn's hot path

None. No hook, service route, unit or extension reads anything here. The cost
is repository size (about 4.4 MB of demo binaries, three `.pt` checkpoints,
103,000 lines of results) and CI time for the checksum step.

## Evidence (quoted from the audit of `dd39238`)

- Plan, batch 1: "No hook, service route, unit or extension reads any of it."
  "results/ is 103,438 of about 210,900 text lines."
- `results/raw/` + `phase1-summary.json`: verdict ARCHIVE_OUT_OF_REPO. "CI
  re-verifies that a frozen 2026-09-16 research claim still matches its frozen
  rows; the data cannot change, so the check can only fail if someone edits it."
- `results/decision-backends/`: verdict DELETE. "396 of 70,843 lines are read by
  one test; the rest, including two stdout.json dumps totalling 22,908 lines
  and 6,768 lines of router.log, has no reader." What it found: "no eligible
  backend (pareto.md: empty; best arm 44/77 at p50 5.1 s)."
- `benchmarks/claude_code/`: verdict ARCHIVE_OUT_OF_REPO, medium. "Source of the
  lean-profile and ObservationPack savings figures quoted in
  docs/claude-code.md (cold -63.8% median, long-task -30.3% total); not wired
  to CI." "This is the only benchmark here that measures tokens per verified
  task, so it should survive somewhere, just not as unrun files."
- `benchmarks/state_packet/`: "docs/claude-code.md reports held-out 14/14 vs
  12/14 and about -30% input tokens for the packet, which is the kind of number
  the north star asks for, but nothing re-runs or checks it."
- `benchmarks/julia_claims/` + `results/julia-claims/`: ARCHIVE_OUT_OF_REPO.
  "NONE recorded in a form the router consumes."
- `benchmarks/decision_opportunity/`, `image-jev-bench/`,
  `local_evidence_sufficiency.py`, `results/evidence-sufficiency/`: DELETE.
  "No reader; the image fixture is a red square never loaded by its own test."
- `webgpu-demo/`: ARCHIVE_OUT_OF_REPO. "A September marketing demo whose only
  consumer is a test that greps its own HTML on every CI run."
- `demo/` and `assets/`: DELETE. "Unreferenced since 2026-09-16."
- `examples/jevlike`: DELETE. "34 files, committed model weights contrary to
  AGENTS.md:33."
- Historical docs and root reports: ARCHIVE_OUT_OF_REPO / DELETE. "Dated
  reports, research notes, reconciliation ledgers and design-only plans."
- Owner decision 8: "Where research goes: archive tag only, or also push
  results/, benchmarks/ and the phase-1 docs to kvnloo/z0evals. The claude_code
  and state_packet benchmarks are the only ones that measure tokens per
  verified task and are needed for decisions 1-4."

In short: two of the benchmark directories are the source of every positive
number the repo has; the rest records experiments that found nothing usable.

## Did it run live on the audited host

No. Nothing here is on a live path. The benchmark runners were run by hand at
the time the results were committed; none was rerun for the audit or for this
cut.

## Known defects

- `benchmarks/claude_code/run_pair.py:12` and `failure_injection.sh:7` attach
  the plugin `harness-adapters/claude-code-z0intelligence`, which is not on this
  branch (it is on `donor/claude-code-core`). `run_pair.py` cannot run its
  plugin arm here. The ObservationPack plugin directory is kept.
- `benchmarks/claude_code/tasks/{suite-full,suite-triage,debug-suite}/task.json`
  name repository tests that are not on this branch.
- `src/z0int/cli.py` is kept with only its module-level imports followed (it is
  here because `benchmarks/state_packet/run.py:185` and
  `scripts/bench-host-roster.sh:21` run `python -m z0int ...`). Working:
  `context packet`, `backends`, `cognition`, `models`, `receipt`,
  `counterfactual` (mine), `posture`. Every other subcommand raises
  `ModuleNotFoundError` when run (`doctor`, `status`, `onboard`, `claude-code`,
  `ao`, `task`, `routine`, `cascade`, `aodl`, `repair`, `abab`, `hermes`,
  `os-context`, `artifacts`, `contrastive`, `context-state`, `autoresearch`,
  `kerdoios`, `preflight`).
- `src/z0int/posture.py:444` imports `worker_routing`, which is not here; the
  caller at `:511` is fail-open and reports that source as an error.
- `benchmarks/state_packet/action_selector.py:20`, `pin_fixtures.py` and the
  decision-opportunity scripts read pinned fixture repositories from the user's
  private z0int state directory; those are not in git.
- `examples/jevlike` commits three `.pt` checkpoints.
- `pyproject.toml` keeps only the `openjev-data` and `z0int` console scripts.
  `openjev_phase1/cli.py` (`openjev-score`) and `openjev_phase1/vllm_route/`
  are not on this branch, so the `openjev-score` commands in
  `docs/REPRODUCE.md` need `donor/omp-jev-extensions` or
  `donor/decision-backends`.
- `AGENTS.md`, `README.md`, `ROADMAP.md` are the whole-repo versions.
- `docs/REPRODUCE.md` and `docs/METHOD.md` describe commands against the full
  tree; they were not re-walked on this branch.

## Extra files kept beyond the assignment

- `src/` (74 files): `backends/` with `bench/`, `openjev_phase1/` (without
  `cli.py` and `vllm_route/`), `cognition/` (via `cli.py` and the eval scripts),
  `state_packet.py`, `decision_opportunity.py`, `context_resolve.py`,
  `file_search.py`, `memory/`, `posture.py`, `receipt.py`, `counterfactual.py`,
  `models_mgmt.py`, `claude_code_skills.py`, `claude_code_obs.py`, `paths.py`,
  `cli.py`. Each is imported by a benchmark script, an example, or a kept test.
- `manifests/` (16 files): read by the kept backend and cognition tests.
- `scripts/`: `bench_pareto_table.py`, `bench-host-roster.sh`,
  `evidence-sufficiency-roster.sh`, `with-gguf-router.sh`,
  `orchestration_eval.py`, `compiler_contract_eval.py`,
  `local_cognition_backend_eval.py`, `probe_local_cognition.py`: they read or
  write `benchmarks/fixtures/` and `results/`.
- `harness-adapters/claude-code-z0-obspack/`: named by `run_pair.py`.
- `AGENTS.md`: holds the benchmark rules (create-only outputs, checksum and
  `verify_published.py` validation).

Assignment rows not followed: `harness-adapters/claude-code-z0intelligence`
was excluded (it would pull the Claude Code hooks, the router MCP server and
their closure); `examples/refinement/` is not in this feature.

## To earn a place on master

Nothing here should return to master as files. What could justify taking a
piece:

- `benchmarks/claude_code/` (run_pair.py, attribute.py, tasks/) and
  `benchmarks/state_packet/`: needed the moment someone runs owner decisions
  1-4 (lean profile, ObservationPack net effect, State Packet A/B). That is the
  consumer. Take those two directories, or better, run them from this branch
  or from `kvnloo/z0evals` and commit only the one-line result.
- `results/decision-backends/20260930-mbp-dcv1/{raw.jsonl,tokenomics-events.jsonl}`
  (396 lines) and `benchmarks/fixtures/{decision-capability-v1,mutation-retry-v0,orchestration-v1}`:
  only if the backend bench or cognition tests are salvaged; they are test
  inputs.
- Everything else: archive only.

Uncertain: whether `kvnloo/z0evals` already holds copies of `results/raw` and
the phase-1 docs. Not checked.
