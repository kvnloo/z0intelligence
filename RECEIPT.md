# RECEIPT — grok/z0intelligence/125-memory-status-docs (local only, not pushed)

- Source PR: kvnloo/z0intelligence#125 "docs(memory): consolidate continuity ownership and audited implementation status" (head `d76bff977aa5da6203ab832e47080642774ea232`, base branch `preview`)
- Base: `origin/master` @ `43b796aa8752e703fdb119b8660a0d28c1de0a44` (fetched 2026-10-06; `origin/preview` 11e1454 is an ancestor of master, 0 commits ahead)
- Content head (before this receipt commit): `0b8cd00` — the branch head is the commit that adds this file.

## What changed
1. Cherry-picked PR #125's 3 commits cleanly (no conflicts): `aa2f315`, `f979fa1`, `37f5d14` (orig `05943ae`, `3ad577e`, `d76bff9`). Adds `docs/memory-status.md`, routes `AGENTS.md` and `docs/ONBOARDING.md` to it.
2. In-scope fix `0b8cd00` (docs/memory-status.md only, +2/-1):
   - Rebasing onto master pulls in #128 (`43b796a`, OptChat fresh-view turns: `src/z0int/optchat/`, `omp-extensions/z0-optchat/`), merged after the audit at `0159808`. Added an audit-table row recording it as merged source only, not verified use; #120 stays open.
   - The closing paragraph said "This documentation branch targets `preview`". Changed it to say the doc was prepared against `preview` and re-applied on `master` @ `43b796a`.

## Verification (test first, then fix)
Check script (local, not committed): `/workspace/grok-ready/checks/z0-125-doccheck.py`. It checks relative links, pinned blob/tree SHAs and paths, that every backticked SHA is a real commit, that the doc names the right base branch, and that post-audit master commits touching memory/optchat are mentioned.
- RED on `37f5d14` (pure cherry-pick): exit 1 — `says branch targets preview` + `post-audit default-branch change not acknowledged: 43b796a ... (#128)`. All links/SHAs/paths already resolved.
- GREEN on `0b8cd00`: `doccheck OK`, exit 0.
- Manually checked facts: wiring branch `integrate/wiring-20261003` = `05e5015`, 96 commits ahead of `0159808` (and of current master); `feat/salvage-stack-metrics` = `d9a3615`; `feat/optchat-harness-v0` = `0159808`; `b345aa2` is in master; `optmem_tree.py` still has `deterministic_structural_baseline` + `CoverBudgetExceeded`; `surface.py` exists at `158931b`; #22/#66/#116/#120 open, #122 closed.
- Repo CI `core-unit` job, run locally anyway (the PR's docs-only paths don't trigger it):
  - `python3 -m py_compile <9 files from core-unit.yml>` → OK
  - `PYTHONPATH=src Z0INT_PYTHON=python3 python3 -m unittest tests.test_receipt.ReceiptBuild tests.test_receipt.ReceiptClose tests.test_bridge_worker.BridgeRuntimeTests tests.test_memory_event_log.EventLogTests tests.test_memory_optmem_tree.OptMemTreeTests tests.test_ao_bridge.AOBridgeTests` → Ran 36, OK
  - `PYTHONPATH=src python3 -m unittest tests.test_optchat` → Ran 24, OK
  - Not run: `offline-suite` (needs torch CPU wheel + kerdoios checkout). Docs-only change, so it's out of scope.
- `Evidence receipt` CI check: it failed on #125 because the PR body has no YAML receipt block. I reproduced it locally with `.github/scripts/check-receipt.py` (missing issue/base_revision/head_revision/tests). Fixing it means editing the PR body (a GitHub write), so I didn't. A draft block is below; it passes `check-receipt.py` locally against the content head. Set `head_revision` to the real pushed head first.

```yaml
issue: "#22"
base_revision: 43b796aa8752e703fdb119b8660a0d28c1de0a44
head_revision: 0b8cd00
tests:
  red: "doccheck exit 1 on cherry-picked 37f5d14: stale preview-target sentence + unacknowledged post-audit #128"
  green: "doccheck OK on 0b8cd00; core-unit unittest 36 OK; tests.test_optchat 24 OK"
  sabotage: "Not run."
mutation: "n/a — docs only"
runtime_evidence: []
limitations:
  - "Documentation only; #128 content not re-audited beyond file inventory."
  - "offline-suite not run locally."
ai_assistance: "AI-assisted rebase and verification; independent review pending."
```

## Open risks / decisions
- PR #125 targets `preview`. This leaf is based on `master`, as instructed. CoS decides: retarget #125 to master, or push this branch as a new PR.
- The #22 issue-body reconciliation from the PR description happened on GitHub and isn't part of this diff. I didn't re-verify it.
- The PR description says "must not auto-close #22/#116/#66". The commits only say "refs", nothing auto-closes.
