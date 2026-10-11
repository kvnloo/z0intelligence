# DONOR: turn-receipts

Donor branch cut from `dd39238` (tag `archive/pre-reorg-dd39238`). One commit
removes everything outside this feature; history and blame of the kept files
are intact. This file describes the branch. `README.md` is the old whole-repo
README (kept because `pyproject.toml` names it) and `zer0.repo.yaml` is the
unmodified whole-repo map; neither describes this branch.

## What it is

The OMP (oh-my-pi) turn ledger: an OMP extension keeps one resident Python
worker and, on every agent turn, sends `turn_open` (preflight + a decision
receipt) before the model runs and `turn_close` (measured provider tokens +
outcome) after it. The Python side appends the receipts to JSONL ledgers under
`$Z0INT_HOME`, joins outcomes to them, and can summarize token economics,
mine historical sessions and export rows for the external Kerdoios planner.

## Entry points

| Entry point | Where |
| --- | --- |
| OMP extension default export `z0intBridge` | `omp-extensions/z0int-bridge/index.ts:537` |
| worker spawn (`python -u -m z0int.bridge.worker --generation N`) | `omp-extensions/z0int-bridge/index.ts:123` |
| `before_agent_start` -> op `turn_open` | `omp-extensions/z0int-bridge/index.ts:634`, `:657` |
| `agent_end` -> op `agent_end` / `turn_close` | `omp-extensions/z0int-bridge/index.ts:747`, `:702` |
| `/z0int-bridge-status`, `/z0int-bridge-reload`, `/z0int-close` | `omp-extensions/z0int-bridge/index.ts:768`, `:784`, `:808` |
| worker `main` / `serve` (JSON lines on stdin/stdout) | `src/z0int/bridge/worker.py:173`, `:51` |
| `BridgeRuntime.turn_open` / `turn_close` | `src/z0int/bridge/runtime.py:416`, `:551` |
| `append_receipt`, `close_turn`, `summarize_tokenomics` | `src/z0int/receipt.py:412`, `:531`, `:617` |
| `preflight_dict` | `src/z0int/preflight.py:243` |
| `list_artifacts` (called by preflight every turn) | `src/z0int/artifacts.py:230` |
| `emit_provider_usage` | `src/z0int/tokenomics_emit.py:35` |
| `export_observations` | `src/z0int/kerdoios_export.py:91` |
| `mine_omp_sessions`, `summarize_replay` | `src/z0int/counterfactual.py:108`, `:299` |
| CLI `z0int receipt` / `counterfactual` / `artifacts` / `kerdoios` / `preflight` | `src/z0int/cli.py:979`, `:994`, `:1007`, `:1137`, `:1165` |

Python entry points that import on this branch (checked):
`z0int.bridge.worker`, `z0int.bridge.runtime`, `z0int.receipt`,
`z0int.preflight`, `z0int.artifacts`, `z0int.tokenomics_emit`,
`z0int.kerdoios_export`, `z0int.counterfactual`, `z0int.cli`.

## How to run its tests

```
pip install -e '.[test]'          # plus the tokenomics package and a kerdoios checkout, as in core-unit.yml
python -m pytest -q -m 'not live'
```

Result on this branch, in a clean environment (empty HOME, `Z0INT_HOME` in a
scratch directory): `602 passed, 2 skipped, 5 deselected`, no collection
errors. `make_donor.py --verify-imports`: 81 modules under `src/` import, 47
other `.py` files compile. The full tree at `dd39238` gives 1164 passed.

The feature's own tests are `tests/test_receipt.py`,
`tests/test_bridge_worker.py`, `tests/test_bridge_decision.py`,
`tests/test_hot_path.py`, `tests/test_network_cutover_phase_a.py` and
`tests/test_tokenomics_emit.py`. The other 37 test files ride along because
their imports are all inside the kept closure.

No kept extension has `*.test.ts`, so there is no bun test to run.
`bun build omp-extensions/z0int-bridge/index.ts --packages=external` bundles
(4 modules). A manual smoke of the worker on this branch (ops `hello`,
`self_check`, `turn_open`, `turn_close`, `shutdown` piped to
`python -u -m z0int.bridge.worker`) answered all five with `ok: true`, and
`z0int receipt summary --json` then reported the closed turn. The extension
itself was not loaded into OMP from this branch.

## What it costs on a turn's hot path

From the audit (code paths and file sizes; the audit measured no timings):

- Session start: one Python spawn (20 s handshake timeout) and
  `compute_build_id` twice (`worker.py:54`, `runtime.py:271`), six git
  subprocesses including `git diff HEAD`. The `z0_file_search` tool schema is
  `loadMode: "essential"`, so it rides on every request (auditor's estimate
  100-130 tokens, not measured).
- Turn open: awaited in `before_agent_start` with a 45 s timeout
  (`index.ts:39`). Work: preflight (three registry file probes, artifact
  listing, backend spec loop), `kerdoios_plan`, four JSONL appends (one
  fsynced), one JSON write, one read of `bridge-current.json`.
- Turn close: `find_receipt` (`receipt.py:445`) full-scans three files and is
  called twice per close; on the audited host that was about 46 MB of JSON
  parsed per turn, growing with every turn.

One observation of mine, not from the audit: the smoke above recorded
`latency_ms` 60 for `turn_open` with empty ledgers and no Kerdoios
interpreter. That is a single run, not a benchmark.

## Measured evidence for or against

Against. Quotes are from the audit of `dd39238`.

- "NONE for turn_open/turn_close: receipt is stamped execution=log_only
  (runtime.py:488) and no caller reads the reply."
- "The bridge awaits turn_open before every agent start (45 s timeout) and
  then discards the reply: index.ts:656 is a bare `await request(...)`.
  Nothing from preflight, the plan or the receipt can reach the turn."
- "Net effect of the open/close half on tokens or latency is negative."
- Preflight: "the route is `model` on every turn"; its local-model branch
  "is structurally dead: it only matches backend ids containing nanojev/local
  and then requires a decide or predict method, but NanoJevBackend exposes
  evaluate only (preflight.py:142-148 vs backends/nanojev.py:116)".
- "`actual_tokens_saved` is noise: baseline is len(prompt)//4 plus
  max(32, that//4) (preflight.py:183, 237) and it is compared to real provider
  usage, so max(0, baseline - measured) is essentially always 0."
- "verified_success is always null on ambient closes (index.ts:714), so it
  cannot count verified tasks."
- Store growth on the audited host: "decisions.jsonl 6640 rows / 19 MB,
  bridge.jsonl 1237 rows, preflight.jsonl 1237, open_turns 1234";
  "open_turns.jsonl, shadow/preflight.jsonl and bridge_quarantine.jsonl have
  zero readers in the repo."
- `kerdoios_export.py`: "No non-human caller"; a second auditor rated it
  UNSURE because it has a named consumer in another repository.
- `counterfactual.py`: reachable only from `z0int counterfactual mine|summary`;
  its output files "were last written 2026-09-18".

For: nothing measured. The audit keeps two pieces on their role, not on a
number: `receipt.py` is "the ledger other slices read" (about 20 importers)
and `tokenomics_emit.py` is "the only place real provider token usage is
recorded for Claude Code; a north-star metric needs exactly this".

Audit verdicts: `index.ts` FIX_THEN_KEEP (one map) / DELETE of the open/close
path (another map); `runtime.py` FIX_THEN_KEEP; `worker.py` KEEP;
`protocol.py` FIX_THEN_KEEP; `generation.py` DELETE; `decision_cache.py`
DELETE; `preflight.py` and `artifacts.py` DELETE (one map) / UNSURE (another);
`receipt.py` FIX_THEN_KEEP; `tokenomics_emit.py` KEEP; `kerdoios_export.py`
DELETE / UNSURE; `counterfactual.py` DELETE.

## Did it run live on the audited host

Yes for the bridge: the extension was symlinked into the OMP extension
directory, two `z0int.bridge.worker` processes were running, and the ledgers
were written on the audit day. The live copy came from a different, modified
checkout, and the audit did not diff it against `dd39238`. `kerdoios_plan`
did not run there (interpreter missing, so it returned without spawning).
`kerdoios_export.py` and `counterfactual.py`: no evidence of a run since
2026-09-18. `scripts/e2e_spine.sh`: zero inbound references, not run.

## Known defects

From the audit:

- `turn_open` blocks the turn and its reply is discarded (`index.ts:656`).
- `find_receipt` re-parses the whole ledger twice per close (`receipt.py:445-464`);
  `outcomes.jsonl` re-embeds the full receipt (about 3.6 KB per row).
- `generation.py` is wrong with two OMP sessions: both write the same
  `bridge-current.json`, so a reload in one makes the other's `turn_open`
  return `quarantined_stale`.
- When `kerdoios_plan` returns an error dict the receipt is still labelled
  `provider: "kerdoios_plan"` (`runtime.py:482`). The smoke above shows it.
- `runtime.py:150-157` and `:191-199` fall back to hard-coded host-specific
  default paths for the Kerdoios root and interpreter; the same lookup is
  copy-pasted in both functions.
- `runtime.py:672-686` enqueues autoresearch jobs with placeholder
  measurements (`required_ops ["exact_path"]`, `baseline_wall_ms 800`), and
  only when a person types `/z0int-close --verified`.
- No file in the repo sends op `decision`; `decision_warm` fires only when
  `OMP_SHADOW_WORKER_NEEDED` is set.
- `worker.py:17` imports `publish_current` and never calls it.
- One turn is written to five places at open and four more at close.
- Provider-usage extraction is written twice in `index.ts` (`:360`, `:458`).
- `scripts/e2e_spine.sh` needs an Evolution Lab checkout and a Kerdoios
  install that are not in this repository; its default paths are host-specific.

Caused by the cut (this branch only):

- `src/z0int/cli.py` is kept so `z0int receipt|counterfactual|artifacts|kerdoios|preflight`
  and the tests that call `z0int.cli.main` work, but only its module-level
  imports were followed. These subcommands parse and then raise `ImportError`
  when run: `doctor`, `status`, `onboard`, `models`, `task`, `backends eval`
  (bench), `context-state`, `os-context`, `ao`, `counterfactual
  worker-needed-ingest`, `contrastive race`, `routine`, `cascade`, `aodl`,
  `repair`, `abab`, `claude-code`, `hermes`. `autoresearch research-once`
  already failed at `dd39238` (`cli.py:1114` imports a module that does not
  exist). Still working: `receipt`, `counterfactual mine|summary`, `context`,
  `backends list|doctor`, `cognition`, `artifacts`, `contrastive` (other
  subcommands), `autoresearch` (other subcommands), `kerdoios`, `posture`,
  `preflight`.
- `tests/test_architecture_manifest.py` is dropped: it asserts every path in
  `zer0.repo.yaml` exists, and most do not here.
- `src/z0int/credit.py` is not on this branch. It is imported only by
  `cascade.py` and `routines.py`, which belong to the compilers-and-routines
  donor.

## Why the branch is 168 files and not 20

The feature's own code is 13 files plus six test files. The rest is import closure that the
bridge really reaches, kept so every op and every kept test works:

- `bridge/runtime.py` lazily imports `cognition.shadow` (op `cognition_shadow`),
  `context_resolve` -> `file_search`, `memory` (op `file_search`),
  `autoresearch.queue` (turn close) and `backends` (ops `decision*`, preflight).
- `bridge/worker.py` lazily imports `automatic` (op `automatic_event`), which
  reaches `intelligence` -> `worker_routing`, `functions/`, `dispatch_authority`,
  `provider_saturation`, `quota_budget`, `posture`, `aodl_dispatch`,
  `aodl_admission` and the manifests they read.
- `omp-extensions/z0int-bridge/index.ts:15` imports
  `../z0int-intelligence/index.ts`, which imports the two client shims in
  `harness-adapters/`.
- `cli.py` imports `cognition.cli` at module level, which keeps the whole
  `cognition` package and `scripts/probe_local_cognition.py`.
- `tests/test_network_cutover_phase_a.py` imports `autoresearch.daemon`.

Those modules are owned by other donors (file-search, cognition-shadow,
router, decision-backends, autoresearch-task-loop, memory) and are duplicated
here on purpose.

Tests dropped: 68 files. 67 of them import a module, or read a script, that is not on this branch
(the tool's report names the first missing one for each); the remaining
one is `test_architecture_manifest.py`, above. No test of the 13 feature
files was dropped. Some dropped tests are the dedicated tests of closure
modules that are kept here but owned elsewhere, and they need more removed
code to import:
`test_dispatch_authority.py`, `test_provider_saturation.py`, `test_free_only.py`
(need `remote_executor.py`), `test_llama_http_backend.py`,
`test_backend_bench.py`, `test_tokenomics_bridge.py`, `test_eligibility.py`
(need `backends/bench/`), `test_contrastive_evidence.py` (needs
`capabilities/`), `test_intelligence_canonical.py` (needs
`intelligence_mcp.py`), `test_cognition_orchestration.py`,
`test_mutation_policy.py` (need `cognition/orchestration.py`,
`cognition/mutation_policy.py`). Those modules are therefore less tested here
than on the donor that owns them; salvage them from that donor, not this one.

## To earn a place on master

The one thing that would justify it: a consumer that reads a receipt-derived
number and acts on it. Concretely, tokens per verified task computed from
receipts where the token count is real provider usage and `verified_success`
is set by a verifier (not null, not a person typing a command), reported for
two arms of the same task set. Today the baseline is `len(prompt)//4`, the
outcome is null on every ambient close and the open reply is discarded, so
the ledger cannot produce that number.

Smallest subset worth taking:

- `src/z0int/receipt.py` (`append_receipt`, `close_turn`, `join_outcome`,
  `summarize_tokenomics`), after fixing `find_receipt`, with
  `tests/test_receipt.py` minus its counterfactual cases.
- `src/z0int/tokenomics_emit.py` with `tests/test_tokenomics_emit.py`.
- `src/z0int/paths.py`.
- Only if a resident op survives on master: `src/z0int/bridge/worker.py` and
  `bridge/protocol.py` as transport (replace `compute_build_id` with a
  constant), with the turn ops called fire-and-forget rather than awaited.

Not worth taking as they stand, per the audit: `preflight.py`, `artifacts.py`,
`bridge/generation.py`, `bridge/decision_cache.py`, `counterfactual.py`.
`kerdoios_export.py` only if the Kerdoios planner is confirmed to use its rows.

Uncertain: whether the live, modified copy of the extension differs from this
one in ways that matter; whether anything outside this repository reads the
ledgers; how long the offline suite takes on CI for this branch (it took
about 8 minutes locally while other suites were running).
