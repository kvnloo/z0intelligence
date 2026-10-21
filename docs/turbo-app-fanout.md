# Experimental app fan-out (RFC #191 Lane B)

Stacked on Turbo #176; this is a separate, explicit consumer-app lane. It does not change `turbo run`, turn on Turbo mode, select paid fallbacks, or merge production branches. The owner explicitly opted into the account risk; **automated consumer ChatGPT access may conflict with OpenAI's terms and can risk the same account used for Codex**. No live launch was performed for this implementation.

## State and command surface

`z0int turbo app-fanout plan|launch|harvest|queue|stop`.

For source-only operation use `PYTHONPATH=src python3 -m z0int.cli turbo app-fanout ...`. The lane itself and simulator use stdlib only. Optional live Android driver: install `uiautomator2>=3,<4` into a project venv, or install the `app-fanout` extra. No implicit install, pairing, unlocking, phone selection, or Luna inference. phone-harness's Android transport is adb/accessibility; this implementation uses optional u2 for Unicode/multiline editor input instead of its ASCII-only adb `input text` path. All coordinates derive from the fresh accessibility node. Nothing runs on a phone at import time.

The run directory holds plan.json, launches.json, candidates.json, queue.json, private diffs, integration worktrees, build receipts and events.jsonl (`z0int.app_fanout.event.v1`, z0evals-ready generic JSONL, not a claim of ingestion). Tokens and dollars stay null. Artifacts use 0600 files / new 0700 directories. Do not put tokens or credential files in metadata. Issue bodies and model output are untrusted task context, never shell commands.

`plan --run-dir RUN -n 5 --mode codex --issues https://github.com/kvnloo/dash/issues/107 --metadata metadata.json`

Metadata is an object keyed by exact issue URL. Each value needs title, body, acceptance (nonempty string array), owner_words (verbatim owner quote copied from the issue/source), route (exact screen/deep link), and base for non-Dash repos. The planner refuses to invent acceptance criteria, an owner quote, or a route. Dash base is always `turbo/dash-live-20261021`; other repositories require an explicit non-production base. Multiple issue URLs are assigned round-robin, N may be 1..100. N is the total requested briefs, not the number concurrently admitted. Every brief carries `[fanout:RUN:K]`, an exact `fanout-RUN-K` branch instruction and draft-PR-to-base instruction. Chat mode requests one complete fenced unified diff when repository access is unavailable.

`launch --run-dir RUN --dry-run --fixture tests/fixtures/app_fanout/synthetic.json --pace 20 --concurrency 5`

Live launch additionally requires `--opt-in`. Default device is s22; an explicit `--serial` and S22+ model family match are mandatory. S25 requires both `--device s25 --owner-ok` and its own model-family match; this is a later owner gate, NOT permission granted by this PR. Invoke with `env -u ADB_SERVER_SOCKET` rather than changing the global environment. Never promote a simulated launch ledger to live. The S22+ Termux SSH address is not an adb serial; reconnect/pair the test device separately when it returns online.

Pace minimum 20 seconds, default 20, measured from the preceding verified send to the next launch start. Default outstanding cap 5; maximum 20. A shared account lock and outstanding ledger under `$Z0INT_HOME/state/app-fanout` (default `~/.z0int`) serialize this lane across run directories and preserve spacing/caps across processes on the host. These are client policy limits, not provider quota claims. External/manual ChatGPT usage is not visible to this ledger. Run one host/account ledger; do not evade caps by changing Z0INT_HOME.

A reservation is persisted before sending. Unknown/ambiguous selectors abort without guessing; the error names `openai-codex/gpt-6-luna` for later review. There is NO wired Luna invocation in this increment. Every executed step logs a landed proof (foreground package, screen hash change, complete editor text or sent-tag-outside-editor postcondition). A tag observed in the conversation is a correlation reference, NOT proof of a server task's completion. URL/title remain null rather than being fabricated when the app does not expose them. No automatic retries after an uncertain send. Rate-limit phrases persist STOP and abort. Rate detection is English-only and intentionally conservative.

`stop --run-dir RUN` (or create `RUN/STOP`) revokes new actions. Stop checks run before each device read/action and every <=250ms of pacing/observation. An already executing u2 RPC may return before stop is observed; no subsequent UI action is sent. Stop does not cancel server-side ChatGPT/Codex tasks and does not free uncertain reservations. Reconcile those explicitly; never delete ledgers blindly to retry a send.

`harvest --run-dir RUN`

One bounded polling pass; rerun when desired. Uses `gh api --paginate --slurp` for all PR pages and branches. PRs match exact bracket tags + repo + base; bare branches match the exact instructed branch name and use pinned head SHAs. No prefix collision between K=1 and K=11. A positively matched candidate releases its local/account reservation. Harvest is not independent acceptance.

Chat extraction: `harvest --run-dir RUN --reply-dump final.xml` parses a read-only captured final hierarchy with exactly one visible run tag and no Stop-generating marker; or `--replies replies.json` accepts owner-bound tag-to-final-reply text. The response must contain one complete fenced diff. `git apply --check` is the ultimate patch validity gate. This increment does not automatically reopen conversation history, scroll replies, reconstruct off-screen code, or recover app task URLs/titles. Do not call a truncated visible reply complete. `--pr-fixture` provides explicitly simulated GitHub rows. Simulation taint propagates from launches into candidates and cannot enter the live queue.

`queue --run-dir RUN --repo DASH_REPO --preview TURBO_WORKTREE --opt-in`

This is a second, separate integration opt-in, restricted to kvnloo/dash and the exact turbo base. It refuses dirty previews, serializes by Git common directory, creates a detached integration worktree, merges a pinned revision or checks/applies a patch, then runs on MBP:

    fleet-build --node mbp --wait 1800 --fetch app/dist -- bash -c 'bun install --frozen-lockfile && (cd bridge && bun install --frozen-lockfile) && (cd app && bun install --frozen-lockfile) && bun test app/src bridge/src shared && (cd app && EXPO_PUBLIC_DASH_TURBO=1 bunx expo export --platform android)'

No groot fallback. Candidate source/dependency scripts execute on the build worker; a Git worktree is isolation from other edits, NOT a security sandbox. Independently review candidates before opting into the queue. The live dependency/build/export path has not been exercised by the synthetic smoke.

After a passing build, preview must still match its pinned base; only then does a fast-forward expose the integration commit to existing Metro/Fast Refresh. Journal observation pins a cursor in `dash-metro-preview-8098`; optional `--metro-log` watches only newly appended bytes of a supplied log. A missing cursor/rotated log fails verification. Default/minimum observation is 20 seconds. Error/redbox patterns trigger `git revert`, not reset, only if no unrelated edit/commit advanced the preview. Ambiguous rollback stops for manual reconciliation. No screenshot or owner acceptance is implied by a clean journal; absence of new log output is not proof that the phone loaded the feature.

On a green observation, append `/mnt/zer0models/workspace/zer0/data/operations/turbo/now-testing.jsonl` with feature, owner_words, acceptance, route, issue, tag, revision and `verdict: awaiting_owner`. This is a data producer only; rendering/route navigation in Dash is separate. Queue rejection/revert exits nonzero. Interrupted transactions fail closed and retain worktrees for inspection. No auto push, production merge, cleanup/reset, or phone navigation.

## Offline evidence

    fleet-build --node mbp --wait 1800 --timeout 300 -- env PYTHONPATH=src python3 -m unittest discover -s tests -p test_app_fanout.py -v
    fleet-build --node mbp --wait 1800 --timeout 300 --fetch .app-fanout-smoke -- env PYTHONPATH=src python3 scripts/app_fanout_smoke.py --out .app-fanout-smoke

The smoke uses real 20-second pacing, synthetic XML, real temporary Git worktrees/apply/ff/revert, a stub build and fake Metro log. Every synthetic output stays isolated from the live Now testing path. It produces a durable summary and both success/reverted receipts. Choose a new output directory for another run.

The two permitted read-only S25 dump attempts returned `ERROR: could not get idle state.` (adb exit 0). No recorded ChatGPT hierarchy was available. Synthetic fixtures derive only their public label vocabulary from z0live's explicitly synthetic `desktop/tests/fixtures/z0live-control-cases.json`; Codex screens are unqualified contract fixtures. Actual ChatGPT 1.2026.286 resource IDs, Codex availability/repository picker, title/URL recovery, final-reply scrolling, Unicode paste, fresh-window races, rate-limit UI and physical Fast Refresh still need the S22+ online or fresh explicit S25 owner permission. Do not enable unattended live runs before that qualification.
