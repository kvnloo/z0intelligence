# hermes-z0intelligence

The one z0 plugin for Hermes: a standalone Hermes plugin (`plugin.yaml` + `register(ctx)`, stdlib only, no Hermes
core change). It replaces the retired `hermes-z0int-decisions` plugin and the z0 capture half of bend-native
(#385). With `mode: shadow` it records each Hermes turn into the shared z0int record family; every hook returns
`None`, so nothing reaches the model, the user or a tool.

| Hermes hook | What is kept |
| --- | --- |
| `pre_llm_call` | turn ids, work item, capture-time cohort; a DecisionOpportunity for user turns (built in a detached z0int child) |
| `post_llm_call`, `on_session_end` | `turn_outcome.v0`: asked_user, escalated, approval_requested, tool_calls, ended, wall_s (session end closes unfinished turns) |
| `pre_approval_request` | an approval count on the open turn (never the command) |
| `post_tool_call` | tool name, status, and for `terminal` only the check class and exit status (never args or output) |
| `on_session_start`, `pre/post_api_request`, `api_request_error`, `pre/post_auxiliary_call`, `subagent_stop` | content-free scalar fields and token usage |

`pre_tool_call` is never registered. With `mode: off` (the default) no capture hook is registered at all.

Rows land in `$Z0INT_HOME/state/hermes/` (never under `HERMES_HOME`): `events.jsonl`, `opportunities.jsonl`,
`outcomes.jsonl`, `failures.jsonl` (`z0int_unavailable`, `config_warning`, `double_capture_guard`, the core's
`missing_verifier` / `partial_measurement`), `drops.jsonl` (queue full, fan-out cap, unavailable z0int, close).
Turn ids are the canonical `harness_id.turn_key`; the old raw `<session>:<turn>` and bend sha256 trace ids map to
the same key through the C1 aliases. Cohort at capture: `interactive`; `automated` for cron, kanban and cluster
service sessions (platform, `HERMES_SESSION_SOURCE`, `HERMES_KANBAN_TASK`); `agent` for subagents; `harness` for
injected system turns. `z0int hermes decisions` reports gate vs observed behaviour and `rows_dropped`.

## Settings (`plugins.entries.hermes-z0intelligence.settings`)

| Key | Default | Meaning |
| --- | --- | --- |
| `mode` | `off` | `off` or `shadow` |
| `opportunities` | `true` | build DecisionOpportunities for user turns |
| `z0int_python` | (empty) | interpreter of the installed z0int; else `Z0INT_PYTHON`, else `$Z0INT_HOME/config/hermes.json` `python` |
| `z0int_home` | (empty) | else `Z0INT_HOME`, else `~/.z0int` |
| `persist_packet_text` | `false` | see privacy below |
| `memory_inject` | (empty) | z0 memory seam: `off`, `shadow`, `canary`, `on`; empty follows `mode` (see Memory) |
| `memory_injector` | (empty) | owner id for the single-injector guard (default `z0-memory:hermes`) |

There is no service host or port: capture writes files only and opens no socket. A host/port/url setting (for
example bend's `stack_service_port`) is ignored and written down as a counted `config_warning`. Kill switch:
`Z0INT_CAPTURE=0`, or `$Z0INT_HOME/config/capture.json` `{"enabled": false}`.

## Privacy classes

- Request text: kept in an opportunity only with `Z0INT_CAPTURE_PRIVACY=request_opt_in` (default `content_free`).
- State Packet text (`packet_text` on each opportunity row): `redacted` by default, which replaces every claim
  value (branch names, commit subjects, README open items) with its sha256 digest; `opt_in` with
  `persist_packet_text: true` keeps the text. Without the opt-in the State Packet also keeps no snapshot of its
  own (`state/state_packet/<repo>/latest.json`, `history.jsonl`). A Hermes projection reads the task repo's git
  and docs only, never another harness's transcript store. Training/export tables never carry either text.
- Tool args and output, assistant replies and conversation history are never written.

## Runtime shape

Hooks put a small job on a bounded in-process queue (4,096). One writer thread hands batches, in order, to
`<z0int_python> -m z0int.hermes_capture batch` (a detached child), which writes through `z0int.harness_capture`.
A projection runs in a detached `project` child holding one of `MAX_CHILDREN` build slots; when none is free it is
refused and counted (`fanout_cap`), so event writes never wait for it. `close()` (plugin unload, and interpreter
exit) returns within 2 s: it drains while time remains, stops the child, waits for running projections while time
remains and counts what is left. Projections are tied to the plugin's lifetime by a per-instance gate file
(`$Z0INT_HOME/runtime/hermes-gates/`): a projection writes only while holding the gate, and `close()` removes it, so
nothing is written after `close()` returns; a projection cut off this way is counted as a `closed` drop. If z0int is
missing or broken, hooks still return at once, the turn completes, and
the batch is counted as `z0int_unavailable` (retried after 30 s).

## One capture vehicle

If `z0int-decisions` is enabled, or `bend` is enabled with `stack_opportunities: true`, in the same profile, this
plugin emits no opportunities and writes one `double_capture_guard` failure row. Outcomes and events continue.
The retired `z0int-decisions` child (`z0int hermes opportunity`) no longer records anything but one content-free
`retired_vehicle` failure row per call, so a stale install shows up in `failures.jsonl` and writes no opportunity.

## Automatic path (#95)

The routing call (`z0int.automatic`) is registered only while `<z0int_home>/config/automatic.json` has
`{"hermes": {"enabled": true}}` at load; today it is off, so no subprocess is spawned for it on any turn. When on,
it runs the same interpreter as capture (`z0int_python` / `Z0INT_PYTHON`) with `Z0INT_HOME` set to that home.

The API-attempt scorer/evaluator and the `hermes z0` runtime CLI move here in a later step (C4b).

## Memory (C8, `memory.py`)

The z0 memory seam shares the one `pre_llm_call` with capture and automatic (their contexts are joined), and the
one `post_llm_call` (capture's outcome, then the memory-use receipt of an injected turn). Hermes puts a
`pre_llm_call` context into the current user message at API time only; the plugin never writes the session store.

| `memory_inject` | What happens per user turn |
| --- | --- |
| `off` | nothing |
| `shadow` (default with `mode: shadow`) | one detached `z0int_python -m z0int.memory.seam shadow` child; the hook returns at once; a `z0int.memory_seam.v0` row with `would_inject` and the MemoryUseReceipt |
| `canary` / `on` | `... seam turn` within 300 ms (counted from the job start); the brief joins the user message; past the deadline or on any failure the turn is native and a `timeout` / `error` row is counted |

Canary/on inject only into a loopback model (`model.base_url`) unless the owner sets
`{"inject": {"hermes": {"allow_cloud_injection": true}}}` in `<z0int_home>/config/memory.json`. With Hermes's own
`memory.provider: memory_tencentdb` the z0 brief leaves TencentDB out (that provider already injects it). A
replayed turn never injects twice; a second injector for the same turn refuses (`double_inject_guard`). Rows:
`<z0int_home>/state/memory/seam/hermes.jsonl`. Memory reads go through the z0 memory surface only; the memory
tools for the model are the `z0-memory` MCP server (Hermes toolsets carry `no_mcp`, so that is for MCP-enabled
profiles). Activation target profile: chiefstaff/clean (owner decision; not activated here).

## Install (activation step A3; owner-approved, after backups)

```bash
hermes plugins install https://github.com/kvnloo/z0intelligence/tree/<sha>/harness-adapters/hermes-z0intelligence --ref <sha> --force
```

then set `mode: shadow`, `z0int_python` and `z0int_home` in the plugin settings.

Unverified: this GitHub-URL install has not been run (Hermes PM syncs its runtime from the network first; the
isolated e2e had no network). A local path (`hermes plugins install <dir>`) is parsed as GitHub `owner/repo`
shorthand by this Hermes, so it does not install a checkout. Fallback, the layout the e2e installs and runs:

```bash
git -C <z0intelligence checkout> archive <sha> harness-adapters/hermes-z0intelligence | tar -x -C <scratch>
mv <scratch>/harness-adapters/hermes-z0intelligence <profile>/plugins/hermes-z0intelligence
```

then add `hermes-z0intelligence` to `plugins.enabled` in `<profile>/config.yaml` with the settings above.
