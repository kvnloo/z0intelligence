# Prior findings (2026-10-03, verified by three independent read-only audits + the orchestrator)

## Shipped today (z0 shadow loop v0)
- kvnloo/z0intelligence branch `feat/shadow-loop-v0` @6fee859 (pushed, no PR), worktree /mnt/zer0models/z0-wt/shadow-loop.
  Brought outcome_verifier, verification_density, loop_export, claude_code_engagement (+tests) from
  `integrate/claude-code-z0-stack` as files; CLI `z0int outcomes {verify,density,export,join}` + `z0int loop`;
  claude_code.on_opportunity records non-git turns (empty state packet). Full suite 964 pass / 11 pre-existing env fails.
- Claude Code plugin (marketplace kvnloo/z0intelligence: `z0intelligence` hooks UserPromptSubmit/Stop/SessionEnd/SessionStart
  + MCP route_worker; `z0-obspack`) is installed on this host; venv /mnt/zer0models/z0-wt/venv-claude-code is editable from
  the shadow-loop worktree. Captures ~/.z0int/state/claude-code/{opportunities,outcomes,outcomes_verified}.jsonl.
- evolution-lab `exp/verified-loop-v0` (worktree /mnt/zer0models/z0-wt/evolution-lab-verified-loop): `python -m
  evolution_lab.verified_loop --table T.jsonl --out R.json --md R.md`, prereg docs/prereg/verified-loop-v0.md
  (MIN_ROWS 300, MIN_NEG 30, MIN_GROUPS 10). This host: INSUFFICIENT_DATA (0 analysis rows; 7-day sweep 149 turns,
  10 resolved). Bottleneck = label volume/resolution, not search.
- RFC addendum "the full self-improving shadow loop" appended to z0intelligence#56 (stages/owners/genome/milestones M0-M5).

## Unified memory, as it exists
- agentsview v0.39.0 (`~/.local/bin/agentsview`, data dir /mnt/zer0models/sft-svlm/data/agentsview = ~/.agentsview):
  sessions.db 20 GB SQLite + FTS5 (messages_fts, recall_entries_fts, recall_evidence_fts, tool_calls), 12 agents
  (chatgpt 2502, omp 2485, grok 2411, hermes 1789, codex 1690, kimi, claude 108, antigravity-cli, opencode, omo, cursor,
  gemini). NO dsh agent. Last session 2026-10-01 — sync/daemon NOT running. `agentsview mcp` = read-only retrieval MCP;
  `agentsview recall {extract,query,brief,...}`. config.toml has require_auth (never print it).
- Hermes memory provider: `memory_tencentdb` (configured in ~/.hermes/config.yaml memory.provider). TencentDB
  characterised 09-19/20 but never wired cross-harness; 8 s TencentDB call identified as a critical-path blocker.
  Hermes FTS5 repaired 09-19 (214,405 rows).
- z0int memory control plane: docs/memory-control-plane-contract.md + memory_contract.py (capabilities fts5, tencentdb;
  MemorySnapshot, EventIdentity, BitemporalClaim) — contract-only. Next slices listed there: (1) EventLog/AgentsView
  importers emit EventIdentity; (2) #22 reducer emits scoped BitemporalClaim; (3) bind StatePacket/DecisionOpportunity to
  MemorySnapshot; (4) record memory-use in real OMP/Hermes/DSH receipts; (5) z0evals cohorts; (6) feed verified episodes
  to the existing routine compiler (no second procedural memory).
- DSH: deepseek-harness PR #2 merged 09-26 = AgentsView stdio MCP memory overlay; profile `cordis.patch.yml` has
  memory-agentsview (AGENTSVIEW_DATA_DIR=/mnt/zer0models/sft-svlm/data/agentsview) + intelligence-z0intelligence MCP
  (stale .work checkout) + automatic-z0intelligence. hermes-jev-dsh `memory.js` (read-only Hermes backend, TencentDB
  fails closed) is a stub, not exposed as a tool.
- "universal recall" built 09-22 (DSH session 9797ce6f): 11,079 sessions / 348,934 messages; `recall_mcp`, `statepack orient`;
  LongMemEval bakeoff kept current z0 (Mem0, Sibyl rejected). z0evals#56 unified-memory study: DSH lane runner `dca8fd2`
  (unpushed), Hermes lane `study/hermes-unified-memory-56@c84663880a` (local), OMP lane oh-my-pi#107 — cohort blocked.

## Data collection / shadows, as it exists
- Claude Code: DecisionOpportunity + observed + verified (above). Hermes: `hermes_decisions.py` on z0int master;
  hermes-agent#319 sample collection (posted 10-02). OMP: older v1 spine — `~/.z0int/shadow/cognition-shadow.jsonl`
  (7,077 rows, 6,915 OMP, no consumer), `shadow/jev-fly.jsonl` 766, `shadow/preflight.jsonl` 661,
  `receipts/decisions.jsonl` 3,582 (`z0int.decision_receipt.v1`), `receipts/outcomes.jsonl` 649 (`z0int.outcome_join.v1`,
  OMP bridge), `episodes/next_action.jsonl` 76,970 (Hermes state.db mined), `replay/task_snapshots.jsonl` 1,008;
  oh-my-pi#109 = OMP child of #62 (open). `~/.z0int/config/automatic.json`: omp on, hermes off, dsh off.
- DSH: real learning data is `~/.dsh/jev/receipts.jsonl` (13,192 rows 09-21..09-30; model_request 12,672, jev_decision 244
  with route_changed=false 244/244, decision_receipt 195, decision_comparison 8) written by kvnloo/hermes-jev-skills branch
  `hermes-jev/omp-adapter` @9ea5777 (unmerged, no PR; local ~/zer0/oss/hermes-jev-skills/dsh), private schema, NO
  consumer, NO outcomes. No DSH DecisionOpportunity/credit emitter anywhere (#62). DSH sessions: ~/.dsh/sessions/**/
  session.v3.jsonl.zstd (203 sessions). DSH installed: npm @deepseek-ai/dsh 0.1.5-rc.2; fork kvnloo/deepseek-harness
  (Issues disabled → DSH work tracked in z0int#62). Profiles ~/.dsh/profiles/{web,headless,sdk-minimal,deepseek-sdk}.
- Four divergent z0int trees serve DSH: `.work/z0intelligence` (78869d9, untracked adapter code), ~/tmp/openjev
  (`feat/steal-sweep` c76f621, shadow backends), live service ~/tmp/z0int-canonical (`feat/promote-local-cognition-
  extension` a9cbbed = closed PR #35, dirty, lacks decision_opportunity/state_packet/outcome_observation/memory_contract),
  and master 0159808.
- Codex: plugin `z0intelligence@personal` (route_worker) from 09-27; OMO onboarded as z0 executor.
- Older genetic/promotion attempt: OMP sessions 09-17/18 "specialist factory" (promote-only champion writes, safe-coverage
  cascade, GPU-evolved MB champion 0.572 vs ridge 0.363, ResearchDriver promotion-bug fix) — all shadow, never promoted.

## Owner rules (verbatim fragments)
- "Do NOT make or merge a PR into DeepSeek Harness core." Routing/quota/measurement/learning "belong in the Zer0 plugin ecosystem."
- "Keep this integration shadow-first." "JEV is an observer/backend/reference lane, not ground truth or authority."
- "evidence ≠ belief/state ≠ authority ≠ action ≠ outcome." "execution completion is not verified success"; "missing telemetry ≠ zero."
- "Do not invent a second NanoJev runtime." "make sure we aren't committing the weights bc it's trained on my personal data."
- "u didn't even run a single live test?? … so that i dont spend 1 week 'collecting data' for nothing."
- #95: governed dispatch "deliberately stops before DSH/Hermes expansion" until the OMP AODL golden canary closes green
  (that gate covers governed dispatch/remote execution, not observe-only capture or read-only memory).
