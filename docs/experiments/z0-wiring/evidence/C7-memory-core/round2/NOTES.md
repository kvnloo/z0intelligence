# C7-memory-core round 2 (fix of round2/C7-last-verdict.json, verdict REVISE)

Worktree wt/C7-memory-core, branch feat/wire-memory-core-20261003, new commits on 8d619cf (no rewrite, no push):
7c68f4b test (red) -> c067b57 fix -> f8f2e09 test (red, doctor) -> 772ba7d fix (doctor). Base for the suite: 914dc99.
publish/SUMMARY.md does not exist; round-1 open problems for C7 were taken from the C7 PR body (UNVERIFIED) and
evidence/integration/E2E.md (unified memory BLOCKED on C7/C8 verification). No other C7 problem is listed there.

MAJOR (surface.py, TencentDB revision): checked against oss/TencentDB-Agent-Memory MemoryCore/src/gateway
(types.ts HealthResponse = status/version/uptime/stores; v2-router.ts handleAtomicSearch = data.items with
id/type/content/version/team_id/user_id/agent_id/task_id/created_at/updated_at/score; envelope code/message/request_id).
- FakeTencentDB now emits exactly those shapes (no invented `revision`).
- TencentDBClient.revision() is `unversioned` for a reachable gateway (never `rev:<software version>`); search no
  longer records a revision; each semantic evidence_ref.source_version is the item's `v<version>@<updated_at>`.
- memory_brief: when the snapshot's tencentdb source is `unversioned`, the persistent cache is neither read nor
  written (`cache: bypass:tencentdb_unversioned`), so new gateway content always reaches the next brief.
  Cache still works whenever the gateway is not configured or unavailable (versioned sources only).
- Hook-path snapshot (state_packet) records `unversioned` after a real probe; it cannot track gateway content
  (the gateway exposes no content revision), which is stated in the docstrings.
- Follow-up found by the round-2 e2e: memory doctor parsed `rev:` and crashed (IndexError) on `unversioned`;
  red test f8f2e09 (red-doctor.txt), fix 772ba7d.
- Red test note: the stale-brief test uses an unscoped policy (gateway items carry no scope, so a scoped policy
  never admits them); the amended test was re-run red on the red tree (addendum in red.txt).

minor (event_log.py, O(ledger)): uid map entries carry offset/length (+ inode guard); a duplicate is read from its
offset (no _scan_locked); ingest_reference/record_claim reuse one writer per ledger per process. perf.txt has the
before/after on a 20k-event synthetic ledger. NOT fixed: the temporal layer still scans every event per query
(perf.txt); bounding it via the OptMem tree is a design change, left open for C8 (its 300 ms canary deadline).

minor (hermetic tests): tests/conftest.py sets AGENTSVIEW_DATA_DIR to a tmp dir for every test; the receipt-binding
file also isolates Z0INT_HOME. Both have red tests.

Evidence: red.txt, red-doctor.txt, green.txt (84 passed), suite.txt (+ raw), e2e.txt (48/48, 0 socket-guard
violations), perf.txt; scripts/ (tests.sh, e2e.py, perf.py; run.sh/suite.sh reused from ../scripts).
TencentDB: only the loopback stub on private ports 11545/11546; the live gateway :8420 and live AgentsView were not touched.
