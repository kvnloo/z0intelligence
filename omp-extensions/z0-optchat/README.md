# OptChat on OMP's native loop

OptChat defaults to off. `/optchat on|off|status` uses the backend flag,
`$Z0INT_HOME/optchat/enabled`, defaulting to `~/.z0int/optchat/enabled`.
With `Z0INT_OPTCHAT_ROOT`, the flag is the chat directory's sibling `enabled`.
Only the exact trimmed text `on` enables it. `/optchat-browse` writes the
backend's HTML memory page when enabled.

A switch during a user turn is pending until the next real user submission.
The current prefix, zoom budget, subprocess and active tool set stay intact,
including through idle gaps and automatic continuation. Status reports both
the effective mode and any pending change. Repeating `on` does nothing;
`on` also cancels a pending `off`. External flag changes are sampled at the
next user turn. With no turn yet, commands apply immediately.

When off at startup, no OptChat tools are registered and no RPC process starts.
After on then off, there are zero **active, model-visible** OptChat tools and
direct invocation returns an error without an RPC. OMP has no tool-unregister
API. The two definitions remain in its extension registry and can still appear
in administrative tool inventories such as `getAllTools()`. They cannot be
removed from that inventory through the public extension API. Disabling stops
only this extension's child process. It never rebuilds the host registry.

`index.ts` owns the flag and resident RPC process. `native.ts` installs the OMP
hooks and tools. The older standalone loop in `turn.ts` remains for existing
conformance scripts; the extension does not invoke that loop.

## Turn boundary and preparation

`before_agent_start` only stages the last persisted branch message as an anchor.
It cannot distinguish user submissions from synthetic prompts. A run-initiating
`message_start` with role `user`, without agent attribution or a synthetic flag,
opens a user turn. Ordinary tool rounds, streaming steering messages and automatic
agent-authored developer continuations keep the existing boundary and zoom budget.
The first context call also checks the actual messages in case the notification
is delayed. A late notification cannot reset a turn after its first context.

The awaited interactive `input` hook applies pending flags and tool selection
before OMP starts the next prompt. Message identity also handles prompts from
non-interactive hosts, which do not emit that hook. OMP delivers message events
as asynchronous notifications; the replay checks context fallback explicitly.
Installed-runtime provider tool serialization is not established by this replay.

Only history through the anchor is eligible for compression. The complete live
suffix, including prompt preludes, images, assistant payloads, tool calls, tool
results and steering messages, is cloned and retained. Later context handlers
never receive the cached objects.

Preparation has one shared promise and a **25-second overall deadline**, measured
from context-handler entry, below OMP's 30-second handler timeout. Each RPC also
gets at most the remaining budget or five seconds. Work uses private candidate
chunks and publishes only after the entire operation succeeds within budget.
The deadline race works even if a backend ignores its RPC timeout. Late results
cannot update chunks, the turn's prefix or a subsequent turn.

Failure or timeout freezes the previous frozen chunks plus all newly eligible
originals, never partly prepared summaries. A new turn can retry. Historical
images, unsupported messages and unfinished summaries keep their originals.
Completed historical chunks retain constant timestamps and their exact summary
text across later turns. There is no global context-size cap or zoom hint.
Two zoom requests are reserved before awaiting the backend, including failures.
Only a real new user turn resets that budget.

## Compaction and pruning

Active enabled turns cancel `session_before_compact`. A new idle interactive
submission releases the old turn before pre-prompt maintenance, allowing
compaction between user turns. If compaction still occurs, `session_compact`
disables OptChat transforms and source tools for the rest of that turn.
The supplied host also disables speculative/async compaction whenever an
extension registers this cancellation hook. OptChat registers it only on first
enable, so an off-only session keeps its original scheduling. There is no
hook-unregister API; the host-wide scheduling limitation persists after on then
off for that extension instance.

Each context call checks the complete previous snapshot, including live tool
results, against the host array. A missing anchor, changed historical message,
removed live message, changed tool output or pruning marker also disables
OptChat for that turn. All remaining calls pass through the **whole host
history** unchanged. They never combine a frozen prefix with a rewritten suffix,
and do not resume compression if the old bytes later return. A real new user
turn can prepare again. The snapshot covers messages observed at the context
hook; it does not claim to recover bytes already pruned before observation.

OMP exposes no extension veto for tool-output pruning. Its rewrite cannot alter
our owned snapshot. After a rewrite, tool definitions retain their current turn's
presentation but reject execution, so degradation itself does not change schemas
mid-turn. An explicit pending off removes them from the active set at the next
user turn. Host compaction may change provider prefixes; OptChat does not claim
cache stability across that degradation.

## Checked OMP contracts and verification limits

References are relative to `/home/kvn/workspace/oh-my-pi`. Its source manifest
reports **18.1.10**, while `/home/kvn/.local/bin/omp --version` reports
**18.11.0**. Code comments cite these checked source contracts. They do not assert
that the source is a matching installed-runtime build.

- `packages/coding-agent/src/extensibility/extensions/runner.ts:86,1320,1618`
  gives context handlers 30 seconds, discards timed-out results and chains
  replacements of cloned context arrays.
- `packages/coding-agent/src/session/agent-session.ts:3747,6040,6310`
  sends automatic continuation as an agent-attributed synthetic developer
  message, creates actual user messages separately, and emits the same
  `before_agent_start` for both paths. At `:2865` notification handling is
  asynchronous. `packages/agent/src/agent-loop.ts:994` emits input messages
  before the first provider call.
- `packages/coding-agent/src/modes/controllers/input-controller.ts:764`
  awaits interactive `input` handlers. Other host modes do not emit this event
  in the supplied source. `extensions/types.ts:481` exposes `ctx.isIdle()`.
- `packages/coding-agent/src/session/agent-session.ts:5934` executes extension
  commands immediately, even while streaming.
- `packages/coding-agent/src/extensibility/shared-events.ts:376` supports
  compaction cancellation. `session/session-context.ts:447` rebuilds compacted
  history; `packages/agent/src/compaction/pruning.ts:304` mutates tool output.
  `session/session-maintenance.ts:863,1265,1324` honors cancellation and disables
  speculative/async compaction when the cancellation hook exists.
- `packages/coding-agent/src/extensibility/extensions/types.ts:1299,1450`
  exposes register/get-all/get-active/set-active tools, with no unregister tool.
  `extensions/loader.ts:180` retains definitions. `session/session-tools.ts:1039`
  installs the active execution tools. `extensions/loader.ts:73` leaves action
  APIs unbound during extension loading, so startup only registers definitions.

The replay measures extension message records, not outbound provider bytes.
OMP converts messages and normalizes tools after the context hook in
`packages/agent/src/agent-loop.ts:1593`. Matching 18.11.0 outbound captures and
reload tests from finding 6 remain outside this patch's verified results.

## Offline replay

```sh
bun test ./omp-extensions/z0-optchat
bun run ./omp-extensions/z0-optchat/replay/replay.ts
bun run ./omp-extensions/z0-optchat/replay/replay.ts regressions
PYTHONPATH=src python -m pytest -q tests/test_optchat_history.py tests/test_optchat.py tests/test_hermes_optchat.py
```

The comparison drives eight historical messages, a new prompt and ten tool
rounds through legacy, fixed and off modes. The regression run covers a real
25-second deadline with 29 historical messages at 1.1 seconds per RPC, automatic
and tool continuation, compaction/pruning, streaming switches, redundant on,
and off-after-on tool state. It uses the real installer with fake host events
and a deterministic backend, without models or host conversation data.

The table and JSON report common prefixes of concatenated JSON message records.
The cold call is excluded from average stability. Cache read/write values are
synthetic character counts divided by four. They exclude provider conversion,
system text, tool schemas, breakpoints, cache minimums, TTL and real billing.
`inTurnDropped` sums missing original messages over calls. `zoomExecuted` counts
accepted requests out of ten attempts. These metrics do not validate provider
cache behavior or installed-version reload behavior.
