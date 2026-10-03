# R4 — Roadmap, ordering, gates, boundaries, owner intent

Surveyor R4 of 4 (read-only), 2026-10-03. Extends `/mnt/zer0models/z0-wt/wiring/prior-findings.md`; the audits recorded
there are not redone here.

Pinned refs read:
- kvnloo/z0 @7933914
- kvnloo/z0intelligence origin/master @0159808 and `feat/shadow-loop-v0` @6fee859
- kvnloo/z0evals @fb14919
- kvnloo/deepseek-harness @70215673fe
- kvnloo/bend-native @e85e65e5
- Hermes fork refs from `/mnt/zer0models/hermes-wt/bend-integration`

Issue bodies and comments were fetched on 2026-10-03 with `gh` (read-only). Scratch copies are in `spec/scratch/`.

Tags used below:
- **INFERRED** marks my reasoning; it is not a quoted or verified fact.
- **VERIFIED** means I read the code, issue or config myself in this run.

---

## 0. The relayed question: "it shouldn't require any changes to hermes directly, ya? it should just be a plugin / mcp / api?"

**Yes. The repos already agree, and every harness has a native seam that needs no core change.** VERIFIED evidence:

| Harness | Seam the integration uses | Where the z0-owned integration code already is | Turn-on step (config only) |
|---|---|---|---|
| Hermes | Native plugin API: `plugin.yaml` plus hooks `pre_llm_call`, `post_llm_call`, `on_session_end`, `pre_approval_request` and `pre_tool_call`. Returning `{"context": ...}` from `pre_llm_call` injects ephemeral context. It does not mutate the user message, the history or the session DB (hermes-agent#320 comment, 2026-09-30). | z0int `harness-adapters/hermes-z0int-decisions/` (shadow capture), `harness-adapters/hermes-z0intelligence/` (automatic), `adapters/hermes_z0int/` (identity and outcome joins); kvnloo/bend-native (standalone plugin) | `hermes plugins install …` / `plugins.enabled` in the profile `config.yaml` |
| Claude Code | Plugin hooks `UserPromptSubmit`/`Stop`/`SessionEnd`/`SessionStart`, plus `.mcp.json` | z0int `harness-adapters/claude-code-z0intelligence/`, `claude-code-z0-obspack/` | Marketplace plugin install (already installed on this host) |
| OMP | Extensions dir `~/.omp/agent/extensions/<name>`, symlinked to the z0int checkout; hooks such as `before_agent_start` and `context` | z0int `omp-extensions/{z0int-bridge,z0int-intelligence,local-cognition,flyforge-jev,…}` | Symlink or extension enable |
| DSH | Cordis plugin `apply(ctx)` with `ctx.on('llm/stream', …)`; generic MCP client through a profile overlay | z0int `harness-adapters/dsh-z0intelligence/`; DSH PR #2 = **one config example file** `apps/cli/config/examples/mcp-memory/agentsview.cordis.yml` | Profile `cordis.patch.yml` entry |
| Codex | Plugin `z0intelligence@personal` (route_worker MCP, installed 09-27, per prior findings) plus MCP config | z0int MCP `bin/z0int-mcp` | Codex plugin/MCP config |
| Every harness (read-only memory) | `agentsview mcp` (stdio, read-only), launched with `AGENTSVIEW_DATA_DIR=/mnt/zer0models/sft-svlm/data/agentsview` | External tool, no code | MCP entry per harness |

Repo statements of the same rule:
- z0int README: "Thin Claude Code plugin over the canonical `z0int.automatic` path, the same seam Hermes (`pre_llm_call`), OMP (`before_agent_start`) and DSH (`llm/stream`) use."
- bend-native README: "without changing Hermes core"
- bend-stack SYNTHESIS §1: "needs no Hermes core change"
- H3a (hermes-agent#319): "no core Hermes file references z0int … 106/106 runs"

**Places where existing work conflicts with this direction** (to re-home, never to extend):

1. **Hermes fork z0 lab branches.** `fork/lab/z0int/{full-observer-v0,retry-shadow-v0,shadow-eval-v0}` (PRs #385/#386/#387 on kvnloo/hermes-agent) add plugin-shaped files under `lab/z0_hermes_observer/` in the **fork tree**. VERIFIED from the diff-stat: no core files change. bend-native already copies them unchanged (`stack/sources.json`). Under the 10-03 direction their home is bend-native or z0int `harness-adapters/`, not the fork.
2. **Hermes study and lab branches on the fork.** `study/hermes-unified-memory-56@c84663880a` (local) and `lab/track4/rlm-state-shadow-v0` (hermes-agent#320). INFERRED: the "MemoryPacketCache" worktree `/workspace/hermes-home/hermes-agent/.worktrees/wt/memory-packet-cache` (owner archive, 09-21) touched Hermes `memory_manager` tests, so it was probably a core change. It must be re-expressed as a `pre_llm_call` plugin or a memory-provider plugin.
3. **OMP PR #107.** `feat(ext): shadow cognitive-state packet on the context event` (oh-my-pi#107, branch `feat/cognitive-state-shadow-rfc79`) lives in the OMP fork. INFERRED: it should move to z0int `omp-extensions/`, which follows the owner's 09-19 rule that "Everything experimentally intelligent lives behind extensions."
4. **NeMo Relay RFC.** `z0/docs/architecture/rfc-nemo-relay-runtime-observation.md` names `kvnloo/hermes-agent` as the owner of the "Hermes pilot", and z0#21 says the fork "already contains a first-class Relay runtime (`agent/relay_runtime.py`)". That RFC is optional observability and **not on this program's path**. Do not use it as the capture vehicle.
5. **Unverified seam.** The owner accepted an OMP `core/live-runtime` branch on 09-19 that carries "the minimum unavoidable OMP-core seam". Whether the current OMP extension API needs that delta is **UNVERIFIED** here; R1-R3 should check that the hooks used exist on OMP `main`/`dogfood`.

---

## 1. Owner's stated end state (quoted)

### 1a. The request this workflow serves (2026-10-03, verbatim)

> "ok so i need you to hook up a few things - data collection for all harnesses. so that our shadow procs are
> automatically learning at all times. next i want the unified memory system fully working and wired up across all
> harnesses properly. if you take a look at all the issues and roadmaps and RFCs i should have explained very clearly
> what i want. please spin up a new ultracode workflow that uses tdd to wire everything up"

> "well it shouldn't require any changes to hermes directly, ya? it should just be a plugin / mcp / api ?"

### 1b. Data collection and continuous shadow learning

- **09-18 04:34 (OMP archive):** "**Stop searching broadly for a better fly. Turn your live OMP usage into a self-improving data engine, and use the GPU to repeatedly evolve the smallest specialist that can safely absorb more traffic.**"
- **09-18 05:32:** "I would make **'clone → ask your agent to onboard → working self-improving z0int'** a hard product requirement. … **The LLM decides intent and handles genuinely semantic steps. Deterministic setup/training logic lives in code.**"
- **09-19 07:48:** "we should stop reorganizing Git now … needs real-world pressure … Next phase should be almost entirely dogfooding … `Jev shadow decisions → receipts → /context + Tokenomics → verified outcomes` … **Everything experimentally intelligent lives behind extensions.**"
- **09-19 07:48 / U[520]:** "I would treat **normal usage as the experiment** … The most important discipline now is that **activity is not savings**."
- **09-18 06:28:** "lmao yea so u didn't even run a single live test?? that's crazy pls do so that i dont spend 1 week 'collecting data' for nothing"
- **09-18 15:53:** "wait wtf ik w didn't get overnight data - but i thought we did wire up omp for live traffic data"
- **z0int#56 addendum** (posted to the owner's RFC 2026-10-03; describes "the owner's goal"): "a system that improves from ordinary use. Every decision opportunity is shadowed by a population of candidate policies. Candidates are scored against verified outcomes and mutated in Evolution Lab. A candidate that keeps winning is promoted, and is deoptimized when its validity stops holding."
- **09-21 01:24 (Hermes archive):** "run NanoJev locally in SHADOW on the same model-routing decisions … collect paired measurements … do NOT let NanoJev affect production behavior yet"
- **z0int#14:** "Sequencing correction: dogfood first … First activate the existing `kvnloo/hermes-jev-skills` plugin on one reversible Hermes hot path … Fail open … Capture compact replayable receipts while it runs."

### 1c. Unified memory

- **09-22 12:25 (DSH archive), definition of done:** "Your task is not to design another memory architecture. Your task is to make my existing conversation history and memory stores universally searchable by every harness as fast as safely possible, then prove with real tests … SCAN FIRST. WIRE WHAT EXISTS. INGEST WHAT IS NOT INDEXED. FIX WHAT IS BROKEN. EXPOSE ONE SIMPLE RECALL INTERFACE. TEST IT. … I can start Hermes, OMP/Pi, Codex, Claude Code, fx, o8, OpenCode, or another local harness and ask … The harness can retrieve relevant evidence from my historical conversations regardless of which product originally generated the conversation. Answers must include enough provenance to identify the original source, harness, session/conversation, timestamp when available, and source path or stable identifier. The system must search actual historical records, not only MEMORY.md summaries."
- **09-25 13:47:** "isn't that what we were working on this whole time is creating a custom memory system that allows my agents to actually use all these layers like hermes, tencentdb, jsonl from pi, conversation logs from gpt + claude … so what skill should i be pointing all my harnesses at in order to use the unified memory system that we created?"
- **09-25 14:13:** "so based on the initial architectural ASK - you're telling me that nothing is really wired up yet? what's the easiest way to get everything wired up to the universal memory?"
- **09-25 14:19 (text the owner sent back, endorsing it):** "You asked for **unified memory that OMP, Hermes, DSH, Grok, etc. could actually use** … **every harness can call it during real work.** … I made this worse by repeatedly treating things like 'adapter exists,' 'MCP configured,' 'extension present,' or 'seam identified' as if they meant 'wired and functioning in the actual harness.' They do not. … I did not maintain a verified matrix of **live query through harness → unified memory → returned evidence**."
- **09-26 14:14:** "Two critical paths only. ### Unified memory … `agentsview mcp → stdio → DSH generic MCP client` No new memory service, no `:8791`, no custom DSH implementation … I'm not calling DSH green until that real local call happens."
- **09-29 15:48 (z0evals#56 DSH lane):** "Do not count returning retrieval JSON directly to the user as injection. Evidence must reach the next model-visible context. … Stop rather than inventing architecture if stable injection/provenance cannot be demonstrated."
- **z0int#63:** "**events are truth; everything else is a projection or index** … without creating another competing memory backend." The layers it lists:
  - EPISODIC: EventLog + OptMem
  - RETRIEVAL: FTS5 + AgentsView
  - SEMANTIC: TencentDB L1/L2/L3
  - WORKING STATE: StatePacket, which is "the query-time memory router, not another memory store"

**INFERRED synthesis of the end state.** Every harness the owner uses (Claude Code, Hermes, OMP/OMO, DSH, Codex, and Grok, OpenCode and others where a seam exists) does two things through its own extension seam.

(A) It **continuously and automatically emits** DecisionOpportunity, observed-behaviour and verified-outcome records. These go into one cross-harness semantic record (z0int#62). Registered challenger policies score that record in shadow, and Evolution Lab evolves the challengers against a protected judge. The owner promotes a winner per decision family. Nobody has to remember to "collect data".

(B) It can **call one unified recall surface during real work** and gets provenance-bearing evidence from all historical stores. Optionally, evidence-derived State Packets are injected, through the staged shadow → canary path. Every step from capture to answer is verified live in the harness, not by "config exists".

---

## 2. Required order of work and every gate

### 2a. The four classes of work and whether each is allowed now

| Class | Examples | Allowed now? | Governing gate |
|---|---|---|---|
| **Observe-only capture** (hooks return None, nothing changes the turn) | DecisionOpportunity + outcome rows; API-attempt observer rows; usage receipts | **YES** | Shadow-inertness gate (G-CAP below). **Not** gated by #95. VERIFIED: #95 "deliberately stops before DSH/Hermes expansion" of the *governed canary*; #94 "do not widen the canary to DSH/Hermes or automatic routing". |
| **Read-only memory as a tool** (the model calls a recall tool; nothing is written to stores) | `agentsview mcp` `search_sessions`; z0int `context_resolve` read | **YES** | Unified-memory proof gate (G-MEM). Not gated by #95. Precedent: DSH PR #2 was merged and DSH was declared GREEN on 09-26 after one real call. |
| **Automatic memory injection** into model-visible context (`pre_llm_call` returns `{"context": …}`, State Packet replacement) | Hermes #320 Phase 2/3; OMP #79 Stage B | **Staged**: shadow (record what would be injected) → explicit eval canary on the frozen cohort → default-on. Active context replacement comes **last**. | #320 phases; z0int#22 comment ("First gate is shadow packet/provenance/cache invalidation; active context replacement comes later"); oh-my-pi #79 "Stage A — shadow / Stage B — explicit eval canary". Not #95. |
| **Governed dispatch, remote execution, automatic routing** | `route_worker` exposure to new harnesses; `/v1/governed-worker`; `automatic.json` hermes/dsh `on`; the DSH `llm/stream` plugin replacing native output | **NO, blocked** | #95 checklist, which needs #94 PASS + z0evals#82 import green; then "open the next packet for DSH/Hermes using the same contract rather than inventing a new path"; z0int#47 acceptance. **Status on 2026-10-03: #94 has 1 comment (the packet tracker) and no PASS; z0evals#82 is OPEN. The gate is closed.** |

Note (VERIFIED): the DSH adapter on master, `harness-adapters/dsh-z0intelligence/index.mjs`, **is not observe-only**. When `result.action === 'context'` it yields its own text blocks and `finish` in place of the native stream. It must stay disabled for DSH (automatic.json dsh=off) until #95 opens. DSH capture needs a separate, observe-only plugin path (gap G4 in §5).

### 2b. Ordered build waves

The owner's 10-03 order is explicit: **data collection first, then unified memory**. Inside capture, the owner's 09-26 23:19 harness order applies: "Reconcile **OMP → Hermes → DSH** onto the canonical z0intelligence function/router/receipt interfaces", then a cross-repo E2E with replay, conflicting-fingerprint and uncertain-execution invariants. The z0int#56 milestones fix the learning order: M0 → M1 label volume → M2 shadow slot → M3 population → M4 promotion → M5 deopt drill. The addendum states "the critical path is **label volume and resolution, not search**".

```text
W0  Safety + decisions (config/decision only, no harness code)
 │   G0: no build artifact can reach the live z0 service (127.0.0.1:11501) or the live Hermes home;
 │       bend-native P-1 handled (pin or fix default port) before ANY install; agentsview require_auth stays on;
 │       pick ONE Hermes capture vehicle (avoid double capture), see §3c.
 ▼
W1  Observe-only capture on every harness           (allowed now)
 │   order: Claude Code (live; finish M0) → OMP → Hermes → DSH → Codex/others
 │   G-CAP per harness (below) must be green before the harness counts as "collecting"
 ▼
W2  One cross-harness record + outcome resolution (M1 "label volume")
 │   generalise outcome_verifier / loop_export (today HARNESS='claude-code' hard-coded, VERIFIED) to every harness;
 │   merge privacy-safe tables across hosts by turn_key; cohorts never pooled
 │   G-SUFF: el#24 sufficiency (MIN_ROWS 300, MIN_NEG 30, MIN_GROUPS 10, every fold both classes) on ≥1 cohort
 ▼
W3  Unified memory, read-only tool on every harness  (allowed now; may run in parallel with W2)
 │   one recall surface (AgentsView MCP + z0int context_resolve/StatePacket as router; no new backend)
 │   G-MEM per harness: z0evals#56 cases A–F with receipts validating against
 │         studies/unified-memory-v0/receipt.schema.json on the frozen cohort.json
 ▼
W4  Memory control plane slices (z0int#63/#66 "next slices") + memory-use receipts
 │   EventIdentity from AgentsView/EventLog importers → scoped BitemporalClaims → StatePacket/DO bound to
 │   MemorySnapshot → memory-use in OMP/Hermes/DSH receipts (DecisionReceipt.extra) → z0evals cohorts
 │   G-#63: the P0 acceptance list (below); "no migration or mutation of existing production memory stores"
 ▼
W5  Shadow slot (M2): registered challengers decide counterfactually per opportunity, inert, off hot path
 │   G-SLOT: inertness + latency measured + fail-open; requires G-SUFF for any comparison to mean anything
 ▼
W6  Population search (M3, Evolution Lab) against protected judge (z0evals#72/#74 score API) under Tokenomics budget
 ▼
W7  Promotion (M4): OWNER APPROVAL per candidate per decision family; canary then promotion
W8  Deopt drill (M5, z0evals#74)
 ┄┄
WX  Governed dispatch / remote execution / automatic routing for Hermes/DSH: BLOCKED behind #94 → z0evals#82 → #95
     promotion gate → "next packet for DSH/Hermes using the same contract"; then z0int#47 acceptance.
```

Automatic memory **injection** (Hermes #320 Phase 2/3, OMP #79 Stage B) slots in after W3 G-MEM, as its own shadow → canary track. It is not a prerequisite for W5.

### 2c. Gate definitions (what must pass before what)

**G0 — Safety preconditions** (before any install or enable):
1. bend-native **P-1**: `stack_service_port` defaults to 11501, which is the live z0 service, and "the bridge bypasses the proxy". Either fix the default (require explicit opt-in) or pin a private port in every profile before enabling. SYNTHESIS §8 rates this "high (safety)".
2. Tests and smokes use a private `Z0INT_HOME` and `HERMES_HOME` and private ports. Never `~/.hermes` or `/workspace/hermes-home`, and never 11501 (hard rules; SYNTHESIS §2 shows the pattern).
3. AgentsView `require_auth = true` stays on. Owner, 09-22 18:02: "**Turn on `require_auth = true` first.** … you have already indexed credential-bearing tool output."
4. One Hermes capture vehicle (§3c). Two observers on one turn would double-count opportunities. #62 requires "the same semantic record, not three harness-specific schemas".

**G-CAP — per-harness capture is "on"** (composed from #319 acceptance, #62, #56 addendum, the 09-19 P0.7 directive and the CONTRACT lane):
- One REAL turn in that harness produces an opportunity row and an observed-outcome row joined on the same `trace_id/turn_id` (#319 H1). Stable IDs survive restart and replay (#62).
- Shadow is inert: hooks return None. Off vs shadow gives an identical Hermes-built first request (CONTRACT H1 design: 0 Hermes-built divergences). There are 0 block directives (H2).
- Fail-open: backend or service down → harness exit 0, and rows are receipted as `backend_unavailable`/`backend_error`, never dropped silently (#319 H4).
- Off the hot path. Owner 09-19 P0.7: "Jev shadow work must not materially delay the root provider request." Callback p99 is measured; `emit_opportunity_async` is the pattern (#56 addendum).
- Drops are counted and persisted. "missing telemetry ≠ zero". This is bend-native P-3, which today fails it.
- Bounded unload/close (bend-native P-2 fails it today).
- Privacy: training rows contain no prompt, response, command, path or claim text (#56 addendum). Opportunity stores that keep request text are opt-in and documented (P-9).
- The cwd/repo used for state is the task repo, not the process cwd (P-7 fails it today: "gate says ACT with every git fact unknown").
- `execution_completed` stays distinct from `verified_success` (#62, critical-path-phase0, README).
- **Live proof, not config.** Owner 09-25 14:19: "adapter exists / MCP configured / extension present / seam identified" ≠ wired. Owner 09-18: "didn't even run a single live test??".

**G-SUFF — data sufficiency before any learning claim** (#56 addendum; el#24 prereg `docs/prereg/verified-loop-v0.md` on `exp/verified-loop-v0`):
- `MIN_ROWS 300`, `MIN_NEG 30`, `MIN_GROUPS 10`, and every one of 5 folds contains both classes. Below these, every comparison is `INSUFFICIENT_DATA`.
- Falsification: "If label resolution stays too low to reach sufficiency for any cohort within 8 weeks of M1 (proposed bound), stop search work. Put the effort into outcome oracles (#54) instead."
- Current state: this host gives 0 analysis rows. The 7-day sweep found 149 turns, 10 resolved (prior findings). Hermes `verification_needed`: no backend beats the base rate, and `api.attempt_will_fail` is DEGENERATE at 0/653 (#319, #14).

**G-MEM — per-harness unified memory is "wired"** (z0evals#56 acceptance and stop conditions, z0int#22 promotion gate, owner 09-29):
- A retrieval: a real query returns source-backed evidence with stable provenance.
- B injection: the evidence reaches the **next model-visible context**. "Indexing/config alone is not a pass"; "Do not count returning retrieval JSON directly to the user as injection."
- C use: the answer is supported by the injected evidence.
- D abstention: with a required source removed, the harness reports the gap.
- E idempotency: replay causes no duplicate injection or side effect.
- F supersession: a newer contradictory fact changes current state, and history stays inspectable.
- Rows validate against `studies/unified-memory-v0/receipt.schema.json` (`z0eval.unified_memory_receipt.v0`) on z0evals main. The frozen `cohort.json` has 6 questions: exact-identifier, supersession, cross-harness, contradiction, missing-evidence, minimal-context. #56 comment: "Do not count any lane as passing until its emitted rows validate against … receipt.schema.json on main."
- z0int#22 promotion gate: "no harness counts as integrated merely because indexing/configuration exists. Require one real supported answer from injected evidence, one abstention/failure-injection case, one idempotent replay, and one supersession case."
- Stop conditions (z0evals#56): stop and document if a harness cannot expose model-visible injection, stable session/trace identity is missing, evidence cannot descend to a source pointer, or the test would require copying private raw history into z0evals.

**G-#63 — memory control plane P0** (z0int#63 acceptance, verbatim list):
- 10k synthetic turns always render within the configured history budget.
- The newest raw-tail budget remains verbatim.
- `zoom` to event 0 returns the original bytes exactly.
- `forget + renap` never mutates `events.jsonl`.
- Appending only to the raw tail keeps the coarse-history hash stable.
- FTS5 hits return canonical event IDs.
- TencentDB-derived memories include canonical provenance.
- Workers cannot open or write the canonical event ledger.
- Held-out facts remain recoverable after a 1024-event nap cascade.
- StatePacket can resolve one query across temporal, lexical and semantic sources without duplicate evidence.
- DSH worker injection + receipt round-trip works.
- Hermes worker injection + receipt round-trip works.
- No migration or mutation of existing production memory stores during P0.

Status (VERIFIED on master 0159808): items 1-5 and 9 are covered by integrated #67/#69 (`src/z0int/memory/{event_log,optmem_tree}.py`). The **adapters checklist** (#63 P0 items 3, 4, 6, 7, 8: fts5 projection, tencentdb projection, `adapters/agentsview`, `adapters/dsh`, `adapters/hermes`) has **no code on master**. `git grep agentsview` in `src/` returns nothing, and `context_resolve.py:298` says "Memory recall is off by default (avoid double-inject with TencentDB proxy)."

**G-#66 — contract acceptance**, already integrated as #68: stable UID on replay, scope rejection before ranking, no evidence-less `verified`, bitemporal, no destructive supersession, `instruction_capability=True` rejected, snapshot changes on source-revision change, attaches to `DecisionReceipt.extra` without a schema change, deterministic tests, no production mutation. Any W4 code must keep these tests green.

**G-SLOT** (#56 M2, #319, #14): challengers load read-only. Their decision is "recorded and never shown to the model, the user or a tool". Added latency is measured and off the hot path. JEV and teacher agreement are never gold. Full distributions are retained. Grouped splits run event → trace → attempt → work-item → family → harness.

**G-PROMO** (#56 addendum; lifecycle `evolution-lab-promotion` [sanity, replay, shadow, promoted]; #14; #55):
- "Promotion past `shadow` needs owner approval per candidate and decision family."
- A candidate must win on the protected eval **and** in live shadow, with the full contract: operating region, calibration, invalidators, fallback, cost vector and lineage.
- Simple controls (deterministic gate, unconditional escalate) stay in the population.
- Even a promoted policy "still only *recommends*; AODL / deterministic authority can deny".

**G-#95 — governed dispatch expansion.** All of the following must hold:
- #94 PASS: one physical call, a real OMP registered tool, no parent-model/Jev call, linked admission/permit, replay with zero second inference, changed-task rejection, cap refusal, no-takeover, no task text in the governance snapshot, exact `CANONICAL_OK`, gold join, and `structural_execution_complete` + `verified_outcome_complete` true.
- z0evals#82 import green: schemas, pinned SHA `28db1ee…`, `raw_state_imported == false`, hashes, `tests/test_golden_trace.py`.
- After that: "mark the OMP governed canary proven; promote/merge the stack in dependency order; open the next packet for DSH/Hermes using the same contract rather than inventing a new path." Any failure means: "keep the rollout narrow and fix the first failure before continuing."

---

## 3. Ownership per repo map (what owns each piece)

### 3a. Canonical map

Sources: `z0/rfcs/verified-learning-control-loop/README.md`, z0#15, `registry/components.yaml` boundaries, `registry/mechanisms.yaml`.

| Piece | Owner | Evidence | Not here |
|---|---|---|---|
| **Capture semantics** (DecisionOpportunity, trace/attempt IDs, outcome/credit record, cross-harness contract) | **kvnloo/z0intelligence** | #53/#54/#62 ("z0intelligence owns the cross-harness cognition contract"; "Use thin adapters") | harness forks |
| **Capture integration code** (hooks/plugins/extensions) | **z0intelligence `harness-adapters/`, `omp-extensions/`, `adapters/`** or a standalone plugin repo (**kvnloo/bend-native** for Hermes) | Owner direction 10-03; z0int README "Harness bridges"; bend-native README | Hermes/OMP/DSH core; z0 (map only) |
| **Execution + trajectory/event emission** | Harnesses (OMP, Hermes, DSH, Codex, Pi) | z0#15 table: "execution + trajectory/event emission"; must not own "cross-harness cognition policy" | — |
| **Archival evidence index/retrieval** | AgentsView (external, `~/.local/bin/agentsview` v0.39.0) | RFC map: "archival evidence indexing/retrieval", not "authoritative current belief"; z0int#63 "Do not use AgentsView as the master context assembler" | belief/state |
| **Memory control plane** (EventLog, OptMem, memory_contract, context_resolve, StatePacket router) | **kvnloo/z0intelligence** | mechanism `unified-memory-evidence-path` implemented_by z0intelligence; #63 "Implement in **z0intelligence** first" | z0int `not_here: source memory databases` |
| **Semantic memory** | TencentDB (external capability; Hermes `memory.provider: memory_tencentdb`) | #63 L1/L2/L3; "Do not make TencentDB L0 a second canonical transcript" | canonical transcript |
| **Private traces** | memento / z0intelligence | `ARCHITECTURE.md` data-ownership table | frontier-kb |
| **Learning: candidate search/training, ABAB, Pareto/MAP-Elites, promotion evidence** | **kvnloo/evolution-lab** | mechanism `verified-specialist-evolution`; components `owns: [experiment search, …, promotion evidence]` | sealed truth, production authority, personal traces |
| **Shadow slot runtime** (challengers per opportunity) | z0intelligence (*proposed* owner for the stage z0#15 leaves unowned) | #56 addendum stage 4 | — |
| **Eval: frozen/protected studies, score API** | **kvnloo/z0evals** | #72/#74/#56/#82; mechanism `frozen-eval-publication` | training/search loops |
| **Promotion contract** (validity, drift, deopt) | z0intelligence (#56) + **owner approval**; lifecycle owned by evolution-lab | #56 addendum stage 7; `lifecycles.yaml` | — |
| **Cost/usage/outcome measurement** | **kvnloo/tokenomics** | `tokenomics.event.v0`/`report.v1`; #19/#4; NeMo RFC "Tokenomics remains the measurement/outcome authority" | semantic routing policy |
| **Placement / quota** | **kvnloo/kerdoios** | #55, #50, #51 | semantic suitability |
| **Authority** (intent, budgets, structural legality) | **kvnloo/aodl** (contract) + z0int `dispatch_authority` (enforcement) + deterministic policy | #13/#47/#55, aodl#35; "model confidence is not permission" | mutable belief |
| **Architecture map / registry** | kvnloo/z0 (map only) | AGENTS.md rule 8: "Never add runtime code here" | runtime |
| **Derived projection** | kvnloo/z0archy | z0archy#27 | authority |

**Tension to record (INFERRED).** `components.yaml` lists z0intelligence as `not_here: [… experiment promotion …]`, while the #56 addendum puts stage 7 "Promotion … z0intelligence (this RFC); owner approval". The reading that reconciles both: Evolution Lab produces the promotion *evidence*, z0int owns the *runtime contract* a promoted mechanism must carry, and the owner owns the *decision*.

**Registry gaps** (VERIFIED absent from `suite.yaml` / `components.yaml`): kvnloo/bend-native, AgentsView, kvnloo/hermes-jev-skills `hermes-jev/omp-adapter` (the DSH receipt writer), the z0int `harness-adapters/*` plugins, and the Claude Code harness (`harnesses.yaml` has omp/hermes/deepseek/codex/pi only). z0#16 tracks identity drift. Registry edits belong to kvnloo/z0 and are out of scope for a build that must not touch z0 runtime.

### 3b. Per-harness capture and memory status

Seam names are VERIFIED from the code and issues read; the status column extends prior findings.

| Harness | Capture vehicle (z0-owned) | Capture status | Memory path | Memory status |
|---|---|---|---|---|
| Claude Code | `claude-code-z0intelligence` hooks → `~/.z0int/state/claude-code/*` | **Live** (M0, `feat/shadow-loop-v0` 6fee859, pushed, no PR) | `.mcp.json` has only `z0intelligence` (route_worker) | AgentsView MCP not configured here (INFERRED from `.mcp.json`) |
| Hermes | `hermes-z0int-decisions` plugin on master (pre/post_llm_call, on_session_end, pre_approval_request → `$Z0INT_HOME/state/hermes/{opportunities,outcomes}.jsonl`) **or** bend-native observer | **Not enabled.** Live profile enables `hermes-z0intelligence` (automatic) only. VERIFIED from redacted `~/.hermes/config.yaml` `plugins.enabled`: `[hermes-handoff, dashboard_auth/basic, hermes-agent-cluster, kanban, hermes-z0intelligence]`. `automatic.json` hermes=off (prior findings). | `pre_llm_call` `{"context":…}` injection (#320); memory-provider plugin | Live toolsets include **`no_mcp`** (VERIFIED), so the MCP route is off in the live profile. `memory.provider: memory_tencentdb`; owner saw "configured but not installed" on 09-29. Smoke-only proof on `study/hermes-unified-memory-56@c84663880a` (local branch; full #56 not passed). |
| OMP | `omp-extensions/z0int-bridge` (v1 spine → `cognition-shadow.jsonl` 7,077 rows, **no consumer**) | DecisionOpportunity emitter per oh-my-pi#109: **not built** | #107 context-extension (fork PR, shadow default, 15/15 tests), AgentsView lane failed on a symlink | Needs `AGENTSVIEW_DATA_DIR`; relocate #107 into z0int (INFERRED) |
| DSH | `dsh-z0intelligence` is **automatic routing** (can replace output). `hermes-jev-skills hermes-jev/omp-adapter@9ea5777` writes a private `~/.dsh/jev/receipts.jsonl` (13,192 rows, no consumer) | **No DecisionOpportunity emitter** (#62) | `agentsview mcp` overlay (DSH PR #2, config) + profile `cordis.patch.yml` | GREEN for one real `search_sessions` call (owner 09-26). z0evals#56 lane runner `dca8fd2` is unpushed. |
| Codex | `z0intelligence@personal` plugin (route_worker) | No capture hooks found (INFERRED: transcript-derived only, via AgentsView, which indexes 1,690 codex sessions) | MCP config | Not verified |
| OMO / OpenCode / Grok / others | — | not wired | OMO session_search (z0evals#56 comment 09-29, `534b966`) | OMO lane GREEN for search only |

### 3c. Evaluation of kvnloo/bend-native as the Hermes capture vehicle

**What it gets right** (VERIFIED, SYNTHESIS + README):
- It is a standalone native plugin. No Hermes core change: H3a 106/106, and the 0-Hermes-built-divergence H1 design.
- Shadow defaults to off. Hooks return None, with 0 block directives in 122 runs (H2). Profile isolation holds (H4). No raw content is stored (H5). A disabled plugin leaves zero footprint (H6).
- It bundles the #385 observer, the #386/#387 scorer and evaluator, and z0's StatePacket and DecisionOpportunity builders, pinned by SHA in `stack/sources.json`.
- It calls the canonical z0 service API unchanged and adds no second ledger or scheduler.
- It is exactly the shape the owner's 10-03 direction asks for.

**Why it is not ready as-is** (SYNTHESIS §8, "REVISE"):
- P-1, high: the bridge port defaults to the live service.
- P-2: `close()` cannot stop a full-queue worker.
- P-3: drops are not persisted, which violates "missing telemetry ≠ zero".
- P-4: FAIL receipts cannot be replayed.
- P-5: `score` strips `HF_HUB_OFFLINE` and the checkpoint env.
- P-6: `evaluate` is hard-wired to `api.attempt_will_fail`, which is a DEGENERATE question (0/653).
- P-7: projection reads the process cwd, so the gate says ACT with unknown facts. That would poison training labels.
- P-9: undocumented persistence of README, commit-subject and branch text.
- P-8, P-10, P-11, P-12 are lower severity.

**Record-shape mismatch.** INFERRED from code read: bend-native writes `plugin-data/bend/z0/` (observer rows, API-attempt examples, `opportunities.jsonl`). The z0int loop reads `$Z0INT_HOME/state/<harness>/{opportunities,outcomes,outcomes_verified}.jsonl`. #62 requires one semantic record across harnesses.

**Packaging concern** (INFERRED): z0 capture is bundled inside a *Bend proof* plugin. That couples two lifecycles: Bend release qualification (#389) and capture correctness. Its `stack/vendor/z0int/*` copies of `decision_opportunity.py`, `state_packet.py` and `context_resolve.py` can drift from z0int master.

**Recommendation for the build planner** (INFERRED; the owner should confirm):
- **Primary Hermes capture vehicle:** z0int's own `harness-adapters/hermes-z0int-decisions`. It writes the same record layout the Claude Code loop already verifies and exports, it lives in a z0-owned repo, and it needs only `plugins.enabled` plus `$Z0INT_HOME/config/hermes.json`.
- **bend-native:** keep it as the Bend verifier, the API-attempt observer and the `hermes z0` scorer, after the P-1..P-7 fix PR on kvnloo/bend-native (SYNTHESIS §11 step 1). Configure `stack_opportunities: false` wherever the z0int decisions plugin is enabled, so opportunities are not captured twice.
- **If the owner prefers bend-native as the single vehicle:** gate it on P-1, P-2, P-3 and P-7 plus a projection into the shared `state/hermes/` record, each with its lane packet as the regression fixture.

Either way, **do not propose a third vehicle.**

---

## 4. Owner rules that constrain the implementation

Quoted where the source has exact words. Each rule maps to a TDD assertion where possible.

**Harness and repository boundaries**
1. **No harness core changes; integrate through seams only.** "NO harness needs core code changes … integration code living in z0-owned repos … Turning it on is a config/install step" (direction 10-03). "Do NOT make or merge a PR into DeepSeek Harness core" (09-21). "Everything experimentally intelligent lives behind extensions" (09-19). NeMo RFC stop condition: "adapters require invasive forks of harness loops when stable extension seams exist". Hermes #320: "Do not patch system prompts or transcript persistence."
2. **Routing, quota, measurement and learning live in the Zer0 plugin ecosystem.** "Provider routing, quota allocation, measurement, and learning belong in the Zer0 plugin ecosystem." Owners: DSH = execution; z0int = semantic selection; Kerdoios = capacity/quota; Tokenomics = usage/outcome; Evolution Lab = training (09-21). "Do not create another static model registry."

**Shadow-first, authority and verification**

3. **Shadow first; no authority from shadows.** "Keep this integration shadow-first." "JEV is an observer/backend/reference lane, **not ground truth or authority**" (z0#15). "Shadows are inert. A challenger's decision is recorded and never shown to the model, the user or a tool" (#56). "do NOT let NanoJev affect production behavior yet" (09-21). "Do NOT add Jev execution authority. Do NOT auto-route" (09-19 P0.7).
4. **The shadow hot path is free.** "Jev shadow work must not materially delay the root provider request." Every detached promise must have `.catch`. "root turn continues - no unhandledRejection" (09-19 P0.7A).
5. **Separation of concepts.** "evidence ≠ belief/state ≠ authority ≠ action ≠ outcome" (#53); extended in #66 with "≠ learned procedure". "**execution completion is not verified success**." "**missing telemetry ≠ zero**" (tokenomics#19). "Unknown stays unknown. Partial measurement stays partial" (z0int README). "Code existence is not capability proof" (z0 RFC). "activity is not savings" (09-19).
6. **Verified ≠ completed ≠ configured.** "adapter exists / MCP configured / extension present / seam identified" ≠ wired (09-25). "I'm not calling DSH green until that real local call happens" (09-26). "No harness counts as integrated merely because indexing/configuration exists" (#22). "Do not count any lane as passing until its emitted rows validate against … receipt.schema.json on main" (z0evals#56).
7. **Live tests are mandatory.** "u didn't even run a single live test?? … so that i dont spend 1 week 'collecting data' for nothing" (09-18).

**Memory**

8. **No second memory backend or framework.** "Your task is not to design another memory architecture" (09-22). "No new memory service, no `:8791`" (09-26). "do not resurrect the dead :8791 path, create another memory DB, or redesign routing" (09-29). #63 non-goals: no other vector DB, no other canonical conversation store, no autonomous background compression daemon, no automatic migration. #23: adopt existing capabilities as evidence providers, "no new universal memory DB / vector DB / graph DB / scheduler". The LongMemEval bakeoff kept the current z0 resolver; Mem0 and Sibyl were rejected (prior findings). #66: "Do not create a second outcome/receipt system"; "reuse the existing routine compiler" (no second procedural memory).
9. **Memory is data, never instruction authority.** "`instruction_capability=True` is rejected." "Workers cannot directly write semantic/global beliefs"; "do not give Hermes direct write access to the canonical life log" (#63). Scope filtering happens before ranking (#66).
10. **No double injection.** "Proxy can inject memory - resolver must default **allow_memory=False** to avoid double-inject" (critical-path-phase0). `context_resolve` has recall off by default.
11. **A fast cached state must be able to abstain.** "A fast cached State Packet is dangerous unless it can say **'I do not know enough, go back down the stack.'**" (09-22 18:02). The supporting result: warm reuse was 99.5% cheaper, but 6/12 vs 12/12 correct, and every wrong cached answer reported `hit=true`.

**Privacy and data**

12. **Privacy.**
    - "make sure we aren't committing the weights bc it's trained on my personal data" (prior findings).
    - Training rows never contain prompt, response, command, path or claim text (#56).
    - "raw private conversation text stays local" (#22). "Never freeze raw private conversation text" (z0evals#56). "Do not upload or commit `raw/`" (#94).
    - Never print secrets. Keep `require_auth = true` (09-22).
    - "no hosted system receives private corpus without explicit authorization" (#23).

**Promotion and process**

13. **No automatic promotion.** Owner approval per candidate per family; "automatic promotion without owner approval" is a non-goal; no "live mutation of production config or prompts" (#56). "Do not flip z0int-bridge `execution: log_only` to live until host consumes runnable results" (critical-path-phase0).
14. **Respect the governed-dispatch stop.** "If this does not pass cleanly, **do not widen the canary to DSH/Hermes or automatic routing**" (#94).
15. **Stage, don't promote, on upstream forks** (owner memory `feedback-stage-dont-promote`).
    - Never open or post anything upstream (NousResearch/hermes-agent etc.). Stage on the fork as pushed branches plus PR bodies.
    - Break `owner/repo#N` autolinks in fork content.
    - `--no-follow-tags`.
    - This is why the bend-stack ADDR commits are held (#319 comment).
16. **One NanoJev runtime.** "Do not invent a second NanoJev runtime" (prior findings).
17. **Minimal surface.** Use the smallest implementation that keeps the behaviour. Question every cache and timer by naming the invalidation event (owner memory `feedback-minimal-surface`). "Do not build another Decider daemon or HTTP service. Extend the existing bridge" (09-19). "Do not build another LLM gateway or scheduler" (#47).
18. **Salvage, don't merge wholesale.** "Teknium-style salvage": treat old branches as carriers of behaviour (09-26 23:45). Example: the #10 and #21 stacked branches were made draft as "unsafe as a merge unit" (09-26 14:14).
19. **Setup is deterministic code, not LLM recall.** "clone → ask your agent to onboard → working self-improving z0int"; "Deterministic setup/training logic lives in code" (09-18).
20. **Sufficiency before search.** Below the thresholds, every comparison is `INSUFFICIENT_DATA`, and a population "would only fit noise" (#56). Simple controls always stay in the population; "the simplest candidate that clears the frozen gate wins" (#14).

---

## 5. What "done" means: acceptance checklists for verification

### D1. "Data collection for all harnesses" is done when, per harness (Claude Code, Hermes, OMP, DSH, and Codex where a seam exists):

- [ ] The capture plugin/extension comes from a z0-owned repo or a standalone plugin repo. Turn-on is config/install only. `git diff` of the harness tree = 0 files.
- [ ] One REAL turn produces opportunity + outcome rows joined on `trace_id/turn_id`, in the shared record under `$Z0INT_HOME/state/<harness>/`.
- [ ] On/off produces an identical harness-built first request. Hooks return None or nothing.
- [ ] Backend down → harness exit 0, rows receipted as unavailable, 0 rows dropped silently. Drops persisted and reported.
- [ ] Hook callback latency is measured and not on the provider critical path.
- [ ] Unload/close is bounded and no worker writes after unload.
- [ ] Stable IDs survive restart and replay. Duplicates are detected.
- [ ] System, cron and subagent messages are excluded or cohort-tagged and never pooled with interactive traffic (#56 M1).
- [ ] No raw prompt, response or path text reaches any training/export artifact.
- [ ] The verifier and exporter handle this harness's transcripts. Today `outcome_verifier.HARNESS = 'claude-code'` and `loop_export.HARNESS = 'claude-code'`, so they need generalising (gap G2).
- [ ] It runs **automatically at all times** (the owner's words), with no manual collection step. INFERRED: hooks are always on, and the verify/export/join sweep is scheduled by deterministic code (#56 M1).
- [ ] The live z0 service, `~/.hermes` and secrets are never touched by tests.

### D2. "Shadow procs automatically learning at all times" is done when:

- [ ] M1: privacy-safe tables merge across hosts by `turn_key`. Cohorts (`interactive`/`agent`/`harness`/`unknown`, per `loop_export.COHORTS`) are kept separate. Resolution oracles exist where `unverified` dominates.
- [ ] G-SUFF passes on at least one cohort. Until then, every report says `INSUFFICIENT_DATA`, and that counts as an honest pass of the pipeline, not of learning.
- [ ] M2 shadow slot: registered challengers load read-only and decide per opportunity. Decisions are recorded, never shown, and latency is measured.
- [ ] M3: Evolution Lab search reads only the z0evals score API. The protected judge sits outside the evolvable surface. Every negative result is kept. Tokenomics budget accounting is included.
- [ ] Simple controls (deterministic gate, unconditional escalate, base-rate constant) are always in the population.
- [ ] Per #319/#14, add a discrimination metric and a base-rate reference. "No row discriminates" was the 10-02 finding.
- [ ] M4: promotion needs the owner. No automatic promotion. A promotion carries the full #56 contract fields.
- [ ] M5: a drift drill forces fallback and demotion, and it is recorded.
- [ ] No weights trained on personal data are committed anywhere.

### D3. "Unified memory fully working and wired up across all harnesses" is done when, per harness:

- [ ] There is one recall surface. AgentsView MCP (read-only) and/or the z0int `context_resolve` → StatePacket router are reachable as a tool in the harness. No new memory service, DB or port.
- [ ] z0evals#56 cases A–F pass on the frozen `cohort.json`, with rows validating against `receipt.schema.json` on main.
- [ ] One real supported answer from injected evidence, one abstention, one idempotent replay, one supersession (#22).
- [ ] Provenance identifies source, harness, session, timestamp and a stable locator (owner 09-22).
- [ ] Cross-product recall works: a Hermes session can retrieve a Claude, ChatGPT, OMP or Codex conversation (owner 09-22 DoD).
- [ ] AgentsView `require_auth` stays on. The data dir comes from `AGENTSVIEW_DATA_DIR`; the 20 GB corpus is never moved or copied.
- [ ] Index freshness is visible. VERIFIED gap: the last indexed session is 2026-10-01 and sync/daemon is not running. Enabling sync is an owner-approved config step; the build must not start it.
- [ ] No double injection (resolver memory off while a provider proxy injects). The cache invalidates on source-revision change.
- [ ] Hermes route: live toolsets contain `no_mcp`, so either the owner enables MCP for that profile (config) or Hermes memory goes through the `pre_llm_call` plugin seam. Either is config/plugin only. INFERRED.
- [ ] TencentDB: Hermes `memory_tencentdb` is installed as a plugin, or the provider config is changed. It stays a semantic projection with provenance and is never a canonical transcript. The 8 s call stays off the critical path.
- [ ] Automatic injection, if enabled, went shadow → frozen-cohort canary → on, and preserves a fallback to native context (#320).

### D4. Memory control plane (W4) is done when:

- [ ] The G-#63 checklist (above) is green, including the adapters checklist: `projections/fts5`, `projections/tencentdb`, `adapters/agentsview` (historical import → canonical events with `EventIdentity`), `adapters/dsh` (inject StatePacket → bounded receipt → event), `adapters/hermes` (same, no raw life-log access).
- [ ] The #66 contract tests stay green. Memory-use data attaches to `DecisionReceipt.extra` without a schema change, in real OMP/Hermes/DSH receipts.
- [ ] No production memory store is migrated or mutated.

### D5. Governed dispatch to Hermes/DSH (out of scope until G-#95)

- [ ] #94 PASS bundle + z0evals#82 import green + every #95 checklist box, then a new packet that uses the same contract.

---

## 6. Gaps found (for the build planner)

- **G1, Hermes capture not enabled.** The z0int `hermes-z0int-decisions` plugin exists on master, but the live profile enables only `hermes-z0intelligence` (automatic; INFERRED to be effectively a pass-through while `automatic.json` hermes=off). The capture vehicle decision is still open (§3c).
- **G2, verifier/exporter are Claude Code only.** `outcome_verifier.py:46` and `loop_export.py:38` hard-code `HARNESS = 'claude-code'` (VERIFIED on 6fee859). There are no Hermes, OMP, DSH or Codex outcome resolvers in the loop.
- **G3, OMP v1 spine has no consumer.** `cognition-shadow.jsonl` (7,077 rows) and `receipts/decisions.jsonl` are not in the #62 record. oh-my-pi#109 is unbuilt.
- **G4, no observe-only DSH emitter.** The master DSH plugin is an active router. DSH JEV receipts use a private schema and have no outcomes (#62). DSH Issues are disabled, so DSH work is tracked in z0int#62.
- **G5, #63 adapters checklist has no code on master** (fts5, tencentdb, agentsview, dsh, hermes adapters).
- **G6, AgentsView is stale** (sync not running), and there is no `dsh` agent in AgentsView.
- **G7, Hermes MCP path is off.** Live toolsets contain `no_mcp`, and the TencentDB provider install state is unknown.
- **G8, bend-native needs the REVISE work.** P-1..P-12 unfixed. P-1 is a safety blocker for any install.
- **G9, integration code lives in harness forks.** Hermes `lab/z0_hermes_observer/` (#385-387), Hermes `study/hermes-unified-memory-56` and `lab/track4/rlm-state-shadow-v0`, and OMP #107 should be re-homed (§0).
- **G10, service revision drift.** The registry pins z0int `0563ed7`, master is `0159808`, and the live service runs `a9cbbed` (closed PR #35, no decision_opportunity, state_packet, outcome_observation or memory_contract, per prior findings). bend-native tested `0563ed7a`, while the bend-stack lanes used dev `6764ae78`. INFERRED: a capture plugin that calls the live service API may hit endpoints the live service lacks. File-spool capture, as in hermes-z0int-decisions and Claude Code, avoids that.
- **G11, registry gaps.** bend-native, AgentsView, the Claude Code harness and the z0int harness-adapters are missing from `registry/*.yaml` (z0#16).
- **G12, governed gate closed.** #94 has no PASS and z0evals#82 is open, so no `route_worker` or automatic-routing exposure to Hermes or DSH.
