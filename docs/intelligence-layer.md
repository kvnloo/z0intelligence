# Canonical intelligence layer

`manifests/capabilities.v1.json` records function-specific evidence, metrics, measurement confidence, latency, unknown cost, locality, availability probe policy, execution adapter, and fallbacks. Unknown quality/cost is null, never zero. The worker providers retain their existing model configuration. Jev is not ranked globally best.

Jev evidence sufficiency uses the evaluated three-way choice: supported, insufficient, contradicted. Authored evidence interpretation scored 46/48; perturbations scored 36/36 but contains no insufficient labels. These are authored, model-reviewed examples, not a broad human-adjudicated quality guarantee. The real TypeSafe backend pins jev-1.13.0 and reuses scripts/race-omp-backends.py's environment/OMP credential path. No credentials were copied.

Laya verification_needed remains experimental: its prior evidence is only 2/2 and explicitly ineligible for production. The adapter uses the exact structured state and question from the fixture. A paraphrased untested-patch probe in this integration returned the wrong answer, reinforcing that restriction. NanoJev is excluded from evidence sufficiency after failing to distinguish supported, unsupported and ambiguous packets.

## Calls

Host clients use `POST http://127.0.0.1:11501/v1/intelligence` or the same `z0int.intelligence.dispatch` host function. Prefer the service to reuse a warm Laya instance. Required fields: harness, stable trace_id, parent_agent, function, task. Remote calls require allow_remote=true; do not set it for source or sensitive receipts without authorization. Typed evidence state requires question and evidence. Experimental Laya state requires files_changed, tests_run, user_asked_ship. Text workers accept context, max_tokens, expected_parent_tokens and expected_parent_ms.

The Codex plugin's existing route_worker now calls this service. Its required fields are task, parent_agent, trace_id and function; harness is codex. Missing task-size/latency estimates lead to PARENT_ONLY. The plugin source is /home/kvn/plugins/z0intelligence, installed version 0.1.0+codex.20260926233910. Existing already-loaded tool schemas may require a new Codex task to refresh; the installed stdio handshake was tested.

Small or unknown text tasks return PARENT_ONLY; their caller must do the work. Larger tasks must exceed both the 256-token provisional floor and the observed 30.804-second workflow overhead from the single tiny-task comparison. These are conservative heuristics from limited evidence, not a learned policy or a positive savings claim. Caller estimates must not be inflated to force offload. Typed functions use their separately declared capability contract. Unvalidated functions, missing credentials and exhausted execution fallbacks return control to the parent.

Responses distinguish route selection from executed. A PARENT_ONLY response has executed=false and requires_parent=true. It is a dispatch decision, not proof the parent completed its task. Real function/text attempts emit canonical decision receipts before/after execution. The activity reader displays those attempts; dispatch coordination rows lack worker identity and are not double-counted as model calls.

## Concurrency and replay

All cooperating canonical receipt writers hold a POSIX file lock, flush, and fsync before returning. Dispatch is keyed by harness plus trace_id, with an exact request fingerprint. The same request replays its result from canonical receipts; changed content with the same identity is rejected. An interrupted started dispatch is uncertain and never automatically re-executed. This provides at-most-one automatic execution per identity on this host, not distributed exactly-once side effects. Do not retry an uncertain operation with a new ID without reconciliation.

The service admits four active connections, has a socket backlog of eight, and immediately rejects excess accepted requests with 503/Retry-After. There is no unbounded execution queue. Duplicate waits consume bounded slots. Laya is serialized within the host process. GET /healthz reports process liveness; /readyz reports dispatch configuration readiness, not proof every provider is live. The service is trusted-local lab access, not an authenticated public multi-tenant API; harness labels are attribution, not credentials. It refuses wildcard binds.

## Existing Kubernetes lane

The existing kind-hermes-lab context and hermes-lab namespace are reused. /home/kvn/zer0/oss/hermes-k8s-lab/k8s/intelligence-host-service.yaml defines a Service plus host EndpointSlice for 172.19.0.1:11501. This keeps weights, credentials, canonical receipts, and latency-sensitive inference on the host. There is no separate model deployment, replica authority, or HPA.

Host calls and concurrent harness-style clients passed. The initial pod-to-host timeout was resolved with user-authorized sudo and one persistent UFW ingress rule: TCP from 172.19.0.0/16 on br-bac6912bfec4 to 172.19.0.1 port 11501. Both direct pod access and Service DNS readiness passed, followed by a real PARENT_ONLY request through the Service. EndpointSlice is now ready=true. No wildcard listener or firewall reset was used. Evidence: /home/kvn/Documents/ChatGPT/z0/receipts/kind-repair/pod-verification.jsonl. The host process is now supervised by the enabled user systemd unit z0intelligence.service. OMP, Hermes and DSH have shared-service tool integrations verified through their installed runtimes. Existing sessions require reload/tool discovery; automatic per-turn defaulting is not implied. See [operations](intelligence-operations.md) for lifecycle, configuration, concurrency evidence and the autoscaling prerequisite.

## Observability and validation

Use the existing `python -m z0int.worker_activity --watch --parent-agent codex:intelligence-acceptance`. No dashboard or second telemetry format was added. The acceptance evidence under /home/kvn/Documents/ChatGPT/z0/receipts/intelligence includes real four-route results, the failed exploratory run, concurrent same-trace replay, replay after restart, Codex MCP PARENT_ONLY, and Kubernetes readiness failures.

49 affected tests passed. Full suite: 420 passed, one skipped, one pre-existing Decider BackendCapabilities constructor failure. No HPA or net token-savings claim is made.
