# OptChat on OMP's native loop

OptChat defaults to off. `/optchat on|off|status` uses the backend's flag,
`$Z0INT_HOME/optchat/enabled`, defaulting to `~/.z0int/optchat/enabled`.
If `Z0INT_OPTCHAT_ROOT` overrides the chat directory, the flag is its sibling
`enabled`. Only the exact trimmed text `on` enables it. `/optchat-browse`
writes the backend's HTML memory page when enabled.

When off, no tools are registered at startup, no RPC process starts, and no
context, label, widget or hint is changed. Turning off removes OptChat tools
from the active tool set and stops this extension's child process. OMP has no
tool unregister API, so definitions registered during an earlier enabled period
remain in its registry but are inactive and refuse execution.

`index.ts` owns the switch and resident RPC process. `native.ts` installs the
native OMP hooks and tools. The older standalone loop in `turn.ts` remains for
the existing conformance scripts; the extension no longer invokes it.

## Turn boundary and failure behavior

`before_agent_start` captures the last persisted branch message and allocates a
monotonic prompt ID scoped to the session. OMP has not appended the new prompt
or its preludes at this point. The first `context` call finds that anchor and
prepares only the messages through it. All later messages, including prompt
preludes, images, assistant payloads, tool calls, tool results and steering
messages, pass through unchanged.

Preparation is a single promise owned by that prompt ID. Concurrent context
calls share it. Historical messages are imported into the existing backend log;
`view_ids` reads summaries covering exactly those IDs. It never includes a
neighboring session's messages or the live turn. Complete historical chunks
become fixed user messages with constant timestamps. Later prompts append new
chunks and retain earlier frozen chunks byte for byte.

An unfinished summary, RPC failure, historical image or unsupported message
keeps the original messages. Unfinished chunks can become summaries at the next
prompt, never during a tool loop. A missing or changed boundary preserves the
entire input. This favors preservation over compression. Frozen chunks are
retained for the session; this change does not impose a global context-size cap.

There is no model-facing zoom hint. Tool descriptions explain optional source
access. The zoom counter is reserved before awaiting the backend, so concurrent
calls and failures consume the same two-request budget. Only a new prompt resets
it. Session changes and explicit switches invalidate preparation.

## OMP event and provider findings

Read-only reference: `/home/kvn/workspace/oh-my-pi`. Its package manifest reports
18.1.10, while the supplied installed-binary version is 18.11.0. These findings
describe the supplied source; no installed session or credential files were read.

- `packages/coding-agent/src/session/agent-session.ts:6308` emits
  `before_agent_start` once during prompt setup, before `agent.prompt` starts
  the loop. Returned `message` values become custom messages appended alongside
  the prompt and are persisted. Returned `systemPrompt` replaces that turn's
  system prompt. This event cannot replace history. OMP's `turn_start` refers
  to an assistant/tool round, so it must not reset OptChat's prompt state.
- `packages/agent/src/agent-loop.ts:1593` runs `transformContext` during
  `prepareProviderCall`, before conversion to provider messages.
  `packages/coding-agent/src/sdk.ts:3418` connects it to `emitContext`.
  `packages/coding-agent/src/extensibility/extensions/runner.ts:1618` clones the
  messages, chains handlers, and replaces the outbound list when a handler
  returns `{ messages }`. It does not replace persisted session history.
- `packages/ai/src/providers/anthropic.ts:3477` marks the last eligible block
  of each of the last two messages. It skips a synthetic trailing `Continue.`
  pad, transient `clear_at` messages and generated thinking blocks.
  `applyHeadCaching` at line 3532 also marks the final non-deferred tool and
  final system block unless the head already has an upstream marker, keeping
  within four breakpoints. Tool/system content precedes message content in the
  cache prefix. The extension leaves provider breakpoint policy to OMP.
- `packages/ai/src/providers/openai-responses.ts:1093` and
  `openai-completions.ts:1612` gate explicit breakpoints on endpoint compatibility.
  Responses selects the latest cacheable block before the latest user/developer
  message and restores markers from a stateful baseline when applicable.
  Completions selects a preceding user/developer/system block. Generic OpenAI
  caching uses the provider's prefix cache and supported routing/retention
  options, without adding Anthropic markers or unsupported explicit fields.
- OMP action APIs throw before runtime initialization, as shown in
  `packages/coding-agent/src/extensibility/extensions/loader.ts:73`.
  Definitions register during load only when enabled; activation occurs at
  `session_start`, a command, or `before_agent_start`.

The supplied groot revision `4de1aa9` is absent from the reference checkout.
`git show 4de1aa9 --stat` fails with `unknown revision`. The worktree's starting
extension already supplied `/optchat` and `/optchat-browse`; both commands remain.

## Offline replay

```sh
bun test ./omp-extensions/z0-optchat
bun run ./omp-extensions/z0-optchat/replay/replay.ts
bun run ./omp-extensions/z0-optchat/replay/replay.ts legacy
python -m pytest -q tests/test_optchat.py tests/test_optchat_history.py
```

The installed OMP executable can also provide Bun without installing another
binary: `BUN_BE_BUN=1 /home/kvn/.local/bin/omp test ./omp-extensions/z0-optchat`.
Use `run` in place of `test` to execute the replay script.

The replay drives fake OMP events with eight historical messages, a new prompt
and ten assistant/tool-result rounds. It makes no model calls and reads no host
conversation data. The legacy hook reconstructs a changing view as one user
message plus only the final native message and repeats the zoom imperative.
The fixed mode runs the real `installOptchat` implementation.

Both the table and JSON report the longest common prefix of concatenated JSON
message records between consecutive calls. Prefix-stable percent is the common
prefix divided by the previous request's serialized length; the cold first call
is excluded from the average. `cacheRead = commonPrefixChars / 4` and
`cacheWrite = (requestChars - commonPrefixChars) / 4`. These are synthetic
message-only token estimates, excluding provider conversion, system text, tool
schemas, breakpoint placement, minimum cache size, TTL and real billing.
`inTurnDropped` sums missing original messages over provider calls, so one
message can count more than once. `zoomHints` counts calls displaying the legacy
imperative. `zoomExecuted` counts accepted requests out of ten attempts.
