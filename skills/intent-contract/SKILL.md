---
name: intent-contract
description: Reconcile complex asks and amendments through the local intent service, then request explicit human approval of exact tasks and verifiers before action. Use when the user invokes intent-contract or /aodl.
disable-model-invocation: true
---

# Intent contract

Use the installed, content-stamped OMP intent extension and the local intent service. The skill is a workflow, not approval authority. No live installation or enablement is implied by reading it.

## Start only on demand

- The service binds `127.0.0.1:8431`; it does not need a public listener. Run `python -m z0int.intent_service --port 8431` from the approved environment only when needed. Do not create an always-on unit or install/enable the extension without the user's authorization.
- State is private under `$Z0INT_HOME/intent/` (default `~/.z0int/intent/`). The extension must have its installer-generated `.content-sha256` file. Missing stamps, input receipts, review UI, or service responses fail closed; never work around them with hand-authored approval JSON.
- Use the existing provider and memory configuration. Do not load new weights, expose credentials, or invent a project registry. Project-name features come from explicit repository URLs and project/product path components in the canonical STATE-PACKET, not its headings. Never edit that generated packet.

## Input routing

1. Deterministic triage records each observed input as FAST, CHECK, or CONTRACT. Sentence/imperative counts, prior references, live risk, scope, delegation, named projects, corrections/frustration, length, and non-text ambiguity contribute. Uncertainty escalates.
2. FAST records a durable receipt and does not call the service. CHECK calls `/check` and supplies a short memory-context note, never approval. CONTRACT calls `/reconcile` before action.
3. Leading `/aodl` forces CONTRACT. Leading `quick:` forces FAST. Both overrides are logged; only the agent-facing prefix is removed. An override does not grant tool, account, host, or other authority.
4. Open-contract asks are reconciled against the current contract ID as amendments and reviewed again. Do not silently substitute a new interpretation or treat a past approval as approval of changed scope.

## Human review

The actual OMP `ctx.ui.select` dialog presents tasks, targets, typed acceptance verifiers, memory sources, assumptions, conflicts, delegation, and **look good?**, with **Approve**, **Edit**, and **Reject** choices.

- **Approve** alone calls `/approve` with the exact `{session_id, contract_id, revision, sourceHash, contractHash}` displayed revision. The approved document's `plan.bindings.approval` must bind Kevin, the session, both hashes, and an approval timestamp.
- **Edit** opens `ctx.ui.editor`. The amendment is a new immutable source entry, reconciles against the previous proposal ID, and shows another review dialog. It does not replace the original source history or inherit approval. A `quick:` edit cannot bypass the review loop.
- **Reject**, cancellation, or a missing UI never approves. Reject uses `/reject`; failures stop the input rather than dispatching an action.
- Text that resembles assent, a model's `approval` classification, a memory fact, an injected context note, and this skill itself are not approval. Only the explicit human dialog selection can invoke `/approve`.

The sibling canonical HOTL 0.2 gate enforces mutation/tool laws. The client does not duplicate, disable, or weaken it. A contract is not a replacement for separate high-impact, provider, or account approvals.

## Source and API boundaries

OMP's `InputEvent` has no session-entry ID and runs before the normal user message is appended. The extension appends a `z0-intent-input` custom entry containing `rawText`, its exact UTF-8 SHA256, timestamp, kind, and installed extension content stamp, then obtains its **actual** ID via `sessionManager.getLeafId()`. The triage receipt binds that ID and exact observed input hash. UI edits and rejections use the same authenticated source type.

The gate must authenticate the installed content stamp and cross-check the following root user text against the source after documented prefix/context transformations. These custom input records are not fabricated root-user transcript entries.

Caveats:

- Interactive OMP trims text and may expand emoticons before emitting `input`. Exact hashing covers `event.text`, not bytes that OMP has already discarded.
- Interactive input precedes slash dispatch. The registered `/aodl` command also covers direct `session.prompt` command dispatch, whose handler exposes only parsed arguments; pre-dispatch whitespace cannot be recovered there.
- RPC/print callers without a real select/editor surface cannot approve. No terminal-text or no-op UI fallback counts as consent.
- The input hook does not cover every possible SDK/internal continuation; missing authenticated receipt remains a gate failure, not permission to mutate.

## Offline qualification (MBP only for this assignment)

Add/run no inference on Groot. The parent runs checks on the MBP; do not execute them as part of a skill read.

- Unit tests: `node --test omp-extensions/z0-intent-service/*.test.mjs` (or the parent-approved compatible runtime).
- Real SDK seam (opt-in, no inference): set `Z0INT_INTENT_REAL_OMP=1` when running the tests with the parent-provided coherent OMP package tree. This checks the actual input runner, custom entry persistence, and `getLeafId()` receipt binding; it does not start a model turn.
- Replay: `node scripts/replay-intent-triage.mjs`. Inputs default to the private absolute `operations/ask-audit/{labels,messages}.jsonl` snapshot. Artifacts go only to a fresh directory under `/mnt/zer0models/workspace/zer0/data/operations/intent-service/`, never Git. On another host, supply absolute `--audit` and `--packet` paths; `--out` remains constrained to that private operations directory.
- The replay uses a stable SHA256, session-grouped 30% holdout. Threshold selection uses training rows only. It reports reviewed failure/control-versus-tier confusion, failure recall for CHECK+CONTRACT, original-ask recall, population FAST share, ten worst FAST misses, and p95 timings. Unreviewed messages are not negative-label truth.
- `triage_p95_ms` measures deterministic classification. `cheap_path.p95_ms` includes lock, receipt append, and fsync, with an explicit `<1000 ms` result; neither promises service or human-review latency. Calibration results do not silently change production thresholds.

Keep historical transcripts, replay rows, receipt logs, approval state, and generated reports out of the repository. Do not claim qualification until the parent has actual MBP evidence.
