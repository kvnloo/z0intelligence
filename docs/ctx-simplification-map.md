# #116: what ctx could replace in the z0 wiring memory code (simplification map)

> Status: written before the step-6 bakeoff. The bakeoff has since run (`benchmarks/ctx_history/results/`): on 26 real-history questions the control answered 6, ctx lexical 0, ctx + exact hydration 3. That strengthens the conclusion below: nothing here is promoted, and ctx stays opt-in and off by default.

Date: 2026-10-03. This is a read-only analysis. I changed no code in any worktree.

| input | ref |
| --- | --- |
| z0 wiring program | `integrate/wiring-20261003` @ `05e5015` |
| #117 | `feat/ctx-history-capability-116` @ `c441aba` |
| ctx | 2.2.7, isolated local data root, generation `f1404f74…`, manual indexing, claude + hermes imported, semantic disabled |
| AgentsView | v0.44 fork build, live data dir opened `mode=ro` only, through z0's own reader |

Raw probe output stayed in a private local directory and is not committed. This file holds key names, counts, latencies and ids only. File names in the evidence column (`probe_out.json`, `prove.md`, ...) refer to that private local evidence.

## Bottom line

1. **Most of what we built is not replaceable, and #116 says it should not be.** C7, C8 and C2 are mostly z0 policy:
   - scope;
   - the single-injector guard;
   - seams and deadlines;
   - receipts and acceptance rows;
   - claims and supersession;
   - snapshot and cache keys;
   - turn joins;
   - cohorts;
   - label semantics.

   Under the #116 boundary, ctx is only the retrieval capability. The only parts it can take over are the **AgentsView read path**: about 300 of roughly 2,500 src lines in scope, before counting the code needed to wrap ctx.
2. **Net LOC goes up today.** #117 already adds 975 lines: the adapter is 313, its tests 306 and the seam tests 356. Even if ctx fully replaced AgentsView, the best case is about -350 to -400 lines net (src plus tests).
   - So the payoff is not "less code to manage".
   - The payoff is three things: ctx's provider coverage, its stable identities across generations, and Blame.
3. **AgentsView cannot be retired yet.** The C2 labels and three of our seven harnesses depend on it:
   - **Labels.** The C2 labels need per-harness tool exit evidence and session-kind cohorts.
   - **OMP and OMO.** ctx's `pi` source is `missing`. OMO is a patch on our AgentsView fork.
   - **DSH.** ctx's `deepseek_harness` source is `empty`. AgentsView has 203 DSH sessions.
   - So in the near term ctx adds ops; it does not remove any.
4. **ctx cannot sit on the 300 ms push path.**
   - Measured on this host (nice 19, idle io): `ctx search --refresh off` takes about 2.0 s cold and 440-450 ms warm, and `ctx status` takes 1.6-2.7 s.
   - On the same terms, AgentsView FTS5 takes 313 ms cold and 45-46 ms warm for 200 candidates, and its snapshot probe takes 2 ms.
   - So ctx can only serve the detached shadow child, the pull MCP and the opt-in `resolve_context` seam (#117). It cannot serve the C8 canary/on push or the hook-path `memory_snapshot_id` (20 ms budget).

## Probe results (this run, read-only)

| question | answer | evidence |
| --- | --- | --- |
| Does ctx normalize tool exit codes? | **No.** `command_finished` events: 0 for claude and for hermes. `activity.facts` kinds seen: `branch`, `session_cwd`, `command`, `file`. None of them is an exit fact. | `probe2_out.json` |
| Is exit evidence in the retained body? | **Yes, as the provider wrote it.** Claude `tool_output.structured_content.is_error` is in 90/100. Hermes `tool_output` body JSON has `"exit_code"` in 41/69. So z0's per-harness frame parser (`turn_readers.exit_code`) is still needed and would read ctx bodies instead of `tool_result_events`. Codex, OMP, OMO, DSH and Grok bodies are not in this index. | same |
| Subagent / cohort signals | `agent_scope` (primary or subagent) and `session_relationship` (`delegated`, `workflow_child`), plus `parent_ctx_session_id` / `root_ctx_session_id`. These can replace AgentsView `relationship_type`. I saw no equivalent of `session_kind` / `is_automated` (codex exec, roborev), nor of the Hermes cron/kanban project. | `probe_out.json`, `probe2_out.json` |
| Can project scope be applied before ranking? | **No.** 0/5 real search hits carried `cwd`. `--workspace` is a **substring** filter over workspace/cwd/repo-name text. An exact project needs per-session hydration (the `session_cwd` fact) after ctx has ranked and limited. | `search_2.json` |
| Blame for credit | Unavailable in this root: `availability.{file,commit,pull_request}_blame=false` and all coverage counts 0, because the imports ran `--no-blame`. Not proven locally. | `status_1.json` |
| Secret redaction | ctx does not redact. Its storage docs warn that history and paths may contain credentials. There is no counterpart of AgentsView `secret_findings`, so z0's regex scrub would be the only line of defence. | `.ctx_doc_storage.md` |
| Writes from these probes | **0 files changed** in the data root across 3x `status`, 3x `search` and the `list events` probes (with `CTX_LOCAL_USAGE_ENABLED=false` and `CTX_ANALYTICS_ENABLED=false`). Agent configs: unchanged. | `root_b2/a2.txt`, `cfg_before/after.txt` |

## Per-module map

Verdicts:
- **keep**: z0 owns it under #116;
- **replace**: delete, and ctx does the job;
- **thin-wrap**: z0 keeps the contract and calls ctx underneath;
- **bakeoff**: undecided until the step-6 bakeoff and a parity run decide.

### C7 memory core

| module (src LOC) | verdict | reason |
| --- | --- | --- |
| `agentsview_ro.py` (67) | **bakeoff**, then replace | It has one job: open sessions.db read-only and guard the schema. If the lexical layer and the labels both move to ctx, it goes. `CtxHistoryCapability._run_json` plus the schema checks already play the same role for ctx. It stays while C2 labels read AgentsView. |
| `memory/surface.py` lexical layer (about 126 of 823: `_FTS_SQL`, `_agentsview_candidates`, `_finding_spans`, `agentsview_generation`, the AgentsView half of `inspect`) | **thin-wrap ctx (bakeoff)** | Swap the layer's backend for `CtxHistoryCapability.search` / `show_event`. Keep the `{status, reason}` layer contract and the `EvidenceRef` shape. z0 must keep or add three things, because ctx does not provide them: (a) an exact project post-filter (hydrate `session_cwd`, then `project_of`); (b) the brief-echo cut (`BRIEF_MARKER`), which needs the full event body (ctx snippets are bounded) or another hydration per hit; (c) a provider-to-harness map (`deepseek_harness`, `grok_build`; `pi` cannot be split into omp/omo). |
| `memory/surface.py` other layers: TencentDB client, temporal EventLog/claims, merge, `ScopePolicy`, `memory_brief` + cache, `claim_injection`, `unknowns`, `verify`, `bench` | **keep** | These are z0's scope, claims, authority and receipt logic, plus the SEMANTIC (TencentDB) and EPISODIC (EventLog/OptMem) tiers. The #116 non-goals forbid replacing them. |
| `MemorySnapshot` / `source_revisions` | **keep, thin-wrap the revision** | The ctx `generation_id` is a better lexical revision than AgentsView's `uv:max(id)`, because it is immutable and published. But it is not readable within the 20 ms hook budget: `ctx status` takes 1.6-2.7 s. Use the generation that the last real ctx call observed, the same pattern as `TencentDBClient.last_revision`. Do not read ctx internal files, which are not a contract. |
| `EventIdentity` (memory_contract) | **keep** | ctx maps cleanly to it: `source_system=ctx:<provider>`, and the uid and payload_hash were stable across two generations (`prove.md`). But the namespace differs from the AgentsView-derived uids already in receipts, ledger references and TencentDB `source_event_ids`. With no alias, cross-layer merge on `event_uid` stops matching, and #116 forbids migrating private memory. |
| `memory/event_log.py` identity ingest (+117) | **keep, needs one fix if ctx refs are ingested** | The body-field guard checks `identity.source_system in HARNESS_TRANSCRIPT_SOURCES` by exact name. `ctx:claude` and `ctx:hermes` are not in that set, so a ctx-sourced reference would **bypass the references-only guard**. Prefix-match `ctx:` before any ctx ingest. |
| `memory/scrub.py` (69) | **keep**; `redact_spans` (8) goes when AgentsView goes | ctx does not redact. Dropping AgentsView's `secret_findings` spans leaves only the regex backstop. That is a real privacy regression risk, not a simplification. |
| `memory/doctor.py` (111) | **thin-wrap (bakeoff)** | About 70 lines are AgentsView checks (user_version vs binary, freshness, DSH agent, Claude home indexed, require_auth). A ctx doctor would check different things: status `initialized`, `indexing.mode=manual`, generation, per-provider source status, semantic disabled, and no hosted history. Roughly 30 lines. The TencentDB check stays. |
| memory-only MCP (`intelligence_mcp` profile, +77) | **keep** | `ctx mcp serve` is not an acceptable stand-in. Its startup "may recover the default-enabled persistent daemon", which violates the #116 rule of no daemon refreshes from z0. Its output carries absolute paths, transcript text and MCP arguments unscrubbed, and it has no z0 scope, receipt or abstention. Our MCP may call ctx underneath via the surface. |
| `context_resolve` memory branch (+44) | **keep, and reconcile with #117** | `git merge-tree c441aba 05e5015` → **CONFLICT in `src/z0int/context_resolve.py`**. #117's `allow_ctx` seam and C7's `allow_memory` + `turn_key` guard both rewrite the `need.kind == "memory"` branch. They need one merge decision: ctx as a layer under the memory surface, or as a sibling op. |
| `state_packet` memory snapshot (+20) | **keep** | It only carries the snapshot id. See the MemorySnapshot row. |

### C8 per-harness memory seams

| module | verdict | reason |
| --- | --- | --- |
| `memory/seam.py` (382), `memory/hook.py` (99), JS client + DSH/OMP/OMO/Hermes shims (about 450) | **keep** | These are harness integration, modes, deadline, cloud-egress gate, replay idempotency, slots and receipt settlement, none of which is retrieval. The seams call `memory_brief`, so a ctx-backed lexical layer reaches them with no shim change. But canary/on cannot use ctx within the 300 ms deadline (see "Bottom line" item 4): a seam would have to exclude the ctx layer, or use it only in shadow. |
| `memory/acceptance.py` (229) | **keep** | z0evals A-F rows over a frozen synthetic cohort. `seed_cohort` writes a synthetic AgentsView DB. A ctx variant would need a synthetic ctx root (`ctx import` of fixture history), which is more setup, not less. |
| `project_of` (seam) | **keep** | It derives the project the way AgentsView names it. ctx has no project field, so z0 still needs it to post-filter ctx hits by `session_cwd`. |

### C2 label readers

| module | verdict | reason |
| --- | --- | --- |
| `turn_readers.AgentsViewReader` (about 125 of 344: `candidates`, `turns`, `_tool`, `reader_error`) | **bakeoff (label parity)** | It could be rewritten over `ctx locate session --provider … --provider-session …` plus `ctx list events --session … --content full`, giving about the same LOC (net 0). The blockers come first: (a) no OMP/OMO/DSH history in ctx here; (b) no `session_kind`/automation flag, so Codex exec and Hermes cron cohorts would degrade to `unknown`; (c) the ordinal join rule ("n-th captured turn = n-th user message") depends on which user messages the indexer keeps, so every `JOIN_RULE` must be re-derived and re-pinned; (d) a label shift feeds straight into the C6 learning tick, so we need a parity run (label agreement per harness on the same turns) before any switch. Each `list events` call is a subprocess of about 140-270 ms, against sqlite's ms. |
| `exit_code` frame parsers, `JOIN_RULES`, `classify_cohort`, `captured_turns`, `join_session`, `TEST_LABEL_POLARITIES` | **keep** | ctx does not expose exit codes. These encode z0 label semantics. |
| `legacy_import.py`, `outcome_verifier` (Claude transcripts path), `loop_export` | **keep** | Not AgentsView retrieval: z0 receipts, the Claude transcript reader and table export. |

### Ops

| item | verdict | reason |
| --- | --- | --- |
| AgentsView v0.44 fork build (OMO cherry-picks plus our Codex orphan-fork fix) and `agentsview-sync` timer (28 lines of units) | **keep (bakeoff)** | AgentsView is still the only index with OMO/OMP/DSH, and the C2 labels need it. Retire it only after ctx covers those providers and passes label parity. |
| ctx index maintenance (new) | **adds ops** | Running ctx needs these, and none is free: (a) a manual-mode `ctx import` timer under the quiet lock; (b) disk: 6.3 GB of ctx data for 2.68 GB of claude+hermes source, against 12 GB of AgentsView sessions.db for all harnesses, with the full codex (15 GB) and opencode (6 GB) corpus still to index; (c) managed install hygiene: `~/.ctx` is in `auto` mode, `ctx.service` crash-looped on a full disk, and auto-upgrade can refresh `~/.claude/skills/ctx` on a `show` (`prove.md`); (d) hermes import rejected 66,547 records, still unexplained. |

## What ctx does not cover

- **Outcome labels.** There are no normalized exit codes. Bodies keep the provider's own frame: Claude `is_error`, Hermes `exit_code` JSON. Codex, OMP and DSH frames are unverified because those providers are not indexed here. `TEST_LABEL_POLARITIES` stays a z0 concept.
- **Blame for credit.** It exists in ctx but is unproven here: availability is false because of the `--no-blame` imports. #116 step 7 keeps it as a separate adapter. A session that mentions a commit does not prove it produced that commit.
- **Automation cohorts.** Nothing like `session_kind` or `is_automated` was seen.
- **Exact project scope before ranking.** There is no project field and `--workspace` is a substring filter.
- **Secret redaction.**
- **Brief-echo exclusion.** Snippets are bounded. ctx's automatic current-session exclusion is caller-dependent and is not an echo guard.
- **OMP/OMO/DSH history on this host.**
- **Hook-path latency** (20 ms snapshot, 300 ms push).

## LOC estimate (best case: ctx fully replaces the AgentsView read path)

| area | removable | new code needed | net |
| --- | --- | --- | --- |
| `agentsview_ro.py` | -67 | 0 (adapter already in #117) | -67 |
| surface lexical + inspect + revision | about -126 | about +60 (post-filter, echo cut, harness map, last-seen generation) | about -66 |
| scrub `redact_spans` | -8 | 0 | -8 |
| doctor AgentsView checks | about -70 | about +30 | about -40 |
| `turn_readers` AgentsView reader | about -125 | about +125 | about 0 |
| deploy units | -28 | about +25 (ctx import timer) | about -3 |
| tests: AgentsView fixtures (80 + 313 SQL + 62) | -455 | about +200 to 250 (ctx JSON / synthetic-root fixtures) | about -200 |
| **total** | **about -880** | **about +440 to 490** | **about -390 to -440** |

Against that sits #117's **+975** (adapter 313 + tests 662). So the net across the program is positive. ctx earns its place only if the bakeoff shows better recall, provenance or coverage, not on LOC.

## Risks

1. **Identity namespace split.** `ctx:<provider>` uids do not equal the AgentsView-derived uids already in receipts, ledger and TencentDB. Without an alias table, merge on `event_uid` silently stops deduplicating across layers.
2. **Guard bypass.** `ctx:*` sources skip the EventLog references-only body guard (exact-name match).
3. **Scope leakage.** A substring `--workspace` filter (e.g. `z0` also matches `z0evals` and `z0intelligence`), or a post-limit filter that starves results.
4. **Privacy.** Losing AgentsView `secret_findings` leaves regex-only redaction.
5. **Label discontinuity** in C6 learning if the turn readers switch without a parity gate.
6. **Latency.** ctx on the push path breaks the 300 ms deadline, and in a hook it would block the turn.
7. **Ops.** Disk, the auto-mode managed install, skill refresh on upgrade, unexplained hermes rejects, and a cold-index timeout miss (the 8 s default in the seam review).
8. **Merge conflict** between #117 and the wiring branch in `context_resolve.py`.

## Recommended order (no default change, per #116)

1. Merge #117 into the wiring line with ctx as an **opt-in lexical layer** under `memory/surface`, not as a sibling path. This resolves the `context_resolve` conflict once. Pull, MCP and shadow only. Also fix the `ctx:` ingest guard.
2. Run the step-6 bakeoff: same questions, AgentsView FTS5 as the control, ctx lexical, and ctx plus exact hydration. Include a scope-leak check and the echo-cut cost.
3. Separately, run a label-parity study for the turn readers per harness, once ctx indexes codex. OMP/OMO/DSH need ctx provider support first.
4. Retire `agentsview_ro`, the AgentsView sync timer and the fork build only after steps 2 and 3 pass. Until then FTS5/AgentsView stays as the control and fallback, as #116 itself says.

## Checks

- Commands ran under `flock -s quiet-lane.lock nice -n 19 ionice -c3`, with the ctx env from `ctxenv.sh` plus `CTX_LOCAL_USAGE_ENABLED=false`.
- Agent configs (`cfgsnap.sh`: `~/.claude-home/settings.json`, `~/.codex`, `~/.omp`, `~/.hermes`, `~/.dsh` and their config files): identical before and after (private before/after snapshots match).
- ctx data root: my probes changed 0 files.
  - One unattributed write did happen: `usage.sqlite` mtime moved to 22:57:33.6Z. That was between my baseline snapshot and my first probe, with no ctx process of mine running.
  - The mtime was unchanged after all my probes, so the write was not from them. It is consistent with another session that uses the same `ctx-data` root.
- AgentsView live data dir: opened only through z0's `agentsview_ro` (`mode=ro`), for 3 queries and 1 revision probe.
- `git merge-tree` wrote tree objects into the shared clone's object store. No ref or worktree changed.
- Nothing was committed or pushed in this step.
