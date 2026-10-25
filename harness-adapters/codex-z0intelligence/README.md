# codex-z0intelligence

Hooks-only Codex plugin: shadow capture of each Codex turn into the shared z0int record family
(`z0int.codex.{opportunity_record,turn_outcome,failure}.v0` under `$Z0INT_HOME/state/codex/`).
It also carries the z0 memory seam (a second `UserPromptSubmit` entry, `python -m z0int.memory.hook --harness
codex prompt`; shadow by default, so nothing is printed into the turn) and the memory-only `z0-memory` MCP server
(`.mcp.json`, `--profile memory`: memory tools only). `Z0INT_MEMORY_INJECT=canary|on` injects `additionalContext`
(parity with Claude Code: INFERRED, live B unverified) only into a loopback model (`OPENAI_BASE_URL`) unless the
owner sets `{"inject": {"codex": {"allow_cloud_injection": true}}}` in `~/.z0int/config/memory.json`.

| Codex hook | Command |
| --- | --- |
| `UserPromptSubmit` | `python -m z0int.hook_adapter --harness codex prompt` (ids + work item; opportunity built in a detached child) |
| `Stop` | `... --harness codex stop` (content-free outcome: asked_user only; missing fields are reported as `partial_measurement`) |
| `SubagentStart` / `SubagentStop` | `... subagent-start` / `subagent-stop` (cohort `agent`, keyed by the subagent id) |

The interpreter is `Z0INT_PYTHON` (default `~/.z0int/bin/python`), which must have this repository installed.
Kill switch: `Z0INT_CAPTURE=0`. Request text is stored only with `Z0INT_CAPTURE_PRIVACY=request_opt_in`.

Install from a local checkout (activation step A5; the existing `z0intelligence@personal` plugin is a
separate plugin and is left as it is):

```bash
codex plugin marketplace add /path/to/z0intelligence      # reads .agents/plugins/marketplace.json
codex plugin add codex-z0intelligence@z0intelligence      # then trust the plugin hooks once
```
