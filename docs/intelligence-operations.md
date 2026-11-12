# Supervised shared intelligence operation

Current update: [validated free-only execution](free-only-execution.md). The single executor is now Ready on the repaired OpenRouter free route; OMP alone has its automatic decision hook enabled. Earlier cap0/unready descriptions below are historical.

The canonical service is owned by the user systemd unit `z0intelligence.service`. It is enabled at the user default target; `loginctl show-user kvn -p Linger` reports yes. Provider credentials still come from the existing dsh-env-exec allowlist and TypeSafe host configuration. No secrets are stored in the unit or harness adapters.

Source unit: `deploy/systemd/z0intelligence.service`, linked into `/home/kvn/.config/systemd/user/`. Use `systemctl --user status|restart|stop z0intelligence.service`; inspect logs with `journalctl --user -u z0intelligence.service`. The process drains accepted work on SIGTERM, stops new admission, and waits up to the unit's 125-second stop timeout. Crash recovery uses Restart=on-failure and a three-second delay. A hard crash leaves uncertain canonical requests unreplayed rather than silently executing twice.

`scripts/intelligence-readiness.py` waits for the host listeners before publishing the existing Kind EndpointSlice ready=true and publishes false on service stop. Kind publication failure is logged and does not disable the host fast path. A user-service restart after a SIGKILL fault recovered host readiness in 3.28 seconds. Reboot behavior is configured via enabled unit plus lingering, not tested by rebooting this workstation. The current gateway/interface/UFW rule remain tied to this Kind network; network recreation with different addressing requires reconciliation.

## Harness wiring

All integrations return the shared service result to the caller. They do not merely observe a selected route. Host integrations use loopback to the same authority published through Kubernetes Service DNS. This preserves host fast paths without forcing a cluster round trip.

- OMP: `/home/kvn/.omp/agent/extensions/z0int-intelligence` points to canonical `omp-extensions/z0int-intelligence`. Tool `z0int_route_worker` is essential. Tested through OMP's installed `loadExtensions` and registered tool executor.
- Hermes: existing MCP client configured as `z0intelligence` in root, chiefstaff and intake configs. Tool `mcp__z0intelligence__route_worker`. Tested via the installed Hermes MCP discovery and ToolRegistry dispatch.
- DSH: existing `@deepseek-ai/dsh-mcp-client` configured in web, headless, deepseek-sdk and sdk-minimal Cordis patches. Tool `mcp__z0intelligence__route_worker`. Tested using the installed Cordis context, SystemPrompt, ToolRuntime and MCP plugin, including cleanup. The adapter removes minimum/maximum schema annotations that DSH's smaller schema vocabulary rejects; the service still validates all numeric bounds.
- Codex: `z0intelligence@personal` version `0.1.0+codex.20260927001037`, with its existing route_worker shim importing canonical `z0int.intelligence_mcp`.

Canonical MCP transport: `src/z0int/intelligence_mcp.py`. Harness identity is fixed by configuration, not a model argument. Clients provide stable trace_id, parent_agent, function and task. Honest estimates control delegation worthiness. Remote context remains opt-in; source/sensitive receipts remain prohibited unless separately authorized. On timeout, execution is uncertain and the same request/trace must be reconciled. Do not create a fresh trace to blindly retry.

These are explicit router-tool integrations. Existing per-turn Jev middleware and shadow observers were not silently converted into new routing authorities. Fresh runtime instances loaded and consumed results during verification. Already-running sessions may require their supported reload/new-session operation; unrelated live gateways were not restarted.

## Concurrency metrics and scaling gate

`GET /metrics` exposes Prometheus counters and gauges for active/peak dispatches, completions, failures, replays, cumulative dispatch seconds, admitted connections and backpressure. Metrics are in memory and reset on process restart. Canonical receipts remain the durable execution source; no parallel execution log was introduced.

A 12-request simultaneous control-plane burst across OMP/Hermes identities produced four successful PARENT_ONLY responses and eight HTTP 503 responses, with peak active dispatches four. This verifies bounded admission, not model throughput. Client timing and counter snapshots are retained under `/home/kvn/Documents/ChatGPT/z0/receipts/supervised-intelligence`.

Do not attach HPA/KEDA to the host EndpointSlice: it has no scale subresource. There is now exactly one remote-only `z0intelligence-executor` Deployment in the existing hermes-lab namespace, using the canonical dispatch authority. No autoscaler is enabled. Provider admission caps and provider-labeled metrics are authority-owned and durable through canonical receipts; the older aggregate process metrics above still reset on restart.

The executor currently has only the existing OpenRouter credential, whose policy cap is 0, so its readiness correctly returns 503. The host default Cerebras returned a real 403 and is persistently account-blocked; host DeepSeek/Jev/local execution passed. Configure a healthy permitted provider through the existing credential lane before useful replica execution. See [dispatch authority and saturation operations](dispatch-authority.md) for ownership semantics, admission, health reset, and evidence. Automatic per-turn harness hooks remain disabled.
