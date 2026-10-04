# RFC — o8 <-> z0intelligence integration v1

Status: downstream draft  
Tracking: #118  
Related evidence: #11  
Protocol: `o8.z0.bridge.v1`  
Production activation: out of scope for this RFC

## Decision

Integrate o8 and z0intelligence through one versioned, transport-independent bridge that preserves the authority already present in both systems.

**o8 remains the execution and human-approval authority for o8-owned work.** It owns lanes, workers, worktrees, tool execution, approval cards, Brain presentation/runtime policy, credentials, and the canonical o8 task lifecycle.

**z0intelligence becomes the bounded intelligence/evidence layer.** It owns context projection, decision/capability routing, cheap-first cascades, z0-owned dispatch identity, receipt joins, verification semantics, token/latency/attention accounting, and learning from independently verified outcomes.

The integration MUST reuse o8's existing durable state rather than create a second task database.

The bridge is not a new agent, orchestrator, or approval system.

## Why this integration exists

o8 already has the physical and operational seams that z0 would otherwise need to invent:

- durable software-engineering tasks in `lanes`;
- ordered task events in `lane_events`;
- worker topology in `worker_runs` and `worker_events`;
- mutation authority in `approvals` and `approval_events`;
- model spend attribution in `usage_logs`;
- typed decision evidence in `judgment_receipts`;
- finished-task outcome records in `session_outcomes`;
- an existing `packetId` correlation seam;
- a Brain surface whose read-oriented execution boundary is already distinct from mutating tools.

z0intelligence already has the complementary pieces:

- bounded State Packets and context provenance;
- a decision-backend abstraction with local-first routes;
- Laya -> Jev verification escalation;
- canonical decision receipts;
- dispatch replay/conflict semantics;
- explicit `execution_completed != verified_success`;
- AODL projection/admission;
- tokenomics and outcome joins;
- routine/specialist promotion from verified evidence.

The integration should therefore connect existing authorities rather than replace them.

## Current evidence baseline

This RFC is grounded in:

- z0 issue #11, which records o8 production observations and the relevant o8 tables;
- current o8 `main` inspected at `432cdb3f4142dd8a67a1b07e7d6e3ab6dd03262d`;
- z0's [State Packet / critical path](../critical-path-phase0.md);
- z0's [dispatch and receipt authority](../dispatch-authority.md);
- z0's [routine compiler](../routine-compiler.md);
- z0's [bridge hot-reload model](../bridge-hot-reload.md);
- z0's current [AODL compatibility target](../../AODL_COMPAT.md).

Issue #11 also supplies an important negative result: Jev's lab separation on approval risk did not hold on real o8 production state, and model-authored approval-looking text could steer the judgment. The integration MUST therefore keep model output outside approval authority and MUST treat real-world outcome evidence as stronger than lab-only routing claims.

## Core invariant

The system loop is:

```text
observe
  -> build the minimum sufficient state
  -> decide only as much as necessary
  -> act under the host's existing authority
  -> observe the world
  -> verify independently
  -> credit or reject the route
  -> learn from evidence
```

Three states MUST remain distinct:

1. **decision selected**
2. **execution completed**
3. **outcome verified**

No transport success, model confidence, worker exit code, or approval-card resolution may collapse these into one state.

## Authority boundaries

### o8 owns

o8 is canonical for:

- `lane` identity and lifecycle;
- repo/worktree/branch ownership;
- worker launch, transport, heartbeats, and recovery;
- physical o8 tool execution;
- o8 approval cards and approval resolution;
- human mutation authority;
- o8 credentials and provider/account access;
- Brain UI and interaction policy;
- o8's task/session/outcome database;
- o8-native retries and reconciliation where already defined.

z0 MUST NOT write directly to o8's canonical database as an alternate authority path.

### z0intelligence owns

z0 is canonical for:

- bounded context/evidence resolution;
- State Packet construction;
- capability and backend selection;
- deterministic/routine/local/frontier routing;
- z0-owned dispatch identity and replay/conflict handling;
- z0 decision receipts;
- independent verification semantics;
- route/outcome credit assignment;
- token, latency, compute, and human-attention accounting;
- promotion/demotion of z0 routines and specialists.

### AODL owns

AODL describes intent, participation, constraints, provenance, budgets, and observed runtime semantics where the current formal profile supports them.

Current z0 compatibility explicitly treats `o8` as a **control room**, not an admitted AODL executor. Current admitted executor ids are:

```text
hermes
omp
grok
codex
claude
pi
fx
```

This RFC MUST NOT silently add `o8` to that list.

For v1:

- o8 is the integration host/control plane;
- z0 may project o8 evidence into an AODL intent/runtime document;
- any admitted worker inside an o8 lane keeps its own valid AODL harness identity;
- structural admission remains fail-closed under the current AODL contract;
- adding `o8` as a first-class executor requires a separate AODL change.

### Ripple owns no authority

Ripple is the proposed ephemeral clarification/presentation pattern.

If o8 adopts it, Ripple may render:

- one clarification;
- one bounded choice;
- one preview;
- one approval explanation.

Ripple MUST NOT become:

- another task database;
- an authorization source;
- a second approval state machine;
- a source of model-authored policy.

The underlying o8 approval or user-input state remains canonical.

## Existing o8 seams

The v1 adapter targets current o8 state rather than a hypothetical API.

### `lanes`

A lane is the primary o8 task correlation object.

Relevant fields include:

- `id`
- `projectId`
- `repoPath`
- `worktreePath`
- `branch`
- `baseBranch`
- `runtime`
- `model`
- `sessionKey`
- `packetId`
- `status`
- `outcome`
- `ownership`
- `lastHeartbeatAt`

The bridge SHOULD use `lane.id` as a correlation key, not duplicate it as a z0 task object.

### `lane_events`

Lane events provide ordered runtime observations:

```text
lane_id
verb
actor
payload_json
timestamp
```

These are evidence, not automatically verified outcomes.

### `worker_runs` / `worker_events`

These provide worker execution topology and event history. They allow z0 to reason about:

- repeated attempts;
- worker transport;
- completion vs failure;
- timing;
- stale worker results.

They do not grant z0 permission to launch or mutate workers.

### `approvals` / `approval_events`

Approval state is a hard authority boundary.

Useful evidence includes:

- `fingerprint`;
- risk;
- gate result;
- diff;
- tool/command identity;
- `laneId`;
- policy rule;
- status;
- resolution;
- audit history.

z0 may consume these facts for routing and learning.

z0 MUST NOT infer `approved` from model output.

For an o8 mutation, the bridge may only report the approval state o8 already owns.

### `usage_logs`

Usage rows let the integration join model spend back to:

- task/lane;
- role;
- attempt;
- model/provider;
- token/cost dimensions.

This is preferable to estimating o8 cost from z0 prompts alone.

### `judgment_receipts`

Typed judgments are the best initial cognition seam because they already record bounded questions, answers, and latency.

The first z0 shadow experiment SHOULD target these rather than replacing the full o8 planner.

### `session_outcomes`

Outcome rows are stronger evidence than task completion, but they still require semantic care.

For example:

```text
merged
```

is observable state.

It does not prove:

```text
the patch correctly satisfied the authored user intent
```

A z0 verifier MAY credit stronger labels only when an independent verifier or frozen acceptance check supports them.

### Brain

o8 Brain is a read-oriented Q&A surface whose tool allowlist intentionally excludes mutating operations.

That makes Brain a suitable advisory integration target after shadow-mode evidence exists.

The z0 bridge MUST preserve Brain's existing execution restrictions.

## Architecture

```text
                       human
                         |
                         v
                  +-------------+
                  |     o8      |
                  | UI / Brain  |
                  +------+------+
                         |
                  lane / packet / event
                         |
                         v
              +----------------------+
              | o8 -> z0 projection  |
              | bridge v1 validator  |
              +----------+-----------+
                         |
                         v
              +----------------------+
              |   z0intelligence     |
              |                      |
              | context / StatePacket|
              | route / verify       |
              | receipts / learning  |
              +----+------------+----+
                   |            |
             pure decision   z0-owned call
                   |            |
                   +------+-----+
                          |
                          v
                 bridge response
                          |
                          v
                  +---------------+
                  |      o8       |
                  | final authority|
                  +-------+-------+
                          |
                     execute/observe
                          |
                          v
               o8 events / outcomes
                          |
                          v
                 z0 receipt joins
```

No z0 response directly writes the o8 world.

o8 receives a bounded result and applies its own existing policies.

## Protocol

Version:

```text
o8.z0.bridge.v1
```

The protocol is transport-independent. Local HTTP, stdio, Unix sockets, or an in-process adapter may carry it later.

The semantic contract MUST NOT depend on the transport.

## Request envelope

Illustrative request:

```json
{
  "protocol_version": "o8.z0.bridge.v1",
  "mode": "shadow",
  "operation_id": "o8:judgment:jr_123",
  "lane": {
    "id": "lane_123",
    "packet_id": "pkt_456",
    "project_id": "project_1",
    "revision": 7
  },
  "task": {
    "capability_id": "coding.next_action",
    "question": "What bounded next action is best?"
  },
  "policy": {
    "risk_class": "read",
    "approval_state": "not_required",
    "allow_remote_context": false,
    "free_only": true
  },
  "evidence": [],
  "deadline_unix_ms": 1791080000000
}
```

### Required identity fields

- `protocol_version`
- `mode`
- `operation_id`
- `lane.id` when a lane exists
- `lane.packet_id` when a packet exists
- monotonic/request `revision`
- absolute deadline

z0 derives a stable bridge `trace_id` from canonical caller identity plus `operation_id`.

The same logical operation MUST keep the same trace across transport retries.

## Request fingerprint

z0 computes a canonical semantic fingerprint over:

- mode;
- capability/task;
- bounded state;
- policy facts;
- evidence digests;
- lane revision;
- deadline class where relevant.

It MUST NOT treat later observation-only joins as a reason to change the execution identity.

Rules:

1. same trace + same semantic fingerprint -> replay/reconcile;
2. same trace + different semantic fingerprint -> conflict;
3. uncertain prior physical attempt -> never execute under a fresh identity merely because the caller timed out.

This extends z0's existing dispatch authority rather than creating separate o8 retry semantics inside z0.

## Modes

### `shadow`

Purpose: measure without changing o8 behavior.

Properties:

- z0 may build context;
- z0 may choose a route;
- pure/local decision work may run;
- no o8 mutation;
- no o8 approval transition;
- no worker launch;
- no incumbent path suppression;
- bridge failure leaves o8 unchanged.

A shadow receipt cannot carry `verified_success=true`.

### `advisory`

Purpose: allow z0 to return a bounded recommendation to an existing o8 decision surface.

Good initial targets:

- Brain answers;
- typed judgment questions;
- verifier selection;
- next-action classification;
- context selection.

Properties:

- o8 remains final authority;
- z0 recommendation cannot weaken o8 policy;
- mutating tools remain under o8 approval;
- bridge timeout does not authorize a second physical call;
- incumbent o8 fallback is allowed only where o8 itself considers it safe.

### `active`

Reserved for narrowly defined z0-owned capabilities after shadow/advisory evidence.

Active does **not** mean "z0 controls o8."

An active operation may be accepted only if:

- capability contract is bounded;
- authority is explicitly granted by o8 policy;
- relevant o8 approval is already granted where required;
- trace/fingerprint replay semantics are durable;
- ambiguous attempts reconcile before retry;
- independent verification is defined.

v1 production activation is explicitly out of scope.

## Decision response

A bridge response is one of:

```text
NOOP
OBSERVE
DECIDE
CLARIFY
PREPARE
ADVISE
ABSTAIN
ERROR
```

`EXECUTE` is intentionally absent from the generic v1 response vocabulary for o8-owned actions.

A z0-owned active capability may return a z0 execution result, but o8-owned mutation is still performed through o8's native authority path.

Illustrative advisory response:

```json
{
  "protocol_version": "o8.z0.bridge.v1",
  "trace_id": "01K...",
  "status": "ADVISE",
  "decision": {
    "capability_id": "coding.next_action",
    "value": "VERIFY",
    "confidence": 0.91
  },
  "route": {
    "kind": "local",
    "backend": "laya_421m"
  },
  "receipt_ref": "z0:decision:..."
}
```

## State projection

The bridge SHOULD construct a bounded z0 State Packet rather than forward complete o8 histories.

Conceptually:

```text
o8 canonical state
    |
    +-- lane
    +-- recent lane events
    +-- approval facts
    +-- worker state
    +-- relevant judgment receipt
    +-- outcome evidence
    |
    v
projector
    |
    v
minimum sufficient State Packet
```

Default exclusions:

- full raw transcript history;
- credentials;
- API tokens;
- unrelated lanes;
- unrelated repo history;
- arbitrary approval text;
- stale event tails not required for the decision.

The packet must preserve:

- source identity;
- retrieval time;
- digest;
- freshness;
- missing required facts.

Missing required facts => `OBSERVE` or `ABSTAIN`, not fabricated certainty.

## Context virtualization

o8 can accumulate large histories across orchestrator, worker, review, and retry loops.

The bridge therefore MUST NOT use:

```text
all lane history -> prompt
```

as the default.

Instead:

```text
canonical o8 evidence
        ->
deterministic reducer
        ->
bounded State Packet
        ->
selected backend
```

Large evidence remains addressable by stable reference.

This is the primary mechanism for lowering repeated prompt cost.

## Cheap-first route cascade

The intended order is:

```text
deterministic predicate
  -> promoted routine
  -> learned specialist
  -> Laya / finite bounded local decision
  -> Jev verification/escalation
  -> other eligible local backend
  -> eligible free remote backend
  -> stronger paid/frontier route only if explicitly allowed
  -> human
```

The exact route is a z0 plan detail, not an AODL intent mutation.

A route is promoted only from measured evidence.

## Jev safety lesson from o8

Issue #11 is a hard design input.

Observed o8 data showed:

- lab risk separation did not reproduce reliably in production;
- agent-authored approval-looking text could steer some judgments;
- adding deterministic risk state reduced some injection sensitivity.

Therefore:

1. Jev MUST NOT authorize o8 approvals.
2. model-generated PR titles, review text, or "Reviewed-by" style strings MUST NOT become policy facts.
3. deterministic o8 gate/risk fields must remain separate from model-authored text.
4. Jev output is a bounded decision feature or verifier opinion, not mutation authority.
5. production promotion requires o8 outcome evidence, not only synthetic/lab AUC.

## Approval and mutation semantics

For any o8-owned mutation:

```text
z0 advice
   |
   v
o8 policy / gate
   |
   v
o8 approval state
   |
   v
physical mutation
   |
   v
o8 observation
   |
   v
z0 outcome join / verification
```

z0 cannot skip the middle steps.

If approval is `required`, `denied`, or `unknown`, a z0 recommendation does not change it.

## Ambiguous execution

The dangerous case is:

```text
mutation starts
  -> transport times out
  -> caller does not know whether it landed
```

The rule is:

```text
never blind retry
  -> reconcile original trace/fingerprint
  -> observe current o8/world state
  -> retry only when absence/non-execution is proven
```

This applies to:

- GitHub mutations;
- merges;
- branch operations;
- file deletion;
- external API writes;
- messages/notifications with external side effects;
- any future desktop automation.

## Revision and stale-result handling

Every asynchronous decision carries:

- `trace_id`;
- `operation_id`;
- lane id;
- packet id;
- request revision.

If revision 8 resolves after revision 9 became authoritative, revision 8 may be stored as evidence but MUST NOT overwrite the newer decision state.

The host decides whether a result is still applicable.

## Outcome model

The integration uses a staged outcome model:

```text
request accepted
route selected
physical call attempted
physical execution completed
o8 action applied
world observation recorded
acceptance/verifier evaluated
outcome verified
```

These are not synonyms.

### Example

A worker exits zero and a PR is opened.

Valid:

```text
execution_completed = true
pr_opened = true
```

Invalid without further evidence:

```text
verified_success = true
```

Verification may require:

- frozen tests;
- authored acceptance criteria;
- independent diff verifier;
- merge result plus post-merge check;
- other capability-specific evidence.

## Receipt joins

The bridge SHOULD make the following join graph possible:

```text
o8 lane
  |
  +-- o8 lane_events
  +-- o8 worker_runs/events
  +-- o8 approval + approval_events
  +-- o8 usage_logs
  +-- o8 judgment_receipts
  +-- o8 session_outcome
  |
  +---------- operation_id / packet_id / lane_id ----------+
                                                          |
                                                          v
                                                   z0 decision receipt
                                                          |
                                                   z0 dispatch receipt
                                                          |
                                                   z0 outcome join
                                                          |
                                                   promotion evidence
```

z0 SHOULD reference o8 records by stable IDs/digests rather than copying raw private rows into the repository.

Personalized/runtime state remains under `~/.z0int/`, never committed.

## Tokenomics

The integration measures cost at the task/outcome level.

Per bridge trace, record where available:

- z0 route;
- z0 input/output tokens;
- o8 `usage_logs` joined token/cost totals;
- latency;
- local compute class;
- number of evidence reads;
- clarification count;
- approval wait time;
- retries;
- worker attempts;
- final observable outcome;
- independent verification state.

Primary metrics:

```text
cost / verified task
latency / verified task
frontier calls / verified task
tokens / verified task
human interruptions / verified task
```

Secondary savings:

```text
tokens avoided
frontier calls avoided
duplicate physical calls avoided
unnecessary clarifications avoided
repeated context avoided
```

## Attention model

Human attention is a budget dimension.

A result may carry:

```text
SILENT
WHEN_IDLE
INTERRUPT
```

The host, not the model, decides how this maps to concrete o8 UI.

Examples:

- successful background context preparation -> `SILENT`;
- useful completed analysis -> `WHEN_IDLE`;
- blocked mutation requiring explicit approval -> existing o8 approval UI;
- safety/reconciliation failure -> `INTERRUPT` if o8 policy requires it.

This prevents "proactivity" from being measured only as prediction accuracy.

## Ripple clarification pattern

For ambiguity that truly blocks a decision, z0 may return one bounded clarification proposal.

Examples:

```text
Use the existing PR or open a new one?
[existing] [new]
```

or:

```text
Two repositories match "ace".
[website] [digital twin]
```

Requirements:

- one unresolved decision;
- choices map to deterministic host state;
- no autosend;
- no side effect from merely rendering the clarification;
- response is re-bound to the same logical operation;
- UI disappears after resolution.

If o8 does not implement Ripple, the same contract may render through existing o8 UI.

## Predict -> prepare -> surface -> commit

Longer-term proactive integration follows:

```text
PREDICT
  -> PREPARE
  -> SURFACE
  -> COMMIT
```

### Predict

Infer a likely next bounded need.

Example:

```text
PR opened -> likely inspect CI
```

### Prepare

Perform only cheap/reversible work that current authority allows:

- retrieve CI state;
- resolve relevant context;
- warm a local backend;
- prepare verifier inputs.

### Surface

Show only if expected attention savings exceed interruption cost.

### Commit

Any mutation follows native o8 authority and approval rules.

Prediction never creates permission.

## Routine learning

Repeated verified decisions may become z0 routine candidates.

Promotion pipeline:

```text
observed traces
  -> bounded capability dataset
  -> candidate rule/specialist
  -> train/dev
  -> sealed evaluation
  -> promotion
  -> future drift monitoring
```

A routine may optimize:

- next-action classification;
- whether verification is required;
- which evidence slice is sufficient;
- which local backend is sufficient;
- whether a clarification is necessary.

A routine MUST NOT learn:

- "auto-approve this class of mutation" from sparse human approval history;
- credential policy;
- authority escalation;
- arbitrary generated code execution.

## Security and privacy

### Credentials

z0 receives capability facts, not secrets.

Good:

```json
{"github_write_available": true}
```

Forbidden:

```json
{"github_token": "ghp_..."}
```

Credential-shaped fields MUST be rejected by bridge validation where practical.

### Privacy classes

Evidence may be classified:

```text
PUBLIC
LOCAL
PRIVATE
SECRET
EPHEMERAL
```

Backend eligibility must respect the class.

`SECRET` material never enters model context.

### Raw identities

The bridge should prefer stable opaque/correlation identifiers over raw user/account identity.

No requirement in v1 needs z0 to know a user's email, OAuth token, or provider credential.

## Failure behavior

Native o8 behavior must survive z0 failure.

Degradation order:

```text
full z0 route
  -> local z0 only
  -> cached/promoted routines
  -> native o8 incumbent path
```

The bridge MUST use:

- bounded queues;
- absolute deadlines;
- cancellation that preserves execution identity;
- backpressure;
- circuit breakers;
- stale revision rejection;
- fail-closed validation for authority-bearing requests.

A slow z0 request cannot freeze o8's main interaction/execution loop.

## Failure matrix

| Situation | shadow | advisory | active z0-owned capability |
| --- | --- | --- | --- |
| unsupported protocol | reject, native o8 unchanged | reject, native o8 policy decides fallback | reject |
| expired deadline | no new work | no new work | no physical call |
| z0 unavailable | native o8 unchanged | native o8 policy decides fallback | fail/reconcile |
| same trace + same fingerprint | replay | replay/reconcile | replay/reconcile |
| same trace + changed fingerprint | conflict | conflict | conflict |
| stale lane revision | discard as actionable | discard as actionable | no commit |
| ambiguous physical attempt | n/a | reconcile if a z0 call occurred | reconcile; no blind retry |
| approval denied/unknown | observation only | advice cannot weaken it | cannot authorize o8 mutation |
| outcome missing | unknown | unknown | unknown |
| worker exit 0 only | not verified | not verified | not verified |

## Initial implementation slices

### Phase 0 — RFC and protocol

Deliver:

- this RFC;
- `schemas/o8-z0-bridge.v1.schema.json`;
- synthetic fixtures;
- validator tests.

No runtime call site.

### Phase 1 — read-only projector

Implement a pure projector from synthetic/current o8-shaped records to a z0 State Packet.

Inputs:

- lane;
- bounded recent lane events;
- optional worker state;
- optional approval facts;
- optional judgment receipt;
- optional outcome observation.

Outputs:

- provenance-backed State Packet;
- missing/stale fact list;
- no authorization.

### Phase 2 — shadow judgment adapter

Target one bounded o8 judgment family.

Recommended first candidate:

```text
coding.next_action
```

or:

```text
coding.needs_verification
```

Requirements:

- incumbent o8 answer remains authoritative;
- z0 route is recorded only;
- no live behavior changes;
- join answer latency and later lane outcome.

### Phase 3 — offline replay / ABAB

Replay frozen synthetic/sanitized traces.

Compare:

```text
A = incumbent o8 route
B = z0 cheap-first route
A = incumbent
B = z0
```

Measure:

- agreement/disagreement;
- verified outcome when available;
- latency;
- token/cost;
- abstention;
- clarification;
- failure cases.

Do not promote from agreement alone.

### Phase 4 — advisory Brain / typed judgments

If shadow evidence is positive, allow z0 to answer bounded read-oriented decisions.

o8 remains final authority and preserves Brain's read-only restrictions.

### Phase 5 — verified bounded active capability

Only after explicit acceptance criteria exist.

Candidate should be:

- narrow;
- idempotent or reconcilable;
- independently verifiable;
- low-risk;
- measurable.

Do not start with merge, approval, or broad shell authority.

### Phase 6 — routine compilation

Compile zero-token/sub-millisecond routines only from verified, leakage-controlled datasets.

## First vertical slice

The first end-to-end slice is deliberately small:

```text
o8 lane + packet
  -> bridge v1 shadow request
  -> z0 State Packet
  -> local bounded route
  -> z0 decision receipt
  -> join back to o8 lane/judgment
  -> later outcome observation
```

No o8 mutation authority changes.

### Acceptance criteria

1. same o8 operation produces one stable z0 trace;
2. same trace + changed semantic request fails as conflict;
3. packet contains bounded evidence with provenance;
4. no raw credential-shaped values cross the bridge fixture;
5. stale revision cannot become actionable;
6. bridge timeout does not cause duplicate physical execution;
7. z0 failure leaves native o8 behavior unchanged;
8. receipt can join back to lane + packet + judgment;
9. `execution_completed` cannot mint `verified_success`;
10. local route latency/cost is measurable against incumbent.

## Conformance suite

Minimum tests:

1. protocol-version rejection;
2. deterministic trace derivation;
3. semantic fingerprint stability;
4. changed-request conflict;
5. deadline expiry;
6. stale revision rejection;
7. credential-field rejection;
8. bounded evidence digest validation;
9. missing fact -> `OBSERVE`/`ABSTAIN`;
10. shadow cannot request o8 mutation;
11. advisory cannot weaken approval state;
12. ambiguous physical attempt requires reconcile;
13. duplicate observation join is idempotent;
14. transport success alone cannot verify outcome;
15. o8 model-authored text cannot override deterministic risk/approval facts;
16. unknown AODL harness/control-room semantics fail closed;
17. z0 failure preserves incumbent shadow behavior.

## Suggested z0 code layout

The first implementation should fit existing boundaries:

```text
schemas/
  o8-z0-bridge.v1.schema.json

src/z0int/
  o8_bridge.py

tests/
  test_o8_bridge.py
  fixtures/
    o8_bridge/
```

Avoid adding a new service until the pure schema/projector/replay boundary is proven.

If a runtime transport is later needed, it should reuse the existing z0 host/bridge process model rather than create an unrelated daemon.

## Suggested o8 downstream shape

A later o8-side PR should remain small:

```text
o8 canonical DB/state
  -> pure bridge request builder
  -> local validator
  -> shadow transport
  -> correlation-only logging
```

It should NOT:

- change approval semantics;
- route mutating Brain tools;
- add z0 credentials to o8 prompts;
- make z0 mandatory for startup;
- replace lane/session outcome tables;
- retry uncertain z0 active calls under new IDs.

## Evaluation gates

Promotion requires evidence on real or sanitized representative traces.

### Shadow -> advisory

Require:

- reproducible positive result on at least one bounded judgment family;
- no authority regression;
- no material latency regression at the user-visible level;
- bounded context reduction vs naive/full-history prompt;
- explicit failure cases documented.

### Advisory -> active

Require:

- independent verifier;
- stable replay/reconcile semantics;
- fault-injection coverage;
- zero approval bypasses;
- measurable verified-outcome, latency, cost, or attention benefit;
- rollback path to native o8 behavior.

### Routine promotion

Use the existing train/dev/sealed/future discipline.

Sparse approval data MUST NOT be treated as sufficient routine authority evidence.

## Success metrics

The integration is successful if it improves one or more of:

```text
time to verified outcome          down
tokens per verified task          down
frontier calls per verified task  down
cost per verified task            down
duplicate work                    down
context size                      down
human interruptions               down
unsafe/unknown retries            down
local successful resolution       up
verified outcome rate             up
routine reuse                     up
```

without materially degrading task success or authority guarantees.

## Stop conditions

Pause or remove an integration path if:

- latency rises without reliability gain;
- context machinery costs more than the tokens it saves;
- proactive surfaces consume more attention than they save;
- local routing materially reduces verified success;
- shadow/advisory behavior alters approval authority;
- routine learning starts encoding sparse approval habits;
- outcome joins remain too weak to distinguish completion from success;
- z0 becomes required for basic o8 operation.

Every major optimization should remain experimentally removable.

## Non-goals

This RFC does not make z0:

- o8's database;
- o8's approval system;
- o8's worktree manager;
- an o8 credential vault;
- an o8 UI framework;
- a replacement for o8 Brain;
- a general shell executor.

This RFC does not make o8:

- z0's canonical memory engine;
- z0's decision receipt authority;
- z0's experiment/evaluation system;
- z0's routine compiler;
- a new AODL executor id.

## Migration / compatibility

No existing o8 or z0 path is superseded by landing the RFC.

The rollout is additive:

```text
current o8
   |
   +-- optional bridge shadow
           |
           +-- measured advisory
                   |
                   +-- narrowly promoted capabilities
```

Bridge protocol changes require a new protocol version rather than silently changing v1 semantics.

## Open questions

1. Which current o8 typed judgment family has enough representative outcome labels to be the first shadow target?
2. Should the first transport be local HTTP, stdio, or reuse an existing z0 bridge worker RPC?
3. Which o8 event is the strongest stable `operation_id` source for each judgment class?
4. Which `session_outcomes` fields can be treated as observations vs independent verifier evidence?
5. Which evidence classes may leave the machine under o8's current privacy policy?
6. Does Ripple land as an o8-native presentation primitive, or remain only a semantic clarification contract?
7. When, if ever, should AODL admit o8 as an executor rather than a control room?

None of these block the Phase 0 schema/projector work.

## Recommended first PR after this RFC

A docs/schema/test-only z0 PR:

1. add `schemas/o8-z0-bridge.v1.schema.json`;
2. add a pure `src/z0int/o8_bridge.py` validator/projector;
3. add synthetic fixtures modeled on o8 `lanes`, `approvals`, `judgment_receipts`, and `session_outcomes`;
4. add conformance tests;
5. emit no network calls;
6. change no o8 behavior;
7. change no AODL executor catalog.

That gives both projects one stable contract to implement against before any live integration is attempted.

## North star

The intended experience is not:

> send every o8 decision to another large model.

It is:

> use the minimum intelligence required, reuse the evidence o8 already has, preserve o8's execution authority, independently verify what actually happened, and continuously compile repeated verified decisions downward into cheaper and faster paths.

In short:

```text
o8 makes the work physically real.
z0 makes each bounded decision progressively cheaper, better evidenced, and easier to verify.
```
