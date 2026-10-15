# AODL runtime observation and drift

AODL owns authored intent. z0intelligence owns live observed state.

Runtime drift is represented as a deterministic HOTL `stateUpdate` event plus
one canonical `z0int.decision_receipt.v1` observation receipt. The input AODL
document is never mutated in place.

```
authored AODL
   |
   +-- intentGraph / policies / constraints / provenance.sourceHash  (unchanged)
   |
runtime observation
   |
   v
copy(document) + eventLog[stateUpdate]
   |
   +-- full aodl-canon-1 semantic fingerprint changes
   `-- authored provenance.sourceHash lineage stays unchanged
```

## Idempotency

The stateUpdate event id is derived from:

- authored source hash
- contract revision
- caller trace id
- observation payload
- observation source
- causal parents

Replaying the same observation yields the same event id. If that event is
already in `eventLog`, it is not appended again. The paired
`aodl.observation` receipt is likewise append-once.

A same-id/different-payload conflict fails closed.

## Evidence boundary

Observation receipts use `execution=log_only`. They do not contain
`success`, `verified_success`, or any equivalent quality claim.

Structural admission latency is separately projected once into Tokenomics as
`z0int.aodl_gate_latency.v1` with:

- measured gate latency
- ALLOW/DENY and reason codes
- semantic fingerprint
- intent/source lineage
- `task_success: null`
- `verified_success: null`

Tokenomics projection is non-authoritative. The canonical decision receipt
remains the source of truth; replay can retry a missing measurement projection
without reevaluating authority.
