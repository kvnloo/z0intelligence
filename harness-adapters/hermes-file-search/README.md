# Native Hermes repository-search tool

Opt-in, **tool only**: registers `z0_file_search` and static priority guidance.
No `before_turn`, `pre_llm_call`, routing, middleware, worker delegation or model
calls. This is separate from the existing Hermes z0intelligence routing plugin.

The plugin is a stdlib HTTP client for the existing shared intelligence service:
`POST /v1/file-search`. It needs **no z0int, adapters or FFF package in Hermes's
Python**. The service's Python needs `fff-search==0.11.0`; the service owns the
resident index and canonical EventLog. No backend replica is created in Hermes.

## Operator activation (not performed by implementation/tests)

1. Deploy the reviewed service/MCP code and make sure its runtime has the pinned
   FFF wheel. On that service process, set `Z0INT_FILE_SEARCH_ROOTS` to a JSON
   array of allowed absolute repository directories. File search is denied by
   default; private `Z0INT_HOME` state and overlapping directories are excluded.
   Existing service admission and trusted-interface policies remain.
2. Copy this directory into the intended active profile's
   `$HERMES_HOME/plugins/hermes-file-search/` (not another profile).
3. Enable with `hermes plugins enable hermes-file-search`, then start a fresh
   Hermes session. This plugin does not enable or change any routing plugin.
4. Optional setting via the supported CLI:

   ```bash
   hermes config set plugins.entries.hermes-file-search.settings.service_url http://127.0.0.1:11501
   ```

   Origin precedence: plugin `service_url` setting, existing `Z0INT_SERVICE_URL`
   environment, then `http://127.0.0.1:11501`. It is trusted configuration, not a
   model tool argument. Do not point it at an untrusted service: queries and file
   excerpts are repository data. No credentials belong in this setting.

## Tool use and identity

- Known exact file: use `read_file`.
- Repository path/symbol/content discovery: prefer `z0_file_search` with an
  **explicit absolute `repository_root` on the service host** and `query`.
- `kind`: `natural_language` (default), `exact_symbol`, or `exact_path`.
- Optional `limit` (1–20; default 5), `time_budget_ms` (10–1000; default 120).
- Optional `trace_id` and `session_id` propagate when the host has no injected
  identity. Host context `trace_id` / `task_id` and `session_id` take precedence.
  Harness is always `hermes`: neither tool arguments nor `Z0INT_HARNESS` can spoof
  the EventLog source through this plugin.
- Missing service/index or rejected request returns JSON with `ok=false` and
  explicit fallback guidance. It does not create another backend or silently
  search notes. Normal misses remain ContextPackets with unresolved gaps.
- Use QMD separately for notes; use `stat`/`du` for metadata and
  `git worktree list` for the Git worktree registry, not FFF.

Responses are provenance, not authorization or verified task success. Read the
canonical file before relying on an index hit. See the complete
[HTTP contract and integration gate](../../docs/file-search.md).

The automated native probe uses the real Hermes manifest parser, `PluginContext`,
`register_tool` registry and handler with isolated scratch homes. It does not
start a model session or change an installed profile. Set
`HERMES_PLUGIN_TEST_PYTHON` and `HERMES_PLUGIN_TEST_ROOT` as documented in that gate.

## Plugin API references

- [Official native plugin guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins)
- [Official plugin activation and tool registration](https://hermes-agent.nousresearch.com/docs/user-guide/features/plugins)

Manifest v2 and `register(ctx)` follow those public APIs. Static priority text is
registered once; no hook rewrites the prompt on every turn.
