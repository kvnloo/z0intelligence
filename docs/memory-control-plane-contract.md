# Lifelong memory control-plane contract

Tracks [#66](https://github.com/kvnloo/z0intelligence/issues/66), extending
[#63](https://github.com/kvnloo/z0intelligence/issues/63).

## Boundary

The memory control plane does **not** introduce a new universal memory database.

```text
immutable evidence
      |
      v
 Event Ledger
      |
      +--> OptMem temporal projection
      +--> lexical/entity indexes
      +--> semantic memory backends
      |
      v
 claim/state compiler
      |
      v
 StatePacket / DecisionOpportunity
      |
      v
 action -> independent outcome -> credit
```

The shared invariant is:

```text
evidence != claim/belief != state != authority != action != outcome
```

AODL/policy remains the authority boundary. Memory can supply evidence; it
cannot mint authority.

## Stable event identity

`EventIdentity` separates durable source identity from local append order.

- `event_uid`: stable across replay/import.
- `ledger_seq`: local ordering only.
- `source_system/source_session/source_event_id/source_seq`: origin identity.
- `payload_hash`: detects conflicting source payloads without comparing raw text.

When a source-native event ID exists it wins over `source_seq`, so an export
that renumbers rows does not silently change identity.

## Scope before similarity

`MemoryScope` is hierarchical:

```text
global -> user -> project -> repo -> task
```

A request may consume eligible ancestor memories, but sibling project/repo/task
memory is rejected **before** semantic ranking.

This is a visibility contract, not an authorization system. Privacy and tool
authority still live in their existing policy boundaries.

## Retrieval projections

Retrieval systems are rebuildable evidence projections, not canonical truth.

- **AgentsView / FTS5** remains the local lexical control/fallback.
- **ctx Core** is an optional normalized cross-harness history projection. z0 reads it only with
  `--refresh off`, disables ctx usage/analytics writes, preserves the Core `generation_id`, and
  keeps exact `ctx:event:<id>` / session provenance.
- **TencentDB** remains the semantic claim/memory backend when configured.
- **EventLog + OptMem** remains the admitted episodic/temporal ledger and projection.

ctx hits enter as conversation evidence. Search score/rank never makes a hit a claim, verified fact,
instruction, action, or AODL authority. Unknown ctx project scope fails closed for project-scoped
requests. Semantic/hybrid ctx retrieval requires explicit opt-in.

The latency-sensitive harness push path does not include ctx by default. DSH and other harnesses can
pull ctx through the read-only unified-memory MCP, then hydrate exact events only when needed. Default
promotion remains evidence-driven by the real-history bakeoff.

## Bitemporal claims

`BitemporalClaim` preserves two distinct timelines:

- observation/recording: `observed_at`, `recorded_at`
- world validity: `valid_from`, `valid_to`

Lifecycle values remain aligned with the State Packet work:

```text
unknown -> provisional -> observed -> verified
                     \-> contradicted
                     \-> stale
```

Newer evidence must not destroy historical claims. Supersession is explicit via
`superseded_by`; contradiction remains inspectable.

`verified` claims require source event IDs. Confidence is metadata only.

## Memory is never instruction authority

Persisted or retrieved memory is data even if it originated from a tool result,
web page, worker transcript, or prior model output.

The contract carries:

- `origin_trust`
- `privacy_class`
- `derived_from_untrusted`
- `instruction_capability`

`instruction_capability=True` is rejected. If something needs instruction or
policy authority it belongs in the existing policy/AODL surface, not memory.

## Decision-time snapshots

`MemorySnapshot` content-addresses the memory/state inputs to a decision:

- scope
- state revision
- source revisions
- claim IDs
- evidence event IDs
- optional policy/ontology revisions

The snapshot ID intentionally excludes `created_at`; identical state produces
an identical ID. Any relevant source revision change produces a different ID.

This gives replay/evals a stable answer to: **what memory state did this
decision actually see?**

## Outcome linkage

`MemoryUseReceipt` does not create another receipt system. It projects into
the existing `DecisionReceipt.extra` field:

```json
{
  "extra": {
    "memory": {
      "schema": "z0int.memory_contract.v1",
      "snapshot_id": "mem_...",
      "capability_ids": ["fts5", "tencentdb"],
      "query_ids": ["q0"],
      "included_claim_ids": ["claim-1"],
      "excluded_claim_ids": [],
      "evidence_event_uids": ["evt_..."],
      "retrieval_latency_ms": 12.5,
      "input_tokens": 311,
      "raw_source_reads": 1,
      "tainted_evidence": false
    }
  }
}
```

That is the seam for #54 / Evolution Lab to measure whether a particular memory
choice helped or hurt the verified outcome.

## Follow-on work

This PR is deliberately contract-only. Next slices should:

1. make EventLog/AgentsView importers emit `EventIdentity`;
2. make the #22 reducer emit scoped `BitemporalClaim` records;
3. bind StatePacket/DecisionOpportunity to `MemorySnapshot`;
4. record memory-use data in real OMP/Hermes/DSH receipts;
5. add z0evals cohorts for stale memory, precision/noise, cross-scope leakage,
   contradiction, correction/forgetting, and taint propagation;
6. feed repeated **verified** episodes into the existing routine compiler rather
   than creating a second procedural-memory system.

No production memory migration or live authorization change is part of this
slice.
