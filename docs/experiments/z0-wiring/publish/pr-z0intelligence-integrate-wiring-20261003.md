# integrate/wiring-20261003: verified z0 wiring components merged (C1, C3, C4a, C5, C7, C8)

Branch `integrate/wiring-20261003` @ `b99bc316e2d23e3f0d901b5fe7542d4ce1058060` · base `feat/shadow-loop-v0` (6fee859)

**This SHA is the activation pin (round 2).** It replaces the round-1 pin e02bcf5. Every new live seam must point at exactly this SHA.

Owner decision: after review, merge this branch into `master`, then install from that commit. `origin/master` (0159808) is an ancestor of this head, so the merge is a fast-forward of 48 commits, and `master` will be `b99bc31` exactly. The 48 commits include the `feat/shadow-loop-v0` base. This PR is staged only: it has not been opened or merged.

## What

No-ff merges, with nothing rebased or forced:

| merge | component | head | round |
|---|---|---|---|
| cc5c82f | C1-loop-core | 914dc99 | 1 |
| ebb290d | C3-omp-omo-capture | fe077db | 1 |
| a0a9cf5 | C4a-hermes-capture | ee7cae0 (+ conflict resolution and `tests/test_integrate_wiring.py`) | 1 |
| 3a3b6e9 | C5-dsh-plugin | 73fe3d7 | 1 |
| e02bcf5 | C3 integration fix | 3f6d139 (OMO print-mode hang) | 1 |
| aaa4f8f | C7-memory-core | 772ba7d | 2 |
| b99bc31 | C8-memory-wiring | 41aec0f | 2 |

Not merged because they are not verified:
- C2-loop-consumers is pushed as `feat/wire-loop-consumers-20261003-unverified` @ f20c2b7.
- C6-learning-tick has no branch; it was never built.

**Round-2 conflict:** `src/z0int/harness_capture.py`, in the `opportunity_record` return.
- C4a's P-9 `packet_text` redaction and C7's validated `MemoryUseReceipt` row field are both kept.
- The resolution is byte-identical to the one C8 used in its own base merge 3028e0c.
- Guard test `tests/test_integrate_wiring.py::test_opportunity_row_keeps_packet_text_redaction_and_memory_receipt`:
  - on C7 alone it fails with `TypeError` (`packet_text`);
  - on e02bcf5 alone it fails with `AssertionError` (the receipt is not validated);
  - it passes on the merge.

The C8 merge had no textual conflict. C8 had already merged e02bcf5 into its base, so the Appendix B seams were already composed there: Hermes `pre_llm_call`, DSH `index.mjs`, `.mcp.json` and `hooks.json`. C8's tests cover the "registered exactly once" behaviour, and they are green here.

## Why

SPEC Appendix B: one tree carries every verified capture and memory seam, so that live activation pins to one SHA.

## Tests

The full suite ran under `flock -s` quiet-lane, `env -i`, isolated homes and the socket guard:

| tree | pytest | node | bun |
|---|---|---|---|
| base 6fee859 | 11 failed (known environment set) | n/a | n/a |
| e02bcf5 (round 1) | 11 failed / 1065 passed / 8 skipped | rc 0 | 28 pass / 1 skip |
| **b99bc31** | 11 failed / 1224 passed / 8 skipped / 1 xfailed | 34/34 | 37 pass / 1 skip |

The FAILED set is identical to the base.

## Evidence

Reports: `/mnt/zer0models/z0-wt/wiring/evidence/integration/E2E-round2.md` and `round2/*.json`.

- **Capture e2e (round-1 drivers):** 63/63 for all 7 harnesses. It covers capture, inert shadows, privacy and isolation.
- **Memory e2e (C8 drivers on this tree):** 45/45. It covers the push and pull seams, scope, the cloud block, the scrub and the acceptance rows.
- **Unified read:** all 6 `z0-memory` MCP seams return the same evidence set. Push receipts for Claude Code, Hermes, OMP, Codex and DSH share event uids.
- **Provenance and scope:**
  - locators use canonical AgentsView / EventLog forms;
  - every would-inject receipt carries event uids;
  - the sibling-project canary never leaked.
- **Secret scrub:** 5 planted fake keys are in the substrate, but in 0 outputs, 0 model requests and 0 MCP responses.
- **Cloud injection is off by default:**
  - the default mode is `shadow`;
  - a canary to a public endpoint gives `cloud_injection_blocked`;
  - `allow_cloud_injection` is false for all 7 harnesses.

## Known gaps (4 of the 17 round-2 e2e checks FAIL)

1. **No harness-generic `outcomes verify --harness`.** Blocked: needs C2.
2. **Harness-generic export:** claude-code has 4 rows and the other six harnesses have 0. Needs C2. The frozen bundle does hold unlabelled capture rows for all 7.
3. **No `z0int loop tick`.** Blocked: C6 was never built. The learner run by hand reports INSUFFICIENT_DATA and promotes nothing.
4. **`opportunity_record.memory` is never filled.** C8 does not pass the receipt into the capture ctx, so DoD D3 is UNMET for opportunity rows. The receipts are in the seam rows and the acceptance rows.

Other open items:
- Brief persistence in the host for Hermes, Claude Code and DSH is an owner decision.
- #63 TencentDB provenance is UNMET: gateway items have no project scope, so they never enter a scoped brief.
- The memory seam defaults to `shadow` independently of capture.

## Activation

See `/mnt/zer0models/z0-wt/wiring/publish/ACTIVATE.md`. Nothing has been activated.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
