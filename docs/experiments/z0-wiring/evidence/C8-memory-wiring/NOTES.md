# C8-memory-wiring: notes (deviations from the plan, activation)

Worktree `/mnt/zer0models/z0-wt/wiring/wt/C8-memory-wiring`, branch `feat/wire-memory-harnesses-20261003`,
base `3028e0c`. Round 1: `4b1b005` (red) .. `c875c6b`. Round 2 (verifier REVISE): `55cbb4d` (red tests) and
`41aec0f` (fix). Round-2 evidence: `round2/{red,green,suite,e2e}.txt`. Round-1 evidence stays at the top level.

## Round 2: what changed (one entry per verifier finding)

| sev | finding | fix | tests |
|---|---|---|---|
| major | `Z0INT_MEMORY_ENDPOINT` could mark a cloud harness loopback | removed from the gate; the endpoint is the harness's own model setting only (CC `ANTHROPIC_BASE_URL` / Codex `OPENAI_BASE_URL`, else the public API; Hermes `model.base_url`; OMP `ctx.model.baseUrl`; DSH `config.model_endpoint`); missing = cloud | `test_a_generic_endpoint_env_never_turns_a_cloud_harness_into_loopback[cc,codex]`, DSH and OMP equivalents; e2e cloud arm (real CC + hook command) |
| major | brief persisted by DSH, Hermes, CC; vacuous tests; DSH briefs re-indexed into recall | DSH has no non-durable seam (its agent-loop invariant requires every loop-built request to equal the durable derivation), so pre-step stays and the deviation is recorded; echo guard in C7 lexical recall (`surface._without_brief`: message text is cut at `BRIEF_MARKER`, an all-brief message is dropped). Hermes/CC: host design, recorded below, owner decision pending | `test_a_persisted_z0_brief_is_never_recalled_as_evidence`; Hermes test renamed to what it proves (the plugin writes nothing) + strict xfail documenting the host `api_content` sidecar; DSH persistence asserted; e2e `persistence.per_shim_matches_recorded_deviations` + `echo_guard.reindexed_briefs_are_never_evidence` |
| major | no cwd = unscoped brief from every project; project = basename | fail closed: no resolvable project -> `no_scope` row in shadow/canary/on, nothing spawned or injected; `seam.project_of` names the project as AgentsView `ExtractProjectFromCwd` does (git repo root, linked worktree -> main checkout, `-` -> `_`, else the directory's own name) | `test_a_turn_without_a_task_project_...[None,'']`, `test_the_project_is_named_the_way_agentsview_names_it`, Hermes/DSH no-cwd tests; e2e sibling canary + DSH nocwd arm |
| minor | unbounded shadow children; Hermes runs on non-user platforms | seam: `harness_capture.try_slot(pool='memory-slots')`, the child inherits the slot; memory-client.mjs and Hermes memory.py: 4 in flight; past it a counted `queue_saturated` row. Hermes skips `NON_USER_PLATFORMS` and child sessions (`parent_session_id`) | seam, DSH (memory-client) and Hermes cap tests; Hermes platform test |
| minor | seam turn markers never pruned | `surface.prune_markers` shared with `claim_injection` (same 1-day TTL) | `test_turn_markers_are_pruned_after_the_ttl` |
| minor | `--seed-av-db` writes any DB and the default ledger | `seed_cohort` refuses an existing DB file and has no default ledger; CLI needs `--seed-ledger`; eval takes `--cwd` (the task project) | `test_seeding_refuses_an_existing_db_and_needs_an_explicit_ledger` |
| minor | CC/Codex hook budget excluded interpreter start; SessionStart seam missing | `hook.process_started_at()` (/proc start time, else module import) is passed as `started_at`; SessionStart omission recorded below | `test_the_hook_counts_the_deadline_from_process_start` |
| minor | OMP turn key `user-<count>` repeats after compaction; replays uncounted | key = the user message's timestamp (`user@<ts>`; count only as fallback); `counters.replay` in memory-client | OMP compaction/replay test |
| minor | NOTES.md missing | this file | |

Consequence worth knowing: TencentDB gateway items carry no project provenance (C7 sets `scope: None`; #63 provenance
is UNMET), and a scoped request never admits an unscoped item, so with fail-closed scope the semantic layer is still
queried (receipt `capability_ids` include `semantic`) but its items never enter a project brief. The two
TencentDB-exclusion tests now assert the layer is queried / not queried instead of the item text.

## Deviations from the plan (all open ones need an owner decision or a spec amendment; none is redefined as met)

1. **Hermes: brief persisted in the session store (spec C8 red test 2 UNMET).** Hermes core stamps the bytes it sent,
   `pre_llm_call` context included, on the user row's `api_content` sidecar in `state.db` and replays them on later
   turns (`agent/turn_context.py` `_stamp_api_content_sidecar`, "persist what you send"). A plugin cannot avoid it
   without a Hermes core change (forbidden). The plugin itself writes nothing under HERMES_HOME. Test:
   `test_hermes_host_keeps_the_brief_out_of_the_session_store` (strict xfail). Owner options: accept (amend the spec
   clause for Hermes), or keep Hermes at shadow until a host change exists.
2. **Claude Code: brief persisted in its transcript.** Claude Code records hook `additionalContext` in the session
   transcript (host design). Same owner decision as 1. The echo guard keeps a re-indexed copy out of recall.
3. **DSH: brief persisted in the DSH session log.** `agent/pre-step` admitted messages are durable, and DSH's
   agent-loop invariant (`packages/core/agent-loop/src/invariant.ts`) rejects a loop-built `llm/stream` request that
   differs from the durable derivation, so there is no non-durable request seam for a plugin. AgentsView v0.44 indexes
   every DSH user message whatever its `source.kind`; the echo guard cuts briefs from recall. Same owner decision.
4. **OMP/OMO: compliant** (the `context` event result is model-visible only; e2e found no brief in the session files).
5. **No SessionStart memory seam for Claude Code / Codex.** A session start carries no user query to brief; the
   `UserPromptSubmit` seam covers #56-B. The plan listed `UserPromptSubmit/SessionStart`.
6. **Codex, Grok, OMO case B** are fixture/MCP-only here (live-only, UNVERIFIED, not counted), Grok B=UNSUPPORTED
   (pull-only) by default, as planned.
7. **claude-code / codex / grok rows** are `z0int.memory_acceptance.v0` ("z0 memory acceptance"), never the
   z0evals study schema, until C12 (planned).
8. **Semantic layer in briefs:** see the consequence above (UNMET #63 provenance, not redefined).

## Activation notes (NOT performed; all live changes need owner approval)

Order (PLAN A8; the owner decided memory injection into cloud models is opt-in per harness, default off, shadow first):
1. Prerequisites: A6 (AgentsView v0.44 synced, timer on) and `z0int memory doctor` green except, optionally, the
   semantic layer. Back up every file first (`cp -a <f> <f>.bak-z0wiring-20261003`).
2. A8a, `z0-memory` MCP entries (memory profile only): CC plugin `.mcp.json` via a plugin update, Codex plugin,
   `~/.omp/agent/mcp.json` (from `omp-extensions/z0-memory/mcp.json`), `~/.omo/agent/mcp.json`, every
   `~/.dsh/profiles/*/cordis.patch.yml` (from `harness-adapters/dsh-z0intelligence/z0-memory.cordis.yml`),
   `~/.grok/config.toml` (from `harness-adapters/grok-z0intelligence/mcp/z0-memory.toml`).
3. A8b, `memory_inject: shadow` everywhere (Hermes plugin setting in the target profile chiefstaff/clean only;
   CC/Codex `Z0INT_MEMORY_INJECT=shadow` or unset; OMP/OMO env; DSH plugin config with `model_endpoint` set to the
   profile's model URL). `allow_cloud_injection` stays false. Watch `$Z0INT_HOME/state/memory/seam/<h>.jsonl`
   (`shadow`, `no_scope`, `queue_saturated` counts).
4. A8c, per harness: `z0int memory eval --harness <h> --cohort <cohort.json> --revision <sha> --out <rows> --cwd
   <project dir>` (never `--seed-av-db` against live data: it refuses an existing DB and needs an explicit
   `--seed-ledger`). The owner reviews A-F, decides deviations 1-3, then sets
   `{"inject": {"<h>": {"allow_cloud_injection": true}}}` in `$Z0INT_HOME/config/memory.json` only for a cloud
   harness they opt in, and flips canary, then on, one harness at a time.
5. Optional A8-sem: the TencentDB gateway now runs at 127.0.0.1:8420 (owner); its items reach briefs only once they
   carry project provenance.
Rollback: `memory_inject: off` everywhere (immediate); restore the `.bak-z0wiring-20261003` copies.

## Round-2 results
red `round2/red.txt` (30 pytest + 3 node + 2 bun failures for the intended reasons), green `round2/green.txt`
(67 passed + 1 strict xfail; node 11/11; bun 9/9), suite `round2/suite.txt` (head 41aec0f vs base 3028e0c: identical
11 environment failures plus one wall-clock flake that passes in isolation), e2e `round2/e2e.txt` (45/45 checks;
persistence observed: OMP none, Hermes 7 api_content cells, Claude Code 8 transcript files, DSH 7 durable briefs).

## Round 3 (integration open item 4: MemoryUseReceipt in opportunity_record.memory, DoD D3; kill switch)
Commits on feat/wire-memory-harnesses-20261003 (new only, no rewrite), on round-2 head 41aec0f:
49e3478 red tests, ab8962d Grok contract test, 7673829 test field-name fix, 057091c implementation, a1863fe Grok
inventory test, a821ab2 late-child red test, 9655bca grace fix, 93e5478 Hermes activation doc, 085a4d1
first-turn red test, 5e69533 seam-installed marker, 8b5061b Hermes first-turn red test, 73ff88e Hermes marks on first turn.

Design (smallest join of the two detached children of one turn, on the canonical turn_key):
- `z0int.memory.seam`: after the replay gate, the turn is marked pending in `state/memory/receipts/<sha(h,turn_key)>`;
  every terminal seam row settles it (`.json`, `{"memory": receipt|null}`; first receipt wins, so a second injector's
  `double_inject_guard` row never replaces it). `receipt_for(h, turn_key)` waits up to 10 s for a pending turn,
  3 s for an unmarked turn of a harness whose seam has run here or whose shim marked it installed
  (`seam/<h>.active`, written when a non-off JS client is created and on the Hermes seam's first pre_llm_call: a cold
  child on a fresh home), else not at all. Never raises.
- `harness_capture.opportunity_record`: with no `ctx['memory']`, reads `receipt_for` and stores it validated through
  `MemoryUseReceipt.from_dict` (an invalid receipt is left out; the opportunity is still recorded). This is the one
  builder behind every harness (hook_entry child for CC/Codex/Grok/OMP/OMO/DSH, hermes_capture project child).
- Turn-key agreement per harness: CC/Codex (same hook payload functions), Hermes (`<session>:<turn>`), DSH (lineage
  turn key) already agreed (guard tests). OMP/OMO did not (memory used `user@<timestamp>`, capture a random trace id):
  the z0int-bridge now publishes its open turn per session through `globalThis[Symbol.for("z0int.bridge.openTurn")]`
  and z0-memory keys by it (falls back to the message id without the bridge). Grok had no per-turn seam: new
  `harness-adapters/grok-z0intelligence/hooks/z0-memory.json` (its own file; z0-capture.json stays capture-only) runs
  `z0int.memory.hook --harness grok prompt`, which only records the shadow receipt (any mode but off), never prints.
- Kill switch: `Z0INT_CAPTURE=0` or `config/capture.json {"enabled": false}` now turns the memory seam off too
  (seam.settings via harness_capture.enabled; JS memoryMode; Hermes memory.create). One switch makes every hook native.

Deviations: Grok gains a receipt-only hook (still pull-only, B stays UNSUPPORTED); the opportunity child may hold its
capture slot up to 10 s while a brief runs (detached, never the turn). The round-2 e2e ran CC with Z0INT_CAPTURE=0;
the round-3 copy (round2/scripts/e2e-r3.sh) runs capture on.

Activation runbook delta (not performed): Hermes target profile is `clean` only (chiefstaff retired, never targeted);
also set clean's `memory.provider: memory_tencentdb` (owner-deferred). Grok: copy hooks/z0-memory.json to
`~/.grok/hooks/` with A8b. Memory injection into cloud models stays opt-in per harness (allow_cloud_injection,
default false; shadow first). Rollback adds: remove ~/.grok/hooks/z0-memory.json.

Round-3 results (final head 73ff88e): red round2/red.txt (23 pytest + 1 changed-contract + 2 bun for the intended reasons, then
3 follow-up red/green cycles found by the e2e/suite), green round2/green.txt, suite round2/suite.txt (failure set identical to
base 41aec0f), e2e round2/e2e.txt (round-2 checks 45/45; join 10/10: opportunity_record.memory joined for all 7 harnesses, 0
missing/foreign; kill switch holds in a real CC run). Not pushed.
