# z0intelligence for Claude Code

Thin Claude Code plugin over the canonical `z0int.automatic` path, the same seam
Hermes (`pre_llm_call`), OMP (`before_agent_start`) and DSH (`llm/stream`) use.

Every hook runs `python -m z0int.hook_adapter --harness claude-code <event>` (the lean shared entry, also
used by the Codex and Grok shims); `z0int.claude_code` keeps only what is Claude Code specific.

| Claude Code seam | z0int surface | Default |
| --- | --- | --- |
| `UserPromptSubmit` | `prompt` → capture ids + detached DecisionOpportunity build; `automatic.handle_event` | shadow: routed and receipted, never injected |
| `Stop`, `SessionEnd` | `stop` → Tokenomics `claude-code.provider_usage.v0` + `z0int.claude_code.turn_outcome.v0` | on |
| `SubagentStart`, `SubagentStop` | `subagent-start` / `subagent-stop` → cohort `agent` opportunity + outcome, joined on `turn_key` | on |
| `SessionStart` | `session-start` → opt-in State Packet (`Z0INT_CLAUDE_CODE_PACKET=1`) | off |
| `UserPromptSubmit` (second entry) | `python -m z0int.memory.hook --harness claude-code prompt` → z0 memory brief as `additionalContext` | `Z0INT_MEMORY_INJECT` unset = shadow (detached, prints nothing) |
| MCP `route_worker` | `z0int.intelligence_mcp` with `Z0INT_HARNESS=claude-code` | on |
| MCP `z0-memory` | `bin/z0int-mcp --profile memory`: memory_search, orient, inspect, history, unknowns, verify | on |

The memory seam is its own hook entry, so capture and memory fail independently. `Z0INT_MEMORY_INJECT=canary|on`
injects only into a loopback model (`ANTHROPIC_BASE_URL`, else the public API; no other variable counts) unless the
owner sets `{"inject": {"claude-code": {"allow_cloud_injection": true}}}` in `~/.z0int/config/memory.json`. The
brief is scoped to the prompt's project (repo root as AgentsView names it; no cwd, no brief). Claude Code records
hook `additionalContext` in its own transcript, so the brief is persisted there by host design (recorded
deviation); z0 recall cuts a re-indexed brief. There is no SessionStart memory seam (no user query to brief yet).

A turn's outcome is measured from the transcript messages of its own prompt (`promptId`). When Claude Code
fires `Stop` before the reply is flushed, the outcome comes from the payload with a `partial_measurement`
row, and the late messages close that turn with a `partial_measurement` row (`late_messages`) instead of
being credited to the next prompt. Harness-injected prompts (`<task-notification>` etc.) get no
opportunity; their outcome is cohort `harness`.

For a Claude Code build without `SubagentStart`/`SubagentStop`, register `PreToolUse` and `PostToolUse` with
matcher `Agent|Task` to `subagent-start` / `subagent-stop` instead (both keyed by `tool_use_id`). Never
register both pairs: each subagent would be captured twice.

Opportunity rows written before capture flags existed export as cohort `unknown` until
`z0int outcomes backfill-capture` flags them and drops their stored request text (one-shot, idempotent).

Capture records live under `$Z0INT_HOME/state/claude-code/`; `Z0INT_CAPTURE=0` turns capture off, and
request text is stored only with `Z0INT_CAPTURE_PRIVACY=request_opt_in`.

Requires `pip install -e .` of this repo (or `Z0INT_PYTHON` pointing at that
environment). Every hook fails open.

```bash
claude --plugin-dir harness-adapters/claude-code-z0intelligence
Z0INT_CLAUDE_CODE_SHADOW=0   # allow delivered context (requires automatic.json enabled for claude-code)
```

Usage receipts dedupe transcript messages by id, split `root` and `subagent`
roles, and keep cache reads/writes separate from uncached input. They measure
billed usage, not verified outcomes.
