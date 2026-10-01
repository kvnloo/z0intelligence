# Claude Code engagement (z0 mechanisms reaching real sessions)

Status: v0, 2026-09-30. Code: `src/z0int/claude_code_engagement.py`. No savings are claimed here;
the lean/packet/obspack numbers below come from the earlier paired z0evals runs, not from this branch.

## Diagnosis (count-only, transcripts under `~/.claude/projects`, 7-day window)

The tokenomics backfill (167 sessions / 322 turns) mixed three populations. Split by entrypoint and cwd:

| cohort | root sessions | what it is |
|---|---:|---|
| harness | 162 | z0evals/probe arms (`/tmp/cc-*`, `/tmp/tmp.*`, `/tmp/claude-N/*`, `~/.cache/z0-savings*`), `entrypoint=sdk-cli` |
| agent | 5 | SDK-driven sessions in `~/workspace/z0intelligence-stack` |
| interactive | 1 | the user's own `claude` (`entrypoint=cli`), cwd `~/workspace`, 94 prompts, 71 subagents (6,144 assistant messages) |

Every root transcript in the window started on 2026-09-30; the user-scope plugin was installed at
15:15Z that day, after the interactive session started (04:06Z).

| mechanism | backfill number | cause | hypothesis verdict |
|---|---|---|---|
| State Packet | 7 sessions | (1) `session_start_hook` returns nothing unless cwd is a git repo, and the interactive session starts in `~/workspace` (not a repo, 144 repos under it); (2) the plugin was installed mid-session, so startup ran without it, and the first packet arrived on a `compact` (cwd then inside a repo); (3) subagents get no SessionStart: 0/71 got a packet; (4) eval arms only get a packet when the arm asks for one | git-repo-only: **confirmed**. SessionStart-only: confirmed (fires on startup/resume/clear/compact, never per turn, never for subagents). `shadow: true` suppressing it: **rejected** (shadow gates only UserPromptSubmit routing context). |
| lean profile | 2 confirmed / 55 inferred | lean exists only as launcher flags (`--setting-sources project --strict-mcp-config --disable-slash-commands`); the interactive session is plain `claude` (skill listing present). The 55 "inferred" sessions are eval/probe arms with no skill listing, not user sessions | launcher-only: **confirmed** |
| route_worker | 0 calls | MCP tool is deferred (needs ToolSearch); the server sent no `instructions`; nothing in context says when to offload; 0 ToolSearch queries for it across 71 subagents. Factory posture has been BURN in all 8 recorded points (claude pool, unused capacity perishing), and the packet's NOW line says "Do not offload work these pools can absorb" | model never told: **confirmed**; BURN says don't offload now: **confirmed** — zero is partly the correct answer today |
| ObservationPack | 62 turns | works where installed: 131 harness obs arms, 185 obs results in interactive subagents | not an engagement problem |

Interactive static prefix observed (chars, one session): skill_listing 32,664; lavish-axi SessionStart
stdout 9,416; deferred tools 7,401; MCP instructions 4,748; agent listing 2,887.

## Fixes (branch `feat/engagement-v0`)

| # | fix | default | user decision? |
|---|---|---|---|
| F1 | Workspace packet: when cwd is not a repo, `<z0-workspace-packet>` lists the 12 most recently touched repos under it (branch, dirty count, unpushed, last commit age); stat-ranked, scan capped at 500 dirs. ~1.2k chars / ~3 s on the loaded host for `~/workspace` | on whenever `packet: true` (`workspace_packet: false` or `Z0INT_CLAUDE_CODE_WORKSPACE_PACKET=0` disables) | no (already opted into `packet`) |
| F2 | Subagent packet: plugin registers `SubagentStart` on the same handler | off (`subagent_packet: true` / `Z0INT_CLAUDE_CODE_SUBAGENT_PACKET=1`) | yes: adds ~0.3-1.5k tokens to each subagent; unmeasured |
| F3 | Posture-aware offload hint `<z0-offload posture=...>` appended to the packet: BURN → do it here, route_worker only for must-stay-local work; OFFLOAD/RESERVE → try route_worker first for bounded self-contained subtasks; names the ToolSearch select string | on with `packet`; `offload_hint: true` enables it alone | no |
| F4 | MCP `initialize.instructions` (Claude Code harness only): when route_worker fits, that posture decides, `allow_remote` needs user authorization | on | no |
| F5 | `z0int claude-code engagement lean-settings`: documented settings that approximate lean (`skillListingMaxDescChars`, `skillListingBudgetFraction`, `disableClaudeAiConnectors`) plus removing the lavish-axi SessionStart hook. No settings key exists for `--setting-sources`, `--strict-mcp-config` or `--disable-slash-commands`; a plugin `settings.json` only honours `agent`/`subagentStatusLine`. Printed, never applied | n/a | yes: connectors and axi hooks are things the user uses |
| F6 | `z0int claude-code engagement shell-init fish|bash|zsh [--name claude]`: shell function for the lean launcher. Default name `claude-lean`; `--name claude` shadows the binary | n/a | yes: lean drops user settings, permissions, skills and slash commands |
| F7 | `z0int claude-code engagement report`: count-only cohort table (interactive / agent / harness × root / subagent). This is the before/after instrument | n/a | no |
| F8 (`feat/packet-gating-v0`) | Prompt-gated packet `packet: "gated"`: SessionStart carries no packet; UserPromptSubmit injects only the facts of the families `decision_opportunity.required_families(prompt)` names (deterministic substring gate, vocabulary unchanged), each family once per session; outside a repo the workspace packet once. Motivated by claude-code-savings-v1 (session packet −35.5% on qa, +10.8% on repo tasks) | off (opt-in mode; `packet: true` unchanged) | yes: measured in `benchmarks/claude_code_v1b` before any default change |
| F9 (`feat/packet-gating-v0`) | Lean keeps route_worker: `launch --profile lean` adds `--mcp-config` with only the z0 server (all other MCP stays stripped) and points the offload hint at `mcp__z0intelligence__route_worker` | on with the plugin | no |

## Pre-registered measurement plan

Instrument: `z0int claude-code engagement report --days N --json`, plus
`z0int claude-code tokenomics` for billed tokens. Baseline (2026-09-30, before this branch is live):

| cohort/role | sessions | z0 SessionStart | packet | workspace_packet | offload_hint | route_worker calls |
|---|---:|---:|---:|---:|---:|---:|
| interactive/root | 1 | 1 | 1 | 0 | 0 | 0 |
| interactive/subagent | 71 | 0 | 0 | 0 | 0 | 0 |
| agent/root | 5 | 4 | 4 | 0 | 0 | 0 |

Analyses use only the interactive and agent cohorts; harness rows are excluded.

- **M1 reach (F1, F3):** 7 days after the branch is live, interactive/root sessions with
  (`packet` or `workspace_packet`) / sessions. Success: ≥ 90%. Failure means a gate is still
  blocking it. This measures delivery only, not savings.
- **M2 workspace packet value (F1):** paired z0evals arms from `~/workspace` (not a repo), same
  tasks: stock vs stock+workspace-packet, n ≥ 8 tasks × 3 seeds. Primary: billed input tokens to
  first relevant file read; secondary: task pass rate. Claim only if the paired median CI excludes 0
  and pass rate does not drop.
- **M3 offload hint (F3, F4):** route_worker calls per 100 interactive turns, split by
  `factory_posture`. Prediction: about 0 under BURN (correct behaviour), > 0 under OFFLOAD/RESERVE.
  Quality guard: share of `PARENT_ONLY` answers and worker receipts with `ok=false`. Until a
  non-BURN window has been observed, report it as untested and do not call it a failure.
- **M4 subagent packet (F2, opt-in):** randomized per-session flag on agent-cohort runs; primary
  is subagent billed input tokens per task, guard is task pass rate. Default stays off unless the
  paired run shows a net reduction.
- **M5 settings-lean (F5):** z0evals warm and cold arms: stock vs stock+lean-settings vs launcher
  lean, same tasks as claude-code-savings-v0. Report billed cost per arm. Lean's −27% / −64% does
  not carry over to settings-lean until this run measures it.

## Not done

No live config changes and no installs: `~/.z0int/config/claude-code.json`, `~/.claude/settings.json`
and the shell config are untouched. The tokenomics backfill should also segment by cohort (F7's
`cohort()`), so eval arms stop being read as user sessions.
