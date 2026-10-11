# Donor: governed-remote-executor

Cut from `archive/pre-reorg-dd39238` (dd39238d). One commit on top of it removes
everything this feature does not need. Nothing here is on master's hot path; it
is kept so the code can be salvaged later if it earns it.

## What it is

A "governed" variant of `route_worker`: the host intelligence service checks a
worker request against an AODL contract, records an admission receipt, and
forwards the bounded worker call to a stateless executor running off-host (a
pod in a Kind cluster). The executor claims the dispatch from the host
authority over HTTP, runs one provider call, and reports receipts back.

## Entry points

| What | Where |
| --- | --- |
| Executor process, `python -m z0int.remote_executor serve` (port 11503) | `src/z0int/remote_executor.py:118` (`main`), `:37` (`execute`), `:17` (authority RPC client) |
| Executor image and manifests | `deploy/executor/Dockerfile:8`, `deploy/k8s/intelligence-executor.yaml`, `deploy/k8s/intelligence-host-service.yaml`, `deploy/lab/up` |
| Host route `POST /v1/governed-worker` | `src/z0int/intelligence_service.py:174` -> `src/z0int/governed_worker.py:325` (`execute`) |
| Opt-in switch `Z0INT_GOVERNED_REMOTE=1` | `src/z0int/governed_worker.py:257` (`enabled`), `harness-adapters/governed-client.mjs:7` |
| Request builder against the contract | `src/z0int/governed_worker.py:190`, `:261`; contract `contracts/aodl/governed-worker-v1.json` |
| Authority RPC `POST /v1/authority/*` (claim, emit, complete) | `src/z0int/intelligence_service.py:162` -> `src/z0int/dispatch_authority.py:217` (`rpc`), `:118` (`claim`), `:99` (`validate_remote`) |
| AODL admission on a claim | `src/z0int/dispatch_authority.py:125` -> `src/z0int/aodl_dispatch.py:130` (`ensure`) -> `src/z0int/aodl_admission.py:124` (`decide_spawn`) |
| OMP tool that selects governed or plain routing | `omp-extensions/z0int-intelligence/index.ts:40` -> `harness-adapters/governed-client.mjs:1` |
| Host service the above runs inside | `python -m z0int.intelligence_service`, `src/z0int/intelligence_service.py:210` |

Modules verified to import on this branch: `z0int.governed_worker`,
`z0int.remote_executor`, `z0int.intelligence_service`,
`z0int.dispatch_authority`, `z0int.worker_routing`.

## Running the tests

```
pip install -e '.[test]'
PYTHONPATH=src python -m pytest -m 'not live' -q
node --test tests/test-governed-client.mjs
```

Result on this branch (offline, with the `tokenomics` and `kerdoios` checkouts
on the path as in the full-tree baseline): 718 passed, 2 skipped, 5 deselected, 0 failed, 0 collection errors.
`node --test tests/test-governed-client.mjs`: 4 pass, 0 fail.
The feature's own tests are `tests/test_governed_worker.py`,
`tests/test_dispatch_authority.py`, `tests/test_aodl_admission.py`,
`tests/test_golden_canary_wrapper.py` and `tests/test-governed-client.mjs`.
`.github/workflows/aodl-admission.yml` is kept unmodified and every path it
names exists on this branch; it additionally needs the external `aodl_contract`
package (the workflow installs it from git).

## Cost on a turn's hot path

None while the flag is off, which is the default. With
`Z0INT_GOVERNED_REMOTE=1` a `z0int_route_worker` tool call becomes: host
service -> AODL admission (one receipt append, ledger re-read) -> HTTP to the
executor -> three authority RPCs back to the host -> one provider call.

There is a standing cost even when the flag is off, if the executor pod is
left running: its readiness probe calls `GET /v1/providers` on the host every
5 s with a 2 s timeout (`remote_executor.py:99`,
`deploy/k8s/intelligence-executor.yaml`), and each such call makes the host
re-parse the receipts ledger once per provider.

## Evidence (quoted from the audit of dd39238)

- governed_worker.py: "NONE. Live /v1/providers reports
  governed_remote_enabled:false, so the path is never taken on this host."
  Verdict ARCHIVE_OUT_OF_REPO, confidence medium. "Feature-flagged off, single
  commit (b345aa2, 2026-10-02), described in its own docstring as a canary".
- remote_executor.py: "Negative. The process is running ... in the local kind
  cluster but receives no work because governed remote is off; its 5-second
  readiness probe is the likely source of the 58,159 connections and the
  constant CPU load on the authority." A second auditor: "its poll is the
  likely source of the 684 BrokenPipe tracebacks in the service journal (all
  at the /v1/providers reply)". Both say "likely"; neither proved it.
- Host service while this ran: "26% of a core and 721 MB for 92 dispatches in
  28.8 h"; "GET /v1/providers measured 1.0-2.0 s".
- dispatch_authority.py: "lines 99-249 serve only the governed remote
  executor, which is disabled on this host, and should leave with it." Lines
  1-96 are the live idempotency ledger used by every dispatch.
- aodl_admission.py + aodl_dispatch.py: "It is a safety gate, not a token or
  latency saving; no measured number found." `/readyz` on the host service
  fails without it.
- deploy/k8s, deploy/executor, deploy/lab: "NONE recorded"; verdict UNSURE,
  "Keep only if the remote-executor lane is run on another fleet host".
- Proof scripts (`scripts/prove-*.py|mjs`, `run-aodl-golden-canary.py`):
  "NONE; CI never executes them."

There is no measurement of tokens or latency per task for this feature, in
either direction.

## Did it run live on the audited host

Partly. The executor pod was running and idle; the governed path itself was
disabled (`governed_remote_enabled:false`), so no governed dispatch was
observed. The audit did not read the host's service environment file and did
not inspect the pod or its image. Whether another fleet host runs it is not
known.

## Known defects

- The idle executor's readiness probe loads the host service (see above).
- `scripts/prove-intelligence-omp.mjs` is kept only because
  `aodl-admission.yml` runs `node --check` on it. It loads
  `omp-extensions/z0int-bridge/index.ts`, which is not on this branch, so it
  cannot run here.
- `scripts/prove-governed-omp-tool.mjs`, `scripts/prove-intelligence.py` and
  `scripts/run-aodl-golden-canary.py` need a live host service, an installed
  OMP, provider credentials and (for the canary) a z0evals checkout. None of
  that was exercised when this branch was cut; only
  `tests/test_golden_canary_wrapper.py` (mocked) covers the canary wrapper.
- `deploy/lab/up` publishes a `z0-slm` host service whose unit
  (`deploy/systemd/z0-slm.service`) is not on this branch.
- The host unit `deploy/systemd/z0intelligence.service` is not on this branch
  (it is on `donor/router`); only `service.env.governed-example` is.
- `src/z0int/cli.py` is not on this branch, so the `z0int` console script and
  the tests that drive it (`test_receipt.py`, `test_posture.py`,
  `test_backend_contract.py`, ...) are not here. `[project.scripts]` in
  `pyproject.toml` was emptied of entries whose module is gone.
- `tests/test_hot_path.py` is not here: it needs `z0int.bridge`. Two of its
  five tests touch `quota_budget` and `worker_routing`, which are kept as
  closure; they run on the donor that keeps the bridge.
- `tests/test_architecture_manifest.py` is dropped by design: it asserts every
  path in `zer0.repo.yaml` exists. `zer0.repo.yaml` and `README.md` are the
  unmodified whole-repo files and describe much that is not on this branch.
- `core-unit.yml` was trimmed by line removal to the paths that exist here.

## Why the branch is 190-odd files

The feature is about 500 lines of its own, but it is a route inside the host
service, and the service does not import in pieces:

- `intelligence_service.py` imports the AgentWeb, outcome, reliability and
  experiment modules at module level (kept, with their tests).
- `worker_routing.py:13` imports `cognition.adapters.transport`, which runs
  `cognition/__init__.py` and loads the shadow stack (16 files).
- `intelligence.py` imports `backends` and `functions`, which reach
  `models_mgmt.py`, `openjev_phase1` and the routing manifests.
- Tests of every kept module are kept, which is most of the test count.

Kept beyond the assignment so that kept workflows and tests are valid:
`src/z0int/aodl_observation.py` with its test and doc, `docs/aodl-admission.md`,
`docs/dispatch-authority.md` (all named by `aodl-admission.yml`);
`src/z0int/free_fanout.py` and `src/z0int/intelligence_mcp.py` (so
`test_free_fanout_free_only.py` and `test_intelligence_canonical.py`, which
also assert on `worker_routing` and `intelligence`, still run).

## To earn a place on master

One consumer: a fleet host that actually runs with `Z0INT_GOVERNED_REMOTE=1`
and a task where running the worker off-host is required (isolation of
credentials or of execution), with the admission gate shown to have denied or
allowed something that mattered. A token or latency saving is not expected
from this feature; it adds hops.

Smallest subset worth taking, if that consumer appears:

- `src/z0int/remote_executor.py` (130 lines)
- `src/z0int/governed_worker.py` (342)
- `src/z0int/dispatch_authority.py` lines 99-249 (the RPC half)
- `src/z0int/aodl_admission.py`, `src/z0int/aodl_dispatch.py` (550)
- `contracts/aodl/governed-worker-v1.json`
- `harness-adapters/governed-client.mjs` governed branch
- `deploy/executor/Dockerfile`, `deploy/k8s/intelligence-executor.yaml`
- `tests/test_governed_worker.py`, `tests/test_aodl_admission.py`,
  `tests/test-governed-client.mjs`

It presupposes the router (`donor/router`). Before reusing the pod manifest,
change the readiness probe so it does not poll `/v1/providers`.
