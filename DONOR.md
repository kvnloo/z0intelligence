# Donor: router

Cut from `archive/pre-reorg-dd39238` (dd39238d). One commit on top of it removes
everything this feature does not need. The audit found no evidence that this
feature saves tokens or latency; it is preserved here so it can be measured
and, if it wins, salvaged.

## What it is

`route_worker`: a tool the frontier model can call to hand a bounded task to a
cheaper model or a typed function, decided against a capability registry with
hash-pinned evidence. It is served by a host HTTP service (port 11501) that
also owns provider selection, the free-only policy, per-provider saturation
and cooldown, a quota budget and an idempotency ledger; an MCP server and an
OMP extension expose the tool to harnesses.

## Entry points

| What | Where |
| --- | --- |
| Host service, `python -m z0int.intelligence_service --bind <addr>` | `src/z0int/intelligence_service.py:210` (`main`), unit `deploy/systemd/z0intelligence.service:11` |
| `POST /v1/intelligence` (route + execute) | `src/z0int/intelligence_service.py:198` -> `src/z0int/intelligence.py:176` (`dispatch`), `:67` (`route`), `:120` (`execute`) |
| `POST /v1/worker` (explicit worker) | `src/z0int/intelligence_service.py:165` -> `src/z0int/worker_routing.py:498` (`dispatch_worker`), `:514` (`execute_plan`), `:355` (`execute_attempt`) |
| `GET /healthz`, `/readyz`, `/metrics`, `/v1/providers` | `src/z0int/intelligence_service.py:125`, `:140`, `:137`, `:129` |
| MCP stdio server, `python -m z0int.intelligence_mcp` | `src/z0int/intelligence_mcp.py:106` (`serve`), `:21` (`route_worker`), `:10` (`delegate_worker`) |
| Claude Code plugin registration | `harness-adapters/claude-code-z0intelligence/.mcp.json`, `bin/z0int-mcp:3`, `.claude-plugin/marketplace.json` |
| OMP tool `z0int_route_worker` | `omp-extensions/z0int-intelligence/index.ts:40`; per-turn hook at `:13` |
| Automatic per-prompt path, `python -m z0int.automatic` and `POST /v1/automatic` | `src/z0int/automatic.py:129` (`main`), `:114` (`handle_event`), `:66` (`dispatch_event`) |
| Hermes and DSH shims for the automatic path | `harness-adapters/hermes-z0intelligence/__init__.py:16`, `harness-adapters/dsh-z0intelligence/index.mjs:5`, `harness-adapters/automatic-client.mjs` |
| Provider saturation, `python -m z0int.provider_saturation` | `src/z0int/provider_saturation.py:162` (`main`), `:65` (`acquire`), `:91` (`release`), `:61` (`snapshot`) |
| Quota projection | `src/z0int/quota_budget.py:25` (`project`) |
| Idempotency ledger | `src/z0int/dispatch_authority.py:87` (`run`) |
| Registry and policy | `manifests/capabilities.v1.json`, `manifests/capability-evidence/`, `manifests/worker_routing.v1.json` |
| Readiness publisher | `scripts/intelligence-readiness.py` |
| Human-run extras | `python -m z0int.worker_activity` (`src/z0int/worker_activity.py`), `python -m z0int.free_fanout` (`src/z0int/free_fanout.py:93`) |

Modules verified to import on this branch: `z0int.intelligence`,
`z0int.intelligence_service`, `z0int.intelligence_mcp`, `z0int.worker_routing`,
`z0int.provider_saturation`, `z0int.quota_budget`, `z0int.dispatch_authority`,
`z0int.automatic`, `z0int.free_fanout`, `z0int.worker_activity`.

## Running the tests

```
pip install -e '.[test]'
PYTHONPATH=src python -m pytest -m 'not live' -q
```

Result on this branch (offline, with the `tokenomics` and `kerdoios` checkouts
on the path as in the full-tree baseline): 717 passed, 2 skipped, 5 deselected, 0 failed, 0 collection errors.

The feature's own tests: `test_intelligence.py`,
`test_intelligence_canonical.py`, `test_intelligence_service.py`,
`test_worker_routing.py`, `test_worker_routing_host_override.py`,
`test_provider_saturation.py`, `test_quota_budget.py`,
`test_dispatch_authority.py`, `test_free_only.py`, `test_sidestep_free_only.py`,
`test_free_fanout.py`, `test_free_fanout_free_only.py`,
`test_posture_routing.py`, `test_functions_verify.py`,
`test_typesafe_sdk_adapter.py`, `test_omp_hook_context.py` (drives the OMP
extension through node), `test_worker_activity.py`. No kept extension has bun
tests. `test_provider_saturation.py::test_cluster_cap_two_processes_64_requests`
spawns two processes and can time out on a heavily loaded machine; rerun it
alone before reading anything into a failure.

## Cost on a turn's hot path

From the audit; token figures are estimates, not measurements.

- One tool schema in every request of every harness that loads it: "about
  250-300 schema tokens per harness for route_worker".
- Each tool call: an HTTP round trip to the host service with a 120 s timeout;
  "live mean dispatch 2.77 s (254.5 s / 92)".
- Inside the service, per routed call: "provider_saturation._rows (:23-28) and
  dispatch_authority.previous (:40-51) re-parse the 19 MB receipts ledger about
  a dozen times per routed call; GET /v1/providers took 1.0-2.0 s."
- Import: "worker_routing.py:13 pulls in the whole cognition package (117-151
  ms measured) for one 184-line client"; the MCP server loads "44 modules /
  12,659 lines".
- Automatic path, when a harness enables it: OMP schedules one bridge request
  per turn (`index.ts:13`); the Hermes shim runs "up to two blocking Python
  process starts per Hermes turn (30 s timeout each)"; DSH "one Python spawn
  per turn".
- The standing service: "about 26% of a core and 721 MB for 92 dispatches in
  28.8 h".

## Evidence (quoted from the audit of dd39238)

Against:

- "at master `route_worker` returns PARENT_ONLY for every text task because
  all 76 text-worker entries in `manifests/capabilities.v1.json` are
  `eligible:false` (I re-counted: 79 entries, only the 2 paid
  `evidence_sufficiency` ones are true)."
- "no evidence of any saving, and the repo's own evidence is negative.
  manifests/capabilities.v1.json delegation_policy records 'No measured
  positive delegation slice ... Preserve PARENT_ONLY for this lane' and
  'Neither real A/B task improved latency or uncached parent input';
  intelligence.py:102 and :105 cite +1347 uncached parent tokens and 30,804 ms
  overhead." (At dd39238 those two strings are at `intelligence.py:95` and
  `:98`; the manifest itself labels the +1347 figure "single sequential
  observation; no general causal savings claim".)
- worker_routing.py: "Its savings field is a bytes/4 estimate
  (worker_routing.py:422-424), self-described as 'not a measured
  counterfactual'".
- automatic.py: "NONE for normal prompts: normalize() (lines 56-62) labels them
  native_turn (no registry entry) or summarization (no eligible entry), so the
  result is always PARENT_ONLY".

For, or at least showing use:

- "live service /metrics shows 92 dispatches ok, 0 failed (65 cerebras, 12
  jev) since last start"; over about 29 h "openrouter 82 calls, cerebras 65,
  groq 38, jev 12, others 3 ... openrouter 25 errors (19 HTTP 400)".
- `functions/jev.py`: "The only typed function with admission evidence (46/48
  ...), but it is a paid remote call and no per-turn token or latency saving
  is recorded".

Unresolved in the audit: the live provider calls "are unexplained: the only
in-repo caller of /v1/worker is intelligence_mcp.delegate_worker (:11), which
Z0INT_SHARED_ONLY=1 strips in the Claude Code .mcp.json. They come from the
dirty live checkout or a non-shared Codex install." So the observed traffic is
not evidence about the code on this branch.

## Did it run live on the audited host

Yes. The service was active on port 11501, the MCP tool was loaded in Claude
Code and Codex sessions, and the OMP extension was installed. But the live
service ran "a dirty checkout at 4de1aa9 (26 uncommitted files), not master",
with a hand-edited unit that differs from `deploy/systemd/z0intelligence.service`
(different ExecStart, readiness hooks). The Hermes shim was installed from a
third, older checkout; whether it fires was not established. DSH use was not
established.

## Known defects

- As shipped it delegates nothing for text work (all text entries
  `eligible:false`).
- Provider state is rebuilt from the receipts ledger on every call (see cost).
  "live /v1/providers also shows groq inflight=1 with zero active dispatches,
  i.e. a permit that was never released."
- `worker_routing.available()` "spawns the Hermes venv Python (20 s timeout) on
  the route path for two providers that can never be selected."
- `free_fanout.py` imports a plan from a hard-coded per-user Hermes plugin
  path; it has no caller. `worker_activity.py` has no caller either.
- `functions/__init__.py` imports `laya` and `verify_evidence_sufficiency`,
  which the audit found are used only by `tests/test_functions_verify.py`;
  they load on the service path without being called and drag in
  `backends/` and `models_mgmt.py`.
- The unit template does not match what ran live.
- Not runnable on this branch:
  - `scripts/check-intelligence-omp.mjs` and
    `scripts/prove-intelligence-omp.mjs` load
    `omp-extensions/z0int-bridge/index.ts`, which is not here. They are kept
    as reference only. In a real OMP install the extension is also imported
    by z0int-bridge; standalone loading is what `test_omp_hook_context.py`
    covers.
  - `harness-adapters/claude-code-z0intelligence/hooks/hooks.json` is kept
    with the plugin directory, but the module its four hooks call
    (`z0int.claude_code`) is not here. Each hook ends in `|| true`, so they
    fail silently; only the MCP half of the plugin works on this branch.
  - `.claude-plugin/marketplace.json` still lists a second plugin,
    `z0-obspack`, whose directory is not on this branch.
  - `scripts/prove-intelligence.py` and `scripts/prove-governed-omp-tool.mjs`
    need a live service, credentials and an OMP install; not exercised here.
- `src/z0int/cli.py` is not on this branch, so there is no `z0int` console
  script and the tests that drive it (`test_receipt.py`, `test_posture.py`,
  `test_backend_contract.py`, `test_state_packet.py`, ...) are not here even
  though `receipt.py`, `posture.py` and `backends/registry.py` are kept as
  closure. `[project.scripts]` was emptied of entries whose module is gone.
- `tests/test_hot_path.py` is not here. It imports `z0int.bridge.runtime`,
  and keeping the bridge pulls in `cli.py` and about 135 more files. Two of
  its five tests cover kept router code (`quota_budget.project` without the
  kerdoios package; `worker_routing.credentials` not importing
  `hermes_cli.auth`). They are not run on this branch. This is the one place
  where a test of kept code was left out; it is stated here rather than
  hidden.
- `tests/test_architecture_manifest.py` is dropped by design: it asserts every
  path in `zer0.repo.yaml` exists. `zer0.repo.yaml` and `README.md` are the
  unmodified whole-repo files.
- `core-unit.yml` was trimmed by line removal to the paths that exist here.

## Why the branch is 190-odd files

The audit's "Conditional A" router is about 2.9k lines. The branch is larger
because the modules do not import in pieces at dd39238:

- `intelligence_service.py` imports the AgentWeb, outcome, reliability,
  experiment, AO and governed modules (module level or inside handlers); they
  and their tests are kept so every route still answers.
- `worker_routing.py:13` -> `cognition.adapters.transport` ->
  `cognition/__init__.py` -> the shadow stack (13 more files).
- `intelligence.py:12` -> `backends` -> registry -> decider/julia/laya/
  nanojev/openjev backends, `models_mgmt.py`, `src/openjev_phase1`.
- `agentweb_context_packet.py` -> `context_resolve.py` -> `file_search.py`,
  `memory/`.
- Tests of every kept module are kept.

## To earn a place on master

One paired measurement, the one the audit names: "mark one already-validated
$0 route eligible for cheap_bounded_worker and run tasks above the 256-token
threshold, parent tokens and wall time with and without delegation." If
verified-task quality holds and parent tokens or wall time drop, salvage; if
nobody runs it, the audit's recommendation is to delete the router.

Smallest subset worth taking (the audit's Conditional A, about 2.9k lines):

- `src/z0int/intelligence.py`, `intelligence_mcp.py`, `worker_routing.py`,
  `quota_budget.py`
- `src/z0int/intelligence_service.py` reduced to `/healthz`, `/v1/intelligence`,
  `/v1/worker`
- `src/z0int/provider_saturation.py` with state held in memory
- `src/z0int/dispatch_authority.py` lines 1-96
- `src/z0int/cognition/adapters/transport.py` moved out of `cognition/`
- `src/z0int/receipt.py`; `evidenced()` from `automatic.py` (9 lines)
- `src/z0int/functions/jev.py`, `typesafe_sdk_client.py`, `contract.py` only
  if the paid evidence function is wanted
- `manifests/capabilities.v1.json`, `manifests/capability-evidence/`,
  `manifests/worker_routing.v1.json`
- `omp-extensions/z0int-intelligence/index.ts` lines 38-62 (tool registration)
- `harness-adapters/claude-code-z0intelligence/.mcp.json`, `bin/z0int-mcp`
- `deploy/systemd/z0intelligence.service`, `scripts/intelligence-readiness.py`

Leave behind: the automatic per-prompt path and its three shims, `posture`
annotation, `free_fanout.py`, `worker_activity.py`, `functions/laya.py` and
`verify_evidence_sufficiency.py`.
