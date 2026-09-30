# Claude Code × z0intelligence

Claude Code is a z0 harness like OMP, Hermes and DSH: the same `z0int.automatic`
seam, Tokenomics usage receipts, and levers promoted only on paired, verified evidence
(study: z0evals `studies/claude-code-savings-v0`).

## What to use

| Lever | How | Evidence | Status |
| --- | --- | --- | --- |
| Lean launch profile | `z0int claude-code launch --profile lean -- <claude args>` | cold -63.8% median (5/5), warm -27.2% median (15/15), equal verified quality | **use for headless / fleet sessions** |
| Prefix-cache residency | `z0int claude-code warm DIR...` before placing work | stock cold session ≈ 3× a warm one ($0.126 vs $0.0425/task) | use when choosing worktrees |
| State Packet at SessionStart | `Z0INT_CLAUDE_CODE_PACKET=1` with the plugin | held-out 14/14 vs 12/14, −30% input tokens; pinned held-out 2×2: packet ×0.81–0.83 cost, lean ×0.47–0.49, composed −61% vs stock raw (dev: −74%) | **opt-in; an aid to tools, not a replacement** |
| Usage receipts | plugin `Stop` + `SessionEnd` | equal to Claude Code's billed usage on all four token fields | on |
| Automatic routing | plugin `UserPromptSubmit` | inert in shadow (default) | shadow |
| ObservationPack | `--plugin-dir harness-adapters/claude-code-z0-obspack` | short tasks: null; long recall task (n=12): −30.3% total, Wilcoxon p=0.0068, 12/12 verified | **opt-in for long, output-heavy sessions** |
| Skill exposure | `z0int.claude_code_skills` | best 2/30 misses; bar ≤1/30 | not promoted |

## Install

```bash
pip install -e '.[test]'                                   # this repo, Python 3.10–3.13
claude --plugin-dir harness-adapters/claude-code-z0intelligence   # per session
z0int claude-code launch --profile lean -- -p "…"          # attaches the plugin itself
```

Environment: `Z0INT_PYTHON` (interpreter with z0int), `Z0INT_HOME` (default `~/.z0int`),
`Z0INT_CLAUDE_CODE_SHADOW=0` (allow delivered routing context), `Z0INT_CLAUDE_CODE_PACKET=1`,
`Z0INT_CLAUDE_CACHE_TTL` (seconds; 3600 on a subscription, 300 on API/usage credits).

## Why the lean profile saves money

~10.3k tokens of Claude Code's core prefix are shared across sessions; everything after it
(user hooks' output, skill listings, MCP, environment) is rewritten on each cold session and
re-read on every request. The cache is scoped to directory + startup git snapshot, so every
fresh worktree is a cold start. Measure your own prefix:

```bash
claude -p "Reply with just OK." --output-format json | jq '.usage'
```

## Failure behaviour (measured)

`benchmarks/claude_code/failure_injection.sh`: with the router down, a missing
`Z0INT_PYTHON`, the packet enabled outside git, or an unwritable `Z0INT_HOME`, every session
still answered normally (1.1–1.7 s, no hook stderr). Usage receipts are written whenever z0 can
run and write (3/3 expected cases).

## Hook contract notes (verified empirically on 2.1.285)

- `PostToolUse` `updatedToolOutput` for Bash must mirror the `tool_response` object
  (`{stdout, stderr, interrupted, …}`); a bare string is ignored.
- `Stop` fires before the final assistant message is in the transcript; sweep again at
  `SessionEnd` (dedupe by message id).
- Hook input carries `prompt_id`; use it as the turn id.

## Portable lab

`deploy/lab/up` brings up the same `hermes-lab` Kind lane on any fleet host with detected
host deltas (bridge gateway, GPU). `z0-slm.service` serves a GGUF on the host's best device
(e.g. Radeon R9 M370X via Vulkan); the `llama_http` decision backend reads it
(`Z0INT_SLM_URL`). See `deploy/lab/README.md`.
