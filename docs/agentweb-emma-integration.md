# AgentWeb / Emma integration lab

This branch is downstream-only. It is an experiment surface for making z0intelligence a first-class intelligence and receipt authority for Emma without changing AgentWeb's user-facing safety boundaries.

## Contract

AgentWeb identifies itself as the `agentweb` harness. The caller owns a stable operation id; the adapter derives a pseudonymous `parent_agent` and deterministic `trace_id` without exporting a raw AgentWeb user id or session id.

Two service paths are intentionally distinct:

- `POST /v1/plan` is shadow-only. It runs routing against the current registry and availability snapshot, performs no specialist/worker call, and writes no canonical dispatch receipt.
- `POST /v1/intelligence` is the active canary path. The existing dispatch authority owns claim identity, execution and durable receipts.

For an active call, an ambiguous timeout never authorizes a new physical attempt. Reconcile the identical request with the identical trace id. A changed request reusing that identity must be rejected by the authority.

## Safety defaults

- AgentWeb adapter mode is expected to default to `off`.
- `allow_remote` defaults to false and must not be inferred from prompt text.
- `free_only` is expected to remain true for generic workers.
- Existing AgentWeb confirmation, evaluation-workspace, scheduled-publish and funding guards stay outside z0 and remain authoritative.
- Reliability/tool telemetry is observational evidence. A successful transport or tool call is not a verified-quality outcome and must not mint gold.
- Unknown execution remains unknown.

## Promotion gates

Do not promote this integration upstream until the z0evals AgentWeb/Emma cohort freezes exact tested revisions and demonstrates:

1. stable replay and conflict rejection;
2. zero re-execution after ambiguous active timeouts;
3. no raw user/session identifiers in exported receipts;
4. remote-context and free-only policy enforcement;
5. JEV parity on the eligible decision slice;
6. preservation of AgentWeb safety guards;
7. measurable latency/token benefit on at least one representative workload;
8. no quality regression on the same workloads.


## Evidence-only experiment surfaces

Two additional endpoints exist on this downstream branch. Neither grants production
capability eligibility.

### `POST /v1/experimental/choice`

A bounded AgentWeb-only Jev Choice lane for collecting receipt-backed evidence before
a decision can enter `manifests/capabilities.v1.json` as eligible.

Hard gates:

- `harness=agentweb`;
- `function=experimental_choice`;
- `experimental=true`;
- `allow_remote=true`;
- authority-side `Z0INT_EXPERIMENTAL_JEV_SHADOW=1`;
- 2–16 bounded choice labels;
- state <= 24KB;
- output is always `applied=false`;
- receipts use `execution=shadow` and `quality_authoritative=false`;
- identical trace + identical request replays;
- identical trace + changed request conflicts.

This lane may pay for the explicitly authorized Jev shadow call. It cannot overflow
into arbitrary paid workers and it cannot make itself eligible.

### `POST /v1/observe/reliability`

Idempotent ingest for AgentWeb's already-scrubbed tool-boundary observations.

Accepted rows must already be:

- `z0int.decision_receipt.v1`;
- `provider=agentweb`;
- `route=shadow`;
- `execution=log_only`;
- `measurement_state=partial`;
- pseudonymous `agentweb:<24 hex>` session identity, if present;
- free of `verified_success`, `verification_source`, arbitrary error text, user ids,
  prompt/tool arguments and tool results.

The endpoint adds an observation digest and rejects changed payloads under an existing
observation trace. Observations remain execution/reliability evidence, never quality
gold.


### `POST /v1/context/pack`

Pure metadata/CPU context compiler for already-selected AgentWeb knowledge evidence.

AgentWeb remains authoritative for retrieval and private-store access. Before
transport it pseudonymizes source/file identity; z0 accepts only
`agentweb-kb:<sha256>` source IDs/locators plus the selected excerpt text.
The compiler:

- makes zero model calls;
- writes no private excerpts to `Z0INT_HOME`;
- assigns `index_hit` trust itself rather than trusting caller labels;
- preserves one source for every satisfiable required need;
- preserves at least the top three ranked sources when three or more exist;
- focuses excerpts deterministically around need/query terms, with head+tail fallback;
- marks capped/incomplete retrieval as an unresolved gap;
- keeps the final serialized packet within `max_packet_bytes` or fails closed;
- strips excerpt duplication from the AODL structural projection.

The returned packet remains `applied=false` in this study. Injection into Emma is
a later promotion stage and requires z0eval evidence rather than a configuration
flip alone.
