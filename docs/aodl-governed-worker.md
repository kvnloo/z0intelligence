# Governed remote worker canary

This is the first end-to-end AODL-governed off-host execution lane.

## Boundary

The OMP tool supplies only:

- task/context;
- stable trace id;
- parent session id;
- completion cap;
- explicit `allow_remote=true`.

The host re-checks `allow_remote=true` and requires the configured canary
harness (default `omp`). The client-side selector is not treated as the
authorization boundary.

It does **not** supply provider/model, AODL, current spend, child count, parent
depth, authority ceiling, or budget limits.

The host owns those decisions:

```
OMP explicit tool
 -> /v1/governed-worker
 -> load authored AODL contract
 -> reconstruct canonical controller state from receipts
 -> choose validated $0 provider/model
 -> freeze governance envelope once per trace
 -> remote executor /v1/execute
 -> authority protocol v3
 -> AODL admission
 -> provider permit
 -> physical call
 -> canonical receipts
```

## Replay invariant

The controller state is sampled only when a trace is first prepared. That
governance envelope is frozen in a canonical `aodl.governed_request` receipt.

The preparation receipt stores:

- caller-request SHA-256;
- provider/model;
- AODL document + spawn envelope;
- intent/source lineage.

It deliberately does **not** store task or context text.

An identical retry reconstructs task/context from the caller while reusing the
frozen governance envelope. Same trace + changed caller input fails closed.
This prevents a retry from changing its own `live_children` or observed spend
after the first attempt has already affected controller state.

## Controller-observed state

For the canary parent node, the host derives:

- `live_children`: latest dispatch rows still at `status=started` whose
  admission belongs to the same intent source and parent node;
- observed `tokens`: known input+output tokens from terminal physical receipts
  belonging to those governed dispatches;
- `parent_depth`: operator-owned canary setting;
- proposed `tokens`: estimated worker prompt tokens + `max_tokens`.

Unknown usage is not converted into zero-cost or successful-task claims. This
first contract only has a token Gamma dimension.

## Provider placement

The harness cannot choose placement. The host selects
`Z0INT_GOVERNED_PROVIDER` (default: first `free_provider_order`) and requires
an exact `validated_free_routes` entry with `price_usd == 0`.

The checked-in canary contract is:

`contracts/aodl/governed-worker-v1.json`

It allows one live child at depth one and a 4096-token cumulative budget.

## OMP rollout

The existing `z0int_route_worker` tool switches to this path only when all are
true:

1. `Z0INT_GOVERNED_REMOTE=1`;
2. `function == "cheap_bounded_worker"`;
3. `allow_remote == true`.

Otherwise it continues to use `/v1/intelligence`.

This is intentionally narrower than automatic before-turn routing.

## Host-to-executor networking

Do not change cluster exposure just for the canary.

For a Kind canary, a loopback port-forward is sufficient:

```bash
kubectl -n hermes-lab port-forward svc/z0intelligence-executor 11503:11503
```

Then set:

```
Z0INT_REMOTE_EXECUTOR_URL=http://127.0.0.1:11503
```

A durable network topology should only be selected after the golden trace
demonstrates value.

## One-command full golden canary

Use clean checkouts of this PR stack and z0evals #81, with the existing
OpenRouter credential already exported:

```bash
python scripts/run-aodl-golden-canary.py \
  --omp-root /path/to/oh-my-pi \
  --z0evals-root /path/to/z0evals \
  --output /tmp/aodl-golden-canary
```

Before any provider call the wrapper requires:

- `OPENROUTER_API_KEY` is already present;
- both repositories are clean;
- z0evals' golden manifest pins the exact z0intelligence HEAD;
- `aodl_contract` exposes `aodl-canon-1`;
- the OMP and collector paths exist.

It then runs the live public proof, runs the frozen collector with
`--require-verified`, and writes `bundle.json` plus
`golden-trace.json`. Raw receipts remain under `raw/state` for audit and are
not implicitly committed.

## Proof

`scripts/prove-intelligence.py` now starts the actual authority and remote
executor HTTP servers on loopback and exercises:

- protocol-v3 readiness;
- two concurrent identical requests;
- one physical free-provider call;
- completed replay;
- trace conflict;
- provider-cap refusal;
- deliberately stranded dispatch / no takeover;
- prompt-free governance snapshot;
- actual OMP registered `z0int_route_worker` execution.

The live proof is opt-in because it uses the configured provider credential.

The single physical provider call is initiated by OMP's real registered
`z0int_route_worker` tool via `ExtensionRunner.getRegisteredTool()`; no
parent model is invoked. Replay/conflict/cap/uncertain checks reuse the host
boundary without additional inference.

For the one public diagnostic, the expected answer is exactly
`CANONICAL_OK`. The proof checks that exact string and joins a gold outcome
with verifier identity `exact_string_CANONICAL_OK` to the physical receipt.
That closes the golden-trace verification stage **for this synthetic fixture
only**. It is not evidence of general model quality.

Every other execution continues to keep execution completion separate from
verified task success.
