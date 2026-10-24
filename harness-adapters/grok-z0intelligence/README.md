# grok-z0intelligence

`hooks/z0-capture.json` is a Grok global hooks file: copy it to `~/.grok/hooks/z0-capture.json`
(activation step A5; remove the file to turn it off). Each hook runs
`python -m z0int.hook_adapter --harness grok <event>` and records the turn in the shared z0int record family
under `$Z0INT_HOME/state/grok/`. Capture only: Grok ignores UserPromptSubmit stdout, and the adapter prints
nothing for Grok anyway.

Grok also runs hooks from `~/.claude/settings.json` (Claude compatibility). Attribution never depends on the
file a hook came from: Grok's runner sets `GROK_HOOK_EVENT`, which the adapter reads. `StopFailure` and
`StopCancelled` close the turn as `failed` / `interrupted` and add an `uncertain_execution` failure row.

Memory (C8) is pull-only: `mcp/z0-memory.toml` is the memory-only `z0-memory` MCP server table for
`~/.grok/config.toml` (activation A8a). No memory push hook is registered and no rules file is written by default (a
global rule leaks across projects and every rule is sent to xAI), so Grok case B is UNSUPPORTED. `hooks/z0-memory.json`
(its own file, copied to `~/.grok/hooks/z0-memory.json`; remove it to turn it off) runs
`python -m z0int.memory.hook --harness grok prompt`, which only records the turn's shadow MemoryUseReceipt (never
model-visible, whatever `memory_inject` says, unless it is off) so the turn's capture opportunity carries it in
`opportunity_record.memory` (DoD D3). The owner can
opt in per project: `z0int memory rules --harness grok --scope project --owner-approved --project-dir <repo>
--query <q>` writes a scrubbed, project-scoped `<repo>/.grok/rules/z0-memory.md`.
