# Intelligence operations

Use Python 3.11 with the declared project environment. The optional Jev implementation requires the existing `jevkit` package on `PYTHONPATH`; reuse its existing credential store. KERD quota-controlled providers require the existing `kerdoios.quota` package, either installed or located with `Z0INT_KERDOIOS_ROOT`. Missing quota support refuses admission rather than bypassing the budget.

Start the host service with `python -m z0int.intelligence_service --bind 127.0.0.1`. A second `--bind` can expose the existing Kind bridge interface. Keep this trusted-host API off public interfaces. Host and authority keep one durable `Z0INT_HOME`; remote executors never receive that directory.

`deploy/systemd/z0intelligence.service` is a user-service template. Set `Z0INT_PYTHON`, `Z0INT_BIND`, `PYTHONPATH` and the existing credential-loader environment in `~/.config/z0intelligence/service.env`. Do not duplicate secrets into the repository. The unit restarts on failure and drains accepted work on SIGTERM. Existing host installations may retain their established credential-loader ExecStart. An uncertain execution remains blocked after restart.

`deploy/k8s/` packages the existing hermes-lab Service/EndpointSlice and one remote executor. Reconcile the bridge address before use; the EndpointSlice starts unready. `scripts/intelligence-readiness.py ready|not-ready` publishes readiness after checking host health. Build `deploy/executor/Dockerfile`, load the image through the existing Kind lane and use the existing `openrouter-credentials` Secret. Keep replicas at one. Do not attach HPA/KEDA to the host-backed Service.

OMP's resident bridge registers the shared intelligence extension. Loading the compatibility `z0int-intelligence` extension too does not register a second hook. The native before-agent-start hook consumes actual specialized results; `PARENT_ONLY` preserves native execution. The resident observational bridge remains separate from physical execution receipts and does not claim execution success. Explicit rollback: `Z0INT_AUTO_OMP=0`, or `omp.enabled=false` in `$Z0INT_HOME/config/automatic.json`. `Z0INT_AUTO_ALLOW_REMOTE=1` is required for outbound verification context. Set `Z0INT_PYTHON` and optionally `Z0INT_SERVICE_URL` in the harness environment.

Hermes and DSH adapters are preserved but are not enabled or migrated by this PR. Their corresponding kill switches are `Z0INT_AUTO_HERMES=0` and `Z0INT_AUTO_DSH=0`. A configured hook with zero completed and delivered requests is not healthy: `python -m z0int.automatic health` checks its exact integration instance. Service `/readyz` reports control-plane readiness, not successful inference.

Codex and other MCP callers use `python -m z0int.intelligence_mcp` with a configured harness identity. Stable trace IDs support reconciliation; after a timeout never invent a new identity to force another call. Only public or explicitly authorized context may be sent remotely.
