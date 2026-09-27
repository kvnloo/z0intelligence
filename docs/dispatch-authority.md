# Dispatch authority and saturation policy

Current update: [validated free-only execution](free-only-execution.md). The single executor is now Ready on the repaired OpenRouter free route; OMP alone has its automatic decision hook enabled. Earlier cap0/unready descriptions below are historical.

All protocol-v2 remote executors use the existing intelligence service as their one dispatch and receipt authority. They do not write a local canonical ledger. `dispatch_authority.py` owns CAS, receipt event ordering and completion; `provider_saturation.py` owns provider admission. The pure router receives a snapshot prepared outside routing.

Identity is `(harness, trace_id)`. The original fingerprint and `dispatch-<key>` lookup are retained. Same identity/fingerprint with a completed result replays that result. A changed fingerprint is rejected. Started execution without durable completion is uncertain and cannot be executed again. Executor restart does not reset identity, health, or admission state. The authority remains a single durable host service, not an HA deployment.

The existing `z0int.decision_receipt.v1` JSONL is the durable source for both execution and provider admission. New metadata lives in `extra`; no second telemetry format or executor ledger exists. Authority RPC requires protocol version 2. Claims use owner capabilities, ordered receipt indexes and idempotent completion. Receipt/release/completion transport acknowledgments may be retried; model execution, claim and acquire are not automatically retried. Corrupt ledger reads fail closed.

Provider caps are in `manifests/worker_routing.v1.json`: Cerebras/DeepSeek/Jev 32, Groq 16, NVIDIA 12, local 1, OpenRouter/Vercel 0. Nous/Grok remain unmeasured and ineligible. Caps are shared through authority-owned admission, never multiplied by executor count. A crashed executor can retain a permit; there is no unsafe expiry or takeover. Investigate and reconcile uncertain execution before restoring capacity.

Credential presence is necessary but not sufficient. Cap-zero, saturated, account-blocked and cooling-down providers are excluded. A 401/403 persists until an operator repairs the account and explicitly runs `python -m z0int.provider_saturation reset-health PROVIDER` in the canonical service environment. A 429 imposes a cooldown (default 60 seconds, bounded Retry-After honored). Unknown health is labeled eligible_unprobed; it is not evidence of successful execution. Startup probes the configured default with a tiny public prompt, or reports a cached exclusion without probing again.

The default is Cerebras. Structured/tool/action functions prefer Cerebras, Groq, DeepSeek; plain text prefers Cerebras, DeepSeek, Groq. Capability evidence and typed Jev/local functions remain distinct. Host-local Laya/SolPi/RLM implementations remain in place.

`GET /v1/providers` exposes cap/inflight/health and authority protocol version. `GET /metrics` adds provider-labeled inflight, cap, capped, rate-limit, error, unhealthy and latency histogram metrics reconstructed from canonical receipts. Existing process-level metrics remain available. Physical attempt receipts carry provider, cap, inflight_at_admission and capped in their existing fields/extra metadata.

The one remote-only executor is defined in `/home/kvn/zer0/oss/hermes-k8s-lab/k8s/intelligence-executor.yaml`; image build source is `deploy/executor/Dockerfile`. It supports the existing bounded remote worker execution path, uses a read-only root, and owns no local receipt state. Readiness requires a credential-backed eligible provider and protocol-v2 authority. No HPA/KEDA is installed by this change.

Current operational caveat: Cerebras returned a real 403 and is account-blocked. The executor has only the existing OpenRouter credential; cap 0 correctly keeps it unready. A healthy permitted provider must be configured via the existing secret lane before useful pod execution. Host DeepSeek, Jev and local execution passed. Automatic per-turn harness integrations remain disabled.

Evidence: `/home/kvn/Documents/ChatGPT/z0/receipts/provider-saturation/report.md`. The two-process 64-request cap test uses a local counted HTTP fixture; actual provider calls and earlier live replica-safety proof are reported separately. Policy ceilings come from the supplied handoff, not a new paid-provider saturation run.
