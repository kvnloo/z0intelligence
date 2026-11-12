# Worker activity from canonical receipts

Read activity without changing routing, writing another event log, or granting workers filesystem access:

```sh
PYTHONPATH=src python -m z0int.worker_activity --parent-agent '<parent identifier>'
```

The optional `--receipts` argument selects a JSONL receipt file; otherwise the existing canonical decisions file is used. `worker_activity.v1` returns a `workers` list, with parent and subagent IDs, selected provider/model, provider-reported response model, latest attempt status, fallback flag, attempt-level usage, known token/latency totals, unknown usage count, and estimated frontier tokens avoided. Missing attempt usage stays null. Known totals are lower bounds when unknown usage exists.

OMP, Hermes, DSH, and Codex can poll this same read-only snapshot and render worker rows keyed by `(parent_agent, subagent_id)`, expanding physical attempts by `trace_id`. No native Codex sidebar hook is installed. Polling is a minimal integration contract, not a pushed live UI; each read scans the receipt file. The projection is harness-independent, but each harness must emit the same canonical identity fields before its work can appear.

Each physical call has one trace ID. A terminal record supersedes its start; a later duplicate start cannot erase a terminal. Latest appended terminal wins for conflicting terminal records. Retries retain separate trace IDs and are ordered by attempt index. Known tokens from failed/incomplete attempts remain chargeable. Savings are taken only from the latest successful attempt, never summed across retry history. The existing bytes/4 baseline estimates delegated frontier workload, not measured net savings after orchestration.

Execution completion does not verify output quality or parent consumption. The snapshot explicitly reports `quality_verified: false` and `parent_consumption: not_recorded`; actual parent adoption must be evidenced separately. Started means no terminal receipt has been observed, not proof that the worker is still alive. An unfinished trailing JSONL append is ignored until its newline arrives; malformed complete lines raise an error.

## Live Codex terminal view

Run `PYTHONPATH=src python -m z0int.worker_activity --watch` in a Codex terminal panel. Optional `--parent-agent` scopes the view; `--interval` defaults to two seconds. Stop with Ctrl-C. The reader polls canonical receipts directly and retries read errors. It shows subtask, provider/model, status, known latency and tokens, and the entire fallback chain. Older receipts without subtask text display the worker ID. The route adapter now stores a 120-character task label locally on its canonical receipt; it does not transmit any additional context.

Aggregates distinguish physical attempts, completed attempts, and adopted workers. Unknown usage is counted explicitly. After actually consuming a completed output, a parent may call `acknowledge_adoption(trace_id, output, parent_agent)`. This appends an updated canonical receipt only when parent identity and output SHA-256 match. It records a parent assertion, not independent quality verification. Duplicate lifecycle/adoption records do not inflate the view's totals. Other raw-log consumers must likewise deduplicate by trace ID.

The terminal display strips control characters from receipt text and does not display estimated savings. It is a local polling view, not a native Codex subagent sidebar. No second telemetry log or web service is introduced.
