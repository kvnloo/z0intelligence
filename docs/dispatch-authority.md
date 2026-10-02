# Dispatch and receipt authority

One host authority owns claim compare-and-set, deduplication, ordering and durable append to `z0int.decision_receipt.v1`. It uses host locks and fsynced canonical JSONL; stateless executors call its protocol-v2 RPC rather than writing independent ledgers. The authority itself is not horizontally replicated.

For the same `(harness, trace_id)`:

- A matching fingerprint with a completed result replays that result, even if credentials or execution policy changed afterward.
- A different fingerprint rejects trace reuse.
- A durable start without completion refuses re-execution. No lease expiry or executor restart authorizes another physical call.

Only the canonical ledger determines dispatch state. Observational bridge streams cannot override it. Each remote dispatch receives one authority-owned provider admission. Ordered receipt events are idempotent by payload digest; terminal events must use the start's admission. Admission captures the free-policy evidence so a policy change cannot erase an already-executed call's terminal receipt.

`provider_saturation` owns provider caps, held permits, account blocks and cooldowns through the same ledger. Caps apply across all executors. Crashes can retain permits; reconciliation is required before restoring capacity. A 401/403 stays blocked until explicit operator reset. A 429 observes cooldown and bounded Retry-After. Unknown health is unprobed, never reported as successful execution. Cap-aware fallback remains within eligible free routes; otherwise the parent handles the work.

`/v1/providers` and `/metrics` expose capacity and execution metrics. Provider metrics derive from canonical receipts; process counters reset on service restart. They are not an autoscaling claim.


## AODL-governed remote dispatch (protocol v3)

Remote executor protocol v3 adds a structural AODL admission decision before a
new dispatch-start receipt may be written.

The request carries an `aodl` envelope:

```json
{
  "document": {"specVersion": "0.2", "...": "..."},
  "spawn": {
    "request_revision": 3,
    "parent_node_id": "parent",
    "live_children": 0,
    "parent_depth": 0,
    "observed": {"tokens": 0},
    "proposed": {"tokens": 256},
    "requested": ["execute"]
  }
}
```

Authority ordering is durable and append-only:

```
validate remote worker request
  -> evaluate canonical AODL admission
  -> fsync aodl.structural_admission receipt
     -> DENY: return; no dispatch row exists
     -> ALLOW: fsync intelligence.dispatch start
        -> provider admission
        -> physical execution
```

The structural receipt remains `z0int.decision_receipt.v1`; the
`z0int.aodl_admission.v1` payload is nested in `extra.aodl_admission`. It
contains the full `aodl-canon-1` semantic fingerprint and the authored
`provenance.sourceHash` lineage separately. It cannot mint task success.

An identical denied request replays the same durable denial without appending a
second admission row. An allowed request that already has a dispatch-start row
keeps the existing no-takeover semantics. Reusing the same trace with a
different contract/proposal fails closed.

Protocol v2 remains accepted for historical compatibility, but the current
remote executor uses v3. `/readyz` is unhealthy unless the host can import
exactly `aodl-canon-1`. `/v1/providers` exposes
`aodl_admission_ready`, `aodl_canon_version`, and the supported authority
protocol versions.
