# z0intelligence for Claude Code

Thin Claude Code plugin over the canonical `z0int.automatic` path, the same seam
Hermes (`pre_llm_call`), OMP (`before_agent_start`) and DSH (`llm/stream`) use.

| Claude Code seam | z0int surface | Default |
| --- | --- | --- |
| `UserPromptSubmit` | `z0int.claude_code prompt` → `automatic.handle_event` | shadow: routed and receipted, never injected |
| `Stop`, `SessionEnd` | `z0int.claude_code stop` → Tokenomics `claude-code.provider_usage.v0` | on |
| `SessionStart` | `z0int.claude_code session-start` → State Packet `additionalContext` | off (opt-in `Z0INT_CLAUDE_CODE_PACKET=1`) |
| `SessionStart`, `UserPromptSubmit` | resource-posture hint: one line, only when the session's posture changes ([docs](../../docs/resource-posture-v1.md#5-claude-code-hint-one-line-only-on-change)) | shadow: decided and logged, not injected (`Z0INT_CLAUDE_CODE_POSTURE_HINT=on` / `off`) |
| MCP `route_worker` | `z0int.intelligence_mcp` with `Z0INT_HARNESS=claude-code` | on |

Requires `pip install -e .` of this repo (or `Z0INT_PYTHON` pointing at that
environment). Every hook fails open.

```bash
claude --plugin-dir harness-adapters/claude-code-z0intelligence
Z0INT_CLAUDE_CODE_SHADOW=0   # allow delivered context (requires automatic.json enabled for claude-code)
```

Usage receipts dedupe transcript messages by id, split `root` and `subagent`
roles, and keep cache reads/writes separate from uncached input. They measure
billed usage, not verified outcomes.
