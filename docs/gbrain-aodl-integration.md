# GBrain × AODL × z0intelligence: shadow vertical slice

Status: experimental, shadow-only.

## Boundary

- **GBrain** owns durable memory and context retrieval.
- **AODL** owns authored intent, topology, budgets, constraints, and authority.
- **z0intelligence** owns the decision policy that chooses what deserves compute, preparation, action, or attention.

Memory evidence is not authority. A memory hit cannot widen AODL permissions or mark an outcome verified.

## First slice

```text
GBrain context_pack()/delta()
        |
        v
z0int.gbrain
  - read-only MEMORY_VERBS v1 client
  - world-only defaults
  - provenance-preserving ContextPacket
        |
        v
AODL projection
  - optional memory participant in intentGraph
  - implementation binding stays in plan
        |
        v
shadow candidate
  IGNORE | OBSERVE | PREPARE
  traffic_eligible = false
        |
        v
existing z0int receipts / experiment harness
```

The bridge deliberately exposes only `entity`, `recall`, `context_pack`, and
`delta`. It rejects `remember`, `forget`, `synthesize`, and
`include_private=true`.

## AODL binding

A GBrain-backed contract can declare a stable memory participant without putting
GBrain implementation details into user intent:

```python
AodlBindingConfig(
    memory_id="personal-memory",
    memory_schema="MemoryContext",
    memory_binding={
        "runtime": "gbrain",
        "protocol": "MEMORY_VERBS_v1",
        "readVerbs": ["entity", "recall", "context_pack", "delta"],
        "worldOnlyDefault": True,
    },
)
```

The `memory` node and its `data` edge are part of the intent graph. Runtime,
source placement, endpoint, and other implementation details live in
`plan.bindings`.

## Why shadow first

The deterministic baseline is intentionally conservative:

- open thread -> `PREPARE`
- other memory change -> `OBSERVE`
- no change -> `IGNORE`

It never emits `ACT` or `SURFACE`. z0int's learned/evolutionary policy can
compete against this baseline once we have joined outcome receipts. Promotion
requires evidence that intervention improves the AODL outcome under attention,
latency, privacy, and token budgets.

## Next slices

1. Join GBrain candidate IDs to z0int decision/outcome receipts.
2. Run the attention policy in AB/shadow evaluation.
3. Add Ripple as a surface only after `SURFACE` has a measured precision floor.
4. Add write-back only for verified durable facts, with explicit provenance and
   GBrain write receipts; never write raw sensor streams or model speculation.
