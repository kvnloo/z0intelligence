# local-cognition (OMP extension, shadow-only)

Dogfood lane for [oh-my-pi#83](https://github.com/kvnloo/oh-my-pi/issues/83) and
z0intelligence#20. On every OMP `tool_call`, it asks the resident z0int bridge
worker to compile the deterministic legal action set and then records what the
configured local models *would* have chosen from that set.

Nothing is ever executed. The lane exists to measure agreement between the
compiler's verdict and the local cognition portfolio.

## Safety boundary

- The `tool_call` handler is **synchronous and always returns `undefined`**. It
  never blocks a tool, never revises input, and never approves or denies
  anything.
- Sending is fire-and-forget with a hard timeout. A timeout or error is
  swallowed and recorded — never surfaced as a tool failure.
- No tool, permission, approval, retry, cancellation, provider or model is ever
  changed by this extension.
- The Python op behind it (`cognition_shadow`) compiles first, only shows the
  legal set to models, records an out-of-set answer as `invalid_call` with
  `selected_action: null`, and always writes `selected_action: null` /
  `executed_action: null` in its receipt.

## Install

From the repository root, symlink the extension into OMP the same way the other
z0int extensions are installed:

```bash
ln -s "$PWD/omp-extensions/local-cognition" ~/.omp/agent/extensions/local-cognition
```

One OMP restart is required after adding an `index.ts` extension. The Python side
(`src/z0int/cognition/shadow.py` and `src/z0int/bridge/`) is hot-swappable via
`/reload-plugins` once the `z0int-bridge` shim owns the worker.

The lane prefers the transport published by `z0int-bridge`
(`globalThis.__omp_z0int_bridge_transport__`). If it is missing it tries to
import the sibling `z0int-bridge` extension's `ensureWorker`/`request`, and
otherwise no-ops.

## Configuration

Environment (wins over the settings file):

| Variable | Default | Meaning |
| --- | --- | --- |
| `OMP_Z0INT_COGNITION_SHADOW` | on | `0`/`false`/`off` disables the lane |
| `OMP_Z0INT_COGNITION_TIMEOUT_MS` | `4000` | Hard client-side timeout for one shadow send |
| `OMP_Z0INT_COGNITION_MODELS` | `nemotron_orchestrator_8b,functiongemma_270m` | Model ids to run in shadow |
| `OMP_Z0INT_COGNITION_TOOLS` | discovered/observed | Comma-separated tool allow-list for the action set |
| `OMP_Z0INT_COGNITION_AUTHORITY` | `read` | Risk classes the compiler may declare legal |
| `OMP_Z0INT_COGNITION_BRIDGE_FALLBACK` | on | `0` disables the sibling-extension import fallback |
| `Z0INT_HOME` | `~/.z0int` | Receipt + settings root |

Optional settings file `~/.z0int/config/cognition.json` (env still wins):

```json
{
  "shadow": true,
  "timeout_ms": 4000,
  "models": ["nemotron_orchestrator_8b", "functiongemma_270m"],
  "authority": ["read"]
}
```

## Command

`/z0int-cognition-status [N]` prints the last `N` (default 10) rows from the
receipt file `~/.z0int/shadow/cognition-shadow.jsonl`, falling back to the
in-memory status ring when no receipt exists.

## Test

```bash
cd omp-extensions/local-cognition
bun test
```

The tests use a fake `ExtensionAPI` and a fake bridge transport; they need no
OMP runtime, no Python and no model server.
