# z0intelligence for Claude Code

Thin Claude Code plugin over the canonical `z0int.automatic` path, the same seam
Hermes (`pre_llm_call`), OMP (`before_agent_start`) and DSH (`llm/stream`) use.

| Claude Code seam | z0int surface | Default |
| --- | --- | --- |
| `UserPromptSubmit` | `z0int.claude_code prompt` → `automatic.handle_event`; with `packet: "gated"`, a question-scoped State Packet when the prompt names a packet fact family | shadow: routed and receipted, never injected |
| `Stop`, `SessionEnd` | `z0int.claude_code stop` → Tokenomics `claude-code.provider_usage.v0` | on |
| `SessionStart` | `z0int.claude_code session-start` → repo State Packet, or a multi-repo workspace packet outside a repo, plus a posture-aware `route_worker` hint | off (`packet: true`; `"gated"` keeps SessionStart to the hint only) |
| `SubagentStart` | same handler — NOT registered by default (it would spawn Python per subagent); add it to hooks.json together with `subagent_packet: true` | off |
| MCP `route_worker` | `z0int.intelligence_mcp` with `Z0INT_HARNESS=claude-code`; `initialize.instructions` say when to offload | on; under `z0int claude-code launch --profile lean` (`--strict-mcp-config` drops plugin MCP servers) the launcher passes `--mcp-config` with only this server, so the tool is `mcp__z0intelligence__route_worker` there |

**Packet modes** (`~/.z0int/config/claude-code.json` `packet`, env `Z0INT_CLAUDE_CODE_PACKET` = `1`/`gated`/`0`):
`true` injects the full packet at every SessionStart (v1 behaviour: −35% tokens on current-work
questions, +11% on fresh coding tasks); `"gated"` injects nothing at SessionStart and, at
UserPromptSubmit, only the facts of the families `decision_opportunity.required_families(prompt)`
names, each family at most once per session (count-only ledger:
`state/claude-code/packet-gate.jsonl`); `false` is off. Measured in
`benchmarks/claude_code_v1b`.

Requires `pip install -e .` of this repo (or `Z0INT_PYTHON` pointing at that
environment). Every hook fails open.

```bash
claude --plugin-dir harness-adapters/claude-code-z0intelligence
Z0INT_CLAUDE_CODE_SHADOW=0   # allow delivered context (requires automatic.json enabled for claude-code)
```

Usage receipts dedupe transcript messages by id, split `root` and `subagent`
roles, and keep cache reads/writes separate from uncached input. They measure
billed usage, not verified outcomes.

Engagement (does any of this reach real sessions?): `z0int claude-code engagement report`;
see `docs/engagement.md`.
