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
