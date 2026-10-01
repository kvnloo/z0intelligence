# AgentWeb bridge protocol v1

Tracking: #74  
Counterpart: `kvnloo/agentweb#75` / AgentWeb PR #76.

z0intelligence consumes `agentweb.z0.bridge.v1` as an authority-boundary contract.

This first slice is intentionally validation-only. Existing routing, decision experiments,
request fingerprinting, dispatch authority, and receipt persistence remain unchanged.

Key invariants:

- AgentWeb retains user/account auth, confirmation, evaluation-workspace, scheduling,
  funding and publish-policy authority.
- z0 retains routing, idempotency, request-fingerprint, dispatch-claim and receipt authority.
- no raw AgentWeb user/account/session identifiers or credentials cross the bridge envelope.
- shadow **plan/observe** failures imply no physical execution and may fail open to AgentWeb.
- shadow **decide** failures may be physically ambiguous because a model/provider call can
  already have occurred; those require same-identity reconciliation while remaining unapplied.
- active ambiguity requires reconciliation with the same authority identity.
- a changed request under one authority identity is a conflict, never permission to execute again.
- active external side effects require AgentWeb-confirmed approval before dispatch admission.

The fixture corpus in `tests/fixtures/agentweb-z0-bridge-v1.json` is byte-for-byte aligned
with the AgentWeb-side seed corpus. Exact-head cross-repo CI must remain green before active promotion.
