# ctx coding-history evidence capability

Status: P0 read-only adapter for [#116](https://github.com/kvnloo/z0intelligence/issues/116).

Upstream contract reviewed against `ctxrs/ctx@c13a92db70147533c256532384fe8baab6f87950`
(ctx 2.2.7).

## Boundary

`ctx` is a specialized coding-history retrieval system. z0intelligence should
consume it as an evidence capability, not adopt it as a second canonical memory
database.

```text
provider-owned history
        |
        v
      ctx Core
    search / show
        |
        v
z0 EvidenceRef / EventIdentity
        |
        v
scope + claim/state compiler
        |
        v
StatePacket / DecisionOpportunity
        |
        v
AODL / policy authority
```

Ownership stays explicit:

| Layer | Owner |
| --- | --- |
| provider discovery/import and normalized Core records | ctx |
| lexical/semantic history retrieval and ctx citations | ctx |
| admitted z0 event ledger | z0 EventLog |
| temporal compression | OptMem |
| durable semantic claims/lessons/profiles | TencentDB |
| visibility scope and belief lifecycle | z0 memory contract |
| current-state reduction | StatePacket |
| action authority | AODL / policy |

The invariant remains:

```text
retrieved evidence != claim != current state != authority != action != outcome
```

## P0 implementation

`z0int.capabilities.ctx_history.CtxHistoryCapability` is deliberately small.

### Search

The adapter calls only:

```text
ctx search ... --format=json --refresh off
```

It validates ctx search schema v2, requires the response to confirm
`freshness.mode == "off"`, retains the Core `generation_id`, and projects each
hit onto the existing `EvidenceRef` type.

Every ctx command runs with `CTX_LOCAL_USAGE_ENABLED=false` and
`CTX_ANALYTICS_ENABLED=false`. Without them, a plain `search --refresh off` on
ctx 2.2.7 upserts `<data-root>/usage.sqlite` (observed on real local history),
so a z0 read would still write ctx state. The rest of the caller's environment,
including `CTX_DATA_ROOT`, is passed through.

A hit is treated as conversation evidence. Its search score/rank is diagnostic
only; it does not upgrade the evidence into a verified claim.

Lexical retrieval is the only mode enabled implicitly. `hybrid` and `semantic`
require `allow_semantic=True` because ctx can be configured with an external
semantic executor; z0 must not widen private-history exposure merely by choosing
a retrieval backend.

### Exact event hydration

`show_event()` calls `ctx show event <id> --format json`, which ctx documents as a
read against the active verified Core generation. The complete normalized event
is hashed and mapped onto the existing replay-stable `EventIdentity` contract:

- `source_system = ctx:<provider>`
- `source_session = <ctx_session_id>`
- `source_event_id = <ctx_event_id>`
- `payload_hash = sha256(canonical event JSON)`

This keeps z0 ledger sequence out of durable source identity. If the same ctx
event identity later resolves to different payload bytes, the existing
`EventIdentity.has_payload_conflict()` path exposes the conflict instead of
silently minting a second event identity.

### Opt-in `resolve_context` seam

`z0int.context_resolve.resolve_context` reaches ctx only when the caller passes
`allow_ctx=True`. With the default `allow_ctx=False` the packet, operations,
measurements and gap strings are unchanged and the adapter is never constructed.

When enabled:

- only `natural_language` and `memory` needs search ctx; exact paths and other
  kinds never do;
- `ctx_backend` defaults to `lexical`; `hybrid`/`semantic` raise `ValueError`
  unless `allow_ctx_semantic=True` is also passed;
- hits are appended as the adapter's `conversation` `EvidenceRef`s, deduplicated
  only by exact `(source_id, source_version, locator)`; QMD and ctx evidence for
  similar text are both kept;
- the Core `generation_id` is stored as `ResolutionRecipe.source_epochs["ctx_generation"]`;
  a different generation within one resolve is recorded as a contradiction;
- each need gets a `ctx_search` operation (status, effective mode, hit count,
  added count, latency), and `measurements` gains `ctx_status`, `ctx_results`,
  `ctx_latency_ms` and `ctx_effective_modes`;
- a missing CLI (`ctx_status=absent`) or failing call (`ctx_status=error`) is a
  recorded miss that leaves the need as a gap, never an exception or fabricated
  evidence;
- the resolver issues `ctx search --refresh off` only: no `show`, `setup`,
  `import`, `index` or other maintenance command; AODL authority,
  `verified_success` and transition authorization are untouched.

## Explicit non-goals

This adapter does not call `ctx setup`, `ctx import`, `ctx index`, or semantic
enablement. It does not wake maintenance through search, migrate history,
write ctx state, write z0 memory, publish claims, alter StatePacket routing, or
grant tool/execution authority.

The first promotion decision belongs to evaluation, not integration.

## Evaluation gate

Run the same source-grounded real-history questions against:

1. current z0/FTS/AgentsView control;
2. ctx lexical;
3. ctx hybrid;
4. ctx search + exact event hydration;
5. ctx + Blame for code-origin questions.

Track verified correctness, provenance completeness, irrelevant injected tokens,
bytes opened, cold/warm latency, update cost and abstention. A future router may
select ctx only after this evidence exists.

## Credit / provenance

The normalized agent-history, stable identity, search/citation and Blame
architecture is upstream work from **ctx engineering** and the contributors to
[ctxrs/ctx](https://github.com/ctxrs/ctx). The search/Blame implementation
examined for this adapter includes work by **@willsmanley**. Provider and stable
identity improvements relevant to cross-harness ingestion also include credited
upstream contributions by **Michael Hackner**, **Matt Peter**, and
**Hiroki MASAOKA**.

This z0 change only adapts those upstream read surfaces into existing z0
evidence/identity contracts.
