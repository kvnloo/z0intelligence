# Dispatch and receipt authority

One host authority owns claim compare-and-set, deduplication, ordering and durable append to `z0int.decision_receipt.v1`. It uses host locks and fsynced canonical JSONL; stateless executors call its protocol-v2 RPC rather than writing independent ledgers. The authority itself is not horizontally replicated.

For the same `(harness, trace_id)`:

- A matching fingerprint with a completed result replays that result, even if credentials or execution policy changed afterward.
- A different fingerprint rejects trace reuse.
- A durable start without completion refuses re-execution. No lease expiry or executor restart authorizes another physical call.

Only the canonical ledger determines dispatch state. Observational bridge streams cannot override it. Each remote dispatch receives one authority-owned provider admission. Ordered receipt events are idempotent by payload digest; terminal events must use the start's admission. Admission captures the free-policy evidence so a policy change cannot erase an already-executed call's terminal receipt.

`provider_saturation` owns provider caps, held permits, account blocks and cooldowns through the same ledger. Caps apply across all executors. Crashes can retain permits; reconciliation is required before restoring capacity. A 401/403 stays blocked until explicit operator reset. A 429 observes cooldown and bounded Retry-After. Unknown health is unprobed, never reported as successful execution. Cap-aware fallback remains within eligible free routes; otherwise the parent handles the work.

`/v1/providers` and `/metrics` expose capacity and execution metrics. Provider metrics derive from canonical receipts; process counters reset on service restart. They are not an autoscaling claim.
