# feat(memory): one z0 memory surface over AgentsView, the EventLog and TencentDB (C7)

Branch `feat/wire-memory-core-20261003` @ `772ba7d447a138659cda9683fcc34538539b013f`. It is stacked on `feat/wire-loop-core-20261003` (C1, 914dc99). Component C7-memory-core.

**Status:**
- Verified in round 2 and merged into `integrate/wiring-20261003` (aaa4f8f).
- The older `feat/wire-memory-core-20261003-unverified` @ 8d619cf is an ancestor of this head and is superseded. It was left in place, not deleted.
- Staged only: no PR has been opened.

## What

- **`z0int memory doctor`** (read-only). It checks:
  - freshness;
  - that the deepseek-harness agent is present;
  - that `~/.claude-home` is indexed;
  - that `user_version` equals the binary's dataVersion;
  - that require_auth is present (the value is never printed);
  - the TencentDB gateway status.
- **AgentsView evidence capability** in context_resolve / state_packet:
  - it returns `EvidenceRef agentsview:<sid>#<mid>` plus EventIdentity;
  - it filters by scope before ranking;
  - it reports `unavailable` explicitly.
- **TencentDB read client:**
  - it has a hard deadline (300 ms by default) and takes its bearer from a named env var;
  - it is UNAVAILABLE unless configured;
  - items without source ids are marked `provenance_ok=false`.
- **EventLog `append(identity=EventIdentity)`:**
  - idempotent on event_uid;
  - references only;
  - source ingest stays off until the owner answers Q3.
- **Scrub:** `memory/scrub.py` runs on every memory output.
- **Receipt binding:** MemoryUseReceipt is bound into `DecisionReceipt.extra.memory` and `opportunity_record.memory`.
- **`memory_brief`** with a persistent, snapshot-keyed cache. The cache is bypassed while the TencentDB source is `unversioned`.
- **`z0int memory bench`.**
- **`intelligence_mcp --profile memory`:** memory_search, orient, inspect, history, unknowns and verify. It has no route_worker.

### Round-2 fixes (7c68f4b red → c067b57, f8f2e09 red → 772ba7d)

- **Gateway revision.**
  - The real MemoryCore gateway exposes no data revision, so the client now reports `unversioned` instead of the software version.
  - The brief cache is bypassed for an unversioned gateway, so new gateway content always reaches the next brief.
  - Each semantic evidence ref records `v<version>@<updated_at>`.
  - Doctor no longer crashes on `unversioned`.
- **Ingest cost.**
  - The uid map stores each entry's offset and length, so a duplicate is read in O(1).
  - There is one writer per ledger per process.
  - Still open: the temporal layer scans every event on each query.
- **Hermetic tests.**
  - `tests/conftest.py` points `AGENTSVIEW_DATA_DIR` at a tmp dir for every test.
  - The receipt-binding tests isolate `Z0INT_HOME`.

## Why

These are the slices of z0int#22, #23, #63 and #66 that the z0evals#56 A–F memory cases need. The change adds no new DB, service or port.

## Tests

- **Green:** 84 passed (`evidence/C7-memory-core/round2/green.txt`).
- **Full suite:** head 772ba7d is 11 failed / 1102 passed / 4 skipped. Base 914dc99 is 11 failed / 1018 passed / 4 skipped. The failure set is identical.
- **Isolated e2e:** 48/48 checks pass, with 0 socket-guard violations. It used a synthetic AgentsView fixture with secret-shaped strings, plus a fake TencentDB stub that matches the real gateway response shapes, on private ports 11545/11546.
- The live gateway (:8420) and the live AgentsView DB were not touched.

## Activation

None of its own. C8 activates it per harness (A8). Read-only first check: run `z0int memory doctor` against the live DB (ACTIVATE.md A0).

Known limitation: TencentDB items carry no project scope (#63 provenance UNMET), so they never enter a project-scoped brief.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
