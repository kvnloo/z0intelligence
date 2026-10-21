# Local HTTP boundary (#187)

The intelligence service (:11501), cognition supervisor (:11500), and remote
executor (:11503) share `LocalHTTPGuard`. It runs before route selection, JSON
parsing or execution, for every HTTP method.

- Host must be exactly `127.0.0.1:<listener-port>` or `localhost:<listener-port>`.
  The listener's specific numeric bind address is also accepted. A bridge
  address is not accepted on the loopback listener. Wildcard addresses are not
  added to the list. Duplicate/missing Host and foreign authorities get 421.
- The Origin allow-list is intentionally empty: every Origin header (including
  `null`, local origins and empty values) gets 403. No current browser client
  is enrolled, and this does not add CORS or trust forwarded headers.
- POST requires exactly one application/json Content-Type (charset parameters
  accepted); otherwise 415. Rejections close the connection without executing
  or interpreting the unread body as another request.
- Existing Kubernetes callers use service DNS rather than the endpoint IP.
  Two exact aliases are accepted only on non-loopback listeners, with the
  listener port: `z0intelligence.hermes-lab.svc.cluster.local` for the host
  authority and `z0intelligence-executor.hermes-lab.svc.cluster.local` for the
  executor. These are narrow compatibility exceptions, not wildcard domains.
- llama-server children remain loopback-only and get a fresh random API key
  per process. The supervisor and serving probe pass that key internally.
  Health is the upstream public health route. Direct users of `serve_model`
  must use the yielded server's `api_key`; callers of the supervisor need no
  change. No models or live services are started by the tests.

## Optional z0bridge verification (off by default)

This implements the access-token contract in kvnloo/z0bridge PR #2,
`docs/PAIRING.md` and `docs/ARCHITECTURE.md`; it does not proxy requests through
z0bridge, enroll callers, mint tokens or replace z0int execution authority.

Install the optional `identity` extra (cryptography). Configure all three:

    Z0INT_Z0BRIDGE_AUTH=1
    Z0INT_Z0BRIDGE_ISSUER=z0bridge:groot
    Z0INT_Z0BRIDGE_JWKS=/owner/managed/path/z0bridge-public-jwks.json

The JWKS file contains only public keys. Provision it from the trusted
z0bridge `/v1/jwks` endpoint and pin/verify the signing kid through the owner's
existing identity channel; do not accept a token-supplied URL or key. The file
is cached once per listener, with no network calls on latency-sensitive paths.
Replace the public snapshot and restart for key rotation. Missing identity
configuration or crypto support fails closed (503). Invalid tokens or missing
scopes get 401; health/readiness probes remain unauthenticated but still have
the browser guards.

Verification requires ES256/P-256, `typ: at+jwt`, known kid, valid signature,
exact configured issuer, exact `aud: z0int`, integer iat/nbf/exp, non-future
issuance and activation, non-expiry, maximum 300-second lifetime, and identity
claims sub/dev/app/jti. Route scope mapping:

- context.read: file-search, context/pack, providers, bridge/capabilities,
  models, metrics, supervisor status/history.
- decide: intelligence, plan, experimental/*, automatic*, agent-orchestrator/*,
  chat completions.
- observe: observe/*.
- dispatch: worker, governed-worker, authority/*, agentweb*, execute, release,
  evict and unknown routes (fail closed).

All scope names are prefixed `z0int:`. Revocation latency is bounded by the
access-token lifetime; no online introspection is claimed. Tokens are not
logged. Authentication is OFF unless the flag is exactly 1. Enabling it is an
owner-coordinated migration: existing callers have no enrollments yet and
will need scoped bearer injection first. This PR does not enable it on either
live listener, including Docker; unauthenticated local/container processes
remain trusted until the owner enables enforcement.

## Caller audit

Repository and installed plugin sources were inspected, including the dirty
live checkout (read-only). Existing callers send JSON with application/json
and no Origin:

- OMP z0int-bridge: stdio to `z0int.bridge.worker`, not an HTTP client. Its
  z0int-intelligence tool uses fetch with an explicit JSON Content-Type and
  the loopback service URL. Native process fetch adds Host, not Origin.
- `automatic.post` and its Hermes/DSH/Claude/OMP harness adapters: urllib JSON.
- `intelligence_mcp.post` / route_worker (and the live file-search adapter):
  urllib JSON. Default endpoint is numeric loopback.
- Hermes `hermes-file-search`: urllib JSON; `bend/stack/bridge.py`: numeric
  loopback, explicit JSON, proxy disabled. `hermes-z0intelligence` invokes
  `z0int.automatic` in a child and inherits its HTTP contract. Its hardcoded
  installed paths predate this change; this PR does not repair unrelated paths.
- Jev shadow and bridge worker are stdio/in-process or external-provider
  clients, not new local HTTP listeners. No shadow or log_only mode changes.
- remote_executor authority RPC: explicit JSON. Its /v1/providers readiness
  GET and the governed_worker executor client use the exact Kubernetes DNS
  aliases above. Readiness scripts use loopback and bridge literal GETs.
- prove-intelligence and cognition probe: JSON, now with private child key
  propagation for the latter. Published benchmark rows are unchanged.

Tests exercise real ephemeral loopback listeners for all three handlers,
legitimate JSON POSTs, malicious Host/Origin/Content-Type, listener-specific
bridge/service aliases, and signed token positive/negative/scope cases. They
simulate the non-loopback bind identity while keeping the socket on ephemeral
loopback; they do not bind the live Docker port. Caller compatibility is based
on source audit plus those contract tests, not a claim of a live OMP/Hermes
turn or a physical provider dispatch.

## Owner deployment

Do not deploy by repointing the running service at a clean worktree: the live
checkout has unrelated uncommitted integrations (including file-search).
Review and integrate the small security diff into a preserved copy of that
runtime first. Do not reset/clean the live checkout or merge a draft PR.
Follow `docs/intelligence-operations.md` and the readiness script. Exact
host-specific staged restart/rollback instructions are in the worker's
REPORT.md; no service restart or live-tree edit was performed by this PR.
