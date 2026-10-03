# [UNVERIFIED] feat(loop): labels and cohorts for every harness, per-harness x cohort tables, loop merge, legacy imports (C2)

Branch `feat/wire-loop-consumers-20261003-unverified`. It is the local branch `feat/wire-loop-consumers-20261003` @ `f20c2b7`, stacked on `feat/wire-loop-core-20261003` (C1). Component C2-loop-consumers.

Round 2 fast-forwarded the branch from 352edf2 to f20c2b7 with 10 new commits. Nothing was rewritten.

**Status: UNVERIFIED after round 2.**
- The round-2 workflow did not record a passing verification at f20c2b7.
- The component is not in `integrate/wiring-20261003`.
- It is pushed only as a backup and for review. Do not merge it and do not activate it.
- C6-learning-tick depends on it and was never built.

## What

- **`turn_readers.py`:**
  - a Claude transcript reader;
  - a read-only AgentsView reader (mode=ro) with a staleness guard that reports `pending_index`;
  - one documented join rule per harness (Hermes, Codex, Grok, OMP, OMO, DSH). An ambiguous match gives `unjoined` plus a failure row.
- **Per-harness cohort classifier:** interactive, agent, automated, harness, eval or unknown.
- **`z0int outcomes verify --harness <id>`:**
  - `label_source_empty` and `reader_unavailable` make the run degraded, with exit 3;
  - a DB with no `deepseek-harness` agent gives `agent_missing`.
- **`loop_export`:**
  - per harness × cohort tables with manifests;
  - unknown schemas are counted;
  - latest-wins exports.
- **`z0int loop merge`:** merges by turn_key with no pooling, and refuses to merge across versions.
- **`legacy_import.py`:**
  - imports the OMP v1 spine, OMP cognition-shadow and DSH jev receipts into cohort `legacy`;
  - it is idempotent by watermark.

### Round-2 changes (red tests f8132c8..e537c14; fixes 0a73899, df247e5, f20c2b7)

- **No status is ever a pass.** A tool call with `completed` status but no exit line is no longer exit 0, on every AgentsView harness.
- **Commit credit** counts when git printed `[branch sha] subject`, even if the exit code is unknown.
- **`test_label_polarity`** is recorded in the verify report, the CLI `--json` output and every exported table manifest:

  | harness | polarity |
  |---|---|
  | codex, hermes | both |
  | omp, omo | failure_only |
  | dsh | sparse |
  | grok | none |

  A0 and G-SUFF must read the test-derived class balance through this field.
- **Reader errors** become failure rows.

## Last verifier findings still on record (`round2/C2-last-verdict.json`, written before these fixes)

- **blocker:** `exit_code()` treated an unknown status as a pass. The round-2 fixes target this.
- **major:** the schema guard accepts `user_version` 74 (v0.39), but the reader selects `session_kind`, which v0.39 does not have. The live DB is now v113, so this matters less, but it is not shown fixed.
- **minor:** two turn_key derivations exist within one harness's tables: `build_table` uses `_sha`, while verified and legacy rows use the canonical `harness_id.turn_key`.

## Tests (component evidence; not a verification)

- **Green:** 111/111 at f20c2b7.
- **Full suite:**

  | tree | pytest |
  |---|---|
  | base 914dc99 | 11 failed / 1018 passed |
  | head f20c2b7 | 12 failed / 1128 passed |

  The extra failure is a 5 ms p99 timing test in a module C2 does not touch. It passed 3/3 in isolation on both head and base.
- **Isolated e2e:** passes on synthetic AgentsView DBs that use each harness's real shell shape (`evidence/C2-loop-consumers/round2/e2e.txt`).

## Activation

None of its own. C6 consumes it. Before activating C6, it must be verified, merged into integrate and then into master.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
