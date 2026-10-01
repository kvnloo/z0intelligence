# RFC — AgentWeb <-> z0intelligence bridge protocol v1

Status: downstream draft  
Tracking: #71  
Supersedes no existing runtime path. This RFC extracts the stable contract implied by z0 #49/#51 and the AgentWeb integration labs.

## Decision

Use one versioned, transport-independent request envelope between AgentWeb and z0intelligence.

AgentWeb remains the authority for user identity, approvals, evaluation policy, scheduled-publish consent, funding, and AgentWeb tool execution. z0intelligence remains the authority for bounded decision routing, z0-owned dispatch identity, replay/conflict handling, decision receipts, and z0 worker admission.

The bridge is an authority boundary, not a new agent.

## Non-goals

- z0 does not receive AgentWeb credentials.
- z0 does not execute AgentWeb MCP tools.
- the bridge does not replace AgentWeb ToolContext, ToolRegistry, evaluation policy, scheduled consent, or funding.
- transport success is not outcome verification.
- model text cannot weaken policy fields.
- this RFC does not promote shadow experiments to production.

## Existing seams

AgentWeb current upstream already centralizes important authority checks at the real execution path:

- ToolRegistry executes after evaluation-policy enforcement.
- account-scoped tools are refused for anonymous users.
- scheduled publish consent is revalidated immediately before execution.
- MCP handlers distinguish read-only and internal-only tools.
- ReliabilityEvent records observational tool outcomes.

z0intelligence already owns:

- pure capability routing;
- canonical dispatch identity and receipt-backed replay/conflict semantics;
- provider admission/backpressure;
- bounded typed decisions;
- provenance-backed ContextPacket-style evidence;
- explicit free-only and remote-context policy.

The bridge projects between those systems without duplicating either.

## Wire contract

Machine-readable schema:

`schemas/agentweb-z0-bridge.v1.schema.json`

Canonical validator/projection:

`src/z0int/agentweb_bridge.py`

### Identity

Required fields:

- `protocol_version = agentweb.z0.bridge.v1`
- `operation_id`: stable caller logical operation
- `parent_agent`: pseudonymous `agentweb:<hex>`
- `integration_instance`: pseudonymous `agentweb-instance:<hex>`
- `trace_id = sha256(parent_agent + NUL + operation_id)`

The same logical operation keeps the same trace. A semantic request mutation under the same operation changes the canonical request fingerprint and must be treated as a conflict by dispatch/reconcile logic.

Raw AgentWeb user/session IDs and credential-shaped fields are forbidden anywhere in the envelope.

## Modes

### shadow

Pure route/plan observation.

- no physical z0 backend call;
- no AgentWeb action application;
- transport failure means `not_attempted`;
- incumbent AgentWeb path may continue.

### advisory

z0 may physically execute a bounded decision/verifier, but the result cannot itself authorize an AgentWeb mutation or external side effect.

- physical z0 call may occur;
- AgentWeb remains final authority;
- ambiguous transport means `attempted_unknown`;
- replay/reconciliation uses the same trace;
- incumbent AgentWeb path may continue where its own policy allows it.

### active

Reserved for a z0-owned execution capability with explicit policy authorization.

- this still does not grant AgentWeb MCP/tool authority to z0;
- mutation/external-side-effect risk requires an already-granted AgentWeb approval projection;
- ambiguous transport means `attempted_unknown`;
- no automatic new-identity retry;
- caller must reconcile the original trace.

## Policy projection

AgentWeb sends facts, not prompt instructions:

`policy.allow_remote_context`
: whether z0 may transmit supplied task/context/state to a remote backend.

`policy.free_only`
: generic z0 worker cost policy. Named operator-authorized exceptions remain z0-owned policy and cannot be granted by AgentWeb text.

`policy.risk_class`
: `read | artifact | mutation | external_side_effect`.

`policy.approval_state`
: `not_required | required | granted | denied | unknown`.

These fields are outside model-authored state. Prompt text cannot override them.

## Deadline and cancellation

v1 carries an absolute `deadline_unix_ms`. z0 validates expiry before projection/execution.

A later transport binding may add an explicit cancellation token/endpoint, but cancellation must never mint a second execution identity. If cancellation races a physical call, the result is uncertain until reconciled.

## Evidence

Each evidence item is bounded and carries:

- stable source ID;
- retrieval timestamp;
- SHA-256 digest;
- content.

The digest is verified on receipt.

This preserves provenance while letting downstream context packing reduce the model-facing representation separately. Evidence import does not make the source true and does not grant execution authority.

## Correlation

v1 admits only bounded opaque join IDs:

- `reliability_event_id`
- `observation_id`

They exist to join AgentWeb observations with z0 decision/dispatch receipts. They are not user/session identifiers and do not participate in semantic request fingerprinting when the observation ID changes later.

## Projection onto current z0 runtime

The validated bridge request maps onto the existing `z0int.intelligence` request:

- `harness = agentweb`
- `trace_id`
- `parent_agent`
- `function`
- `task`
- optional `context/state`
- expected parent cost/latency hints
- `allow_remote`
- `experimental`
- `automatic`
- `integration_instance`
- `free_only`
- `max_tokens`

There is no second execution path.

## Failure matrix

| Situation | shadow | advisory | active |
| --- | --- | --- | --- |
| unsupported protocol | reject/no execution | reject/no execution | reject/no execution |
| expired deadline | reject/no execution | reject/no execution | reject/no execution |
| transport failure | not_attempted | attempted_unknown | attempted_unknown |
| reconcile required | no | yes | yes |
| incumbent fallback | yes | yes, subject to AgentWeb policy | no automatic retry/fallback |
| can authorize AgentWeb write | no | no | no direct tool authority |

An AgentWeb native policy check can always veto. A z0 response can never weaken it.

## Request fingerprint

The canonical fingerprint covers semantic request content. A later observation join ID may change without changing the request fingerprint.

Required authority behavior:

1. same trace + same fingerprint -> replay/reconcile;
2. same trace + different fingerprint -> conflict;
3. uncertain prior physical attempt -> never execute again under a new trace merely because the caller timed out.

## Outcome semantics

Keep these distinct:

- request accepted;
- route selected;
- physical attempt started;
- physical execution completed;
- AgentWeb action applied;
- independent outcome observed;
- outcome verified.

Neither HTTP 200 nor AgentWeb `ReliabilityEvent.outcome=success` alone creates verified quality evidence.

## Migration map

### AgentWeb downstream PR #49

Keep:
- pseudonymous identity;
- explicit remote-context authorization;
- incumbent AgentWeb authority;
- stable trace/reconcile semantics.

Replace:
- bespoke request/response shape with bridge v1;
- duplicated validation with generated/manual TS mirror of the schema.

### AgentWeb downstream PR #51

Keep the TypeSafe SDK experiment as backend transport work.

Do not make TypeSafe transport itself the AgentWeb<->z0 protocol. The bridge stays backend-neutral.

### AgentWeb downstream PR #53

Paired model campaigns consume bridge receipts/correlation IDs. They do not define protocol fields.

### AgentWeb downstream PR #55

Route shadow should emit bridge-v1 request fingerprints and decision correlation, while remaining mode=shadow.

### z0 #49

Becomes the integration implementation epic consuming this RFC.

### z0 #51

Remains an internal Jev transport cleanup. It must preserve bridge/dispatch identity and no-hidden-retry semantics.

## First AgentWeb extraction PR

Target a fresh branch from current AgentWeb upstream main.

Scope:

1. TypeScript `AgentWebZ0BridgeRequestV1` types;
2. deterministic pseudonymous identity + trace derivation;
3. local schema validation;
4. bridge request builder from existing AgentWeb context/policy facts;
5. tests only;
6. no production call site yet.

That slice is intentionally upstream-neutral and allows the stale lab branches to rebase onto one contract.

## Conformance gate

Pinned by `tests/test_agentweb_bridge.py`:

- valid projection;
- request fingerprint mutation detection;
- correlation-only observation join stability;
- trace derivation;
- raw identity/credential rejection;
- deadline expiry;
- active side-effect approval requirement;
- provenance digest validation;
- protocol-version failure;
- mode-specific ambiguous-execution semantics.

## Next slices

1. durable dispatch fingerprint/reconcile binding;
2. AgentWeb TS mirror on fresh upstream;
3. shadow route migration;
4. reliability observation projection;
5. evidence provenance adapter;
6. outcome-join ETL and promotion-quality gate.

Production activation is not part of this RFC.
