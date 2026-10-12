# AgentRun × z0intelligence: optional read-only plan review

## Attribution and scope

Uses [AgentRun](https://github.com/Parcha-ai/agentrun) by Parcha Labs, Apache-2.0, version `v0.1.0-beta.2`, and TypeSafe's Jev via `@parcha/agentrun-jev`. The bounded map/judge/escalation design follows the upstream [support workflow](https://github.com/Parcha-ai/agentrun/blob/v0.1.0-beta.2/examples/support-answer.mjs).

This is a **first integration slice**, not an autonomous agent or new router. Existing z0intelligence policy/decision backends, the AODL authority, OMP's hot path and the canonical receipt/outcome store remain authoritative and unchanged. The integration is **disabled by default**. AgentRun's trusted JavaScript `code` nodes are *not* a sandbox: only the shipped, fixed workflow is supported.

## Execution path

```text
host / operator
  → AgentRun workflow (opt-in, Node 22.19+)
  → allowlisted read-only localhost z0intelligence /v1/plan
  → PARENT_ONLY: stop, no external Jev
  → other plan: one TypeSafe Jev route-fit judgment
  → yes with confidence ≥0.8: return plan only
  → no / uncertain / low confidence: escalate to host
```

**Never calls /v1/intelligence or executes the plan.** Jev cannot grant tool permissions, alter z0 policy, or assert verified task success.

Files:
- `harness-adapters/agentrun-z0int.mjs`: allowlisted read-only HTTP tool, localhost-only, unknown-field rejection, deadline, fail-closed response validation, no retries.
- `adapters/agentrun-z0int/workflow.mjs`: typed v2 map/judge/escalation flow.
- `adapters/agentrun-z0int/runner.mjs`: offline-injected adapters versus explicit live `createJevRunner`.
- `adapters/agentrun-z0int/cli.mjs`: operator CLI with redacted output.
- `adapters/agentrun-z0int/plan-review.test.mjs`: actual DSL interpreter + offline adapter tests.

## Install / run

Requires Node **22.19+**, local z0intelligence service, and a TypeSafe Jev key for a live route review.

```sh
# repo root, separate terminal
PYTHONPATH=src python -m z0int.intelligence_service --bind 127.0.0.1

# optional Node host
cd adapters/agentrun-z0int
npm install --ignore-scripts
npm test
```

Example operator-only request file (do not commit personal data):

```json
{
  "request": {
    "trace_id": "agentrun-local-001",
    "parent_agent": "agentrun-operator",
    "function": "cheap_bounded_worker",
    "task": "Summarize a public, non-sensitive repository update",
    "expected_parent_tokens": 6000,
    "expected_parent_ms": 45000,
    "free_only": true
  }
}
```

Load `TYPESAFE_API_KEY` in the server process using the host's existing secret loader; do not paste keys into chat or commit `.env`. Run:

```sh
node ./cli.mjs --input ./request.json --allow-remote-jev
```

Exit 0 = plan complete (not task completion); 2 = escalated; 1 = setup/run failure. The report includes a workflow hash, call counts, status and wall time, not tasks, credentials or raw provider errors.

**Privacy:** a non-PARENT_ONLY route transmits its task and plan to TypeSafe Jev. Only send public or explicitly authorized context. Local z0intelligence ingress rejects remote hosts and unrecognized authority overrides; it does not authenticate arbitrary local processes.

## Promotion gates

1. CI passes pinned **real AgentRun DSL** offline tests. Scripted Jev responses are control-flow evidence, not live model quality.
2. Authorized live Jev smoke with a non-sensitive task and a real credential; observe actual adapter calls and model usage.
3. Compare end-to-end p50/p95, marginal API cost, false escalation and correctness against plain `/v1/plan`. Confidence alone is not calibration.
4. Only then consider `runNode` through an existing authenticated harness with full tool/schema/review/cancellation policy, AODL approval, idempotency, and canonical receipt/outcome joins. Never create a second dispatch authority or enable OMP hot-path calls without measured benefit.

## Rollback

No default hook loads this adapter: stop invoking `runLivePlanReview` or remove the optional Node integration. No schema or provider migration.
