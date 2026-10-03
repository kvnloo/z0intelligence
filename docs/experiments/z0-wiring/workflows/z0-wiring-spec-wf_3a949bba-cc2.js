export const meta = {
  name: 'z0-wiring-spec',
  description: 'Read every z0 RFC/roadmap/issue + existing code to spec cross-harness shadow data collection, continuous shadow learning, and unified memory wiring; TDD-ready build plan',
  phases: [
    { title: 'Survey', detail: '4 parallel read-only surveys: learning capture, unified memory, harness seams, roadmap/gates' },
    { title: 'Design', detail: 'synthesize SPEC + build plan with red tests per component' },
    { title: 'Critique', detail: 'adversarial completeness/boundary/safety critic' },
    { title: 'Revise', detail: 'final PLAN.json for the TDD build workflow' },
  ],
}

const W = '/mnt/zer0models/z0-wt/wiring'
const OWNER_ASK = `Owner request (2026-10-03, verbatim): "ok so i need you to hook up a few things - data collection for all harnesses. so that our shadow procs are automatically learning at all times. next i want the unified memory system fully working and wired up across all harnesses properly. if you take a look at all the issues and roadmaps and RFCs i should have explained very clearly what i want. please spin up a new ultracode workflow that uses tdd to wire everything up"`

const RULES = `
HARD RULES (this phase is READ-ONLY except your output file under ${W}/spec/):
- Context first: read ${W}/prior-findings.md (verified facts from earlier audits today). Do not redo those audits; extend them.
- Read-only clones: /mnt/zer0models/z0-wt/ro/{z0,z0evals,tokenomics,kerdoios,aodl,z0archy,hermes-jev-skills,agentsview,deepseek-harness,oh-my-pi,bend-native}; z0intelligence at /mnt/zer0models/z0-wt/z0intelligence (fetched; use origin/master and remote branches via git -C ... show/grep/log, never checkout/modify) and branch feat/shadow-loop-v0 (worktree /mnt/zer0models/z0-wt/shadow-loop, read-only); evolution-lab at /mnt/zer0models/z0-wt/evolution-lab (+ worktree evolution-lab-verified-loop). Hermes fork code: git -C /mnt/zer0models/hermes-wt/bend-integration (read-only). Use \`gh issue view/list\`, \`gh pr list\`, \`gh api\` read-only for issues/PRs across kvnloo/* repos. Never comment, edit, label or open anything on GitHub.
- NEVER touch the live Hermes install (~/.hermes, /workspace/hermes-home): no state.db, logs, .env, auth. The only allowed read is non-secret keys of ~/.hermes/config.yaml (memory:, plugins:, toolsets) with any key/token/secret/password values redacted.
- NEVER run the \`dsh\` binary or its wrapper (it triggers a credential refresh). Reading DSH source/profiles/plugins is fine; skip secret values.
- Do not call the live z0 service (127.0.0.1:11501 / 172.19.0.1:11501) at all; do not start/stop/restart any service or daemon; do not run agentsview sync/serve/daemon/import/prune (read the DB only via sqlite3 "file:...?mode=ro").
- Never read .env/auth/credential/keystore files; never print secret values. No paid API calls. No network writes.
- Scratch only under ${W}/spec/scratch (not /tmp). Keep CPU light (other timed experiments are running on this host).
- OWNER DIRECTION (2026-10-03): NO harness needs core code changes. Every harness integrates ONLY through its own extension seams — plugin / hook / extension / MCP server / z0 HTTP API — with the integration code living in z0-owned repos (z0intelligence harness-adapters/, or a standalone plugin repo such as kvnloo/bend-native), never in a harness fork. Turning it on is a config/install step (plugin install, MCP entry, profile entry), not a code change.
- kvnloo/bend-native (/mnt/zer0models/z0-wt/ro/bend-native @e85e65e5) is ALREADY a standalone native Hermes plugin (v0.4) that bundles the observer (hermes#385), the API-attempt shadow scorer/evaluator (#386/#387), z0's State Packet + DecisionOpportunity builders and a bridge to a z0int service. A 2026-10-02 integration run found 12 unfixed plugin bugs (summary: /mnt/zer0models/github/cua-lanes/artifacts/bend-stack/SYNTHESIS.md), incl. default stack_service_port 11501 = the live z0 service, close() cannot stop a full-queue worker, failing receipts not replayable, hermes z0 score drops offline/checkpoint settings, shadow projection reads the wrong cwd. Evaluate it as the Hermes capture vehicle before proposing anything new.
- Mark anything inferred as INFERRED. Cite issue numbers, file paths, SHAs.`

const SURVEY_SCHEMA = {
  type: 'object',
  properties: {
    file: { type: 'string', description: 'path of the markdown notes you wrote' },
    key_findings: { type: 'array', items: { type: 'string' } },
    acceptance_criteria: { type: 'array', items: { type: 'object', properties: {
      ref: { type: 'string' }, criterion: { type: 'string' }, status: { type: 'string' } }, required: ['ref', 'criterion', 'status'] } },
    existing_code_to_reuse: { type: 'array', items: { type: 'object', properties: {
      what: { type: 'string' }, where: { type: 'string' }, status: { type: 'string' } }, required: ['what', 'where', 'status'] } },
    gaps: { type: 'array', items: { type: 'string' } },
    hard_rule_breach: { type: 'string', description: '"none" or a description' },
  },
  required: ['file', 'key_findings', 'acceptance_criteria', 'existing_code_to_reuse', 'gaps', 'hard_rule_breach'],
}

const SURVEYS = [
  { key: 'R1-learning-capture', prompt: `Survey: CROSS-HARNESS DATA COLLECTION + CONTINUOUS SHADOW LEARNING.
Read (bodies + all comments): kvnloo/z0 #15 and z0 repo rfcs/verified-learning-control-loop/*, docs/architecture/*; kvnloo/z0intelligence #53 #54 #55 #56 (incl. the 2026-10-03 addendum) #57 #58 #59 #60 #62 #14 #26 #28 #66 and any open issue/PR mentioning shadow, opportunity, outcome, credit, receipt, episode, learning, promotion; kvnloo/evolution-lab #20 #23 #24 #25 #27 #28 and branches exp/verified-loop-v0, experiment/q-route-v0; kvnloo/z0evals #72 #74 #75; kvnloo/tokenomics #19 #20; oh-my-pi#109 (OMP child of #62) and kvnloo/hermes-agent #319 (Hermes shadow decisions; read comments for the 10-02 sample-collection results); kvnloo/bend-native (README, plugin.yaml, stack/, receipts) as the existing standalone Hermes plugin carrying the observer/shadow scorer/DecisionOpportunity builders.
Read code on z0intelligence origin/master + feat/shadow-loop-v0: decision_opportunity.py, claude_code.py, hermes_decisions.py, outcome_observation.py, outcome_verifier.py, loop_export.py, receipt.py, credit.py, cognition/shadow.py, bridge/runtime.py, automatic.py, harness-adapters/*, routines.py, cascade.py; evolution-lab verified_loop.py + its prereg.
Answer: for EACH harness (Claude Code, OMP, Hermes, DSH, Codex, OMO, plus any other active one) — what is captured today (schema, file, writer), what #62/#54 require, what is missing; what "shadow procs automatically learning at all times" maps to in the RFCs (which candidates/learners, which data, who runs them, sufficiency rules, promotion/approval rules); the existing schemas that must be unified (decision_receipt.v1, outcome_join.v1, cognition.shadow.receipt.v1, DecisionOpportunity v0, loop training_row.v0, DSH jev receipts) and whether a converter/importer already exists anywhere.
Write ${W}/spec/R1-learning-capture.md.` },
  { key: 'R2-unified-memory', prompt: `Survey: UNIFIED MEMORY SYSTEM.
Read (bodies + comments): kvnloo/z0intelligence #22 #63 #66 #53 and any issue/PR mentioning memory, recall, statepack, state packet, context_resolve, agentsview, tencentdb, fts5, optmem, belief, EventIdentity; z0intelligence docs/memory-control-plane-contract.md (origin/master) and code memory_contract.py, context_resolve.py, state_packet.py, intelligence_mcp.py, any recall/statepack modules on master AND on remote branches (git -C ... branch -r; grep across branches for recall_mcp, statepack, orient, universal recall); kvnloo/z0evals #56 #57 #71 #73 (+ branches study/unified-memory-56-*, study/hermes-unified-memory-56); kvnloo/z0 registry/*.yaml entries for memory; kvnloo/deepseek-harness PR #2 (AgentsView overlay) and its zer0.repo.yaml manifests (PRs #3/#4); kvnloo/agentsview fork (what it adds: recall, mcp, pg/duckdb); hermes fork memory providers (plugins/memory/* incl. tencentdb, holographic/fts) in /mnt/zer0models/hermes-wt/bend-integration; hermes-jev-skills dsh/plugin/memory.js; kvnloo/supermemory and kvnloo/hermes-lcm forks if relevant (INFERRED relevance — check).
Inspect the agentsview DB read-only (sqlite3 "file:/mnt/zer0models/sft-svlm/data/agentsview/sessions.db?mode=ro"): recall tables, counts, latest timestamps; \`agentsview mcp --help\`, \`agentsview recall --help\` (help only).
Answer: what "unified memory fully working and wired up across all harnesses" means per the owner's RFCs (layers, sources, identity, temporal model, what each harness must be able to read/write, acceptance criteria); what exists and works today; what is broken (e.g. agentsview sync stopped since 10-01, TencentDB not wired, DSH has no orient(), memory-use receipts missing); the per-harness seam the RFCs name (DSH inject()/agent/pre-step, Hermes memory provider/plugin, OMP extension, Claude Code hooks/MCP, Codex plugin). Explicitly: which backend is canonical and what must NOT be built (no second competing backend).
Write ${W}/spec/R2-unified-memory.md.` },
  { key: 'R3-harness-seams', prompt: `Survey: HARNESS INVENTORY + INTEGRATION SEAMS ON THIS HOST.
For every coding-agent harness present on this host (at least: Claude Code, OMP/oh-my-pi, Hermes, DSH, Codex, OMO, Grok CLI, OpenCode, Kimi, Gemini, antigravity-cli, cursor) determine: installed? (binary path/version via \`--version\` ONLY where safe — NEVER run dsh), how recently used (agentsview sessions.db read-only: max(started_at) per agent; DSH: ~/.dsh/sessions mtimes; Claude Code: ~/.claude-home/projects), where its transcripts/session logs live and whether agentsview ingests them, its extension mechanism (hooks, plugins, extensions, MCP config, memory providers) with file paths, and its CURRENT z0 wiring (config files: ~/.z0int/config/*.json, ~/.claude-home/settings.json enabledPlugins, ~/.codex config (no auth files), ~/.omp or oh-my-pi config, ~/.dsh/profiles/*/ (redact secrets), OMO config, ~/.hermes/config.yaml memory/plugins keys only). Also: which z0int tree/venv each wiring points at (there are 4 divergent trees: see prior-findings), whether the live z0 service (systemctl --user cat z0intelligence.service — read only) is on master, and systemd user units/timers related to z0/agentsview/quackles (list-units/cat only).
For each harness produce: active (Y/N + last used), capture seam (where a per-turn decision-opportunity + outcome record can be emitted without touching harness core), memory seam (where unified-memory read/inject happens), test seam (how an isolated end-to-end test can drive it without the live install — e.g. isolated HOME/HERMES_HOME, fake hook payloads, headless CLI), and live-activation step (exact config change, reversible, backup path).
For Hermes specifically: the plugin mechanism (plugin.yaml / entry points / \`hermes plugins install\`), MCP config (mcp_servers), memory-provider plugins, and how kvnloo/bend-native installs — read from the fork source and bend-native, not the live install.
Write ${W}/spec/R3-harness-seams.md.` },
  { key: 'R4-roadmap-gates', prompt: `Survey: ROADMAP, ORDERING, GATES, BOUNDARIES, OWNER INTENT.
Read: kvnloo/z0 README/ARCHITECTURE/SYSTEM/AGENTS, registry/*.yaml + zer0.registry.yaml (repo-map boundaries: which repo owns capture, memory, learning, eval, promotion, cost, placement, authority), rfcs/, docs/; kvnloo/z0archy; all OPEN issues in kvnloo/z0intelligence, kvnloo/z0, kvnloo/z0evals, kvnloo/evolution-lab, kvnloo/tokenomics, kvnloo/kerdoios, kvnloo/aodl (titles for all; full body for any about roadmap, rollout, phase, wave, canary, gate, harness, memory, shadow, learning) — especially z0intelligence #47 #94 #95 (OMP golden canary gate: "stops before DSH/Hermes expansion"), #63 adapters checklist, #20, and any "critical path"/"phase0" docs (docs/critical-path-phase0.md on z0intelligence master).
Also read the owner's own words: grep the conversation-archive extractions in ~/.claude-home/jobs/5bc3424d/tmp/av/ and ~/.claude-home/jobs/5bc3424d/tmp/dsh/ (read-only) for instructions about data collection, shadows, learning, memory, and wiring order.
Answer: (1) the owner's stated end state for data collection + continuous shadow learning + unified memory, quoted; (2) the required ORDER of work and every gate (what must pass before what), distinguishing observe-only capture / read-only memory (allowed now?) from governed dispatch/remote execution (gated by #95); (3) the repo that owns each piece per the repo map; (4) owner rules that constrain implementation (e.g. no DSH core PR, shadow-first, no weights committed, verified≠completed, no second memory backend, stage-don't-promote on upstream forks); (5) what "done" means (acceptance checklists) so a build can be verified.
Write ${W}/spec/R4-roadmap-gates.md.` },
]

phase('Survey')
const surveys = await parallel(SURVEYS.map(s => () => agent(
  `${OWNER_ASK}\n\nYou are one of four parallel read-only surveyors feeding a TDD build plan.\n\n${s.prompt}\n${RULES}\n\nReturn the structured result; put full detail in your markdown file.`,
  { label: s.key, phase: 'Survey', schema: SURVEY_SCHEMA })))
const ok = surveys.filter(Boolean)
log(`${ok.length}/4 surveys returned`)
const isBreach = v => v && !/^\s*none\b/i.test(String(v))
const breaches = ok.filter(s => isBreach(s.hard_rule_breach)).map(s => s.hard_rule_breach)
if (breaches.length) log(`HARD-RULE REPORTS: ${breaches.join(' | ')}`)

const COMPONENT = { type: 'object', properties: {
  id: { type: 'string' }, title: { type: 'string' }, owner_repo: { type: 'string' },
  repo_path: { type: 'string', description: 'local clone/worktree path to branch from' },
  base_ref: { type: 'string' }, branch: { type: 'string', description: 'new branch name to create, e.g. feat/wire-<x>-20261003' },
  harnesses: { type: 'array', items: { type: 'string' } }, purpose: { type: 'string' },
  reuse: { type: 'array', items: { type: 'string' }, description: 'existing modules/branches to reuse, never reimplement' },
  red_tests: { type: 'array', items: { type: 'string' }, description: 'failing tests to write FIRST (behaviour-contract level), each one line' },
  acceptance_refs: { type: 'array', items: { type: 'string' } },
  depends_on: { type: 'array', items: { type: 'string' } },
  isolated_e2e: { type: 'string', description: 'how to prove it end-to-end without touching any live install' },
  activation: { type: 'object', properties: {
    steps: { type: 'array', items: { type: 'string' } }, touches_live: { type: 'array', items: { type: 'string' } },
    rollback: { type: 'string' }, risk: { type: 'string' }, needs_owner_approval: { type: 'boolean' } },
    required: ['steps', 'touches_live', 'rollback', 'risk', 'needs_owner_approval'] },
  gated_by: { type: 'string', description: 'gate that blocks building/activating it, or "none"' },
  build_now: { type: 'boolean' },
}, required: ['id', 'title', 'owner_repo', 'repo_path', 'base_ref', 'branch', 'harnesses', 'purpose', 'reuse', 'red_tests',
  'acceptance_refs', 'depends_on', 'isolated_e2e', 'activation', 'gated_by', 'build_now'] }

const PLAN_SCHEMA = { type: 'object', properties: {
  summary: { type: 'string' },
  definition_of_done: { type: 'array', items: { type: 'string' } },
  components: { type: 'array', items: COMPONENT },
  ordering: { type: 'array', items: { type: 'string' } },
  gates: { type: 'array', items: { type: 'object', properties: { gate: { type: 'string' }, blocks: { type: 'array', items: { type: 'string' } }, status: { type: 'string' } }, required: ['gate', 'blocks', 'status'] } },
  continuous_learning: { type: 'object', properties: {
    schedule: { type: 'string' }, steps: { type: 'array', items: { type: 'string' } }, resource_rules: { type: 'string' },
    promotion_rule: { type: 'string' } }, required: ['schedule', 'steps', 'resource_rules', 'promotion_rule'] },
  out_of_scope: { type: 'array', items: { type: 'string' } },
  open_questions_for_owner: { type: 'array', items: { type: 'string' } },
  file: { type: 'string' },
}, required: ['summary', 'definition_of_done', 'components', 'ordering', 'gates', 'continuous_learning', 'out_of_scope', 'open_questions_for_owner', 'file'] }

const surveyDigest = JSON.stringify(ok.map((s, i) => ({ survey: s.file, key_findings: s.key_findings, gaps: s.gaps,
  reuse: s.existing_code_to_reuse, acceptance: s.acceptance_criteria })))

const DESIGN_RULES = `
Design constraints (owner rules + safety scope — all mandatory):
- Reuse before build: every component names the existing module/branch it extends (repo map owners: z0intelligence = capture/runtime/credit/memory control plane/promotion; evolution-lab = candidate search/learning/promotion EVIDENCE; z0evals = protected eval; tokenomics = cost; kerdoios = placement; aodl = authority). No second memory backend; no second NanoJev runtime; no new framework where an importer/adapter suffices.
- No harness core changes anywhere (owner direction): DSH via plugins/profiles/MCP only (hermes-jev-dsh plugin, dsh-z0intelligence adapter); Hermes via a standalone plugin (reuse/fix kvnloo/bend-native or a z0int harness-adapters/hermes plugin) + MCP + z0 API — NOT code in the kvnloo/hermes-agent fork; OMP via extension; Codex/Claude Code via their plugins; other harnesses via MCP. Develop and test with isolated HOME/HERMES_HOME — never the live ~/.hermes. Activation = install/enable + config entries only.
- PLUGIN LAYOUT (owner decision 2026-10-03, binding): ONE harness-agnostic z0 plugin lives in kvnloo/z0intelligence under harness-adapters/ — a shared core (capture: DecisionOpportunity/observed/outcome records; memory: unified read/inject + memory-use receipts; z0 API client; shadow emission) plus thin per-harness shims in harness-adapters/<harness>-z0intelligence/ (claude-code [exists], dsh [exists: dsh-z0intelligence], hermes [new: a native Hermes plugin with plugin.yaml, installed via \`hermes plugins install\`], omp [extension], codex [plugin], others via the z0 MCP server). No new repos. Moves: (a) the \`hermes z0\` stack (observer, API-attempt scorer/evaluator, State Packet + DecisionOpportunity builders, z0 bridge) moves OUT of kvnloo/bend-native INTO harness-adapters/hermes-z0intelligence — bend-native keeps Bend proof verification only and may optionally depend on the z0 Hermes plugin; fix the 12 known bend-native z0-stack bugs in the moved code (TDD), not in bend-native; (b) the DSH work on kvnloo/hermes-jev-skills branch hermes-jev/omp-adapter (hermes-jev-dsh plugin: shadow decision plane, lineage receipts, memory stub) moves into harness-adapters/dsh-z0intelligence (reconcile with the existing adapter; receipts converge on z0 contracts; Jev reached via the z0 service) — hermes-jev-skills keeps only the Jev decision layer (jevkit, skills, Hermes Jev routing plugin, evals). Component branches for these moves land in z0intelligence (and a removal branch in bend-native).
- Shadows are inert (never change what a model/user/tool sees); continuous learning = scheduled offline capture→verify→export→evolution-lab learner runs on privacy-safe tables (no prompt/response/command/path text in training rows), reports only; promotion past shadow requires per-candidate owner approval. Governed dispatch/remote execution expansion to DSH/Hermes stays gated by #95 — do not plan it as build_now.
- Any scheduled/CPU-heavy job must take the quiet-lane lock in SHARED mode (flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock <cmd>) so it never runs inside an exclusive timed experiment window; GPU use must respect the Quackles arbiter and z0-farm-llama.
- The live z0 service (~/tmp/z0int-canonical on a closed-PR branch, port 11501) and every live harness config are LIVE: changing them is an activation step with backup + rollback + needs_owner_approval=true. Building and testing happen in new worktrees under /mnt/zer0models/z0-wt/wiring/wt/<component-id> on new branches; publishing = push new branches to kvnloo/* (no PRs opened, no force).
- TDD: each component lists red tests (behaviour contracts) to write and see FAIL before implementation, and an isolated e2e that proves it with fake/isolated harness homes. Prefer the smallest implementation surface that delivers the behaviour.
- Size: at most 8 build_now components; merge small ones; order by dependency (shared schema/importer first, then per-harness adapters, then the continuous-learning scheduler, then memory wiring per harness).`

phase('Design')
const draft = await agent(`${OWNER_ASK}

You are the plan synthesizer. Four read-only surveys were written to ${W}/spec/R1-learning-capture.md, R2-unified-memory.md, R3-harness-seams.md, R4-roadmap-gates.md (read them fully) plus ${W}/prior-findings.md. Their structured digests: ${surveyDigest}

Produce the build plan that delivers, per the owner's RFCs: (A) data collection for ALL active harnesses into the verified learning loop (DecisionOpportunity/observed/verified/credit, plus importers that bring the existing OMP v1 spine and DSH jev receipts into the loop where meaningful); (B) shadow procs that learn continuously (scheduled, inert, sufficiency-gated, owner-approved promotion); (C) the unified memory system fully working across all harnesses (one canonical substrate, per-harness read/inject seams, memory-use receipts, sync running).
${DESIGN_RULES}
${RULES.replace('this phase is READ-ONLY except your output file', 'READ-ONLY except your output files')}

Write ${W}/spec/SPEC.md (human-readable: end state, architecture diagram in text, per-harness table, components, order, gates, activation runbook, definition of done) and return the plan object (file = SPEC.md path).`,
  { label: 'synthesize-plan', phase: 'Design', schema: PLAN_SCHEMA, effort: 'high' })

phase('Critique')
const CRIT_SCHEMA = { type: 'object', properties: {
  verdict: { type: 'string', enum: ['ACCEPT', 'REVISE'] },
  issues: { type: 'array', items: { type: 'object', properties: {
    severity: { type: 'string', enum: ['blocker', 'major', 'minor'] }, component: { type: 'string' },
    problem: { type: 'string' }, fix: { type: 'string' } }, required: ['severity', 'component', 'problem', 'fix'] } },
  missing_acceptance: { type: 'array', items: { type: 'string' } },
}, required: ['verdict', 'issues', 'missing_acceptance'] }
const critique = await agent(`Adversarially review this build plan for the owner request below. Default to finding problems.
${OWNER_ASK}
Plan (also in ${W}/spec/SPEC.md): ${JSON.stringify(draft)}
Sources to check it against: ${W}/spec/R1..R4 notes, ${W}/prior-findings.md, and the actual issues/code (read-only gh + git -C ... show).
Check: (1) every RFC/issue acceptance criterion relevant to data collection, continuous shadow learning and unified memory is mapped to a component or explicitly out_of_scope with a reason; (2) no component reimplements existing code or lands in the wrong owner repo (repo map); (3) every red test is a real behaviour contract that would FAIL today and can run offline in an isolated home; (4) safety scope: shadows inert, no raw text in training rows, owner-approved promotion, no live install touched before activation, DSH core untouched, Hermes fork only, #95 gate respected, shared quiet lock for scheduled CPU work; (5) the dependency order is buildable and each isolated_e2e is actually runnable on this host; (6) "all harnesses" really covers every ACTIVE harness from R3 (or justifies exclusions); (7) memory: one canonical substrate, sync actually running, every harness can read it, memory-use receipts.
${RULES}`, { label: 'critic', phase: 'Critique', schema: CRIT_SCHEMA, effort: 'high' })

phase('Revise')
const plan = await agent(`Revise the build plan using the critic's findings. Fix every blocker and major issue (or justify rejecting it in SPEC.md under "Critique disposition"); fold minor ones where cheap.
${OWNER_ASK}
Draft plan: ${JSON.stringify(draft)}
Critique: ${JSON.stringify(critique)}
${DESIGN_RULES}
${RULES.replace('this phase is READ-ONLY except your output file', 'READ-ONLY except your output files')}
Overwrite ${W}/spec/SPEC.md with the final version (add a "Critique disposition" section) and write the final plan object as JSON to ${W}/spec/PLAN.json. Return the same object (file = PLAN.json path).`,
  { label: 'revise-plan', phase: 'Revise', schema: PLAN_SCHEMA, effort: 'high' })

return { plan, critique_verdict: critique && critique.verdict, critique_issues: critique && critique.issues.length,
  survey_files: ok.map(s => s.file), breaches }
