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

The first bridge deliberately exposes only `context_pack` and `delta`. Both are
world-only by default in GBrain. It rejects `recall` on the trusted-local CLI
because local recall can return unredacted stored values; a later remote/scoped
transport can add explicit recall safely. It also rejects writes, `synthesize`,
and `include_private=true`.

## Try the slice

```bash
z0int context gbrain-pack --entity z0intelligence --session-id demo
z0int context gbrain-delta --session-id demo
```

Both commands print a shadow candidate plus its provenance-preserving context
packet. Pass `--aodl contract.json` to correlate the candidate with a compiled
AODL contract. Each candidate carries a deterministic `candidate_id` and
`receipt_extra`; `attach_shadow_candidate()` joins those opaque fields to an
existing z0int receipt without changing action, authority, success, or verifier
state. Neither command executes an action or surfaces an interruption.

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
        "readVerbs": ["context_pack", "delta"],
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

1. Feed joined GBrain candidate/decision/outcome receipts into the existing
   counterfactual evaluator.
2. Run the attention policy in AB/shadow evaluation.
3. Add Ripple as a surface only after `SURFACE` has a measured precision floor.
4. Add write-back only for verified durable facts, with explicit provenance and
   GBrain write receipts; never write raw sensor streams or model speculation.
