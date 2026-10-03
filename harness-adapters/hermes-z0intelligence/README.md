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
refused and counted (`fanout_cap`), so event writes never wait for it. `close()` (plugin unload) returns within
2 s: it drains while time remains, stops the child and counts what is left. A projection already running may still
finish its one opportunity row. If z0int is missing or broken, hooks still return at once, the turn completes, and
the batch is counted as `z0int_unavailable` (retried after 30 s).

## One capture vehicle

If `z0int-decisions` is enabled, or `bend` is enabled with `stack_opportunities: true`, in the same profile, this
plugin emits no opportunities and writes one `double_capture_guard` failure row. Outcomes and events continue.

## Automatic path (#95)

The routing call (`z0int.automatic`) is registered only while `$Z0INT_HOME/config/automatic.json` has
`{"hermes": {"enabled": true}}` at load; today it is off, so no subprocess is spawned for it on any turn.

The API-attempt scorer/evaluator and the `hermes z0` runtime CLI move here in a later step (C4b).

## Install (activation step A3; owner-approved, after backups)

```bash
hermes plugins install https://github.com/kvnloo/z0intelligence/tree/<sha>/harness-adapters/hermes-z0intelligence --ref <sha> --force
```

then set `mode: shadow`, `z0int_python` and `z0int_home` in the plugin settings.
