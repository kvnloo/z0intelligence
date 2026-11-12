# Validated free-only execution

The active worker policy sets `free_only: true`. Only OpenRouter `nvidia/nemotron-3-super-120b-a12b:free` is currently validated and admitted, with a conservative cap of 1. DSH measured this exact route; its proposed Qwen free model had only catalog evidence. Vercel recovered (cap8), but its measured positive cost makes it ineligible for strict zero-priced execution. Other provider caps/models were preserved and their unvalidated routes are excluded.

The exact-route allowlist is in `manifests/worker_routing.v1.json`, with evidence path/hash and zero-price request constraints. Candidate selection, execution, and authority claim/admission enforce it. Caller flags cannot turn off the operator policy. All-free-unavailable work returns PARENT_ONLY or explicit refusal. Historical results can still replay without re-executing paid routes; uncertain work remains refused.

The existing OpenRouter Kubernetes Secret was refreshed through the existing DSH credential lane. One replica of `z0intelligence-executor` is now Ready. A real request through Service DNS returned 42, provider-reported cost0, and an authority-written canonical receipt. Replay after replacement made no second physical call; conflicting fingerprints return400. No HPA/KEDA was enabled.

OMP alone is enabled in `/home/kvn/.z0int/config/automatic.json`. The installed native turn hook calls and consumes the canonical service decision automatically. It currently returns PARENT_ONLY for unmeasured functions; transport/cost evidence is not function-quality evidence. Hermes/DSH remain disabled. Roll back with `omp.enabled=false` or process environment `Z0INT_AUTO_OMP=0`. Remote context remains opt-in. Existing sessions must have loaded the extension.

Evidence and exact traces: `/home/kvn/Documents/ChatGPT/z0/receipts/free-tier/report.md`. This supersedes the earlier cap0/unready snapshot in the saturation report. Free-route quality and measured delegation worthiness remain the next blockers to automatic worker offload.
