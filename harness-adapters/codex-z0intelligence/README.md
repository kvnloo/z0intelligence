# codex-z0intelligence

Hooks-only Codex plugin: shadow capture of each Codex turn into the shared z0int record family
(`z0int.codex.{opportunity_record,turn_outcome,failure}.v0` under `$Z0INT_HOME/state/codex/`).
It carries no MCP server, no skills and no environment plumbing, and prints nothing into the turn.

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
