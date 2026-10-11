# Donor: agentweb-aodl-routes

Cut from `archive/pre-reorg-dd39238` (dd39238d). One commit on top of it removes
everything this feature does not need. The audit found no caller for any of
these routes and no measured effect; they are preserved here in working order
in case an external client turns out to exist.

## What it is

The intelligence service's integration and observation surface: a versioned
AgentWeb bridge envelope with capability negotiation and a context-packet
compiler, append-only ingest routes for outcomes, expectations, randomized
assignments, verified event batches and reliability observations, shadow
choice/noul experiments, and an agent-orchestrator spawn-decision/outcome
bridge. With it come the AODL pieces: the admission gate used on dispatch
claims, a drift projector, and the older AODL document compiler over
routines and cascades.

## Entry points

All HTTP routes are served by `python -m z0int.intelligence_service`
(`src/z0int/intelligence_service.py:210`).

| Route or command | Where |
| --- | --- |
| `GET /v1/bridge/capabilities` | `intelligence_service.py:126` -> `agentweb_bridge_capabilities.py:14` |
| Envelope unwrap on `/v1/intelligence`, `/v1/plan`, `/v1/experimental/*` | `intelligence_service.py:160` -> `agentweb_bridge_wire.py:17`, `:69` |
| `POST /v1/agentweb`, `/v1/agentweb/observation` | `intelligence_service.py:177`, `:180` -> `agentweb_bridge.py:541`, `:352` |
| `POST /v1/context/pack` | `intelligence_service.py:189` -> `agentweb_context_packet.py:228` |
| `POST /v1/observe/reliability` | `intelligence_service.py:184` -> `reliability_observation.py:89` |
| `POST /v1/observe/outcome` | `intelligence_service.py:185` -> `outcome_observation.py:180` |
| `POST /v1/observe/outcome/expectation`, `GET /v1/observe/outcome/coverage` | `intelligence_service.py:186`, `:127` -> `outcome_coverage.py:143`, `:260` |
| `POST /v1/observe/outcome/assignment`, `GET /v1/observe/outcome/randomized-evidence` | `intelligence_service.py:187`, `:128` -> `outcome_randomized_evidence.py:140`, `:274` |
| `POST /v1/observe/event-batch` | `intelligence_service.py:188` -> `verified_event_ingress.py:141` |
| `POST /v1/experimental/choice`, `/noul` (need `Z0INT_EXPERIMENTAL_JEV_SHADOW=1`) | `intelligence_service.py:198` -> `decision_experiment.py:262`, `:284` |
| `POST /v1/integrations/agent-orchestrator/spawn-decision`, `/outcome` | `intelligence_service.py:168`, `:171` -> `ao_bridge.py:140`, `:273` |
| AODL admission on a dispatch claim (`/v1/authority/*`) | `dispatch_authority.py:125` -> `aodl_dispatch.py:130` -> `aodl_admission.py:124` |
| Protocol validators (library) | `agentweb_bridge_protocol.py:123`, `:185` |
| AODL drift projector (library, no caller) | `aodl_observation.py:55`, `:164` |
| AODL document compiler, `python -m z0int.aodl` | `aodl.py:936`, `:295` (`compile_aodl`) |
| AO pair registry and report (library on this branch) | `ao_experiment.py:214`, `ao_report.py:420` |

Modules verified to import on this branch: `z0int.intelligence_service`,
`z0int.agentweb_bridge`, `z0int.agentweb_bridge_protocol`,
`z0int.agentweb_bridge_wire`, `z0int.agentweb_bridge_capabilities`,
`z0int.agentweb_context_packet`, `z0int.aodl`, `z0int.aodl_admission`,
`z0int.aodl_dispatch`, `z0int.aodl_observation`, `z0int.ao_bridge`,
`z0int.ao_experiment`, `z0int.ao_report`, `z0int.outcome_coverage`,
`z0int.outcome_observation`, `z0int.outcome_randomized_evidence`,
`z0int.reliability_observation`, `z0int.verified_event_ingress`,
`z0int.decision_experiment`.

## Running the tests

```
pip install -e '.[test]'
PYTHONPATH=src python -m pytest -m 'not live' -q
node --test tests/test-governed-client.mjs
```

Result on this branch (offline, with the `tokenomics` and `kerdoios` checkouts
on the path as in the full-tree baseline): 747 passed, 2 skipped, 5 deselected, 0 failed, 0 collection errors.
`node --test tests/test-governed-client.mjs`: 4 pass, 0 fail.

The feature's own tests: the eleven `tests/test_agentweb_*.py`,
`test_ao_bridge.py`, `test_ao_experiment.py`, `test_ao_report.py`,
`test_aodl_admission.py`, `test_aodl_observation.py`, `test_z0int_aodl.py`,
`test_outcome_coverage.py`, `test_outcome_observation.py`,
`test_outcome_randomized_evidence.py`, `test_verified_event_ingress.py`,
`test_typesafe_sdk_adapter.py`, `test_golden_canary_wrapper.py`.

Workflows kept unmodified: `aodl-admission.yml` (every path it names exists
here; needs the external `aodl_contract` package), `agentweb-integration-lab.yml`
(PRs to master), and three lab workflows whose triggers are the branches
`rfc/agentweb-z0-bridge-v1` and `lab/agentweb-emma-v0`; the audit found those
branches do not exist on origin, so those three never fire.

## Cost on a turn's hot path

One piece is on a live path: the wire shim runs on every `POST
/v1/intelligence` (`intelligence_service.py:160`), i.e. on every
`route_worker` call, whether or not the caller is AgentWeb. It is a dict
inspection; no timing was recorded. `intelligence_service.py:15-19` also takes
its connection cap and listen backlog (`MAX_ACTIVE`, `SOCKET_BACKLOG`) from
`agentweb_bridge_capabilities.py`, for all routes.

Everything else costs nothing on a turn unless an external client calls the
route. Each ingest route then does a digest and a locked JSONL append. The
experiment routes make paid TypeSafe calls when enabled.

## Evidence (quoted from the audit of dd39238)

- AgentWeb bridge modules (1,619 lines): "NONE verified.
  docs/agentweb-emma-integration.md:3 calls it a 'downstream-only experiment
  surface'; I could not confirm a single request (the service suppresses
  request logging at intelligence_service.py:118)." "No caller of these routes
  in this repo; kvnloo/agentweb default-branch file tree has no file named
  z0*/intelligence*." Verdict UNSURE, confidence medium.
- Observation modules (1,520 lines): "NONE. None of
  agentweb_outcome_observations.jsonl, agentweb_outcome_expectations.jsonl,
  agentweb_outcome_assignments.jsonl or agentweb_verified_events.jsonl exists
  ..., so no row has ever been accepted on the one host that runs the
  service." "Records data nobody sends and nobody reads".
- ao_bridge.py: "NONE. The reply is hard-coded advice with reason
  'shadow_baseline_no_learned_policy' (ao_bridge.py:132,194); on this host 8
  spawn and 5 outcome lock files all date from 2026-10-07 and
  receipts/ao-outcomes.jsonl does not exist." "The single day of activity
  looks like a proof run, not ongoing use."
- ao_experiment.py, ao_report.py, counterfactual.py (1,365 lines): "NONE.
  receipts/ao-experiment-pairs.jsonl does not exist"; ao_report "'never
  promotes a policy' by its own docstring".
- decision_experiment.py: "NONE. Docstring: 'deliberately not production
  capability routes'; receipts are execution=shadow,
  quality_authoritative=false."
- aodl_admission.py + aodl_dispatch.py: "Allow/deny decision on governed
  remote dispatch ... It is a safety gate, not a token or latency saving; no
  measured number found." "/readyz fails without it".
- aodl_observation.py: "NONE. Imported only by tests/test_aodl_observation.py".
- aodl.py: "NONE" (CLI-only; "depends on the dead routines.py and cascade.py.
  Not the same thing as the live AODL admission gate").
- Service: "Service /metrics has no per-route counter ..., so nothing proves
  these routes are ever called"; "15 of 18 POST routes have no client in the
  repo and the service cannot say whether they are ever hit."

The audit could not see the private AgentWeb repository's non-default
branches or the service's request history, so "no caller" means none found,
not none exists.

## Did it run live on the audited host

The service that hosts the routes was running. No request to any of these
routes was confirmed. The observation ledgers did not exist; the
agent-orchestrator route showed one day of activity (2026-10-07). Whether the
experiment flag was set in the host's service environment was not checked.

## Known defects

- No per-route counters and request logging is suppressed, so use cannot be
  established from the service.
- `agentweb_bridge_protocol.py` and `agentweb_bridge.py` both validate the
  same envelope; `schemas/agentweb-z0-bridge.v1.schema.json` is a third
  statement of it that no code or test loads.
- The five ingest modules repeat one digest-plus-locked-append pattern.
- `AODL_COMPAT.md` cites `profiles/intent-contract.md`, which does not exist.
- `src/z0int/cli.py` is not on this branch. `ao_experiment.py`, `ao_report.py`
  and `counterfactual.py` had no entry point other than `z0int ao ...` and
  `z0int counterfactual ...`, so here they are importable and tested but have
  no command. `aodl.py` keeps its own `python -m z0int.aodl`. The tests that
  drive `cli.py` (`test_receipt.py`, `test_posture.py`, ...) are not here.
  `[project.scripts]` was emptied of entries whose module is gone.
- `aodl.py` imports `routines.py` and `cascade.py`, which are kept with their
  tests; `refinement.py` and `task_loop.py` are not, so
  `test_z0int_refinement*.py` and `test_task_loop.py` are not here.
- `scripts/prove-intelligence-omp.mjs` is kept only because
  `aodl-admission.yml` runs `node --check` on it; it loads
  `omp-extensions/z0int-bridge/index.ts`, which is not here, so it cannot run.
- `scripts/run-aodl-golden-canary.py`, `scripts/prove-intelligence.py` and
  `scripts/prove-governed-omp-tool.mjs` need a live service, credentials, an
  OMP install and a z0evals checkout; only the mocked wrapper test covers
  them.
- `tests/test_hot_path.py` is not here (needs `z0int.bridge`); two of its five
  tests touch `quota_budget` and `worker_routing`, kept here as closure.
- `tests/test_architecture_manifest.py` is dropped by design: it asserts every
  path in `zer0.repo.yaml` exists. `zer0.repo.yaml` and `README.md` are the
  unmodified whole-repo files.
- `core-unit.yml` was trimmed by line removal to the paths that exist here.

## Why the branch is 200-odd files

The routes live in `intelligence_service.py`, which is also the router's
service. Keeping it importable keeps `intelligence.py`, `worker_routing.py`,
`provider_saturation.py`, `functions/`, the routing manifests, and through
`worker_routing.py:13` and `intelligence.py:12` the `cognition/` and
`backends/` packages, plus the tests of all of those. This overlaps
`donor/router` and `donor/governed-remote-executor` on purpose.

Kept beyond the assignment: `docs/dispatch-authority.md`,
`tests/test-governed-client.mjs` and `scripts/prove-intelligence-omp.mjs`
(named by `aodl-admission.yml`); `src/z0int/free_fanout.py` and
`src/z0int/intelligence_mcp.py` (so `test_free_fanout_free_only.py` and
`test_intelligence_canonical.py`, which also assert on `worker_routing` and
`intelligence`, still run).

## To earn a place on master

One fact from outside the repository: a named client that calls a route
today (AgentWeb/Emma for `/v1/agentweb`, `/v1/context/pack`, `/v1/observe/*`;
the agent orchestrator for `/v1/integrations/agent-orchestrator/*`), plus a
decision somewhere that reads what the route stores. Adding a per-route
counter to the service and watching it for a week would settle it.

Smallest subset worth taking, per consumer:

- AgentWeb envelope: `agentweb_bridge_wire.py`, `agentweb_bridge_protocol.py`,
  `agentweb_bridge_capabilities.py` and their three tests (drop
  `agentweb_bridge.py`'s duplicate validator).
- Context packet: `agentweb_context_packet.py` and `test_agentweb_context_packet.py`
  (needs `context_resolve.py`).
- Observation: one of the five ingest modules as the pattern
  (`outcome_observation.py`), only together with a reader of its output.
- AODL admission: `aodl_admission.py`, `aodl_dispatch.py`,
  `test_aodl_admission.py`; these travel with the governed executor, not with
  AgentWeb.

Not worth taking on current evidence: `ao_experiment.py`, `ao_report.py`,
`aodl_observation.py`, `aodl.py`, `decision_experiment.py`, the lab workflows,
the schema file.
