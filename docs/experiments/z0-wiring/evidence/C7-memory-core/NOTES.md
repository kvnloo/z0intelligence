# C7-memory-core notes

Worktree /mnt/zer0models/z0-wt/wiring/wt/C7-memory-core, branch feat/wire-memory-core-20261003, base 914dc99 (C1 head).
Commits: efffa4e (red tests and empty skeleton modules), ae3de93 (implementation), 75c28b9 (removes an unused test import). Not pushed.

## Evidence
- red.txt: the first red run (55 failed, 1 passed). red-committed.txt: red on the committed red tree efffa4e, with the same 55/1. Every failure is missing behaviour: AttributeError on empty skeleton modules, TypeError on missing kwargs (identity, profile, turn_key, instruction_capability), a KeyError on memory_snapshot_id, or an assertion. The test that passes is test_default_profile_is_unchanged, a regression guard that the default MCP tool list is untouched.
- green.txt: 56 passed on the final head.
- suite.txt: head 1074 passed / 11 failed; base 1018 / 11; the failure sets are identical, and +56 equals the new tests. The first base run overlapped with the e2e and had 2 extra timing failures; both passed on isolated reruns in both trees (flaky-rerun.txt), and a clean base rerun matches head.
- e2e.txt: 34/34 checks PASS (scripts/e2e.py).
- scripts/: run.sh (env -i isolated runner plus the socket guard, adapted from C1), tests.sh, suite.sh, e2e.py, sitecustomize.py.

## What was built (reuse noted)
- memory/event_log.py: `append(identity=EventIdentity)` is the canonical source-derived ingestion path (#23). It is idempotent on event_uid via an in-process uid map that reads only newly appended bytes. A changed payload_hash raises EventIdentityConflict. For AgentsView agent sources it rejects body fields (references only). It stays OFF until the owner confirms, via `Z0INT_MEMORY_SOURCE_INGEST=references` or `eventlog_source_ingest: references` in $Z0INT_HOME/config/memory.json. `read_only=True` refuses every write, which serves as the worker-facing ledger. events.jsonl is never rewritten.
- memory_contract.MemoryUseReceipt: adds `instruction_capability` (True is rejected) and `from_dict`. The receipt rides DecisionReceipt.extra.memory, so the receipt schema is unchanged, and harness_capture.opportunity_record writes `memory` from ctx (validated).
- memory/scrub.py: ports the kernel `_SECRET_RES` from dcbcc88 and adds BWS access tokens and truncated PEM blocks.
- memory/surface.py has three layers:
  - Lexical: AgentsView FTS5 through C1 `agentsview_ro.connect` (mode=ro), with the kernel's fts_match any-mode. The project boundary is applied inside the SQL, so out-of-scope rows are never retrieved.
  - Temporal: the EventLog and OptMem tree revision, plus scoped BitemporalClaim supersession.
  - Semantic: TencentDB, using the `/v3/atomic/search` shape from the OMP extension and the bearer shape from config.ts.
  - Shared behaviour: scope is filtered before ranking; evidence merges on event_uid (and payload_hash); an unavailable layer has an explicit reason; memory_snapshot_id comes from MemorySnapshot.build; memory_brief uses a persistent cache under the MemoryPacketCache key rules from Hermes c84663880a (policy version, canonical-JSON scope, normalized query, never caching an empty or abstained brief); plus inspect, unknowns, verify (the kernel rule) and bench.
- memory/doctor.py, memory/cli.py, and `z0int memory {doctor,bench}`.
- intelligence_mcp: profile "memory" (server z0-memory), via `--profile memory` or `Z0INT_MCP_PROFILE=memory`; the default profile is unchanged.
- context_resolve: kind=memory resolves only with allow_memory=True, and only after the turn's single injection owner is claimed (claim_injection is cross-process, O_EXCL under $Z0INT_HOME/state/memory/injectors). The old advisory "ensure TencentDB proxy is not active" contradiction is replaced by this real guard.
- state_packet / decision_opportunity: `memory_snapshot_id`. In the opportunity it sits outside the semantic id, so existing semantic ids and joins are unchanged.

## Deviations
1. The 1024-event nap-cascade test was not in the base, so I wrote it red. It recovers held-out facts through the new temporal search and OptMem zoom.
2. I fixed three test-plumbing bugs after the first red run and before the red commit. A rejected append never creates events.jsonl. A spy lambda's parameter name collided with the `uri=` kwarg. Message ids are now computed before patching sqlite3.connect. None of these changes what a test asserts; red-committed.txt is the red run on the committed tests.
3. The red stage used empty skeleton modules (surface, scrub, doctor, cli), so failures show up as AttributeError instead of a collection ImportError.
4. memory bench reuses the bakeoff's per-query measurement idea (hit@expect, latency) but does not import benchmarks/memory_bakeoff. That file lives only on the kernel branch dcbcc88 and drives LongMemEval arms (Sibyl/Mem0) that are out of scope.
5. Semantic (TencentDB) items carry no canonical z0 scope, so a scoped query rejects them before ranking. They appear only in unscoped queries. I corrected the e2e expectation for this; it was not a code change. Inheriting scope through provenance is a possible follow-up.
6. AgentsView source_event_id is the messages.id row id, as the spec says. A full v74 to v113 resync renumbers rows, so event_uids from before and after a resync will differ. (session, ordinal) would survive that, but this needs an owner decision before G-AV/A6.
7. Unit tests bind the fake TencentDB to an ephemeral loopback port (port 0) so parallel components cannot collide. The e2e uses 127.0.0.1:11545.
8. MCP memory_search returns isError when a required layer (lexical by default) is unavailable. unknowns and orient report gaps or abstention with isError=false, because an abstention is an answer, not an error.

## Honest status
- Semantic layer: UNAVAILABLE by default (not_configured). z0int#63's "TencentDB-derived memories carry canonical provenance" stays UNMET: items without source_event_ids are provenance_ok=false and are not counted.
- The doctor has not been run against the real DB; that is A0 (owner). It shells out to `agentsview version` (tests use a fake binary) and maps 0.39 to 74 and 0.44 to 113.
- Nothing live was touched. The real DB, ~/.z0int, 11501 and agentsview were never accessed. The schema came read-only from C2's extracted copy homes/C2-loop-consumers/schema/sessions.schema.sql.

## Activation
None of its own. C8 wires the z0-memory server and inject seams (A8). Optional A8-sem (owner) runs the TencentDB gateway and sets $Z0INT_HOME/config/memory.json `{"tencentdb": {"url": "http://127.0.0.1:8420", "auth_env": "<ENV NAME>", "deadline_ms": 300}}`. Enabling EventLog source ingestion (`eventlog_source_ingest: "references"`) waits for owner question 3.

## Fix round (verifier REVISE, 2026-10-03): commits 9c8dcd5 (red tests) and 8d619cf (fix)
Evidence: red-fix.txt (22 failed / 56 passed on 75c28b9 plus the new tests, before any src change; every failure is
the intended assertion), green.txt (78/78 on 8d619cf), suite.txt (head 1096 passed / 11 failed, base 1017 / 12; no
head-only failure; the base-only failure is the <5 ms p99 timing test, which also fails 1 of 3 isolated reruns on the
untouched base, flaky-rerun.txt), e2e.txt (43/43, adds 9 fix-round checks; 0 socket-guard violations).
- MAJOR scrub: the key=value rule now matches prefixed names (XAI_API_KEY=, HF_TOKEN=) and quoted JSON keys, plus
  xai-/gsk_/hf_/github_pat_ shapes. AgentsView `secret_findings` spans (location_kind=message, matched on session +
  message_ordinal, UTF-8 byte offsets as the Go scanner records them) are blanked before the regex backstop, as the
  kernel's `_redact_secrets` did. The kernel filtered by session only; this port also matches the ordinal, so one
  message's offsets are never applied to another message. A DB without the table falls back to the regexes.
- MAJOR inspect: the whole message or payload is scrubbed first, then sliced.
- MAJOR deadline/hook path: a TencentDBClient carries one deadline budget for every round-trip in a query. The
  revision comes from the search response, and a timed-out or unreachable gateway is not asked again. search() only
  calls the gateway when the semantic layer is requested. state_packet's memory_snapshot_id is local only: a 20 ms
  AgentsView busy timeout and the last gateway revision a real query observed, persisted at
  $Z0INT_HOME/state/memory/tencentdb_revision.json. It never makes a network call from a hook.
- minor: context_resolve with allow_memory and no turn_key now skips with an explicit `double_inject_guard: no
  turn_key` gap. `z0int context resolve --turn-key` was added, so the CLI can still opt in.
- minor: hermetic tests (monkeypatch for Z0INT_HOME/AGENTSVIEW_DATA_DIR, config={}); the BWS fixture UUID is now
  00000000-0000-4000-8000-000000000001.
- minor: memory MCP tools stay unscoped without "project" (cross-product recall), but the schema now says so ("omitted =
  all projects"), and a test pins this. The C8 shims must pass their project when they want a boundary.
- minor: injector markers older than a day are pruned whenever a new turn is claimed.
