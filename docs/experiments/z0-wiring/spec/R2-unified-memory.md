# R2: Unified memory system survey (read-only)

Survey date: 2026-10-03. Surveyor R2 of 4. Read-only, except this file and `spec/scratch/r2/`.
This survey builds on `/mnt/zer0models/z0-wt/wiring/prior-findings.md`. §5.1 corrects several of its statements.
Anything marked **INFERRED** was not verified directly.

---

## 0. Answer in one page

**The owner's question was: "it shouldn't require any changes to hermes directly, ya? it should just be a plugin / mcp / api?"**
Yes. Every harness already has the seams that unified memory needs:

| Harness | Seam |
| --- | --- |
| Hermes | general-plugin `pre_llm_call` returning `{"context": ...}`, plus `post_llm_call` and `on_session_end` |
| DSH | Cordis plugin `agent/pre-step` waterfall and `inject()`, plus the `dsh-mcp-client` MCP client |
| OMP | extension API (`before_agent_start`, `context`, `agent_end`, `registerTool`) plus MCP |
| Claude Code | plugin hooks (`SessionStart`, `UserPromptSubmit`, `Stop`) plus MCP |
| Codex | plugin MCP, with `features.hooks = true` |

Every seam is reachable from a z0-owned repo, and turning each one on is a config or install step.

Two pieces of existing work go against this direction. Neither is needed:
- The Hermes #56 lane `c84663880a` adds `agent/memory_packet_cache.py` to Hermes core. It must be moved into a plugin.
- The staged Hermes core metric #417 is the second one.

**What "unified memory fully working and wired up across all harnesses" means** comes from z0int #22, #23, #53, #63 and #66, plus z0evals #56. In short:

- **One z0 memory surface.** Every client reaches memory through the same z0int MCP/API surface (#23 acceptance).
- **The layers are projections over evidence:**
  - EPISODIC: the immutable z0 EventLog plus the OptMem temporal tree.
  - RETRIEVAL: FTS5 and AgentsView.
  - SEMANTIC: TencentDB L1/L2/L3.
  - WORKING: the StatePacket and DecisionOpportunity, used as a query-time router and reducer, never as a store.
  - PROCEDURAL: the existing routine compiler.
- **Identity:** `EventIdentity.event_uid` is stable under replay.
- **Scope:** filtering by the scope chain global → user → project → repo → task happens before ranking.
- **Time:** claims are bitemporal, and supersession is append-only.
- **Snapshots:** every memory-assisted decision binds a content-addressed `MemorySnapshot`. The memory-use data goes into the existing `DecisionReceipt.extra.memory`.
- **Proof per harness:** each harness must pass the z0evals #56 tests A–F (retrieval, model-visible injection, supported use, abstention, idempotent replay, supersession) on the frozen 6-case cohort.

**Canonical backend:** there is no single new backend. Truth stays in the sources:
- harness transcripts stay in their native stores;
- z0's own events go in the z0int EventLog `events.jsonl`;
- the canonical cross-harness lexical index is AgentsView `sessions.db` (FTS5), not a source of truth;
- the canonical semantic layer is TencentDB.

z0int owns the contract (`z0int.memory_contract.v1`), the reducer (StatePacket) and the one harness-facing surface. **Do not build** another vector, graph or "universal memory" DB, a second conversation store, a second receipt format, a second procedural/skill compiler, or a background compression daemon. Do not switch on AgentsView `recall extract` as a parallel semantic store, and do not adopt Mem0/Sibyl/Graphiti (§3).

**Status today: mostly unwired.**
- The primitives are on master: the contract, EventLog, OptMemTree, state_packet, context_resolve and decision_opportunity, landed by squash `b345aa2` (#109). None of the memory primitives has a consumer.
- `context_resolve` with `kind="memory"` never retrieves anything.
- Master exposes no `orient`/`recall` API or MCP tool. The #22 surface only exists on the unmerged branch `consolidate/z0-kernel-20260922@dcbcc88`.
- AgentsView is stale since 2026-10-01 07:17Z. Its daemon is on-demand and shut down on idle timeout.
- The installed binary is **v0.39.0**, but the DB is already at a ≥v0.40 schema. That causes 23,619 `cost_usd` write errors and 263 sync failures.
- v0.39 has **no DeepSeek-Harness parser** (added in v0.41.0) and does not index `~/.claude-home/projects`.
- AgentsView's own recall layer is empty (`recall_entries` = 0).
- The TencentDB gateway (:8420) is not listening on the host. Nothing outside Hermes reaches TencentDB.
- **Memory-use receipts: 0** across ~4.2k receipt rows.

---

## 1. Sources read (pinned)

| Source | Revision / location | Notes |
| --- | --- | --- |
| kvnloo/z0intelligence master | `0159808` (#110), memory integrated by squash `b345aa2` (#109) | `docs/memory-control-plane-contract.md`, `src/z0int/{memory_contract,context_resolve,state_packet,decision_opportunity,intelligence_mcp,intelligence_service,harness_id,receipt,claude_code}.py`, `src/z0int/memory/{event_log,optmem_tree}.py`, `harness-adapters/*` |
| z0int branch `consolidate/z0-kernel-20260922` | `dcbcc88` (2026-09-23), 71 ahead, **not merged, not in the #109 list** | `src/z0int/mcp_server.py` (resolve/orient/history/inspect/unknowns/verify), `src/z0int/context_providers.py` (1186 lines), `benchmarks/memory_bakeoff/README.md` |
| z0int branches | `feat/shadow-loop-v0@6fee859`, `experiment/state-packet-scope-contract@a32093b` (ThermoContext, not memory), `feat/packet-gating-v0@04b9186` | packet-gating: "gated packet fails both pre-registered tests" (Claude Code savings v1b) |
| Issues z0int | #22, #23, #53, #62, #63, #66 (bodies + comments) | PRs #67/#68/#69 closed, heads integrated via #109 |
| kvnloo/z0evals | main `fb14919`; branches `study/unified-memory-56-dsh-runner@dca8fd2`, `study/executable-unified-memory-56@667223e`, `readiness/unified-memory-cohort-integrity@90023c4`, `study/unified-memory-cohort-v0@15428ba` (squash-merged as `cohort.json`) | issues #56, #57 (PR), #59 (PR), #71, #73 |
| Hermes #56 lane | `study/hermes-unified-memory-56@c84663880a` (local only: `~/zer0/oss/hermes-unified-memory-56`; not on the fork remote) | adds `agent/memory_packet_cache.py` **in Hermes core** plus `evals/unified_memory/*` |
| Hermes fork | `/mnt/zer0models/hermes-wt/bend-integration@ad31bbf079` | `agent/memory_provider.py` (one external provider at a time), `plugins/memory/__init__.py` discovery (bundled / `$HERMES_HOME/plugins` / project / pip entry point) |
| kvnloo/hermes-agent issues | #320 (RLM State Packet consumer), #417 (staged core metric) | branch `lab/track4/rlm-state-shadow-v0` does not exist on the remote |
| kvnloo/deepseek-harness | `70215673fe` (PR #4), PR #2 merge `6dd3aa37` (`apps/cli/config/examples/mcp-memory/agentsview.cordis.yml`), PR #3/#4 `zer0.repo.yaml` | seams: `durable-agent-inbox` and `pre-step-admission` (`packages/core/agent-loop/src/agent.ts`, `inbox.ts`, `packages/core/agent/src/dispatch.ts`) |
| hermes-jev-skills | `origin/hermes-jev/omp-adapter`: `dsh/plugin/memory.js` (191 lines) | reads Hermes via the OMP `hermes-memory/history.py`, plus the TencentDB gateway |
| kvnloo/agentsview fork | main = upstream minus 58 commits (0 ahead); branches `feat/omo-source@c275a2d6`, `feat/agent-memory-recall-behavior@057f3ca5`, `feat/agent-memory-search-contract@53e87c17` | upstream `kenn-io/agentsview@1148d7f2`, latest tag v0.44.0 (2026-09-21) |
| AgentsView DB | `/mnt/zer0models/sft-svlm/data/agentsview/sessions.db` (19.9 GB, opened read-only) | §4.2 |
| TencentDB-Agent-Memory | `/mnt/zer0models/oss/TencentDB-Agent-Memory@0aff21a` (v2.0.0) | `MemoryCore/hermes-plugin/memory/memory_tencentdb/` = the Hermes provider (gateway :8420, supervisor) |
| kvnloo/bend-native | `/mnt/zer0models/z0-wt/ro/bend-native@e85e65e` (v0.4.0) | `stack/STACK.md`, `plugin.yaml`, `stack/vendor/z0int/*`; bugs: `/mnt/zer0models/github/cua-lanes/artifacts/bend-stack/SYNTHESIS.md` |
| kvnloo/z0 registry | `7933914` | `mechanisms.unified-memory-evidence-path`, `evidence_dependencies.unified-memory-preserves-provenance`, `components.z0intelligence.not_here: [source memory databases]` |
| Live configs (non-secret) | `~/.hermes/config.yaml` (memory/plugins/toolsets only), `~/.omp/agent/{config.yml,mcp.json}`, `~/.dsh/profiles/*/cordis.patch.yml`, `~/.codex/config.toml`, `~/.claude*/settings.json`, `~/.claude.json` | secrets redacted, none printed |
| Untracked 09-22 tools | `/mnt/zer0models/workspace/zer0/oss/tools/{recall_mcp.py,statepack.py}` (not in git) | the original "universal recall" |
| Forks checked for relevance | kvnloo/supermemory (no z0 branches: **not relevant**), kvnloo/hermes-lcm (Hermes context-engine plugin; overlaps OptMem for Hermes-local compression), kvnloo/OptMem (upstream design donor) | |

---

## 2. What the RFCs define

### 2.1 Layers (#63; master README "Memory foundations")

```
EPISODIC     "What happened?"           immutable EventLog (events.jsonl) + OptMem age-decay tree
RETRIEVAL    "Where did we discuss X?"  FTS5 + AgentsView
SEMANTIC     "What have we learned?"    TencentDB L1 atomic facts / L2 scenarios / L3 profiles
WORKING      "What matters right now?"  StatePacket (query-time memory router, not a store)
PROCEDURAL   verified repeated episodes -> existing routine compiler (#66 P2)
```

The canonical storage layout in #63 is `events.jsonl` (immutable truth), `TREE/` (rebuildable), `blobs/`, and `indexes/{fts5,agentsview,tencentdb}`. Every derived item carries `source_event_ids`, `source_session`, `project`, `created_at`, `extractor_revision` and `confidence`.

StatePacket routing (#63):

| Query need | Route |
| --- | --- |
| default life context | OptMem cover |
| exact phrase or date | FTS5 / AgentsView |
| facts or preferences | TencentDB L1/L3 |
| prior approach or outcome | TencentDB L2 |
| source detail | `zoom(raw events)` |
| repo state | git / AODL |
| live state | worker |

`MemoryPacketCache` is only a cache. Its key is the intent hash, project, OptMem tree revision, TencentDB revision, AgentsView generation and relevant repo SHA.

### 2.2 Sources

These come from #22, #23, #56 and #63:
- Raw transcripts and files stay authoritative.
- AgentsView, FTS and TencentDB are *evidence capabilities*.
- The #22 P0 slice uses three adapters: current GitHub project/contribution evidence, one local conversation/history source, and repository docs/state.
- #23 says "Reuse the universal recall substrate as source-of-truth". If engines need ingestion, expose one deterministic, idempotent, rebuildable *derived event stream*, never a new canonical raw DB.

### 2.3 Identity (#66 P0; `memory_contract.py`)

- **Event identity:** `event_uid = "evt_" + sha256("event_uid.v1" ‖ {source_system, source_session, source_event_id | source_seq})[:32]`. A source-native ID beats `source_seq`. `payload_hash` is kept separate and only detects conflicts. `ledger_seq` is local ordering and never part of identity. (`derive_event_uid`, memory_contract.py:55-94.)
- **Scope:** `MemoryScope` global → user → project → repo → task. A request may see ancestors only; sibling scopes are rejected **before** ranking.
- **Harness identity:** `HarnessIdentity{harness_id, session_id, trace_id, turn_id, ...}` (`harness_id.py`). AgentsView session IDs carry an agent prefix such as `hermes:20260930_221128_28b40b` or `omo:...`. The locators on the consolidate branch are `agentsview:<sid>#<mid>`.
- **Decision identity:** `DecisionOpportunity` and trace/work-item/attempt IDs (#53, #62).

### 2.4 Temporal model

- **EventLog:** append-only, monotonic `event_id`, `ts`, checksums, blobs over 16 KiB, crash-safe. A malformed committed line fails closed.
- **OptMem:** aligned dyadic age-decay cover over coarse history. The raw tail stays verbatim, `zoom(lo, hi)` is exact, `forget` plus rebuild never mutates `events.jsonl`, and there is one merge between turns.
- **Claims:** bitemporal (`observed_at`/`recorded_at` vs `valid_from`/`valid_to`), lifecycle unknown → provisional → observed → verified, or → contradicted / stale. Supersession uses `superseded_by` and never overwrites. `verified` requires evidence event IDs. Confidence is metadata only.
- **Packets:** a StatePacket is keyed by source revisions. Any change makes it stale, and a stale packet cannot authorize a transition (#22). `MemorySnapshot.id` is content-addressed and excludes `created_at`.

### 2.5 What each harness must read and write

**Read:**
- A bounded, scope-filtered State Packet or evidence bundle **in the next model-visible context**. Indexing or configuration alone does not count (#56 test B; z0 registry invalidator "A configured index is treated as proof of model-visible or answer-supported memory").
- Pull tools for detail: `orient`, `inspect`, `explore`, `unknowns`, `verify`, `history` (#22 P0 API).

**Write:**
- **Workers cannot write semantic or global beliefs directly.** They return evidence plus bounded receipts. z0 appends those as events, and promotion happens downstream (#63 invariants 4-5).
- Hermes gets "a z0 memory adapter for bounded retrieval, but no direct write access to the canonical life log" (#63).
- Transcripts are captured passively by their native stores and indexed by AgentsView.
- Memory use is recorded as `MemoryUseReceipt.to_decision_extra()` → `DecisionReceipt.extra.memory` (#66 P1). No new receipt system.

**Per-harness worker protocol (#63):** StatePacket → harness inject seam → worker executes → bounded receipt → `Event(worker_result)`.

### 2.6 Invariants and non-goals (verbatim intent)

From #63:
- `events.jsonl` is append-only, and TREE is rebuildable.
- Every summary or semantic memory traces to source event IDs.
- There is no duplicate source of truth across Hermes, DSH, TencentDB, AgentsView or FTS5.
- The master has no direct fs/git/browser tools and works within a hard token budget.

From #66: memory is always data, and `instruction_capability=True` is rejected.

From #22: no new canonical raw-data store and no second scheduler. Learned selectors are not authoritative before evaluation.

Non-goals:
- #22, #23, #63, #66: another vector/graph DB, another canonical conversation store, an autonomous background compression daemon, a global ontology, another receipt format, another routine/skill compiler, and automatic migration of private history or production stores during P0.

---

## 3. Canonical backend and what must NOT be built

### 3.1 Canonical assignments

1. **Source truth:** each harness's own transcript store (Claude/Codex JSONL, Hermes `state.db`, OMP sessions, DSH `session.v3.jsonl.zstd`), plus the z0int **EventLog** for z0-originated events: master turns, worker receipts and memory-use/decision events.
   - **INFERRED reconciliation:** the z0 registry says z0intelligence does *not* own "source memory databases", while #63 makes z0int own `events.jsonl`. These are consistent only if harness transcript **bodies are not copied** into `events.jsonl`. Imports should record `EventIdentity` plus locators (references) and leave bodies in their stores. A full-body import would create the forbidden duplicate source of truth (#63 invariant 10).
2. **Retrieval index:** AgentsView `sessions.db` (FTS5 `messages_fts`, `tool_calls`). It is derived and rebuildable.
3. **Semantic layer:** TencentDB (Hermes `memory_tencentdb` provider; gateway :8420; OMP `tencentdb-memory` extension).
   - TencentDB L0 conversation capture must stay a *derived mirror* or carry `z0_event_id` references (#63).
4. **Working state:** z0int StatePacket and ContextPacket (`z0int.context_resolve.v1`, `z0int.state_packet.v0`) with DecisionOpportunity. It is a router, not a store.
5. **Contract:** `z0int.memory_contract.v1`.
6. **Harness surface:** one z0int MCP/API intelligence surface (#23: "all clients still go through the same z0int/MCP intelligence surface").

### 3.2 Do NOT build

- A new universal memory DB, vector DB, graph DB or scheduler (#22, #23, #63, #66).
- A second canonical conversation store, including making TencentDB L0 or EventLog full-body imports a second transcript (#63).
- A second receipt format. Memory-use data goes in `DecisionReceipt.extra.memory` (#66).
- A second procedural/skill learning path. Use the existing routine compiler (#66 P2; contract doc slice 6).
- A background compression daemon, or OptMem bolted into Hermes/DSH/OMP transcript history (#63).
- **AgentsView `recall extract` as a parallel semantic layer.** **INFERRED** from #63 SEMANTIC → TencentDB. `recall_entries` is empty today. Turning it on would create a second LLM-distilled fact store that competes with TencentDB and needs model calls.
- Mem0, Sibyl or Graphiti adoption. The 09-23 LongMemEval bakeoff on `consolidate/z0-kernel-20260922` (`benchmarks/memory_bakeoff/README.md`):
  - kept current z0 FTS5 at rA@10 0.861 / MRR 0.752 and 1.9 ms per query;
  - Mem0 scored 0.833 / 0.652 and was **REJECT**;
  - Sibyl scored 0.778 / 0.579 and was **REJECT**;
  - Graphiti was **BLOCKED**.
- **Per-harness private memory readers** as the integration path. These exist today and should be retired behind the z0 surface (INFERRED, plus the hard rules forbid live `state.db` access):
  - OMP `~/.omp/agent/extensions/hermes-memory/history.py` opens `/workspace/hermes-home/state.db` and profile DBs directly.
  - DSH `memory.js` reuses that reader.
  - consolidate `context_providers.lexical_hermes` globs `/workspace/hermes-home/profiles/*/state.db`.
- Memory code inside harness forks: `agent/memory_packet_cache.py` in Hermes core (`c84663880a`), and the OMP #107 extension under `packages/coding-agent/examples/extensions/` in the oh-my-pi fork. Re-home both into z0-owned repos.
- A z0 `MemoryProvider` that displaces `memory_tencentdb`. Hermes allows **one** external provider (`agent/memory_provider.py` docstring). **INFERRED:** keep TencentDB in that slot and inject z0 packets through a general plugin's `pre_llm_call`.
- Turning `allow_memory=True` on while the TencentDB proxy/prefetch also injects on the same turn ("double-inject", `context_resolve.py:298,396`; `docs/critical-path-phase0.md`).

---

## 4. What exists and works today (verified)

### 4.1 z0int master (`0159808`)

| Piece | File | State |
| --- | --- | --- |
| Memory contract | `src/z0int/memory_contract.py` (401 lines; 10 tests) | `EventIdentity`, `MemoryScope`, `BitemporalClaim`, `MemorySnapshot`, `MemoryUseReceipt.to_decision_extra()`. **No importers anywhere** (git grep: no consumers outside the module). |
| EventLog (#67) | `src/z0int/memory/event_log.py` (561 lines; 9 tests) | Append-only, checksums, blobs, rebuildable index, default root `~/.z0int/memory`. `append()` takes **no `event_uid`**, so it is not idempotent and not bound to `EventIdentity`. `~/.z0int/memory` does not exist, so it has never been used. |
| OptMemTree (#69) | `src/z0int/memory/optmem_tree.py` (353 lines; 8 tests) | Structural `nap`/`cover`/`zoom`, raw tail, no learned summarizer. No consumers. |
| context_resolve | `src/z0int/context_resolve.py` (571 lines; 5 tests) | `EvidenceRef` provenance, gaps, epochs, AODL projection. Natural-language needs go only to `qmd`. **`kind="memory"` never retrieves anything** (lines 387-397); `allow_memory=True` only adds a contradiction note. |
| State Packet | `src/z0int/state_packet.py` (1499 lines; 20 tests) | Adapters git, docs, claude_code, github, resource. **No AgentsView, TencentDB or EventLog adapter.** Claude Code `SessionStart` injects it (`claude_code.py:201-209`). `~/.z0int/state/state_packet/c7861a85658fbc8c/latest.json` is current, so this path works. |
| DecisionOpportunity | `src/z0int/decision_opportunity.py` (12 tests) | Built from a StatePacket. No `MemorySnapshot` binding. |
| MCP | `src/z0int/intelligence_mcp.py` | Tools: `list_models`, `route_worker`, `delegate_worker`. **No memory tools.** |
| HTTP service | `src/z0int/intelligence_service.py` | No `/v1/memory/*`. `/v1/context/pack` is the AgentWeb context packet. The live service runs a stale tree (prior findings). |
| Harness adapters | `harness-adapters/{claude-code-z0intelligence, claude-code-z0-obspack, hermes-z0intelligence, hermes-z0int-decisions, dsh-z0intelligence, automatic-client.mjs, governed-client.mjs}` | Routing and decision capture only. `hermes-z0intelligence` uses `pre_llm_call` → `{'context': ...}` (the right seam). `dsh-z0intelligence` uses `llm/stream`, not `agent/pre-step`. |

### 4.2 AgentsView (installed `~/.local/bin/agentsview` v0.39.0, commit `627a8afa`, built 2026-07-27)

**Corpus:**
- `sessions` 11,294 (max `started_at` 2026-10-01T07:10:45Z); `stats.message_count` 364,064; `messages` max rowid 445,687; `tool_calls` max rowid 432,425.
- Per agent:

  | Agent | Sessions | Latest started |
  | --- | --- | --- |
  | chatgpt | 2502 | 08-25 |
  | omp | 2485 | 10-01 |
  | grok | 2411 | 10-01 |
  | hermes | 1789 | 10-01 |
  | codex | 1690 | 10-01 |
  | kimi | 121 | |
  | claude | 108 | |
  | antigravity-cli | 107 | |
  | opencode | 54 | |
  | omo | 13 | 09-27 |
  | cursor | 9 | |
  | gemini | 4 | |
  | measure | 1 | |

  There is **no `deepseek-harness` agent.**
- `pg_sync_state.last_sync_finished_at` = 2026-10-01T07:17:00Z.
- **Recall layer empty:** `recall_entries` 0, `recall_evidence` 0, `recall_query_events` 0, `recall_extract_generations` 0, `recall_corpus_state` revision 0. The `recall_entries` schema already has scope, status, review_state, confidence, `provenance_ok`, `supersedes_entry_id` and `superseded_by_entry_id`.

**MCP:** `agentsview mcp` is a read-only stdio/HTTP server with `search_sessions`, `list_sessions`, `get_session_overview`, `get_messages`, `search_content`, `get_usage_summary` and `query_recall`. It "starts the daemon when needed".

**Configured as MCP in:**
- OMP `~/.omp/agent/mcp.json` (with `AGENTSVIEW_DATA_DIR`);
- Codex `~/.codex/config.toml` `[mcp_servers.agentsview]`;
- `~/.claude.json` only, not the active `~/.claude-home` config;
- DSH `~/.dsh/profiles/web/cordis.patch.yml` (`memory-agentsview`) only.

### 4.3 Per harness (memory-relevant, verified)

**Hermes (live config, non-secret keys only):**
- `memory.provider: memory_tencentdb`; `plugins.enabled` includes `hermes-z0intelligence`; `toolsets` includes `"no_mcp"`. **INFERRED:** MCP tools are off for the default toolset, so Hermes cannot reach the AgentsView MCP.
- The TencentDB provider source is upstream `MemoryCore/hermes-plugin/memory/memory_tencentdb` (supervises a local Node gateway on 127.0.0.1:8420, with `sync_turn`, `on_memory_write` and `on_session_end` hooks).
- Hermes MemoryManager prefetch has an 8 s bound. That is the critical-path issue in prior findings and Hermes #417.

**OMP:**
- `config.yml memory.backend: local` (OMP-native memory in `~/.omp/agent/memories`; harness-local).
- Extensions:
  - `tencentdb-memory`: `before_agent_start` recall, `agent_end` capture, `tdai_memory_search` tool; gateway :8420.
  - `hermes-memory`: direct read-only Hermes DB reader.
  - `cognitive-state`: `~/tmp/omp-ext-cognitive-state@ce151e5`, local repo with no remote; shadow by default.
  - `z0int-bridge`, `z0int-intelligence` and `local-cognition`: symlinked into the stale `~/tmp/z0int-canonical`.

**DSH:**
- The `web` profile has `memory-agentsview` (`AGENTSVIEW_DATA_DIR`, `toolCallTimeoutMs: 125000`, `failOnStartupError: true`). Its comment says the old `mcp-z0` row pointed at `http://127.0.0.1:8791/mcp`, which "has no implementation found".
  - **That implementation is `consolidate/z0-kernel-20260922:src/z0int/mcp_server.py` (`serve_http(port=8791, path="/mcp")`), unmerged.**
- `headless`, `sdk-minimal` and `deepseek-sdk` have **no** memory row.
- All four profiles run `intelligence-z0intelligence` from `~/tmp/openjev/.venv` (not master).

**Claude Code:**
- `~/.claude-home/settings.json` enables `z0intelligence@z0intelligence` and `z0-obspack@z0intelligence`.
- The `SessionStart` State Packet is injected. The MCP is `route_worker` only (`Z0INT_SHARED_ONLY=1`).

**Codex:**
- `z0intelligence@personal` (route_worker; runs `~/plugins/z0intelligence/scripts/server.py` **with the live Hermes venv python**).
- AgentsView MCP; `[features] hooks = true`.

### 4.4 Prior measured evidence

**Hermes #56 lane `c84663880a`, smoke n=1 (z0evals#56 comments 2026-09-29):**
- 62 focused tests pass.
- Real `pre_llm_call` injection reached model-visible context.
- Source-revision invalidation, missing-evidence abstention and replay suppression all held.
- Labelled smoke only; it does not count as the frozen cohort.

**OMP #107 (`feat/cognitive-state-shadow-rfc79`, open):** 15/15 focused tests and a live z0int exact-path read. Its AgentsView lane failed closed on the symlink issue.

**Claude Code packet gating (`feat/packet-gating-v0@04b9186`):** both pre-registered tests **FAIL**:
- session → gated −7.3% tokens, CI crosses 0;
- QA non-inferiority fail.

This is evidence that injecting more packet content is not free. Gate memory injection on measured value.

---

## 5. What is broken or missing (verified unless marked)

### 5.1 Corrections to prior-findings.md

| # | Prior statement | Correction (evidence) |
| --- | --- | --- |
| C1 | "memory_contract.py … contract-only" | Also integrated: `memory/event_log.py` (#67) and `memory/optmem_tree.py` (#69) via `b345aa2`. All three have **zero consumers**. |
| C2 | "universal recall built 09-22 … `recall_mcp`, `statepack orient`" | The 09-22 code is **untracked** in `/mnt/zer0models/workspace/zer0/oss/tools/{recall_mcp.py (440 lines), statepack.py (323 lines; "COMPATIBILITY WRAPPER. Canonical owner: z0int.context_resolve")}`. Its z0int successor is `consolidate/z0-kernel-20260922@dcbcc88` (`mcp_server.py` replaces "the transitional `tools/recall_mcp.py` union"). It is **not merged** and absent from the #109 integrated list. |
| C3 | "DSH lane runner `dca8fd2` (unpushed)" | Pushed as `origin/study/unified-memory-56-dsh-runner`. It is also in `origin/nightly` (`3f2f91c`) and `origin/readiness/unified-memory-cohort-integrity@90023c4`, **but not on z0evals main**. |
| C4 | "sync/daemon NOT running" | Root cause: an **on-demand daemon with idle shutdown**. `debug.log` 2026-10-01 02:37:03: "idle timeout elapsed; shutting down daemon". It only syncs while some MCP client keeps it alive. Upstream exposes `daemon_idle_timeout = "0s"` (cli.go:916). |
| C5 | "NO dsh agent" | Cause: the DeepSeek-Harness parser landed upstream in **v0.41.0** (#1402, `46086fe9`, 2026-08-16; agent type `deepseek-harness`, honours `DSH_HOME`). The installed binary is v0.39.0. |
| C6 | (not in prior findings) | **Binary/DB schema mismatch.** The DB has `pragma user_version=74` and `usage_events.cost_microdollars` (upstream #1224 `8dc6adc6`, first released v0.40.0). v0.39.0 writes `cost_usd`, giving 23,619 `no column named cost_usd` lines and "263 source or archive failures". The DB also holds 13 `omo` sessions, which v0.39 cannot parse. **INFERRED:** a newer agentsview build (likely the OMO lane, 09-27) migrated this data dir. |
| C7 | (not in prior findings) | Claude Code coverage gap: AgentsView only indexes `~/.claude/projects` (108 sessions). The active Claude Code config dir `~/.claude-home/projects` (14 project dirs) is not indexed. Config key `claude_project_dirs` (env `CLAUDE_PROJECTS_DIR`/`CLAUDE_CONFIG_DIR`) exists upstream. **INFERRED:** whether v0.39 honours multiple dirs. |

### 5.2 Gaps against the RFC slices (contract doc "Follow-on work" 1-6)

1. **EventLog/AgentsView importers do not emit `EventIdentity`.** `EventLog.append` has no `event_uid` and no idempotent dedupe.
2. **The #22 reducer does not emit `BitemporalClaim`.** StatePacket claims are its own `z0int.state_packet.v0` records.
3. **StatePacket/DecisionOpportunity are not bound to `MemorySnapshot`.**
4. **Real OMP/Hermes/DSH receipts record no memory use.** `~/.z0int/receipts/decisions.jsonl` has 3,582 rows and `outcomes.jsonl` 649 rows; Claude Code `opportunities.jsonl` has 7 and `outcomes.jsonl` 20. Rows containing `z0int.memory_contract` or `snapshot_id`: **0**.
5. **No z0evals cohorts for stale/precision/leak/taint.** The #56 frozen cohort exists (`cohort.json`, 6 cases), but `results/harness-receipts.jsonl` is absent on main. No harness has a passing row.
6. Routine-compiler feed: not started.

### 5.3 Other breaks

- **No harness-facing memory API on master**: no `orient`, `inspect`, `unknowns`, `verify`, `history` or `explore`, over MCP or HTTP. The #22 P0 API exists only on the unmerged consolidate branch.
- **TencentDB not wired cross-harness:**
  - The gateway :8420 is not listening on the host.
  - Only the Hermes provider supervises it. **INFERRED:** it runs inside the Hermes environment.
  - OMP `tencentdb-memory` and DSH `memory.js` fail closed or return empty.
  - TencentDB-derived memories carry no canonical `source_event_ids` (#63 acceptance "TencentDB-derived memories include canonical provenance" is unmet).
- **DSH has no `orient()` and no memory injection.** `dsh-z0intelligence` hooks `llm/stream` for routing. Nothing uses `agent/pre-step` or `inject()` for memory. AgentsView MCP is present only in the `web` profile, and is pull-only (the model must call it).
- **Divergent z0int trees serve harnesses:** DSH uses openjev `.venv`; OMP extensions use `~/tmp/z0int-canonical`; bend-native vendors copies under `stack/vendor/z0int/`. A memory slice landed on master will not reach those harnesses until each is repointed (config/install step).
- **Hermes #56 lane puts the cache in Hermes core** (`agent/memory_packet_cache.py`). That is contrary to owner direction, and the code is local-only and unpushed.
- **Direct Hermes DB readers** (OMP `hermes-memory`, DSH `memory.js`, consolidate `lexical_hermes`) bypass AgentsView and touch the live Hermes install.
- **AgentsView `query_recall` returns nothing** (empty recall corpus).
- The `context_resolve` default `allow_memory=False` is correct as a guard. There is no dedupe protocol to make `True` safe alongside TencentDB prefetch.

---

## 6. Per-harness seam matrix (owner direction: extension seams only, code in z0-owned repos, activation by config)

| Harness | Inject (model-visible) seam | Pull seam | Write / capture path | Memory-use receipt seam | Today | Activation step (no harness code) |
| --- | --- | --- | --- | --- | --- | --- |
| **Hermes** | general plugin `pre_llm_call(session_id, user_message, conversation_history, is_first_turn, model, platform)` → `{"context": ...}`. Ephemeral; history and session DB are not mutated (hermes#320 comment 2026-09-30). The MemoryProvider slot stays `memory_tencentdb` (one provider max). | Hermes `mcp_servers` (currently `no_mcp`, INFERRED) or a plugin `provides_tools` tool | transcript → AgentsView `hermes` parser; TencentDB L0 via provider `sync_turn` (derived mirror) | `post_llm_call` / `on_session_end` in `hermes-z0int-decisions` or the bend-native observer | routing context only; no memory injection; no receipts | `plugins.enabled += <z0 memory plugin>` (or extend `hermes-z0intelligence`). Install from `z0intelligence/harness-adapters`. |
| **DSH** | Cordis plugin on `agent/pre-step` waterfall (final model-visible admission) or `inject()` (non-waking next-step context) — `zer0.repo.yaml` subsystems `pre-step-admission` and `durable-agent-inbox` | `@deepseek-ai/dsh-mcp-client` rows (`memory-agentsview`; future z0 memory MCP) | transcript `~/.dsh/sessions/**/session.v3.jsonl.zstd` → AgentsView ≥v0.41 `deepseek-harness` parser | plugin emits a bounded receipt → z0 appends `Event(worker_result)` (#63) | `web` profile AgentsView MCP only; no DSH sessions in AgentsView; no receipts (#62) | upgrade AgentsView; add `memory-*` rows to all `~/.dsh/profiles/*/cordis.patch.yml`; repoint `intelligence-z0intelligence` to the master venv; install updated `dsh-z0intelligence` |
| **OMP** | extension `context` event (OMP #79/#107 seam) or `before_agent_start` | MCP (`~/.omp/agent/mcp.json` agentsview) / `pi.registerTool` | transcript → AgentsView `omp` parser; TencentDB via `tencentdb-memory` `agent_end` | extension `agent_end`; v1 spine `receipts/decisions.jsonl` | AgentsView MCP configured; cognitive-state shadow; z0 extensions point at the stale tree | repoint extension symlinks to a master-built z0 OMP extension; retire `hermes-memory` (owner decision) |
| **Claude Code** | plugin hooks `SessionStart` / `UserPromptSubmit` → `hookSpecificOutput.additionalContext` (`claude_code.py:108,201`) | plugin `.mcp.json` (`z0intelligence`; add memory tools); AgentsView MCP | transcripts → AgentsView `claude` parser (needs `~/.claude-home/projects` in `claude_project_dirs`) | `Stop` / `SessionEnd` hook → `outcomes*.jsonl` | `SessionStart` packet live (git/docs/claude_code/github/resource); no cross-harness memory | plugin update; AgentsView config key |
| **Codex** | plugin hooks (`features.hooks = true`; INFERRED that a plugin can ship them) | plugin MCP (`z0intelligence@personal`) + `[mcp_servers.agentsview]` | sessions → AgentsView `codex` parser (1690 sessions) | z0 MCP call receipts | AgentsView MCP pull only | plugin update (and stop running it from the live Hermes venv) |
| **OMO** | (study lane) native `session_search` tool | AgentsView `omo` source (fork `feat/omo-source`, upstream #2009) | AgentsView | — | automatic z0 routing OFF by owner rule | AgentsView upgrade |
| **Pi** (registry: research) | SoL-Pi ObservationPack (`registry/mechanisms.yaml`) | — | — | — | research only | — |

---

## 7. bend-native (`@e85e65e`, v0.4.0) evaluated as the Hermes vehicle for memory

**What it is.** A standalone Hermes plugin that provides `pre_llm_call`, `post_llm_call`, the API/aux/tool hooks and `on_session_start/end`. It bundles the observer, the shadow scorer/evaluator, and **vendored** `z0int/{state_packet,context_resolve,decision_opportunity,paths}.py` (pinned in `stack/sources.json`).

**What it declares out of scope.** `stack/STACK.md` says "Source-backed context and State Packet: Git/docs revisions; **no ambient personal memory or network retrieval**". It also says "OptMem, semantic memory … Existing RFC/companion ownership". **bend-native is a capture vehicle, not the memory read path.**

**Fit for memory:**
- Good: it can carry `MemoryUseReceipt` rows from `post_llm_call` (memory-use capture) once SYNTHESIS P-1, P-2 and P-7 are fixed.
  - **P-1:** `stack_service_port` defaults to 11501, the live z0.
  - **P-2:** `close()` cannot stop a full-queue worker.
  - **P-7:** the shadow opportunity projection uses the process cwd, not the task repo, so the gate says ACT with every git fact unknown. That violates "unknown forces OBSERVE".
  - **P-9:** an undocumented privacy disclosure. The State Packet persists README text, commit subjects and branch names.
- Risk: its vendored z0int copies will not pick up memory slices landed on master. Re-vendoring per slice means drift. **INFERRED** recommendation: the memory adapter imports an installed z0int package (as `hermes-z0intelligence` does via `PYTHONPATH=…/src`), not new vendored copies.
- Risk: **double injection.** `hermes-z0intelligence` (enabled) and bend-native both register `pre_llm_call`, and `memory_tencentdb` prefetch also injects. Exactly one plugin may own memory context injection, and a test must assert a single injection per turn.

**Verdict:** keep bend-native for Hermes observation and receipts. Put memory injection in one z0int `harness-adapters` Hermes plugin (an extended `hermes-z0intelligence` or a new `hermes-z0-memory`). No new vehicle and no Hermes core change.

---

## 8. Suggested TDD slices (inputs for the build plan; all code lives in z0-owned repos)

The order follows the RFC slices, limited to what the four live proofs need (#22 comment 2026-09-29: "add only what the four live proofs require").

**M0. AgentsView freshness and coverage.**
- Install/config only, plus a z0int read-only probe.
- RED: `z0int memory doctor` (opens `sessions.db` with `mode=ro`) fails today on four checks:
  - freshness lag ≤ N min;
  - a `deepseek-harness` agent is present;
  - Claude sessions from `~/.claude-home` are present;
  - no schema-mismatch write errors since the last sync.
- GREEN: back up `sessions.db`, then install agentsview ≥ v0.41 (v0.44.0). Set `daemon_idle_timeout="0s"` (or a persistent sync service) and `claude_project_dirs` (both dirs). Must not run during timed experiments.

**M1. AgentsView retrieval capability on master.**
- Re-land the AgentsView/FTS subset of `consolidate:context_providers.py`. **Drop** the direct Hermes `state.db` reader.
- Every hit carries an `EvidenceRef` (`agentsview:<sid>#<mid>`) plus `EventIdentity` (`source_system` = agent, `source_session` = sid, `source_event_id` = message id/ordinal, `payload_hash`).
- Tests (synthetic FTS5 fixture):
  - deterministic `event_uid` on replay;
  - scope filter applied before ranking;
  - provider down → explicit `unavailable`, never empty success;
  - `kind="memory"` resolves when allowed.

**M2. TencentDB read capability.**
- A read-only HTTP client (`/v3/atomic/search`, `/v3/conversation/search`) behind the same interface, with a hard deadline (≪ 8 s, fail-open to native).
- Items without canonical provenance are marked `derived_memory`, `provenance_ok=false`.
- Tests: deadline, down-gateway, provenance flagging, dedupe vs the AgentsView hit set.

**M3. Bind MemorySnapshot and memory-use receipts** (contract slices 3-4).
- `StatePacket`/`DecisionOpportunity` get a `memory_snapshot_id`.
- `MemoryUseReceipt.to_decision_extra()` is written into `DecisionReceipt.extra`.
- Tests: the snapshot ID changes with source revision; the receipt round-trips with no schema change; `instruction_capability` is rejected.

**M4. One harness-facing memory surface.**
- Add `orient`, `inspect`, `unknowns` and `verify` (and `history`) to the z0int MCP. Either extend `intelligence_mcp.py` or re-land `mcp_server.py`.
- In-process stdio, so it does not depend on the live 11501 service.
- Tests: `tools/list`, orient provenance, abstain on missing required evidence, error ≠ empty.

**M5. Per-harness shadow → canary injection adapters** in `z0intelligence/harness-adapters`.
- Hermes: `pre_llm_call`, re-homing `MemoryPacketCache` from `c84663880a` into the plugin.
- DSH: `agent/pre-step`.
- OMP: `context` extension.
- Claude Code: `UserPromptSubmit`.
- Codex: plugin MCP/hooks.
- Tests per adapter (hermes#320 TDD list plus #56 A–F):
  - injection is visible in the next model call;
  - it is absent from the persisted transcript;
  - a duplicate trace does not double-inject;
  - a resolver exception falls back to native;
  - a revision change invalidates the cache;
  - there is a single injection owner per turn.
- Then: config-only activation per harness (§6).

**M6. EventLog identity binding** (slice 1).
- `append(..., identity=EventIdentity)` is idempotent on `event_uid`, and payload conflicts are surfaced.
- AgentsView → EventLog imports **references** (identity plus locator), not bodies.
- Tests: replay import makes no duplicates; `ledger_seq` changes without changing the UID; the conflict is detected; `events.jsonl` is never rewritten.

**M7. Reducer emits scoped `BitemporalClaim`** (slice 2). The #66 acceptance tests are the RED set.

**M8. z0evals #56 cohort run** per harness. Receipts validate against `studies/unified-memory-v0/receipt.schema.json`, and the six cases come from `cohort.json`. Merge the DSH runner branch to main first.

---

## 9. Acceptance criteria (consolidated)

Status key: MET / PARTIAL / UNMET (as of 2026-10-03).

| Ref | Criterion | Status |
| --- | --- | --- |
| #56-A | Each harness: a real query returns source-backed evidence with stable provenance | PARTIAL: AgentsView MCP works as a pull tool where configured, but data is stale since 10-01 and DSH sessions are absent |
| #56-B | Each harness: evidence reaches the next model-visible context; indexing/config alone is not a pass | UNMET for cross-harness memory. Hermes smoke n=1 on a core-patched local branch only; Claude Code `SessionStart` packet has no cross-harness evidence. |
| #56-C | The answer is supported by injected evidence (not inferred from retrieval) | UNMET |
| #56-D | Removing a required source → abstention, not invention | PARTIAL: StatePacket forces OBSERVE on missing facts; Hermes smoke only |
| #56-E | Idempotent replay: no duplicate injection or side effect | UNMET (Hermes smoke only) |
| #56-F | A newer contradictory fact supersedes current state while history stays inspectable | UNMET |
| #56 | The same frozen 6-case cohort runs through DSH, Hermes, OMO, OMP; receipts validate the schema; a frozen bundle pins revisions; no private corpus committed; no new canonical DB | UNMET (no `results/harness-receipts.jsonl`) |
| #22 | One real AODL intent → compact State Packet from current local evidence | PARTIAL (`state_packet.py`; no conversation-history adapter on master) |
| #22 | Concurrent fan-out over ≥3 adapters | MET (git/docs/claude_code/github/resource) |
| #22 | Provenance for every material claim; contradictions explicit; blocking unknown forces OBSERVE; cache invalidates on revision change; a stale packet cannot authorize | MET in unit tests (20 tests). bend-native P-7 shows ACT with unknown facts on the wrong cwd. |
| #22 | Measured cold/warm latency, raw reads, tokens, hit rate; one cross-project eval shows materially less raw-data access than direct recall | PARTIAL (`benchmarks/state_packet/*`; the packet-gating study failed its pre-registered tests) |
| #22 | No new canonical raw-data store and no second scheduler | MET so far |
| #63 P0 | 10k synthetic turns within budget; raw tail verbatim; `zoom` to event 0 exact; `forget`+`renap` never mutates `events.jsonl`; coarse hash stable | MET in unit tests (`test_memory_optmem_tree.py`, `test_memory_event_log.py`) |
| #63 P0 | FTS5 hits return canonical event IDs | UNMET |
| #63 P0 | TencentDB-derived memories include canonical provenance | UNMET |
| #63 P0 | Workers cannot open or write the canonical event ledger | UNMET (untested; ledger unused) |
| #63 P0 | Held-out facts recoverable after a 1024-event nap cascade | PARTIAL (structural only) |
| #63 P0 | StatePacket resolves one query across temporal + lexical + semantic without duplicate evidence | UNMET |
| #63 P0 | DSH worker injection plus receipt round-trip | UNMET |
| #63 P0 | Hermes worker injection plus receipt round-trip | UNMET (smoke on a core-patched branch) |
| #63 P0 | No migration or mutation of production memory stores in P0 | MET |
| #66 | Identical replay → same `event_uid`; ledger position not part of identity | MET at contract level; UNMET in EventLog/importers |
| #66 | Cross-project/repo/task visibility rejected before ranking | MET at contract level; UNMET in retrieval |
| #66 | Verified claims without evidence rejected; bitemporal kept separate; supersession non-destructive; memory cannot assert instruction authority | MET at contract level (10 tests); UNMET in the reducer |
| #66 | Snapshot ID changes with source revision; usage attaches to `DecisionReceipt` without a schema change | MET at contract level; UNMET in real receipts (0 rows) |
| #66 | Deterministic tests, no network/model/GPU; no production store mutated | MET |
| #23 | All clients go through the same z0int/MCP intelligence surface | UNMET: each harness uses a different path (AgentsView MCP, `hermes-memory`, `tencentdb-memory`, `memory.js`, the SessionStart packet) |
| #23 | One canonical source-derived ingestion path can feed multiple engines | UNMET |
| #23 | Current z0 retrieval kept as control; Sibyl/Mem0/Graphiti bakeoff or a documented blocker | MET (consolidate-branch bakeoff, 09-23; not on master) |
| #53 | Opportunity reconstructable deterministically from pinned evidence; unknown ≠ false; harness identity does not change semantics | PARTIAL (`decision_opportunity.py`, 12 tests; no memory snapshot) |
| #62 | One fixture per harness round-trips the same semantic record; stable IDs survive replay | UNMET for DSH; partial for Hermes and Claude Code |
| Owner 10-03 | No harness core change; integration in z0-owned repos; activation is config/install | Achievable. Violations to unwind: Hermes `c84663880a`; OMP #107 lives in the fork's `examples/`. |

---

## 10. Open decisions and risks

1. **EventLog scope.** It should hold z0-originated events plus *references* to harness events, not bodies (INFERRED reconciliation of #63 vs the z0 registry `not_here: source memory databases`). This needs owner confirmation before M6.
2. **AgentsView upgrade risk.** The 20 GB DB migration needs a backup and must avoid the timed experiments. The DB already carries a ≥v0.40 schema, so the upgrade *reduces* risk. A persistent daemon costs CPU on the host.
3. **Hermes MCP is off** (`no_mcp`, INFERRED). Hermes memory therefore has to be inject-first (`pre_llm_call`), or a plugin tool.
4. **TencentDB reachability.** The gateway lives under the Hermes provider supervisor (INFERRED). Cross-harness TencentDB reads need it running persistently, which is a config/deploy step. The 8 s prefetch bound stays a Hermes-local latency risk.
5. **Value gate.** The packet-gating study failed its pre-registered tests. Memory injection should ship shadow-first with #56/#71/#73 rulers before canary. The #73 arms are retrieval-only vs learned vs generic.
6. **Stale trees.** DSH (openjev venv), OMP (z0int-canonical) and bend-native (vendored copies) must be repointed to a master-installed z0int, or the slices never reach them.
7. **Retiring direct readers.** OMP `hermes-memory` and DSH `memory.js` are the owner's live tools today. Replacing them is an owner-visible behaviour change.

---

## 11. INFERRED items (not directly verified)

- Hermes `toolsets: [..., "no_mcp"]` disables MCP tools for the default toolset.
- The TencentDB gateway runs only under the Hermes provider supervisor (Hermes environment), so it is unreachable from OMP/DSH when not on the host.
- A newer agentsview build (likely the OMO study lane, around 09-27) migrated `sessions.db` to schema v74.
- v0.39 may not honour extra Claude project dirs.
- A Codex plugin can ship hooks.
- Enabling AgentsView `recall extract` would be a competing semantic layer.
- A z0 MemoryProvider replacing `memory_tencentdb` is undesirable.
- The EventLog references-not-bodies reconciliation.
- bend-native vendoring will drift from master memory slices.

---

## 12. Evidence index (commands, all read-only)

- `git -C /mnt/zer0models/z0-wt/z0intelligence show origin/master:<path>` for every z0int file cited. `git grep` showed no consumers of the memory primitives.
- `gh api repos/kvnloo/{z0intelligence,z0evals,hermes-agent,oh-my-pi,deepseek-harness,z0}/issues/<n>` (+ `/comments`). Raw dumps are in `spec/scratch/r2/*.md`.
- `sqlite3 "file:/mnt/zer0models/sft-svlm/data/agentsview/sessions.db?mode=ro"`: counts, per-agent breakdown, `recall_*` counts, `pg_sync_state`, `stats`, `pragma user_version`, `.schema usage_events`, `.schema recall_entries`.
- `agentsview --version`, `agentsview --help`, `agentsview mcp --help`, `agentsview recall --help`, `agentsview recall extract --help`.
- `tail` of agentsview `serve.log` and `debug.log` (redacted); `grep -c 'no column named cost_usd'` = 23,619.
- `git -C /mnt/zer0models/z0-wt/ro/agentsview log -S cost_microdollars upstream/main`; `--diff-filter=A … deepseek_harness.go`; `tag --contains`.
- `ss -ltn` (passive): only 11501 is listening; 8420, 8080 and 8791 are not.
- `grep -c 'z0int.memory_contract\|"snapshot_id"'` on `~/.z0int/receipts/*.jsonl` and `~/.z0int/state/claude-code/*.jsonl` = 0.
