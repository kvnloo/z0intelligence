# SPEC: wire cross-harness learning capture, continuous shadow learning and unified memory (TDD)

Revision 2, 2026-10-03, after the critic pass (verdict REVISE: 2 blockers, 16 majors, 9 minors). Every blocker and major is fixed or explicitly rejected with a reason in section 12 "Critique disposition". Inputs: `prior-findings.md`, the surveys `R1-learning-capture.md`, `R2-unified-memory.md`, `R3-harness-seams.md`, `R4-roadmap-gates.md`, the critique, and the build script `z0-wiring-build.js` (its base/dependency rule, isolated-homes path and port range are followed).

Facts re-checked read-only for this revision:
- z0evals main fb14919 `studies/unified-memory-v0/receipt.schema.json` harness enum is exactly `[dsh, hermes, omo, omp]`.
- `loop_export.py:213` calls `is_harness_message(intent.request)` at export time (shadow-loop 6fee859).
- `outcome_verifier.py:56-91,218-243` already hold the command regexes; `projects_dir()` (line 169) falls back to `~/.claude/projects` when `CLAUDE_CONFIG_DIR` is unset.
- agentsview clone: upstream main 430b853c; OMO parser only on `origin/feat/omo-source`.
- The Codex `z0intelligence@personal` `.mcp.json` runs route_worker through the untracked `dsh-env-exec.py` with the live Hermes venv and `HERMES_HOME`; the tool reads the DSH credential store `refs:` block (not opened by this plan).
- The build script bases a component on "that dependency's branch head" and skips a component if any dependency is unverified; it has no integrate step of its own, so the Integrate phase follows Appendix B.
- Accepted from the critique without re-measurement: live `sessions.db` user_version 74 = v0.39 dataVersion; v0.44 dataVersion 113 (full resync); `:8420` not listening; CC 2.1.288 has a SubagentStart hook. Anything else not read directly is marked **INFERRED**.

---

## 1. End state

1. **Every active harness collects learning data by itself** through its own seam (Claude Code, Codex, Grok, OMP, OMO, Hermes, DSH), in one #62 record family keyed by one canonical turn key, with work-item/attempt ids, model/policy revision, cohort at capture (subagent and automated turns separated) and no text in training rows. Labels come from Claude transcripts or read-only AgentsView rows via one documented join rule per harness. Legacy OMP v1 and DSH jev data is imported once into cohort `legacy`.
2. **Shadow procs learn at all times.** An hourly user timer runs `z0int loop tick`, which holds the quiet-lane lock shared (in-process flock(2)), verifies, imports, replays offline challengers, exports privacy-safe tables, checks sufficiency, runs `evolution_lab.verified_loop` where a prereg covers the stratum, and reports. It never promotes. It starts only after AgentsView is current, because AgentsView is the label source for six of seven harnesses.
3. **Unified memory works in every harness through one z0 surface, with no new backend**: AgentsView FTS5 + EventLog references + StatePacket router (+ TencentDB only if the owner runs the gateway). Every output is secret-scrubbed. Each harness has a memory-only MCP server `z0-memory` and an inject seam that runs detached in shadow; canary/on need owner approval and per-harness cloud-egress opt-in. dsh/hermes/omo/omp are judged by z0evals#56 A–F; claude-code/codex/grok by the same cases as "z0 memory acceptance" until the frozen study is amended (C12).
4. **Plugin layout (binding):** one harness-agnostic z0 plugin in kvnloo/z0intelligence with thin shims in `harness-adapters/` (claude-code, codex, grok, hermes, dsh) and `omp-extensions/` (OMP, OMO). The bend-native `hermes z0` stack moves in (capture now in C4a, scorer in C4b); hermes-jev-dsh moves into dsh-z0intelligence. No harness core changes, no new repos; turning anything on is install/config only.

---

## 2. Architecture

```
                      HARNESS SEAMS (config/install only; zero harness-core diff)
 Claude Code ─ plugin hooks (UserPromptSubmit/Stop/SubagentStart/SubagentStop/SessionStart) ─┐
 Codex ─ hooks-only plugin ──────────────────────────────────────────────────────────────────┼─► python -m z0int.hook_adapter
 Grok ─ ~/.grok/hooks (env GROK_HOOK_EVENT) ─────────────────────────────────────────────────┘    (lean hook_entry; build detached)
 Hermes ─ hermes-z0intelligence (C4a capture: observer #385 + decisions; automatic pre_llm_call inert while hermes=false)
 OMP/OMO ─ omp-extensions/z0int-bridge turn_open{cwd}/turn_close ─► z0int.bridge.runtime (protocol parity with a9cbbed)
 DSH ─ dsh-z0intelligence observe-only agent/request middleware (router OFF; Jev shadow plane OFF by default)
                                     │  all funnel into
                                     ▼
 ┌──────────────── z0int SHARED CORE (kvnloo/z0intelligence src/z0int) ────────────────────────────────────┐
 │ harness_id.turn_key + aliases · harness_capture: z0int.<harness>.{opportunity_record,turn_outcome,       │
 │ failure}.v0 {turn_key, work_item_id, attempt_id, model_id, policy_revision, cohort, capture flags,       │
 │ privacy_class, recorded_at} · bounded spool, persisted drops, bounded close · check_class (extracted     │
 │ from outcome_verifier) · decision_opportunity + deterministic_gate (task cwd; confidence never = ACT)    │
 └─────────────────────────────────────────────────────────────────────────────────────────────────────────┘
         │ append-only JSONL $Z0INT_HOME/state/<harness>/{opportunities,outcomes,failures,drops}.jsonl
         ▼
 ┌── z0int loop tick (systemd --user timer; in-process flock(2) LOCK_SH on quiet-lane.lock; nice/idle; no GPU) ┐
 │ 1 verify   CC transcripts (CLAUDE_CONFIG_DIR) | AgentsView mode=ro + per-harness join rule + cohort rule │
 │ 2 import   legacy omp-v1 / omp-cognition-shadow / dsh-jev (cohort legacy, idempotent)                   │
 │ 3 shadow   shadow_slot challengers on pinned opportunities ─► shadow_decisions.jsonl                    │
 │ 4 export   per harness × cohort training_row.v0 + manifest (assert_private); `loop merge` across hosts  │
 │ 5 suff.    300 / 30 / 10 / K=5 both classes ─► projection; discrimination vs base rate                  │
 │ 6 learn    evolution_lab.verified_loop (pinned SHA; prereg-covered strata only)                          │
 │ 7 report   summary.json; promotion_request.v0 for the owner — never promotes                             │
 └──────────────────────────────────────────────────────────────────────────────────────────────────────────┘
 UNIFIED MEMORY (read path)
  AgentsView sessions.db (FTS5; v0.44 dataVersion 113 after one-off resync; agentsview-sync timer, flock -s -w 60)
  TencentDB gateway (:8420; UNAVAILABLE until owner A8-sem)        EventLog (references + EventIdentity; #23 path)
            └──► z0int memory: context_resolve kind=memory ─► secret scrub ─► StatePacket router ─► memory_brief
                  ├─ MCP profile "memory" as server z0-memory (stdio; memory tools only; no route_worker)
                  └─ inject seams (off|shadow|canary|on; shadow detached; canary/on 300 ms deadline;
                     single owner; allow_cloud_injection per harness, default false)
  every use ─► MemoryUseReceipt ─► DecisionReceipt.extra.memory + opportunity_record.memory
```

---

## 3. Per-harness table

| Harness | Activity | Capture today | Capture seam (component) | Label source + join rule | Cohort at capture | Memory read | Memory inject (component) | Acceptance gate |
|---|---|---|---|---|---|---|---|---|
| Claude Code | live (10-03) | opp/obs/ver from the shadow-loop branch | plugin hooks + SubagentStart/SubagentStop (C1) | transcripts via CLAUDE_CONFIG_DIR (existing verifier) | interactive / agent / harness | plugin `.mcp.json` z0-memory (C8) | UserPromptSubmit/SessionStart additionalContext (C8) | G-MEMZ0 (G-MEM56 after C12) |
| Codex | live (09-30) | MCP route receipts only | new hooks-only plugin `codex-z0intelligence`; `z0intelligence@personal` untouched (C1, A9) | AgentsView `codex` rows, session + user-message ordinal (C2) | interactive / automated (exec) | plugin z0-memory MCP (C8) | UserPromptSubmit additionalContext (INFERRED parity) (C8) | G-MEMZ0; live B owner-run |
| Grok | live (09-30) | none | `~/.grok/hooks/z0-capture.json`, env detection (C1) | AgentsView `grok`, GROK_SESSION_ID + ordinal (C2) | interactive | z0-memory MCP in config.toml (C8) | none by default: pull-only, B=UNSUPPORTED unless a PostToolUse push seam is proven (C8) | G-MEMZ0 (B stop condition) |
| OMP | live (10-01) | v1 spine; cognition-shadow 97.5% transport errors | z0int-bridge turn_open{cwd}/turn_close (C3) | AgentsView `omp` + legacy gold/negative (C2) | interactive / agent | `~/.omp/agent/mcp.json` z0-memory (C8) | `omp-extensions/z0-memory` context/before_agent_start (C8) | G-MEM56 |
| OMO | low (09-29) | none | senpi re-export of the bridge (C3) | AgentsView `omo` only with a fork build (owner question 6), else none | interactive / agent | `~/.omo/agent/mcp.json` z0-memory (C8) | senpi inject shim, or B=UNSUPPORTED (C8) | G-MEM56 (B stop condition possible) |
| Hermes | live (7 services; 1,789 sessions) | **none** | native plugin `hermes-z0intelligence` capture half (C4a) | AgentsView `hermes:<sid>` + ordinal; post_tool_call check-class (C2/C4a) | interactive / automated (cron, kanban, cluster) | plugin tool (toolsets carry no_mcp) (C8) | memory.py composed into the one pre_llm_call; TencentDB provider keeps its slot (C8) | G-MEM56 |
| DSH | live (09-30) | private jev receipts until 09-30 | `dsh-z0intelligence` observe-only middleware; router off (C5) | AgentsView `deepseek-harness` after G-AV (C2) | interactive / automated | z0-memory MCP rows in every profile (C8) | `memory.mjs` on agent/pre-step (C8) | G-MEM56 |
| agy | intermittent | none | none (executor turns are captured through OMP) | AgentsView floor | n/a | AgentsView/z0 MCP if reactivated | deferred | none |
| OpenCode, Gemini, Kimi, Cursor | dormant | none | deferred | AgentsView floor | n/a | when reactivated | deferred | none |

---

## 4. Components (8 build-now, 5 gated follow-ups)

Conventions: worktree `/mnt/zer0models/z0-wt/wiring/wt/<id>` (`git -C <repo_path> worktree add -b <branch> <wt> <base>`); isolated homes `/mnt/zer0models/z0-wt/wiring/homes/<id>/`; private ports 11540–11559; a socket guard fails any connect to 11501 or a non-loopback address; CPU-heavy runs under `flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock`; red tests first, then the smallest change, then the e2e. "Regression guards" named in a purpose must stay green but are not red tests.

| Id | Base (single dependency) | Build now | One-line scope |
|---|---|---|---|
| C1-loop-core | origin/feat/shadow-loop-v0 (6fee859 = origin/master 0159808 + 1) | yes | One cross-harness capture record, shared capture core, lean hook adapter, and the Claude Code / Codex / Grok capture shims |
| C2-loop-consumers | feat/wire-loop-core-20261003 (C1 head) | yes | Labels and cohorts for every harness: harness-generic verifier/export, AgentsView read-only turn reader with per-harness join rules, cohort classifier, multi-host merge, legacy importers |
| C3-omp-omo-capture | feat/wire-loop-core-20261003 (C1 head) | yes | OMP and OMO capture through the z0-owned bridge (oh-my-pi#109), cognition-shadow hygiene, pinned install with protocol parity |
| C4a-hermes-capture | feat/wire-loop-core-20261003 (C1 head) | yes | Hermes capture plugin: harness-adapters/hermes-z0intelligence absorbs the bend-native observer (#385) and z0int-decisions capture, with the capture-path bug fixes |
| C5-dsh-plugin | feat/wire-loop-core-20261003 (C1 head) | yes | DSH observe-only capture: hermes-jev-dsh folds into harness-adapters/dsh-z0intelligence; router stays off (#95); Jev shadow lanes off by default |
| C6-learning-tick | feat/wire-loop-consumers-20261003 (C2 head) | yes | Continuous learning: scheduled `z0int loop tick` holding the quiet-lane lock shared in-process, offline shadow slot, promotion requests, unit files |
| C7-memory-core | feat/wire-loop-core-20261003 (C1 head) | yes | One z0 memory surface over the existing substrate: doctor, AgentsView + TencentDB capabilities, EventIdentity, MemorySnapshot, secret scrub, memory-use receipts, memory-only MCP profile |
| C8-memory-wiring | feat/wire-memory-core-20261003 (C7 head; C7 is on C1) | yes | Memory read and inject seams in every harness (shadow detached, canary/on with deadline), single injection owner, cloud-egress opt-in, acceptance receipts |
| C4b-hermes-scorer-move | feat/wire-hermes-capture-20261003 (C4a head) | no | Move the bend-native API-attempt scorer/evaluator, score_worker/worker, bridge and `hermes z0` runtime CLI into hermes-z0intelligence (P-1 service half, P-5, P-6) |
| C9-bend-native-z0-removal | main (e85e65e5, v0.4.0) | no | bend-native keeps Bend proof verification only; remove the z0 stack (moved to z0int); fix Bend-proof bugs P-4/P-8/P-12 |
| C10-hermes-jev-skills-dsh-removal | origin/hermes-jev/omp-adapter (9ea5777; dsh/ is absent on main 30f20b1) | no | hermes-jev-skills keeps only the Jev decision layer; the DSH plugin moved to z0int |
| C11-el-verified-loop-v1-prereg | origin/exp/verified-loop-v0 (1b80a4e) | no | evolution-lab verified_loop v1 pre-registration with harness and cohort strata |
| C12-z0evals-memory-study-harnesses | main (fb14919) | no | z0evals unified-memory study v1: add claude-code, codex and grok to the frozen study (harness enum + README scope) |

Full red tests, e2e and activation per component are in Appendix A.

---

## 5. Order

```
C1 loop-core ──┬─► C2 loop-consumers ──► C6 learning-tick ──► (C11 prereg, gated)
               ├─► C3 omp-omo-capture
               ├─► C4a hermes-capture ──► (C4b scorer move, gated) ──► (C9 bend-native removal, gated)
               ├─► C5 dsh-plugin ──► (C10 hermes-jev-skills removal, gated)
               └─► C7 memory-core ──► C8 memory-wiring ──► (C12 z0evals study v1, gated)
Integrate phase: integrate/wiring-20261003 = no-ff merges of all verified heads (Appendix B)
```

1. C1-loop-core: shared record, turn key, lean hook adapter, capture flags, Claude Code + Codex + Grok capture shims (everything depends on it)
2. Wave 2 in parallel, each based on C1 only: C2-loop-consumers (labels, cohorts, merge, legacy), C3-omp-omo-capture, C4a-hermes-capture, C5-dsh-plugin, C7-memory-core (owner capture order OMP -> Hermes -> DSH is kept for activation)
3. Wave 3: C6-learning-tick (depends on C2 only) and C8-memory-wiring (depends on C7 only); a failure in C3/C4a/C5 no longer cancels learning or memory
4. Integrate phase (build script): create local branch integrate/wiring-20261003 in /mnt/zer0models/z0-wt/wiring/wt/integrate from origin/feat/shadow-loop-v0 by no-force `git merge --no-ff` of every VERIFIED z0intelligence component head in the order C1, C2, C6, C3, C4a, C5, C7, C8; resolve shim-dir conflicts (hermes-z0intelligence, dsh-z0intelligence, claude-code/codex .mcp.json) with a merge commit plus a test; run the full suite and the cross-harness e2e from that tree. Publish pushes it as a new branch.
5. Gated follow-ups (next build run): C4b after C4a verified; C9 after C4a+C4b parity; C10 after C5 parity; C11 after owner prereg approval; C12 after owner approval to amend the z0evals study
6. Activation runbook (owner-approved, one step at a time): A0 pin + preflight -> A1 Claude Code -> A2 OMP/OMO -> A3 Hermes -> A4 DSH -> A5 Codex/Grok -> A6 AgentsView upgrade + one-off resync + sync timer -> A7 loop tick timer -> A8 memory shadow per harness (A8-sem optional) -> per-harness canary -> on; A9 legacy route_worker seams per owner decision

---

## 6. Gates

| Gate | Blocks | Status 2026-10-03 |
|---|---|---|
| G0 safety: no build or test reaches 127.0.0.1:11501 or a live HERMES_HOME; no Hermes hook opens a socket; AgentsView require_auth stays on; exactly one Hermes capture vehicle; only claude-code keeps automatic.* calls | every activation step; A3 Hermes | open; enforced by socket-guard and config tests in C1/C4a/C5 |
| G-PIN: integrate/wiring-20261003 pushed; pinned checkout + venv-wiring (incl. evolution-lab 1b80a4e) built from it; install mechanism per harness is local-directory marketplace / pinned path / --ref SHA, unless the owner merges to master | A1-A8 | not met (created by the Integrate/Publish phases) |
| G-CAP per harness: one REAL turn gives joined opp+outcome rows; off vs shadow identical; fail-open receipted; drops persisted; bounded close; off the hot path; task cwd; no text in training rows; harness diff = 0 | the harness counting as collecting (D1) | Claude Code partly met (branch code); others not met; DSH may stay unit-proven only until the owner approves the llm-replay e2e |
| G-AV: AgentsView v0.44.0 (or an owner-approved fork build with feat/omo-source) installed; one-off v74 -> v113 resync completed outside experiment windows after measuring it on a reflink copy; claude_project_dirs includes ~/.claude-home; sync timer on | non-CC labels (D1 verified rows, D2); A7 loop tick timer; D3 doctor green; DSH memory | not met: binary v0.39.0, DB user_version 74, sync not running since 10-01, no DSH agent; OMO parser absent in v0.44.0 |
| G-SUFF (el#24 prereg v0): MIN_ROWS 300, MIN_NEG 30, MIN_GROUPS 10, every one of 5 folds holds both classes, on at least one stratum; stop search if not reached within 8 weeks of M1 | any learning or improvement claim; M3 population search | not met (0 analysis rows on this host) |
| G-PREREG: v1 prereg with harness/cohort strata | verified_loop on non-Claude-Code strata (C11) | not drafted |
| G-SLOT (z0int#56 M2): challengers load read-only, decisions recorded and never shown, latency measured off the hot path, fail-open with backend_unavailable | shadow-slot comparisons counting as evidence | not built (C6 builds the offline form) |
| G-PROMO: protected score API (z0evals#72, RFC only), live shadow evidence, owner approval per candidate x decision family, full #56 contract fields, simple controls retained | any promotion past shadow | not reached; no auto-promotion class exists or is planned |
| G-MEM56 (z0evals#56 A-F on frozen cohort.json; rows validate receipt.schema.json fb14919) for dsh, hermes, omo, omp | those four harnesses counting as memory-wired (D3) | DSH search-only GREEN; Hermes smoke n=1 on a core-patched branch; full matrix not met |
| G-MEMZ0 (z0 memory acceptance A-F, z0int.memory_acceptance.v0) for claude-code, codex, grok; becomes G-MEM56 after C12 | CC/Codex/Grok counting as memory-wired (D3) | not started; the z0evals enum excludes these harnesses |
| G-SEM: TencentDB gateway reachable with owner-supplied auth | semantic layer in live results; z0int#63 TencentDB canonical provenance | UNAVAILABLE: :8420 not listening; provider configured but not installed (owner 09-29) |
| G-INJECT (hermes-agent#320, oh-my-pi#79): shadow -> frozen-cohort canary -> on, single injector, native fallback, per-harness allow_cloud_injection | memory_inject canary/on in any harness | not started |
| G-#63 / G-#66: P0 acceptance list; contract tests must not regress | C7 merge | EventLog/OptMem/contract integrated; adapters checklist unmet |
| G-#95 governed dispatch: #94 PASS + z0evals#82 import green + #95 checklist, then a new packet with the same contract | DSH router:true; automatic.json hermes/dsh on; NEW route_worker/delegate_worker exposure in Hermes/DSH/OMO | CLOSED (#94 has no PASS; z0evals#82 open). Pre-existing route_worker exposure in OMO/DSH is an A9 owner decision |
| G-OWNER-LIVE: every live config/install change has a backup, a rollback and explicit owner approval; the owner runs harness turns that need credentials or are paid (DSH, Grok, Codex/OMO live B) | A0-A9 activation steps | required |

---

## 7. Activation runbook (live; each step needs owner approval; in this order)

General rules: back up every touched file to `<file>.bak-z0wiring-20261003` first; each step needs explicit owner
approval; never restart `z0intelligence.service`; never run `dsh`.

**A0 pin + preflight**
- Publish pushes `integrate/wiring-20261003`. Create a detached checkout `/mnt/zer0models/z0-wt/pinned/z0intelligence` at that SHA and build `/mnt/zer0models/z0-wt/venv-wiring` from it
  (plus evolution-lab at 1b80a4e). Record the SHA in ACTIVATE.md. Alternative (owner question 2): merge to master.
- Read-only, counts only, with `--out-root <scratch>`: `z0int memory doctor` against the real DB (expected red today),
  `z0int loop tick --dry-run` against the real `~/.z0int`, and a C2 importer dry run over `~/.dsh/jev/receipts.jsonl`.
  Record the baseline, including the legacy sources listed out of scope.

**A1 Claude Code (C1)** — local-directory marketplace at the pinned checkout; `Z0INT_PYTHON` → venv-wiring; one real
turn and one subagent turn. Rollback: restore `settings.json`, remove the marketplace entry.

**A2 OMP and OMO (C3)** — save the link listing; `omp_bridge_install.py --target /mnt/zer0models/z0-wt/pinned/z0intelligence` (bridge + local-cognition
only); OMO shim. Rollback: restore link targets, delete the shim.

**A3 Hermes (C4a)** — owner names the profile and checks `hermes --version` against the recorded fork SHA; back up
`config.yaml` and the plugin dir (record `readlink`); `hermes plugins install …/hermes-z0intelligence --ref <SHA> --force`;
settings `mode: shadow`. Rollback: restore both.

**A4 DSH (C5)** — back up every profile; repoint `automatic-z0intelligence` to the pinned checkout with `capture: true`,
`router: false`; remove the hermes-jev-dsh bundle. Owner runs one turn. Rollback: restore backups and link.

**A5 Codex and Grok (C1)** — install the hooks-only `codex-z0intelligence` from the pinned marketplace (leave
`z0intelligence@personal` as is); write `~/.grok/hooks/z0-capture.json`. Owner runs one turn each.

**A6 AgentsView (G-AV; label source for D1/D2, memory substrate for D3)**
1. Snapshot: make sure no agentsview process (incl. an MCP-spawned one) is running, then
   `cp --reflink=always` `sessions.db` together with `sessions.db-wal`/`-shm` (btrfs) and back up `config.toml`.
2. Measure: run the v0.44.0 binary against a reflink copy in a scratch data dir (owner-approved CPU job, outside
   experiment windows) and record the resync duration. If the OMO fork build is chosen (owner question 6), measure that.
3. Upgrade + one-off resync at a time the owner picks outside experiment windows (or chunked per source), NOT under the
   hourly wrapper. Set `claude_project_dirs = [~/.claude/projects, ~/.claude-home/projects]`; keep `require_auth`.
4. Enable `agentsview-sync.timer` (`flock -s -w 60 -E 0`). Proof: `z0int memory doctor` green (semantic layer may be
   UNAVAILABLE). Rollback: disable the timer, restore `config.toml`; restore the DB from the snapshot only if damaged
   (the binary is not downgraded once the DB is v113).

**A7 learning tick (C6)** — install evolution-lab 1b80a4e into venv-wiring and record `learner_sha`; install the unit
files (with `Environment=` lines); `enable --now z0int-loop-tick.timer`. Proof: the first summary is not degraded and
the second is idempotent. Rollback: `disable --now`, remove the units.

**A8 memory (C8)** — A8a z0-memory MCP entries everywhere (backups first); A8b `memory_inject: shadow` everywhere,
`allow_cloud_injection: false`; A8c per harness the acceptance eval runs live, the owner reviews A–F, sets
`allow_cloud_injection` for that harness and flips canary then on. A8-sem (optional): owner runs the TencentDB gateway
with the key from their own secret store. Rollback: `memory_inject: off`, restore configs, stop the gateway.

**A9 legacy route_worker seams (owner decision per seam)** — Codex `z0intelligence@personal`; OMO `~/.omo/agent/mcp.json`
`z0intelligence` entry and skill paths into z0int-canonical and `~/plugins`; the four DSH profiles'
`intelligence-z0intelligence` MCP rows. For each: back up, then either keep it (recorded exception in ACTIVATE.md) or
replace it with the z0-memory profile from the pinned checkout. Rollback: restore the backup.

---

## 8. Definition of done

1. D1 capture: Claude Code, Codex, Grok, OMP, OMO, Hermes and DSH each produce, from one REAL live turn, an opportunity_record.v0 and a turn_outcome.v0 joined on the canonical turn_key in $Z0INT_HOME/state/<harness>/, with work_item_id/attempt_id, model_id/policy_revision and a capture-time cohort (interactive/agent/automated/harness). Shadow is inert (off vs shadow gives an identical harness-built request). Fail-open is receipted, drops are persisted, close() is bounded, the opportunity uses the task cwd, training rows contain no text, the harness tree diff is 0 files, and turn-on was install/config only. Claude Code subagent turns produce agent-cohort opportunities joined to their outcomes.
2. D1b legacy: the OMP v1 spine (gold/negative tiers only as labels), cognition-shadow (served answers only) and DSH jev receipts are imported idempotently into cohort legacy and never pooled with live cohorts. Other legacy sources are excluded with a recorded reason (SPEC section 9).
3. D2 continuous learning: z0int-loop-tick.timer runs `z0int loop tick` with no manual step; the tick holds the quiet-lane lock with flock(2) LOCK_SH in-process (skips with a receipt during exclusive windows) and its unit sets CLAUDE_CONFIG_DIR, Z0INT_HOME and AGENTSVIEW_DATA_DIR. It runs only after AgentsView is upgraded, resynced and syncing (A6), so non-CC labels exist. Each tick writes per-harness x cohort tables with manifests, shadow decisions for every new opportunity, per-stratum sufficiency with a projection, a discrimination metric next to its base rate, and verified_loop reports for prereg-covered strata. It is idempotent, never promotes, never writes harness config, never contacts the live service, and never writes learned artifacts into a git tree. `z0int loop merge` merges host tables by turn_key without pooling.
4. D2b promotion: a SHADOW_CANDIDATE only produces promotion_request.v0 for owner review. Promotion past shadow needs the protected score (z0evals#72), online shadow evidence and owner approval per candidate x decision family through [sanity, replay, shadow, promoted].
5. D3 unified memory: `z0int memory doctor` is green for freshness, deepseek-harness agent present, ~/.claude-home indexed, user_version == binary dataVersion and require_auth on; it reports the TencentDB semantic layer honestly (UNAVAILABLE unless the owner did A8-sem). The z0-memory MCP (memory tools only) is reachable in every harness. One inject seam per harness runs in shadow, detached. For dsh, hermes, omo and omp, z0evals#56 cases A-F pass on the frozen cohort.json with rows validating against receipt.schema.json at fb14919 (B may be recorded UNSUPPORTED for OMO as a documented stop condition). For claude-code, codex and grok the same A-F run is reported as "z0 memory acceptance" (z0int.memory_acceptance.v0), not as a #56 pass, until C12; Grok B is UNSUPPORTED (pull-only) unless a push seam is proven. Fixture-only runs never count. MemoryUseReceipt rows appear in real receipts and opportunity records. No secret-shaped string leaves memory outputs. Cloud-model injection is enabled per harness only by owner opt-in. No new DB, service or port. No double injection.
6. D4 control plane: the z0int#66 contract tests stay green. The z0int#63 P0 items in scope are green: FTS hits carry canonical event_uid; temporal + lexical + semantic resolution has no duplicate evidence; workers cannot write the ledger; no production store is mutated. "TencentDB-derived memories carry canonical provenance" is reported UNMET while the gateway is unavailable (items carry provenance_ok=false), not redefined.
7. Hygiene: all component branches and integrate/wiring-20261003 are pushed to kvnloo/* with no PRs, no force and --no-follow-tags. Every NEW live seam points at the one pinned checkout/venv built from the pushed integrate SHA (recorded in ACTIVATE.md). No new integration code lives in harness forks or non-git dirs. The pre-existing route_worker seams (Codex z0intelligence@personal, OMO mcp.json z0intelligence + skill paths, DSH intelligence-z0intelligence MCP rows) are inventoried and each is either replaced by the memory-only profile or kept as a recorded owner exception (A9). The C4b, bend-native (C9) and hermes-jev-skills (C10) moves are staged after parity.

---

## 9. Out of scope (with reasons)

- Governed dispatch, remote execution and automatic routing for Hermes/DSH (NEW route_worker exposure, automatic.json hermes/dsh on, DSH router:true): blocked by G-#95
- z0evals protected score API (#72) and the deopt drill (#74): owned by z0evals; listed as promotion gates only
- Any auto-promotion class, and any live mutation of production prompts or config by the loop
- Repointing or restarting the live z0intelligence.service (11501, z0int-canonical a9cbbed): separate owner decision (question 12); capture uses file spools and memory uses stdio MCP, so nothing here needs it
- A new memory DB, vector/graph store, recall daemon, :8791 service, second receipt format or second procedural compiler; AgentsView `recall extract` as a semantic store; Mem0/Sibyl/Graphiti
- Live hooks for dormant harnesses (OpenCode, Gemini, Kimi, Cursor): AgentsView memory floor only
- agy (antigravity-cli): AgentsView floor only. It is intermittent (last activity 10-01 02:13), its hooks.json/mcp_config.json seams are known only from binary strings (INFERRED), and its main live use is as an OMP executor, whose turns are already captured by the OMP bridge (C3)
- Legacy ~/.z0int/episodes/next_action.jsonl (76,970 rows, 4,921 gold): carries a raw `user` text field and was mined from the live Hermes state.db by compile.py; importing it would copy private text and re-use a live-DB derivative. The same Hermes signal is reachable read-only via AgentsView hermes rows (C2)
- Legacy replay/task_snapshots.jsonl (1,008), shadow/jev-fly.jsonl (766), shadow/preflight.jsonl (661) and Codex route receipts: no outcome join and a different decision family (routing/preflight); counted in the A0 baseline only
- flow_predictions.jsonl (17,053 flow_prediction.v1 rows, still writing): not turn-level; a tokenomics flow-prediction family with its own self-labels, owned by tokenomics, not a DecisionOpportunity family. Candidate for a separate tokenomics loop later
- z0int#22 cross-project eval with less raw-data access: follows once A-F pass; C7 ships only the per-query measurement (`memory bench`)
- NeMo Relay runtime observation (z0#21) and any code in kvnloo/hermes-agent or oh-my-pi forks
- kvnloo/z0 registry edits (z0#16) and Bend #389/#390 promotion decisions
- Changing the live hermes-jev OMP extension (prompt shell injection via execSync, owner memory): raised as owner question 13, not fixed here
- Opening PRs or posting anywhere upstream; branches are pushed to kvnloo/* only

---

## 10. Open questions for the owner

1. Which Hermes profile is active (clean, chiefstaff or intake), and may A3 install hermes-z0intelligence into it, replacing the routing-only plugin of the same name, after backing up config.yaml and the plugin dir?
2. Install pinning: the Claude/Codex marketplaces read kvnloo/z0intelligence master. Do you want to (a) merge integrate/wiring-20261003 into master of your own repo after review, or (b) install from a pinned local checkout at the integrate SHA (the plan default)?
3. EventLog scope: confirm references only (EventIdentity + locator), never harness transcript bodies (reconciles z0int#63 with the z0 registry "not_here: source memory databases"). C7 ships it behind a flag, off until you confirm.
4. Approve the two new systemd user timers (z0int-loop-tick hourly with an in-process shared lock; agentsview-sync every 30 min under flock -s -w 60) as the "at all times" mechanism, instead of an always-on agentsview daemon?
5. AgentsView upgrade: v0.44.0 has dataVersion 113, so the first start re-syncs every source of the 20 GB v74 DB (about 11k sessions). Approve (1) measuring that resync on a reflink copy in scratch, (2) running it once at a time you choose outside experiment windows, (3) a WAL-consistent snapshot first (no MCP-spawned agentsview running, or -wal/-shm copied too), and (4) claude_project_dirs for ~/.claude-home?
6. OMO in AgentsView: v0.44.0 has no OMO parser (it exists only on fork branch feat/omo-source). Build the binary from that fork branch, or accept that OMO labels and memory stop until upstream has it?
7. TencentDB: the gateway :8420 is not running and the provider was "configured but not installed" (09-29). Do you want it running (A8-sem; you supply the key as an env var at start), or should the semantic layer stay UNAVAILABLE and #63 TencentDB provenance stay UNMET?
8. Legacy route_worker seams (A9): Codex z0intelligence@personal runs route_worker via the untracked dsh-env-exec.py with the live Hermes venv and HERMES_HOME; OMO ~/.omo/agent/mcp.json runs route_worker from the .work tree against 11501 and OMO skills come from z0int-canonical and ~/plugins; all four DSH profiles run intelligence-z0intelligence (route_worker, openjev venv, failOnStartupError). Keep each as a recorded pre-#95 exception, or replace it with the memory-only z0-memory profile?
9. Cloud egress: memory briefs drawn from all harness history (AgentsView indexed credential-bearing tool output) would be sent to xAI/OpenAI/DeepSeek/Anthropic once canary/on. Secret scrubbing is built and default is off per harness; for which harnesses do you approve allow_cloud_injection, and do you want a Grok project-scoped rules file at all?
10. DSH: C5 turns the online Jev shadow lanes off by default (Jev is reached only through a configured z0 service URL) and A4 removes the hermes-jev-dsh bundle, which stops the private jev receipts. Accept that DSH learning is offline shadow-slot only until you configure a URL?
11. Will you run the credentialed or paid live-proof turns (DSH, Grok, Codex, OMO live case B), and approve the DSH llm-replay source-tree e2e?
12. Repointing the live z0 service to the integrate branch (it runs closed-PR branch a9cbbed and lacks decision_opportunity/state_packet/memory_contract) is not required by this plan. Confirm it stays a separate decision.
13. The live OMP hermes-jev extension was flagged (owner memory) for a prompt shell injection via execSync. This plan does not touch it. Do you want it disabled or fixed as a separate item?
14. Approve drafting the verified_loop v1 prereg with harness x cohort strata (C11), and amending the frozen z0evals unified-memory study to add claude-code, codex and grok (C12)?
15. C4b moves the #386/#387 API-attempt scorer/evaluator, whose question is DEGENERATE (0/653). Still move it (next run), or retire it with bend-native C9?
16. OMP automatic.json omp=true is live routing, not shadow. Keep it? C3 deliberately leaves the z0int-intelligence routing extension on its current target.

---

## 11. Continuous learning: schedule, resources, promotion

**Schedule.** systemd user timer z0int-loop-tick.timer: OnCalendar=hourly, RandomizedDelaySec=300, Persistent=true. ExecStart=/mnt/zer0models/z0-wt/venv-wiring/bin/z0int loop tick --lock /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock --lock-wait-s 1800 --budget-s 600 (the tick takes flock(2) LOCK_SH in-process, the same lock `flock -s` takes, so it can write a skipped_exclusive_window receipt and exit 0 on timeout), with Environment=CLAUDE_CONFIG_DIR=~/.claude-home Z0INT_HOME=~/.z0int AGENTSVIEW_DATA_DIR=/mnt/zer0models/sft-svlm/data/agentsview, Nice=19, IOSchedulingClass=idle. A companion agentsview-sync.timer (every 30 min, ExecStart=/usr/bin/flock -s -w 60 -E 0 /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock agentsview sync) keeps the label and memory index fresh instead of an always-on daemon; it is enabled only after the one-off v74 -> v113 resync has completed as a separate owner-scheduled job outside experiment windows. The loop tick timer is enabled only after AgentsView is current (A6 before A7). Capture itself is continuous in the harness hooks and needs no schedule.

**Tick steps:**
1. Acquire flock(2) LOCK_SH on the quiet-lane lock in-process; when --lock-wait-s expires, write skipped_exclusive_window and exit 0
2. verify: outcome_verifier --harness <each> (Claude transcripts for claude-code from CLAUDE_CONFIG_DIR; AgentsView mode=ro TurnReader with the per-harness join rule for the others; pending_index when stale; label_source_empty when a source has zero matching sessions)
3. import: legacy_import omp-v1 / omp-cognition-shadow / dsh-jev, incremental by watermark, cohort legacy
4. shadow: shadow_slot replays the registered challengers (deterministic_gate champion, always_escalate, base_rate, routine decide_shadow, hash-registered el learned gates; model backends only if enabled and served) over every new pinned opportunity record and writes state/<harness>/shadow_decisions.jsonl
5. export: loop_export per harness x cohort gives training_row.v0 + manifest (assert_private: no prompt/response/command/path text) plus a shadow-decision table; merged host tables from `z0int loop merge` are consumed the same way
6. sufficiency: per stratum MIN_ROWS 300 / MIN_NEG 30 / MIN_GROUPS 10 / K=5 with both classes, plus a days-to-sufficiency projection
7. learn: for strata covered by a registered prereg (v0: claude-code; others after C11), run `python -m evolution_lab.verified_loop --table T --out R.json --md R.md` from the pinned learner_sha and record INSUFFICIENT_DATA / NO_IMPROVEMENT / SHADOW_CANDIDATE (learner_missing if the pin does not match)
8. report: ~/.z0int/loop/reports/<day>/summary.json (per-harness captured/verified/labelled, join rate, drops, failures by kind, sufficiency, discrimination metric vs base rate, results) plus a tokenomics event for the tick cost
9. promotion request: a SHADOW_CANDIDATE writes promotion_request.v0 (candidate hash, family, evidence links, #56 contract fields to fill) for the owner; nothing is promoted or configured; release the lock

**Resource rules.** Every scheduled or CPU-heavy job holds the quiet-lane lock /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock in SHARED mode: the tick via in-process flock(2) LOCK_SH, agentsview-sync and all test suites/e2e via `flock -s`. Shared holds are bounded (tick budget 600 s, sync wait 60 s) so they cannot starve exclusive quiet-timed windows; the one-off AgentsView resync (about 11k sessions, 20 GB DB) is NOT run under the hourly wrapper: it is measured first on a reflink copy in scratch, then run once as an owner-scheduled job outside experiment windows (or chunked per source). Jobs run at Nice=19 with idle IO; an exhausted budget is reported as partial_measurement. The tick uses no GPU by default. Model-backed challengers are off by default; when the owner enables them they run only against already-served endpoints (z0-farm-llama) under the Quackles arbiter, and the tick never starts, loads or unloads model servers. A dead backend is counted as backend_unavailable once per tick, with no retries. AgentsView is read with sqlite mode=ro. The tick never calls the live z0 service (11501), never reads the live Hermes home, writes only under $Z0INT_HOME/{state,loop}, and refuses an out-root inside a git tree so learned artifacts trained on personal data are never committed.

**Promotion rule.** No auto-promotion, ever. A verified_loop SHADOW_CANDIDATE (coverage within 0.02, delta < 0 with CI upper bound < 0, delta < 0 in at least 3 of 5 folds, L_crc joint risk <= alpha + 0.01, zero illegal ACTs) earns only shadow status. Going past shadow needs all of: a new registration with online shadow evidence from the shadow slot; a win on the protected z0evals score API (#72) when it exists; the full #56 contract (family identity, evidence/state version, cohorts, operating region, calibration, verifier contract, cost vector, invalidators, drift monitors, fallback, demotion reason, lineage); simple controls retained in the population; and explicit owner approval per candidate per decision family through lifecycle [sanity, replay, shadow, promoted]. A promoted policy only recommends; AODL/deterministic authority can deny, model confidence never grants authority, and drift monitors (routines.py future-split demotion; z0evals#74 drill) force fallback.

---

## 12. Critique disposition

| Severity | Critique item | Disposition |
|---|---|---|
| blocker | C8 / D3 / G-MEM: z0evals receipt.schema.json enum is [dsh, hermes, omo, omp] (verified at fb14919) | ACCEPTED. D3 split: G-MEM56 for the four study harnesses; G-MEMZ0 ("z0 memory acceptance", z0int.memory_acceptance.v0) for claude-code/codex/grok. C8 red test asserts the enum mismatch explicitly. New gated follow-up C12 amends the frozen study as v1 with owner approval. |
| blocker | Ordering: integrate/wiring-20261003 never created; one REVISE on C4 cancels C6 and C8 | ACCEPTED. C6 now depends only on C2 and seeds its e2e with synthetic rows for all seven harnesses. C8 now depends only on C7 (based on C7 head, which is on C1); its seams are separate files in the shim dirs. The integrate branch is created in the Integrate phase (Ordering + Appendix B), which the build script reads from SPEC/PLAN. Per-seam C8a-d split REJECTED: it would exceed the 8 build-now cap, and the C7-only dependency already removes the cascade. |
| major | A7 AgentsView: v74 is v0.39 dataVersion; v0.44 is 113 and triggers a full resync; no OMO parser; sync placed after A6 | ACCEPTED. Fact corrected everywhere. New G-AV gate. A6 (AgentsView) now precedes A7 (loop tick). The resync is measured on a reflink copy in scratch, then run once as an owner-scheduled job outside experiment windows, never under the hourly wrapper. WAL-consistent snapshot. OMO fork build vs gap is owner question 6. C2 reader supports user_version 74 and 113 explicitly. |
| major | C1 privacy vs regression: loop_export.py:213 reads intent.request for is_harness_message | ACCEPTED (verified at line 213). Content-free flags are computed at capture; red test: harness-injected turns land in cohort harness with no request text. The CC regression guard now compares exported rows and cohorts, not raw opportunity rows. |
| major | z0int#56 M1 gaps: multi-host merge, subagent/eval opportunities, non-CC cohort rule | ACCEPTED. C1: SubagentStart (PreToolUse Agent|Task fallback) agent-cohort opportunities joined to SubagentStop. C2: per-harness cohort classifier with Hermes automated, Codex exec and OMP subagent fixtures; `z0int loop merge`. C3/C4a: cohort at capture. |
| major | z0int#62 gaps: stale evidence, model/policy revision, work-item/attempt ids, freeze test, no-authority-from-confidence test | ACCEPTED. All five are C1 red tests; C4a also carries model_id/policy_revision from the Hermes payload. |
| major | C7 TencentDB treated as live; gateway down; bearer auth; provenance criterion weakened | ACCEPTED. Semantic layer is UNAVAILABLE by default; G-SEM gate; doctor checks gateway; bearer from a configured env var (fake in tests); optional A8-sem owner step; owner question 7; D4 keeps the #63 provenance item UNMET instead of redefining it. |
| major | C8 OMO pull-only and Grok static rules file cannot satisfy #56-B; Grok global file leaks scope and egresses | ACCEPTED. OMO gets a senpi before_agent_start/context inject shim or records B=UNSUPPORTED as a stop condition. Grok: no rules file by default; B=UNSUPPORTED (pull-only) unless a PostToolUse push seam is proven; any rules file needs --scope project and owner approval. |
| major | C7/C8 privacy: no secret redaction on memory outputs; cloud injection | ACCEPTED. Secret-scrub red tests in C7 (search, brief, MCP, receipts, EventLog) and C8 (model-visible requests); allow_cloud_injection per harness, default false; owner question 9. |
| major | C8/C4 inject latency (subprocess import 51-99 ms) | ACCEPTED. Shadow computes the brief detached (hook < 20 ms with a 5 s resolver); canary/on hard deadline 300 ms with native fallback; persistent snapshot-keyed cache in C7. |
| major | C6 lock + env: flock -w exits 1 on timeout; fcntl vs flock(2); no CLAUDE_CONFIG_DIR in the user manager; evolution-lab not installed | ACCEPTED. The tick takes flock(2) LOCK_SH in-process (same kernel lock as `flock -s`, satisfying the shared-lock rule) and writes a skip receipt; red test with util-linux `flock -x` and a negative lockf case. Unit Environment= lines; label_source_empty failure; learner pin check and an activation step installing evolution-lab 1b80a4e. agentsview-sync uses `flock -s -w 60 -E 0`. |
| major | Hygiene / G-#95: legacy OMO and DSH route_worker seams not planned | ACCEPTED. A9 activation step (backup/rollback) per seam, owner question 8; G-#95 now says NEW exposure is blocked and pre-existing exposure is an owner decision; hygiene DoD reworded to "every NEW seam" plus an inventory. |
| major | C1 Codex shim drops dsh-env-exec credential injection | RESOLVED by narrowing: the Codex shim is capture (hooks) + memory-only MCP and carries no route_worker, so nothing loses credentials. z0intelligence@personal stays untouched as a recorded legacy exception (A9). Re-homing env-exec into z0int is REJECTED for now: it would put a credential-store reader into the repo for a route_worker path that is out of scope. |
| major | Activation "reinstall at the reviewed SHA" has no mechanism | ACCEPTED. A0 pins: integrate/wiring-20261003 is pushed; a detached checkout at that SHA serves as a local-directory marketplace (CC, Codex) and as the OMP/DSH link target; Hermes installs with --ref <SHA>; the SHA is recorded in ACTIVATE.md. Owner question 2 offers merging to master instead. G-PIN gate. |
| major | C4 combines capture with the degenerate scorer move | ACCEPTED. Split into C4a (capture; build now) and C4b (scorer/evaluator/runtime; P-1 service half, P-5, P-6). C4b is a gated follow-up because of the 8 build-now cap; nothing depends on it except C9. |
| major | C2 join rules only for Hermes | ACCEPTED. One red test per harness (Hermes, Codex, Grok, OMP/OMO, DSH) with documented rules and "unjoined" on ambiguity; join rate in the tick report. |
| major | C8 e2e runnability: Grok/OMO/Codex offline; CC is Anthropic Messages, not OpenAI | ACCEPTED. CC uses an Anthropic-Messages stub via ANTHROPIC_BASE_URL with a dummy key in a scratch CLAUDE_CONFIG_DIR (also used in C1). Codex/Grok/OMO case B is live-only/UNVERIFIED and fixture runs never count toward G-MEM56/G-MEMZ0. |
| minor | C1 hook latency 50 ms p99 fails on this host | ACCEPTED. Lean entry module with an asserted import set; in-process handler bound; subprocess bound relative to a same-run bare-python baseline. |
| minor | C1 generic adapter would give Codex/Grok an automatic-routing seam | ACCEPTED. Red test: no automatic.* call for any harness other than claude-code. |
| minor | C1 check_class duplicates outcome_verifier regexes | ACCEPTED (verified lines 56-91, 218-243). check_class is extracted from them; parity test on a shared corpus. |
| minor | Red tests that pass today | ACCEPTED. Moved to "Regression guards" sentences in each component purpose (CC regression, #66 suite, hermes_decisions, Bend smoke). |
| minor | Coverage: agy, legacy sources, flow_predictions | ACCEPTED. Each is in out_of_scope with a reason; agy is AgentsView floor only with justification. |
| minor | C4/C2 e2e mechanics: build/ in read-only worktree, version pin, toolsets, FTS shadow tables, plugin symlink | ACCEPTED. git archive export into the home dir; owner checks `hermes --version` vs fork SHA at A3; toolsets-shape red test; FTS5 shadow tables filtered from the schema replay; readlink recorded in the Hermes backup. |
| minor | C3 mixed trees; hermes-jev OMP extension | ACCEPTED. Protocol-parity red test against a git-show export of a9cbbed; hermes-jev extension raised as owner question 13 and listed out of scope. |
| minor | C6 systemd-analyze verify with missing venv; DSH shadow change unstated | ACCEPTED. Unit verify on copies templated to a scratch stub; DSH shadow lanes off-by-default is stated in C5 and owner question 10. |
| n/a | Build-script alignment (found while revising) | Isolated homes moved to /mnt/zer0models/z0-wt/wiring/homes/<component-id>/ and private ports to 11540-11559 to match the build script RULES. Real-data dry runs (real ~/.z0int, real sessions.db counts) moved out of build e2e into owner step A0, because the build RULES forbid touching live state. |

Missing-acceptance items from the critique are mapped as follows: #56 M1 multi-host merge (C2 `loop merge`), subagent/eval opportunities (C1), per-harness cohort rule (C2, C3, C4a), resolution where "unverified" dominates (C2 per-harness readers + join-rate reporting; further oracles remain open); #62 stale evidence, model/policy revision, work-item/attempt ids, freeze, no authority from confidence (C1); z0evals#56 enum (C8 + C12); #56-B for OMO/Grok (C8 stop conditions); #63 TencentDB provenance (G-SEM, reported UNMET); #63 1024-event nap cascade (C7, red or regression guard); #22 measurement (C7 `memory bench`; cross-project eval out of scope for now); #23 one ingestion path (C7 EventLog); owner 09-22 cross-product recall and no credential leak (C7, C8); R4 D2 discrimination + base rate and no weights committed (C6); timer environment (C6); AgentsView before the tick with the v74 -> v113 resync planned (A6, G-AV).

---

## Appendix A: per-component TDD contract

### C1-loop-core: One cross-harness capture record, shared capture core, lean hook adapter, and the Claude Code / Codex / Grok capture shims

- **Repo:** kvnloo/z0intelligence  **Clone:** `/mnt/zer0models/z0-wt/z0intelligence`
- **Base:** `origin/feat/shadow-loop-v0 (6fee859 = origin/master 0159808 + 1)`  **Branch:** `feat/wire-loop-core-20261003`
- **Worktree:** `/mnt/zer0models/z0-wt/wiring/wt/C1-loop-core`  **Homes:** `/mnt/zer0models/z0-wt/wiring/homes/C1-loop-core/`
- **Build now:** yes  **Gated by:** none for build; activation needs A0 pinned ref + owner approval
- **Depends on:** nothing
- **Harnesses:** claude-code, codex, grok, shared-core (used by hermes, omp, omo, dsh)
- **Acceptance refs:** z0int#62 R1-R10/F1-F7/A1 + acceptance (freeze, no authority from confidence); z0int#54; z0int#56 M0/M1 (subagent/eval cohort); G-CAP; G0; R1 G0; R3 finding 1-2; owner 10-03 plugin layout

**Purpose.** z0int#62: one semantic record for every harness. Adds harness_id.turn_key() with an alias table for the six trace-id conventions; harness_capture.py (record family z0int.<harness>.{opportunity_record,turn_outcome,failure}.v0 carrying harness, turn_key, work_item_id, attempt_id, recorded_at, model_id, policy_revision, privacy_class and content-free capture-time flags; bounded spool; persisted drops; bounded close; explicit F-case failure rows incl. stale_evidence); src/z0int/agentsview_ro.py (one shared read-only connection helper for C2 and C7: sqlite mode=ro, user_version guard for 74 and 113, staleness); check_class.py EXTRACTED from the outcome_verifier regexes (outcome_verifier then imports it); a lean entry module z0int.hook_entry (stdlib + harness_capture only at import; everything else lazy, in a detached child) behind `python -m z0int.hook_adapter --harness <id> {prompt,stop,subagent-start,subagent-stop,session-start}`, harness from payload/env, never from the hook file location. Only claude-code keeps the automatic.* calls; every other harness is capture-only. claude_code.py becomes a thin wrapper; the CC plugin adds SubagentStart and SubagentStop (PreToolUse on Agent|Task as the fallback opportunity source, payload INFERRED). New shims: harness-adapters/codex-z0intelligence (hooks only; no route_worker, no credential plumbing; the existing non-git z0intelligence@personal route_worker plugin is left untouched as a recorded legacy exception, see A9) and harness-adapters/grok-z0intelligence/hooks/z0-capture.json. Regression guards (must stay green, not red tests): on the CC fixture set the EXPORTED training rows and cohorts equal the 6fee859 export (raw opportunity rows may differ because request text becomes opt-in); tests/test_claude_code_adapter.py and tests/test_hermes_decisions.py stay green; current CC hook stdout inertness.

**Reuse (never reimplement):**
- src/z0int/decision_opportunity.py build_decision_opportunity + deterministic_gate + with_authority_grant (master)
- src/z0int/claude_code.py on_opportunity / emit_opportunity_async / stop / session-start packet (feat/shadow-loop-v0 6fee859)
- src/z0int/outcome_verifier.py _TEST_RE, CHECK_EXTRA, CI_CMD, COMMIT_CMD, PR_MERGE, PIPE_FILTER, effective_exit (lines 56-91, 218-243): moved into check_class.py, not re-written
- src/z0int/loop_export.py is_harness_message (line 213) and the other export-time text features: computed at capture time instead, as booleans/buckets
- src/z0int/hermes_decisions.py + harness-adapters/hermes-z0int-decisions/__init__.py: _outcome, session-end gap filling, MAX_CHILDREN fan-out cap
- src/z0int/harness_id.py HarnessIdentity / detect_harness_id (extend; no new identity module)
- src/z0int/cognition/mutation_outcome.py (uncertain-execution semantics for the F3 failure row)
- src/z0int/tokenomics_emit.py emit_provider_usage
- harness-adapters/claude-code-z0intelligence/{hooks/hooks.json,.mcp.json,bin/z0int-mcp} (template for the Codex shim)
- tests/test_claude_code_adapter.py fake-stdin pattern; tests/test_hermes_decisions.py (<5 ms hook, fail-open, trace parity)

**Red tests (write first, see them fail for the intended reason):**
1. turn_key: the same (harness, session_id, turn_id) gives the same key across processes and replays; different harnesses give different keys; ledger order is not part of the key
2. turn_key aliases: CC prompt_id, Hermes raw "session:turn", bend-native sha256(session\0turn), automatic sha256(parent\0turn), DSH lineage turn_key and an OMP bridge turn trace each map to the canonical key; the two Hermes conventions give the same key for one turn
3. record family: an opportunity written for each of claude-code/codex/grok/hermes/omp/omo/dsh has schema z0int.<harness>.opportunity_record.v0 with harness, turn_key, work_item_id, attempt_id, a non-null recorded_at, and model_id + policy_revision fields present (value or explicit "unknown"), including non-repo turns where built_at is null
4. work item / attempt: re-submitting the same work item (same session, same user-message ordinal, retry flag) keeps work_item_id and increments attempt_id; two different turns get different work_item_ids
5. an unknown harness or schema version writes an explicit failure row (unsupported_harness / unsupported_schema) and is never silently dropped
6. failure rows exist for stale_evidence (a source revision in the opportunity differs from the one seen at outcome time), missing_verifier, partial_measurement, uncertain_execution (mutation_outcome semantics), duplicate_event and misattribution; a duplicate append of the same turn_key+kind is a counted no-op
7. spool (P-3 contract): when the bounded queue is full, drops are counted and persisted to state/<harness>/drops.jsonl, and a fresh process reports the same drop count
8. spool (P-2 contract): close() with a full queue returns within the configured bound (2 s) and nothing is written after close
9. hot path: the in-process prompt handler returns in < 5 ms p99 over 200 fixture payloads with the opportunity build detached; importing z0int.hook_entry loads no module outside stdlib + z0int.harness_capture/harness_id (asserted import set); the subprocess wall time p95 is within bare-python baseline + 40 ms, with the baseline measured in the same test run
10. inertness: hook stdout is byte-identical with capture on vs off, apart from the existing opt-in SessionStart packet
11. harness detection: a payload with GROK_HOOK_EVENT set is attributed to grok even when fired from ~/.claude/settings.json; a Codex payload with turn_id goes to codex; a CC payload with prompt_id goes to claude-code; explicit --harness wins; a conflict writes a misattribution failure row
12. P-7 contract in the core: the opportunity uses the payload cwd (task repo), never the process cwd; when every git fact is unknown the gate never returns ACT
13. capture-time flags: with the default privacy_class (no request text stored) a harness-injected prompt still carries is_harness_message=true and lands in cohort "harness" at export; no export-time code path reads intent.request
14. subagents: a SubagentStart payload (fallback PreToolUse with tool Agent|Task) writes an opportunity with cohort "agent" keyed by the subagent id; the SubagentStop outcome joins to it on turn_key; neither is ever cohort "interactive"
15. #62 A1: Claude Code, Codex and Grok fixtures for the same synthetic turn round-trip to the same semantic record, differing only in harness and ids
16. #62 freeze: one reader builds a z0evals-style frozen bundle (rows + manifest hash) from synthetic fixtures of all seven harnesses; the reader has no harness-specific branch (assert by fixture: adding an eighth harness id with the same fields needs no reader change); rebuilding gives the same hash
17. #62 authority: an opportunity whose payload or backend reports confidence 0.99/1.0 never receives ACT unless deterministic_gate grants it; with_authority_grant ignores every confidence field
18. privacy: outcome and failure rows hold no prompt, response, command or path text; opportunity request text is stored only when privacy_class=request_opt_in
19. check_class: a shared command corpus classifies identically through check_class.classify and through outcome_verifier (which now imports it) into {test, check, ci, git-commit, git-revert, pr-merge, build, other} plus effective exit status; the command string is never persisted
20. agentsview_ro.connect opens with mode=ro (a write raises), returns unavailable(schema) for a user_version other than 74/113 and unavailable(locked/missing) instead of raising into callers
21. capture-only seam: for every harness other than claude-code, hook_adapter never calls automatic.handle_event or automatic.post (mock + socket guard)
22. Codex shim: plugin manifest declares hooks only; no route_worker, no credential env, no HERMES_HOME, no path into .work or ~/plugins; z0intelligence@personal files are not modified

**Isolated e2e.** Homes under /mnt/zer0models/z0-wt/wiring/homes/C1-loop-core/ (HOME, Z0INT_HOME, CLAUDE_CONFIG_DIR, CODEX_HOME, GROK_HOME). (1) Replay recorded synthetic hook payloads through the exact command strings in each shim hooks.json (claude-code, codex, grok); assert joined opp+outcome rows, drops=0 and the latency bounds. (2) Claude Code, no subscription and no network: run `claude -p` with CLAUDE_CONFIG_DIR=<home>/claude, ANTHROPIC_BASE_URL=http://127.0.0.1:<port in 11540-11559> (an Anthropic-Messages-compatible stub that records requests and can script one Task tool_use) and a dummy key, plus `--plugin-dir <wt>/harness-adapters/claude-code-z0intelligence`; assert an interactive opportunity+outcome and an agent-cohort subagent opportunity+outcome. (3) Codex: `CODEX_HOME=<home>/codex codex exec` against an OpenAI-compatible stub configured as a custom model provider on a private port (INFERRED config keys); if the binary refuses, record UNVERIFIED and keep the fixture. (4) Grok: fixtures only (the real CLI is paid). (5) Full suite under `flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock` with the socket guard on (any connect to 11501 or a non-loopback address fails the run).

**Activation.** Needs owner approval: yes. Risk: low-medium: a hook on every turn (bounded by the in-process and subprocess latency tests); Codex hook trust prompts; Grok compat scanning of ~/.claude/settings.json (handled by env-based detection)

Steps:
1. Prerequisite A0: pinned checkout /mnt/zer0models/z0-wt/pinned/z0intelligence at the pushed integrate/wiring-20261003 SHA and venv /mnt/zer0models/z0-wt/venv-wiring built from it
2. A1 Claude Code: back up ~/.claude-home/settings.json to .bak-z0wiring-20261003; register /mnt/zer0models/z0-wt/pinned/z0intelligence as a local-directory marketplace (or, if the owner merges integrate to master, update the kvnloo/z0intelligence marketplace) and reinstall z0intelligence from it; set env Z0INT_PYTHON=/mnt/zer0models/z0-wt/venv-wiring/bin/python; owner runs one real turn and one subagent turn; check state/claude-code rows; record the plugin SHA in ACTIVATE.md
3. A5 Codex: back up ~/.codex/config.toml; add /mnt/zer0models/z0-wt/pinned/z0intelligence as a local marketplace and install codex-z0intelligence (hooks only); leave z0intelligence@personal enabled and unchanged (legacy exception, A9); trust the hooks; owner runs one real turn; check state/codex
4. A5 Grok: write ~/.grok/hooks/z0-capture.json (new file); owner runs one real turn; check state/grok

Touches live:
- ~/.claude-home/settings.json
- Claude plugin cache + known marketplaces
- ~/.codex/config.toml
- ~/.grok/hooks/z0-capture.json

Rollback: Restore settings.json and config.toml from the .bak-z0wiring-20261003 copies (or set enabledPlugins z0intelligence false / disable codex-z0intelligence), remove the local marketplace entry, rm ~/.grok/hooks/z0-capture.json

### C2-loop-consumers: Labels and cohorts for every harness: harness-generic verifier/export, AgentsView read-only turn reader with per-harness join rules, cohort classifier, multi-host merge, legacy importers

- **Repo:** kvnloo/z0intelligence  **Clone:** `/mnt/zer0models/z0-wt/z0intelligence`
- **Base:** `feat/wire-loop-core-20261003 (C1 head)`  **Branch:** `feat/wire-loop-consumers-20261003`
- **Worktree:** `/mnt/zer0models/z0-wt/wiring/wt/C2-loop-consumers`  **Homes:** `/mnt/zer0models/z0-wt/wiring/homes/C2-loop-consumers/`
- **Build now:** yes  **Gated by:** none for build; non-CC labels in production depend on G-AV (AgentsView upgrade + resync, A6)
- **Depends on:** C1-loop-core
- **Harnesses:** claude-code, codex, grok, omp, omo, hermes, dsh
- **Acceptance refs:** z0int#56 M1 (resolution, cohorts, multi-host merge); z0int#54; z0int#62; G2-verifier (R4); G-SUFF prerequisite; el#24 prereg v0 inputs

**Purpose.** Label volume and resolution is the critical path (z0int#56). Adds turn_readers.py: the existing Claude transcript reader plus an AgentsView reader (sqlite mode=ro; schema guard that supports user_version 74 (v0.39) and 113 (v0.44) explicitly; staleness guard) with ONE documented join rule per harness (Hermes, Codex, Grok, OMP, OMO, DSH; ambiguity becomes "unjoined"); a per-harness cohort classifier (interactive / agent / automated / harness / eval / unknown) so Hermes cron/kanban/cluster-service turns, Codex exec runs and OMP subagents are never pooled into interactive; outcome_verifier --harness <id> with the existing signal semantics and a loud label_source_empty failure when a label source has zero matching sessions (catches a missing CLAUDE_CONFIG_DIR); loop_export over the record family with per-harness x cohort tables, failure rows counted in the manifest and the first_opp "~" fix (loop_export.py:303); `z0int loop merge` across host tables by turn_key (z0int#56 M1; el#24 ran on another host); legacy_import.py for the OMP v1 spine, OMP cognition-shadow and DSH jev receipts into cohort "legacy". Regression guard (not a red test): CC verifier and export output on the CC fixture set is byte-identical to 6fee859.

**Reuse (never reimplement):**
- src/z0int/outcome_verifier.py signals/credit_join, verification_density.py, loop_export.py assert_private/manifest/COHORTS/transcript_cohort (feat/shadow-loop-v0 6fee859)
- src/z0int/receipt.py normalize_outcome / effective_tier / scrub_contaminated_outcomes (ambient execution tier is never success)
- src/z0int/cognition/shadow.py receipt shape (cognition.shadow.receipt.v1)
- adapters/hermes_z0int close_observation/join_outcome (execution_completed vs verified_success)
- origin/consolidate/z0-kernel-20260922@dcbcc88 src/z0int/context_providers.py (AgentsView SQL only; drop the lexical_hermes state.db reader)
- AgentsView sessions.db schema (sessions/messages/tool_calls/tool_result_events; session_kind where present; outcome/health columns as weak signals only)
- C1 check_class, capture-time flags, harness_capture failure rows, agentsview_ro.connect

**Red tests (write first, see them fail for the intended reason):**
1. outcome_verifier --harness hermes|omp|omo|codex|grok|dsh on a synthetic AgentsView fixture DB gives verified rows with the same signal semantics as the CC fixture (test/CI exit, commit/revert SZZ, PR merge with self-merge downgrade, correction cues); today these harnesses are rejected
2. join rule hermes: a captured Hermes turn_key joins AgentsView hermes:<sid> plus the user-message ordinal; two candidate rows give "unjoined" plus a counted failure row
3. join rule codex: hook session_id + turn_id join the codex:<session> row by user-message ordinal (rule documented; id mapping INFERRED and pinned by the fixture); ambiguity gives "unjoined"
4. join rule grok: GROK_SESSION_ID joins grok:<id> by user-message ordinal; ambiguity gives "unjoined"
5. join rule omp/omo: the bridge session id joins omp:<sid> / omo:<sid> by user-message ordinal; ambiguity gives "unjoined"
6. join rule dsh: the lineage turn_key joins deepseek-harness:<sid> by user-message ordinal; a DB without the deepseek-harness agent gives reader_unavailable(agent_missing), never empty success
7. cohort classifier: fixtures for a Hermes cron/kanban/cluster-service session -> automated, a Codex exec / non-interactive session_kind -> automated, an OMP subagent -> agent, a harness-injected prompt -> harness, unknown -> unknown (counted, never pooled into interactive); the CC transcript_cohort result is unchanged
8. the AgentsView reader opens with mode=ro (a write raises); user_version other than 74/113 gives a reader_unavailable failure row, not empty success; an index older than N hours gives verification_state pending_index, never negative
9. label_source_empty: claude-code opportunities exist but the transcript projects dir has zero matching sessions -> a failure row naming the resolved dir, and the report status is degraded, not success
10. loop_export accepts every z0int.<harness>.* v0 schema; unknown schemas are counted in the manifest (unsupported_schema), not dropped; tables are per harness and per cohort, and the API cannot pool them
11. first_opp regression: a set where every opportunity is non-repo (built_at null) gives turns_after_first_opportunity > 0, using recorded_at
12. loop merge: two host table sets merge by turn_key with host provenance kept; a second merge gives an identical manifest hash; merging across harness or cohort, or across differing schema versions, is refused
13. legacy omp-v1: decision_receipt.v1 + outcome_join.v1 rows become z0int.omp.imported_turn.v0 in cohort legacy; tier gold/negative (via effective_tier) is the only label; ambient execution is never verified_success; no prompt text is copied; a re-run adds 0 rows
14. legacy cognition-shadow: transport_error and not-served answers only add a backend_unavailable count; served answers become z0int.omp.shadow_decision.v0 with a structured actual_tool and the tool input dropped; a fixture with 6,900/6,902 errors gives the expected small row count
15. legacy dsh-jev: model_request/jev_decision become z0int.dsh.shadow_decision.v0 in cohort legacy with y=null, never gold; the importer never reads /workspace/hermes-home
16. assert_private refuses text keys in every new table (legacy, shadow, merged)
17. every reader and importer is idempotent by watermark: a second run gives identical manifest hashes

**Isolated e2e.** Homes under /mnt/zer0models/z0-wt/wiring/homes/C2-loop-consumers/. Build a synthetic AgentsView DB from the schema only: `sqlite3 "file:/mnt/zer0models/sft-svlm/data/agentsview/sessions.db?mode=ro" .schema`, with FTS5 shadow tables (*_data, *_idx, *_docsize, *_config, *_content) filtered out before replay into a fresh scratch DB; set user_version to 74 and, in a second copy, 113. Fill it with synthetic sessions for every agent incl. deepseek-harness, plus synthetic captured opportunities from the C1 writer and synthetic legacy JSONL fixtures. Run `z0int outcomes verify --harness X`, `z0int loop export` and `z0int loop merge` (two synthetic host dirs) with Z0INT_HOME=<home>. No real ~/.z0int, ~/.dsh or real DB content is read in the build; the read-only dry run against real data is activation step A0. Run under flock -s.

**Activation.** Needs owner approval: no. Risk: low: read-only; a wrong join rule is contained by "unjoined" rows and per-harness join-rate reporting

Steps:
1. None of its own (library + CLI consumed by C6). A0 runs a read-only counts-only dry run against the real ~/.z0int, ~/.dsh/jev/receipts.jsonl and sessions.db (mode=ro) with --out-root scratch, with owner approval

Touches live:
- nothing

Rollback: not applicable (no live change)

### C3-omp-omo-capture: OMP and OMO capture through the z0-owned bridge (oh-my-pi#109), cognition-shadow hygiene, pinned install with protocol parity

- **Repo:** kvnloo/z0intelligence  **Clone:** `/mnt/zer0models/z0-wt/z0intelligence`
- **Base:** `feat/wire-loop-core-20261003 (C1 head)`  **Branch:** `feat/wire-omp-capture-20261003`
- **Worktree:** `/mnt/zer0models/z0-wt/wiring/wt/C3-omp-omo-capture`  **Homes:** `/mnt/zer0models/z0-wt/wiring/homes/C3-omp-omo-capture/`
- **Build now:** yes  **Gated by:** none for build; activation needs A0 + owner approval
- **Depends on:** C1-loop-core
- **Harnesses:** omp, omo
- **Acceptance refs:** oh-my-pi#109; z0int#62; G-CAP (OMP, OMO); owner 09-26 order OMP->Hermes->DSH; oh-my-pi#80 out-of-tree rule

**Purpose.** OMP is the highest-volume live harness (2,485 sessions) and first in the owner capture order. omp-extensions/z0int-bridge passes ctx cwd; bridge.runtime turn_open/turn_close emit z0int.omp.opportunity_record.v0 / turn_outcome.v0 through the C1 core, detached; subagent turns are classified cohort agent at capture; bridge.jsonl stops storing prompt[:400]; local-cognition/cognition_shadow write only when the backend is served (else count backend_unavailable) and record a structured actual_tool without raw tool input; scripts/omp_bridge_install.py is salvaged to repoint only the z0int-bridge and local-cognition links to the pinned checkout. Because z0int-intelligence, openjev, flyforge-* and vllm-jev stay on canonical a9cbbed, a protocol-parity test pins z0int.bridge.v2 frames and bridge-current.json compatibility across the two trees. z0int-intelligence (live routing, automatic.json omp=true) is left alone. An OMO senpi re-export shim is added. The live hermes-jev OMP extension (owner memory: prompt shell injection via execSync) is not touched by this plan and is raised as an owner item.

**Reuse (never reimplement):**
- omp-extensions/z0int-bridge (bridge v2 turn_open/turn_close), omp-extensions/local-cognition (+ index.test.ts fake-pi pattern)
- src/z0int/bridge/{runtime,worker}.py
- src/z0int/cognition/shadow.py run_shadow
- origin/feat/omp-bridge-ready@815c680 scripts/omp_bridge_install.py + tests/test_omp_bridge_ready.py (salvage behaviour, not a wholesale merge)
- ~/tmp/z0int-canonical a9cbbed bridge worker source, read via `git -C ~/tmp/z0int-canonical show a9cbbed:<path>` only, for protocol-parity fixtures (never the dirty tree, never the running process)
- C1 harness_capture + turn_key alias for OMP bridge turn traces

**Red tests (write first, see them fail for the intended reason):**
1. turn_open with cwd writes z0int.omp.opportunity_record.v0 for the user turn via the detached core; the bridge reply latency stays within its current bound over 200 fake turns
2. turn_close writes turn_outcome.v0 joined by turn_key; execution_completed stays separate from verified_success (null until C2 verifies)
3. a subagent turn (bridge parent id set) is cohort agent at capture, never interactive
4. missing cwd gives unknown facts and the gate returns OBSERVE or ASK, never ACT
5. bridge.jsonl rows contain no prompt text
6. cognition_shadow with a dead backend (URLError) writes no shadow answer row and increments backend_unavailable; a served backend writes a row with a structured actual_tool and no tool input in any persisted field
7. protocol parity: z0int.bridge.v2 request/response frames produced by the new bridge are accepted by the canonical a9cbbed worker code (loaded from a git-show export in scratch) and vice versa; bridge-current.json written by either tree is readable by the other
8. bun test with a fake pi: capture handlers return undefined (inert); thrown errors are caught with no unhandledRejection; the extension stays loadable when Z0INT_PYTHON is missing (fail-open, counted)
9. install script --dry-run lists symlink retargets for z0int-bridge and local-cognition only, writes the links backup listing first, is idempotent, refuses a dirty target checkout and never touches z0int-intelligence without --include-routing
10. OMO shim: the senpi re-export loads with a fake senpi API exposing input/turn_end/agent_end; if the API diverges the test records UNSUPPORTED explicitly rather than passing

**Isolated e2e.** Homes under /mnt/zer0models/z0-wt/wiring/homes/C3-omp-omo-capture/: PI_CODING_AGENT_DIR=<home>/omp-agent with extensions symlinked from the worktree, Z0INT_HOME=<home>/z0home, Z0INT_PYTHON=<worktree venv>. Run `omp -p --no-session --no-extensions -e <wt>/omp-extensions/z0int-bridge -e <wt>/omp-extensions/local-cognition --mode json "<prompt>"` against a fake OpenAI-compatible stub on a private port in 11540-11559 (never start a model server). Assert joined rows in state/omp, zero prompt text in bridge.jsonl, a backend_unavailable count, and the parity check against the git-show export of a9cbbed. Run under flock -s.

**Activation.** Needs owner approval: yes. Risk: medium: OMP is the daily driver; the bridge worker interpreter changes from system python3 3.14 to venv-wiring and the bridge runs from a different tree than its peers. Mitigated by fail-open tests, the protocol-parity test and leaving z0int-intelligence untouched

Steps:
1. A2: save the current links: ls -l ~/.omp/agent/extensions > ~/.omp/agent/extensions.links.bak-z0wiring-20261003
2. python scripts/omp_bridge_install.py --target /mnt/zer0models/z0-wt/pinned/z0intelligence (bridge and local-cognition only); set Z0INT_PYTHON for the bridge worker to /mnt/zer0models/z0-wt/venv-wiring/bin/python
3. OMO: write ~/.omo/agent/extensions/z0-capture.js (re-export shim)
4. Live proof: the owner runs one real OMP turn and one OMO turn; check joined rows in ~/.z0int/state/{omp,omo}

Touches live:
- ~/.omp/agent/extensions/z0int-bridge
- ~/.omp/agent/extensions/local-cognition
- ~/.omo/agent/extensions/z0-capture.js

Rollback: Restore each symlink target from extensions.links.bak-z0wiring-20261003 (ln -sfn <old target>), then rm ~/.omo/agent/extensions/z0-capture.js

### C4a-hermes-capture: Hermes capture plugin: harness-adapters/hermes-z0intelligence absorbs the bend-native observer (#385) and z0int-decisions capture, with the capture-path bug fixes

- **Repo:** kvnloo/z0intelligence  **Clone:** `/mnt/zer0models/z0-wt/z0intelligence`
- **Base:** `feat/wire-loop-core-20261003 (C1 head)`  **Branch:** `feat/wire-hermes-capture-20261003`
- **Worktree:** `/mnt/zer0models/z0-wt/wiring/wt/C4a-hermes-capture`  **Homes:** `/mnt/zer0models/z0-wt/wiring/homes/C4a-hermes-capture/`
- **Build now:** yes  **Gated by:** G0 (no socket from hooks, one capture vehicle) before activation
- **Depends on:** C1-loop-core
- **Harnesses:** hermes
- **Acceptance refs:** hermes-agent#319 H1-H6; hermes-agent#385; SYNTHESIS P-1/2/3/7/9/10/11; R1 A1-A4; z0int#62 R5; G0; G-CAP (Hermes); owner 10-03 plugin layout (a)

**Purpose.** Hermes is the largest interactive source (1,789 sessions) and captures nothing today. Following the owner binding layout (a), harness-adapters/hermes-z0intelligence becomes the single standalone Hermes plugin (plugin.yaml, register(ctx), installable with `hermes plugins install`). This component moves only the CAPTURE half: the observer from bend-native@e85e65e5 stack/observer (#385), the State Packet + DecisionOpportunity projection, and the capture code from master hermes-z0int-decisions (that directory is retired). The existing automatic pre_llm_call stays, and stays inert while automatic.json hermes=false (#95). Vendored z0int copies are not carried over; the plugin runs the installed z0int via z0int_python/Z0INT_PYTHON in a detached child. Fixed TDD in the moved code: P-1 (as a capture-path property: no port setting, no socket from any hook), P-2, P-3, P-7, P-9, P-10, P-11, A1 (z0 sink), A2 (turn_outcome rows), A3 (canonical key), A4 (projection off the drain thread), a double-capture guard, model id/policy revision on rows (#62 R5) and capture-time cohort (cron/kanban/cluster-service sessions -> automated). The API-attempt scorer/evaluator, score_worker/worker, bridge.py and the `hermes z0` runtime CLI (P-5, P-6 and the service half of P-1) move in the gated follow-up C4b, so they cannot block capture, learning or memory. Regression guards (not red tests): tests/test_hermes_decisions.py behaviours (13 tests) stay green after the move.

**Reuse (never reimplement):**
- kvnloo/bend-native@e85e65e5 stack/observer/__init__.py (#385) and the State Packet / DecisionOpportunity projection it calls
- z0int master harness-adapters/hermes-z0int-decisions/__init__.py + src/z0int/hermes_decisions.py + tests/test_hermes_decisions.py (13 tests)
- z0int master harness-adapters/hermes-z0intelligence (automatic pre_llm_call; keep the inert path)
- adapters/hermes_z0int (normalize_envelope, close_observation, join_outcome)
- bend-native exp/contract-20261002 (H1 off-vs-shadow divergence harness, H2-H6) and exp/e2e-20261002 packets as regression fixtures
- Hermes fork /mnt/zer0models/hermes-wt/bend-integration (read-only; exported with git archive for an isolated venv; plugins.py VALID_HOOKS, turn_context.py:781-836)
- C1 harness_capture, turn_key, check_class, capture-time flags

**Red tests (write first, see them fail for the intended reason):**
1. P-1 (capture path): the plugin config schema has no service host/port; under a socket guard no hook path opens any socket; a config that sets a port is ignored with a counted warning
2. P-2: close() with a full queue returns within 2 s and writes nothing after unload
3. P-3: drops are persisted; a fresh process reports rows_dropped equal to the persisted count
4. P-7: projection uses the task cwd from hook context, never os.getcwd(); with every git fact unknown, the gate never returns ACT
5. P-9: State Packet README, commit-subject and branch text is not persisted unless persist_packet_text=true (documented privacy class); export tables never contain it
6. P-10: in mode off, zero capture hooks are registered (dispatch cost unchanged) and pre_tool_call is never registered
7. P-11: enabled() is called inside try (a config error fails open); no __pycache__ appears in the installed plugin tree after the child runs
8. A1: rows go to $Z0INT_HOME/state/hermes/{opportunities,outcomes,events,drops}.jsonl, never under HERMES_HOME
9. A2: turn_outcome.v0 rows carry asked_user, escalated, approval_requested, ended and wall_s; on_session_end fills gaps for unfinished turns
10. A3: trace and turn ids use the canonical turn_key and match the C1 alias for the old raw and sha256 forms
11. A4: projection runs in a bounded child pool (MAX_CHILDREN) so event writes never queue behind it; the H3 flood fixture gives 0 dropped events under the old 3,124/3,400 scenario
12. double capture: if z0int-decisions is enabled, or bend has stack_opportunities=true in the same profile config, the plugin emits no opportunities and writes a double_capture_guard failure row
13. inertness: every capture hook returns None; with the CONTRACT H1 harness, off vs shadow gives an identical Hermes-built request across N runs with 0 block directives
14. automatic path: with automatic.json hermes=false (or missing) pre_llm_call returns None and spawns no subprocess per turn
15. post_tool_call stores only check_class and exit status, never tool args or output
16. model/policy: opportunity and outcome rows carry model_id from the hook payload and the plugin policy_revision
17. cohort: a session whose source is cron, kanban or a cluster service is cohort automated at capture
18. toolsets: in a scratch profile whose toolsets list has the same shape as the live one (incl. no_mcp; non-secret keys only), the plugin loads and registers its hooks
19. z0int missing or broken: hooks fail open, z0int_unavailable is counted, and Hermes completes the turn

**Isolated e2e.** Homes under /mnt/zer0models/z0-wt/wiring/homes/C4a-hermes-capture/. Export the Hermes fork with `git -C /mnt/zer0models/hermes-wt/bend-integration archive HEAD | tar -x -C <home>/hermes-src` (never build inside the read-only worktree) and build <home>/hermes-venv from the export; record the fork SHA (the version match with the live Hermes is checked by the owner at A3). HERMES_HOME=<home>/hermes-home, Z0INT_HOME=<home>/z0home. `hermes plugins install <wt>/harness-adapters/hermes-z0intelligence --enable --no-deps`, settings {mode: shadow, opportunities: true, z0int_python: <wt venv>}. Run `hermes chat -q "<prompt>"` N=10 against a fake OpenAI-compatible stub on a private port in 11540-11559 that records requests. Assert joined opp+outcome rows on turn_key, an identical recorded request in off vs shadow, drops persisted, close bounded. Re-run the bend-native exp/contract-20261002 H1/H2/H6 checks against the new plugin. Run under flock -s. The live ~/.hermes is never touched.

**Activation.** Needs owner approval: yes. Risk: medium: Hermes runs 7 live services and the plugin is in every turn. Mitigated by the H1 inertness test, fail-open and one capture vehicle

Steps:
1. A3: the owner identifies the active Hermes profile (reads ~/.hermes/active_profile), checks `hermes --version` against the recorded fork SHA, and approves touching the profile
2. Back up <profile>/config.yaml to config.yaml.bak-z0wiring-20261003; record `readlink -f <profile>/plugins/hermes-z0intelligence`; copy the dir (or the symlink itself) to plugins/hermes-z0intelligence.bak-z0wiring-20261003
3. hermes plugins install https://github.com/kvnloo/z0intelligence/tree/<integrate sha>/harness-adapters/hermes-z0intelligence --ref <integrate sha> --force (replaces the routing-only plugin of the same name; automatic behaviour unchanged)
4. Settings plugins.entries.hermes-z0intelligence.settings: mode shadow, opportunities true, z0int_python /mnt/zer0models/z0-wt/venv-wiring/bin/python, z0int_home ~/.z0int; confirm bend has stack_mode off and z0int-decisions is not enabled
5. Live proof: the owner runs one real Hermes turn; joined rows appear in ~/.z0int/state/hermes; record hook dispatch latency

Touches live:
- active Hermes profile config.yaml
- active Hermes profile plugins/hermes-z0intelligence

Rollback: Restore config.yaml from .bak-z0wiring-20261003 and restore plugins/hermes-z0intelligence from the backup (re-create the symlink if readlink showed one); if Hermes does not reload plugins per session (INFERRED), the owner restarts their own session

### C5-dsh-plugin: DSH observe-only capture: hermes-jev-dsh folds into harness-adapters/dsh-z0intelligence; router stays off (#95); Jev shadow lanes off by default

- **Repo:** kvnloo/z0intelligence  **Clone:** `/mnt/zer0models/z0-wt/z0intelligence`
- **Base:** `feat/wire-loop-core-20261003 (C1 head)`  **Branch:** `feat/wire-dsh-plugin-20261003`
- **Worktree:** `/mnt/zer0models/z0-wt/wiring/wt/C5-dsh-plugin`  **Homes:** `/mnt/zer0models/z0-wt/wiring/homes/C5-dsh-plugin/`
- **Build now:** yes  **Gated by:** G-#95 blocks only router:true and automatic dsh=on; capture is not gated
- **Depends on:** C1-loop-core
- **Harnesses:** dsh
- **Acceptance refs:** z0int#62 (DSH tracked there); G-CAP (DSH); G-#95; owner 10-03 plugin layout (b); owner 09-21 no DSH core PR

**Purpose.** Owner layout (b): the DSH work on kvnloo/hermes-jev-skills origin/hermes-jev/omp-adapter@9ea5777 (dsh/plugin index.js agent/request middleware with shadow sampling and an inflight cap, lineage.js, selftests) moves into harness-adapters/dsh-z0intelligence as capture.mjs, lineage.mjs and shadow.mjs. The existing llm/stream router (index.mjs) is kept but registered only when router:true AND automatic.json dsh=true (#95 closed). Capture is an observe-only middleware that sends normalized hook events to `Z0INT_PYTHON -m z0int.hook_adapter --harness dsh`, detached, so receipts converge on z0 contracts (opportunity_record, turn_outcome, shadow_decision). Behaviour change, stated explicitly: the Jev shadow plane is reached only through a configured z0 service URL and the default is none, so DSH online Jev shadow lanes stop until the owner configures one; DSH learning is then offline shadow-slot only (C6). Removed: the key-file read of ~/.omp/.env, the /workspace/hermes-home routing config, the openjev venv defaults, and memory.js (C8 provides memory). The existing intelligence-z0intelligence MCP rows in the DSH profiles are inventoried but not changed here (A9 owner decision).

**Reuse (never reimplement):**
- z0int master harness-adapters/dsh-z0intelligence/index.mjs + harness-adapters/automatic-client.mjs
- kvnloo/hermes-jev-skills origin/hermes-jev/omp-adapter@9ea5777 dsh/plugin/{index.js,lineage.js,shadow_sampling.selftest.mjs,shadow_wiring.selftest.mjs,lineage.selftest.mjs}
- dsh/bridge/shadow_decide.py lane semantics (replaced by a z0 service call / z0int backends/registry.py; no second NanoJev runtime)
- C1 hook_adapter + harness_capture + turn_key alias (DSH lineage turn_key)
- kvnloo/deepseek-harness packages/test-support/llm-replay + apps/cli/tests/profiles/headless/*.expected.e2e.ts (source tree only)

**Red tests (write first, see them fail for the intended reason):**
1. apply(fakeCtx) registers an agent/request observe-only middleware that yields next() unchanged; the output stream is byte-identical with capture on or off
2. the llm/stream router is not registered unless config.router===true AND automatic.json dsh===true
3. a user turn sends one normalized hook event to hook_adapter --harness dsh (spawn mocked), giving z0int.dsh.opportunity_record.v0 + turn_outcome.v0 joined on the canonical turn_key (DSH lineage turn_key aliased)
4. forbidden paths: the plugin source and runtime env never reference ~/.omp/.env, any *.env/key file, /workspace/hermes-home or ~/tmp/openjev
5. shadow plane: the default config makes no network call and records shadow_plane=off once per process; with a fake z0 service on a private port it samples at the configured rate, respects the max-inflight cap and writes z0int.dsh.shadow_decision.v0; service down gives backend_unavailable counted; a request is never altered; port 11501 is refused unless allow_live_service
6. fail-open: missing python or a spawn failure lets the turn proceed and records a counted failure; every promise has .catch (no unhandledRejection under node --unhandled-rejections=strict)
7. privacy: lineage and shadow rows contain no prompt or response text
8. the plugin does not export or load memory.js (direct Hermes DB reader removed)

**Isolated e2e.** Homes under /mnt/zer0models/z0-wt/wiring/homes/C5-dsh-plugin/. Unit: node --test with a fake cordis ctx and a mocked spawn, plus Python tests through hook_adapter with Z0INT_HOME=<home>/z0home. E2E (owner approval required; runs DSH source code, never the dsh binary or wrapper): in a scratch copy of the deepseek-harness source tree, run the headless profile e2e with packages/test-support/llm-replay, DSH_HOME=<home>/dsh-home and DSH_SNAPSHOT, with the plugin linked from the worktree; assert joined rows in state/dsh and an unchanged replay transcript. Until approved, DSH G-CAP is recorded as "unit-proven only".

**Activation.** Needs owner approval: yes. Risk: medium: cordis patchReload is live; mitigated by an observe-only middleware with fail-open tests and the router disabled

Steps:
1. A4: for each DSH profile (web, headless, sdk-minimal, deepseek-sdk) back up cordis.patch.yml and package.json to .bak-z0wiring-20261003 and record the automatic-z0intelligence node_modules link target
2. Repoint the automatic-z0intelligence node_modules link from the .work tree to /mnt/zer0models/z0-wt/pinned/z0intelligence/harness-adapters/dsh-z0intelligence, with config capture:true and router:false
3. Remove the hermes-jev-dsh bundle entry from the web profile package.json (this stops the private jev receipts; owner question 10)
4. Live proof: the owner runs one real DSH turn (this plan never runs dsh); check joined rows in ~/.z0int/state/dsh

Touches live:
- ~/.dsh/profiles/*/cordis.patch.yml
- ~/.dsh/profiles/*/package.json
- ~/.dsh profile node_modules link

Rollback: Restore cordis.patch.yml and package.json from the backups and restore the recorded node_modules link target

### C6-learning-tick: Continuous learning: scheduled `z0int loop tick` holding the quiet-lane lock shared in-process, offline shadow slot, promotion requests, unit files

- **Repo:** kvnloo/z0intelligence  **Clone:** `/mnt/zer0models/z0-wt/z0intelligence`
- **Base:** `feat/wire-loop-consumers-20261003 (C2 head)`  **Branch:** `feat/wire-learning-tick-20261003`
- **Worktree:** `/mnt/zer0models/z0-wt/wiring/wt/C6-learning-tick`  **Homes:** `/mnt/zer0models/z0-wt/wiring/homes/C6-learning-tick/`
- **Build now:** yes  **Gated by:** Activation: owner approval for a new user timer and G-AV (A6) first so non-CC labels exist. Learning claims: G-SUFF; non-CC strata: G-PREREG (C11)
- **Depends on:** C2-loop-consumers
- **Harnesses:** all (consumes state/<harness>/)
- **Acceptance refs:** z0int#56 stages 2-7, M1, M2; el#24 prereg v0 (MIN_ROWS/NEG/GROUPS); R4 D2 (discrimination + base rate; no weights committed); G-SUFF; G-SLOT; G-PROMO; owner rule: no auto-promotion; quiet-lane shared lock rule

**Purpose.** Implements "shadow procs automatically learning at all times" (z0int#56 stages 2-5 scheduled, plus M2 in its minimal offline form). `z0int loop tick` takes flock(2) LOCK_SH on the quiet-lane lock itself (the same kernel lock as `flock -s`; an fcntl/lockf record lock would not interact with quiet-timed `flock -x`), waits up to --lock-wait-s, and on timeout writes a skipped_exclusive_window report and exits 0. Holding the lock is bounded by --budget-s. Steps: verify (C2) -> legacy import (C2) -> shadow_slot replay -> export per harness x cohort -> sufficiency per stratum (MIN_ROWS 300 / MIN_NEG 30 / MIN_GROUPS 10 / K=5 with both classes) with a projection -> evolution_lab.verified_loop subprocess only for strata covered by a registered prereg (v0 = claude-code) -> report (incl. a discrimination metric with its base-rate reference). shadow_slot.py loads a hash-pinned challenger registry and decides counterfactually on pinned opportunity records, offline, so it is inert with zero hot-path cost. Default challengers: deterministic_gate (champion), always_escalate, base-rate constant, RoutineRegistry.decide_shadow; el learned-gate artifacts load only when registered by hash; model backends are off by default. A SHADOW_CANDIDATE writes promotion_request.v0 and nothing else. Ships deploy/systemd/{z0int-loop-tick,agentsview-sync}.{service,timer} with Environment= CLAUDE_CONFIG_DIR, Z0INT_HOME and AGENTSVIEW_DATA_DIR, Nice=19, IOSchedulingClass=idle. Depends only on C2; its e2e uses synthetic rows for all seven harnesses, and the real cross-component run happens in the Integrate phase.

**Reuse (never reimplement):**
- C2 outcome_verifier/loop_export/loop merge/legacy_import/turn_readers/cohort classifier
- evolution-lab origin/exp/verified-loop-v0@1b80a4e `python -m evolution_lab.verified_loop --table --out --md` (subprocess; thresholds come from its prereg, not duplicated)
- src/z0int/routines.py RoutineRegistry.decide_shadow (line 555) and future-split demotion (591-608)
- src/z0int/decision_opportunity.py deterministic_gate; src/z0int/backends/registry.py (off by default)
- src/z0int/tokenomics_emit.py (tick cost event)
- kvnloo/z0 registry/lifecycles.yaml evolution-lab-promotion [sanity, replay, shadow, promoted] (promotion_request references the stage)
- /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock + quiet-lane-ledger.jsonl convention

**Red tests (write first, see them fail for the intended reason):**
1. tick on a fixture Z0INT_HOME runs the steps in order and writes loop/reports/<day>/summary.json; a second tick adds 0 rows and leaves manifest hashes unchanged
2. never promotes: a tick that yields SHADOW_CANDIDATE writes only promotion_request.v0 under $Z0INT_HOME/loop/requests; the filesystem diff outside $Z0INT_HOME/{state,loop} is empty
3. never contacts the network: under the socket guard any connect fails the test
4. sufficiency: a stratum below the thresholds reports INSUFFICIENT_DATA with a days-to-sufficiency projection; a stratum with no registered prereg reports prereg_missing and verified_loop is not invoked
5. cohorts and harnesses are never pooled; cohort legacy never enters verified_loop input
6. shadow slot: decisions come only from recorded opportunity records and are written only to state/<harness>/shadow_decisions.jsonl (z0int.shadow_decision.v0 with opportunity_id, turn_key, challenger id+sha256, decision, distribution, latency_ms, status)
7. shadow slot fail-open: a backend that is not served writes backend_unavailable once per tick per backend with no retry storm; a model backend is never invoked when disabled
8. simple controls (deterministic_gate, always_escalate, base_rate) are always in the registry; removing one makes registry validation fail
9. lock: while another process holds `flock -x` (util-linux, flock(2)) on a scratch lock file, the tick waits --lock-wait-s, then writes skipped_exclusive_window and exits 0; with the lock free it proceeds holding LOCK_SH; an exclusive POSIX lockf on the same file does not block it (documents the flock(2) choice)
10. time budget: when --budget-s is exhausted the tick stops, releases the lock and marks partial_measurement, never success
11. report: per-harness rows captured/verified/labelled, join rate, drops, failures by kind, sufficiency, a discrimination metric (AUROC or Brier) next to its base-rate reference where labels exist, and the tokenomics cost event
12. environment: with CLAUDE_CONFIG_DIR unset and claude-code opportunities present, the report is degraded with label_source_empty (from C2), never success
13. learner pin: when evolution_lab is not importable from the tick venv, or its recorded git SHA differs from loop config learner_sha, the report says learner_missing and verified_loop is not run
14. no learned artifacts in git: the tick refuses an --out-root inside any git worktree; learned artifacts are written only under $Z0INT_HOME/loop; a repo test fails if any tracked file under src/ or deploy/ matches *.pt|*.safetensors|*.pkl|*.joblib|*.npz
15. unit files: the tick ExecStart has no flock wrapper (the lock is in-process) and sets Nice=19, IOSchedulingClass=idle, Environment= CLAUDE_CONFIG_DIR, Z0INT_HOME, AGENTSVIEW_DATA_DIR and no GPU env; agentsview-sync ExecStart is `flock -s -w 60 -E 0 <lock> agentsview sync` (a timeout exits 0) and no daemon; `systemd-analyze verify` passes on copies templated to a scratch stub ExecStart

**Isolated e2e.** Homes under /mnt/zer0models/z0-wt/wiring/homes/C6-learning-tick/. Seed Z0INT_HOME with synthetic rows for all seven harnesses (C1 writer) and C2 legacy fixtures. Run `z0int loop tick --z0int-home <home>/z0home --lock /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock --lock-wait-s 60 --budget-s 300` twice to show idempotency; run once more against a scratch lock held by `flock -x` to show the skip receipt. Invoke evolution-lab verified_loop from /mnt/zer0models/z0-wt/evolution-lab-verified-loop on the claude-code stratum and expect INSUFFICIENT_DATA. Never install or start a timer; never read the real ~/.z0int (the real-data dry run is A0).

**Activation.** Needs owner approval: yes. Risk: low: idle-priority hourly job; shared-lock hold bounded by the budget

Steps:
1. A7 (after A6 AgentsView): install evolution-lab at 1b80a4e into /mnt/zer0models/z0-wt/venv-wiring and record learner_sha in ~/.z0int/config/loop.json
2. Copy deploy/systemd/z0int-loop-tick.{service,timer} from /mnt/zer0models/z0-wt/pinned/z0intelligence to ~/.config/systemd/user/ (ExecStart uses /mnt/zer0models/z0-wt/venv-wiring; Environment CLAUDE_CONFIG_DIR=~/.claude-home, Z0INT_HOME=~/.z0int, AGENTSVIEW_DATA_DIR=/mnt/zer0models/sft-svlm/data/agentsview)
3. systemctl --user daemon-reload && systemctl --user enable --now z0int-loop-tick.timer
4. Proof: the first summary.json exists, is not degraded, and the next tick shows idempotent counts

Touches live:
- ~/.config/systemd/user/z0int-loop-tick.{service,timer}
- ~/.z0int/loop/
- ~/.z0int/config/loop.json
- venv-wiring (evolution-lab install)

Rollback: systemctl --user disable --now z0int-loop-tick.timer; rm the two unit files; daemon-reload. ~/.z0int/loop data is append-only and harmless

### C7-memory-core: One z0 memory surface over the existing substrate: doctor, AgentsView + TencentDB capabilities, EventIdentity, MemorySnapshot, secret scrub, memory-use receipts, memory-only MCP profile

- **Repo:** kvnloo/z0intelligence  **Clone:** `/mnt/zer0models/z0-wt/z0intelligence`
- **Base:** `feat/wire-loop-core-20261003 (C1 head)`  **Branch:** `feat/wire-memory-core-20261003`
- **Worktree:** `/mnt/zer0models/z0-wt/wiring/wt/C7-memory-core`  **Homes:** `/mnt/zer0models/z0-wt/wiring/homes/C7-memory-core/`
- **Build now:** yes  **Gated by:** Owner decision on EventLog references-vs-bodies (references-only behind a flag, off until confirmed); semantic layer stays UNAVAILABLE until owner activates the TencentDB gateway (G-SEM)
- **Depends on:** C1-loop-core
- **Harnesses:** shared-core (all)
- **Acceptance refs:** z0int#22 promotion gate + measurement; z0int#23 (one source-derived ingestion path); z0int#63 P0; z0int#66; z0evals#56 A,D,E,F prerequisites; G-#63; G-#66; G-SEM; owner 09-22 DoD (provenance, cross-product recall, no credential leak); owner 09-26/09-29 no new memory service/:8791

**Purpose.** The z0int#22/#23/#63/#66 slices that the memory acceptance cases need, nothing more (#22: "add only what the four live proofs require"). Delivers: `z0int memory doctor` (read-only: freshness lag, deepseek-harness agent present, ~/.claude-home indexed, DB user_version vs binary dataVersion, require_auth presence without printing it, TencentDB gateway configured/reachable, recall layer status); an AgentsView evidence capability in context_resolve/state_packet (EvidenceRef agentsview:<sid>#<mid> + EventIdentity, scope filtered before ranking, explicit "unavailable"); a TencentDB read client with a hard deadline and Authorization from an env var named in config (value never logged, never read by tests). Honest status: the gateway :8420 is not listening and the owner saw the provider as configured but not installed (09-29), so the semantic layer is UNAVAILABLE by default and the #63 "TencentDB-derived memories carry canonical provenance" item stays UNMET, not redefined. EventLog append(identity=EventIdentity) is THE canonical source-derived ingestion path (#23) that any engine can consume; idempotent on event_uid; references and locators only, behind a flag pending owner decision. Also: minimal scoped BitemporalClaim supersession; memory_snapshot_id on StatePacket and DecisionOpportunity; a secret-scrub stage applied to every memory output (search results, briefs, MCP responses, receipts, EventLog) because AgentsView has indexed credential-bearing tool output (owner 09-22); MemoryUseReceipt.to_decision_extra() into DecisionReceipt.extra.memory and opportunity_record.memory; memory_brief (bounded, abstains on missing sources, persistent cache keyed by snapshot using the MemoryPacketCache key rules); `z0int memory bench` (#22 measurement on fixtures, reusing benchmarks/memory_bakeoff); and intelligence_mcp profile "memory" (memory_search, orient, inspect, history, unknowns, verify; no route_worker/delegate_worker/list_models), stdio and in-process, with no :8791, no service and no new DB. Regression guards (not red tests): tests/test_memory_contract.py (#66) and the EventLog/OptMem suites stay green; if the base already has a 1024-event nap-cascade recovery test it is a regression guard, otherwise it is written red.

**Reuse (never reimplement):**
- src/z0int/memory_contract.py (EventIdentity/derive_event_uid, MemoryScope, BitemporalClaim, MemorySnapshot, MemoryUseReceipt) + tests/test_memory_contract.py
- src/z0int/memory/event_log.py (append at line 359) + tests; src/z0int/memory/optmem_tree.py
- src/z0int/context_resolve.py (kind=memory no-op at 387-397; allow_memory guard at 298), src/z0int/state_packet.py adapters, src/z0int/decision_opportunity.py, src/z0int/receipt.py extra (224-271), src/z0int/intelligence_mcp.py
- origin/consolidate/z0-kernel-20260922@dcbcc88 src/z0int/mcp_server.py (resolve/orient/history/inspect/unknowns/verify) + src/z0int/context_providers.py AgentsView/FTS subset + benchmarks/memory_bakeoff; drop lexical_hermes
- C1 agentsview_ro.connect (mode=ro, user_version guard, staleness) shared with C2
- Hermes #56 lane c84663880a agent/memory_packet_cache.py (cache key rules only; re-homed, not linked) + evals/unified_memory/*
- /mnt/zer0models/oss/TencentDB-Agent-Memory@0aff21a MemoryCore/src/gateway/config.ts:244-255 (bearer auth shape) and ~/.omp/agent/extensions/tencentdb-memory/index.ts (endpoint shapes; reference only)

**Red tests (write first, see them fail for the intended reason):**
1. EventLog: append(identity) with the same EventIdentity twice is a no-op; ledger_seq differs while event_uid is stable; the same uid with a different payload_hash raises a conflict; events.jsonl bytes are never rewritten; a body field is rejected for source_system in the harness transcript set (references only)
2. AgentsView capability: a hit returns EvidenceRef agentsview:<sid>#<mid> with EventIdentity (source_system=agent, source_session=sid, source_event_id=message id); replay gives the same event_uid; the connection is mode=ro
3. scope: a sibling project/repo/task item is rejected before ranking (the ranker never sees it)
4. unavailable: a locked DB, schema mismatch or missing file returns status unavailable with a reason, never empty success
5. TencentDB: with no gateway configured the semantic layer reports unavailable(not_configured); a fake gateway sleeping past the deadline returns unavailable within deadline+50 ms; the client sends the bearer from the configured env var (fake value) and never logs it; items without source_event_ids get provenance_ok=false and are not counted as canonical provenance; duplicates of AgentsView hits are removed
6. secret scrub: a fixture whose tool output contains API-key, bearer-token, BWS access-token, AWS-key and PEM private-key shaped strings never surfaces them in memory_search results, memory_brief, MCP responses, MemoryUseReceipt rows or EventLog; the scrubbed count is reported
7. cross-product recall (owner 09-22): a Hermes-scoped query with a cross-harness scope policy returns Claude, Codex and OMP fixture evidence, each with source_system, harness, session id, timestamp and a stable locator
8. context_resolve kind=memory resolves only with allow_memory=True and when no other injector is registered for the turn (double-inject guard)
9. #63 P0: one query resolves across temporal (EventLog/OptMem), lexical (AgentsView) and semantic (TencentDB fake) with no duplicate evidence
10. #63 P0: a worker-facing API cannot open the ledger for write (raises); FTS hits carry the canonical event_uid
11. memory_snapshot_id changes when any source revision changes (AgentsView generation, TencentDB rev, repo SHA) and is unchanged otherwise
12. MemoryUseReceipt round-trips into DecisionReceipt.extra.memory with no schema change and into opportunity_record.memory; instruction_capability=True is rejected
13. memory_brief: within its token bound; abstains (explicit gap) when a required source is removed; supersession shows the newer claim as current while history() returns both; a second process with the same snapshot id hits the persistent cache without calling the resolver
14. memory bench: on the synthetic fixture it reports cold and warm latency, raw reads, tokens and hit rate per query
15. MCP profile memory: tools/list returns exactly the memory tools; tool errors return isError, never empty results; it works with the network disabled
16. memory doctor: fails on a stale fixture DB, a missing deepseek-harness agent, a missing claude-home dir and a user_version/dataVersion mismatch, and reports the TencentDB gateway as not_configured/unreachable; passes on a fresh fixture; output never contains config secret values

**Isolated e2e.** Homes under /mnt/zer0models/z0-wt/wiring/homes/C7-memory-core/. A synthetic AgentsView fixture DB (schema copied read-only with FTS5 shadow tables filtered, synthetic content across agents incl. secret-shaped strings), a fake TencentDB HTTP stub with bearer check on a private port in 11540-11559, Z0INT_HOME=<home>/z0home. Drive the memory-profile MCP over stdio with a JSON-RPC script: tools/list, memory_search, orient, history, verify. Check provenance, abstention, supersession, scrub and cache. Run `z0int memory doctor` against the fixture (fails stale, passes fresh). Run under flock -s. The doctor run against the real DB is A0 (owner).

**Activation.** Needs owner approval: no. Risk: low: read-only; EventLog writes only under $Z0INT_HOME/memory

Steps:
1. None of its own (library + MCP profile); activated per harness via C8 (A8). Optional A8-sem (owner): make the TencentDB gateway reachable

Touches live:
- nothing

Rollback: not applicable

### C8-memory-wiring: Memory read and inject seams in every harness (shadow detached, canary/on with deadline), single injection owner, cloud-egress opt-in, acceptance receipts

- **Repo:** kvnloo/z0intelligence  **Clone:** `/mnt/zer0models/z0-wt/z0intelligence`
- **Base:** `feat/wire-memory-core-20261003 (C7 head; C7 is on C1)`  **Branch:** `feat/wire-memory-harnesses-20261003`
- **Worktree:** `/mnt/zer0models/z0-wt/wiring/wt/C8-memory-wiring`  **Homes:** `/mnt/zer0models/z0-wt/wiring/homes/C8-memory-wiring/`
- **Build now:** yes  **Gated by:** G-INJECT (shadow -> frozen-cohort canary -> on, per harness, owner approval and allow_cloud_injection to leave shadow); live use needs G-AV (A6)
- **Depends on:** C7-memory-core
- **Harnesses:** claude-code, codex, grok, hermes, omp, omo, dsh
- **Acceptance refs:** z0evals#56 A-F + receipt.schema.json (dsh/hermes/omo/omp); z0 memory acceptance A-F (claude-code/codex/grok, pending C12); z0int#22 promotion gate; z0int#63 P0 DSH/Hermes inject+receipt round-trip; hermes-agent#320 phases; oh-my-pi#79 Stage A/B; G-MEM56; G-MEMZ0; G-INJECT; owner 09-22 no credential leak; owner 09-25/09-29 (injection must reach model-visible context)

**Purpose.** Connects the C7 surface to every harness through its own push and pull seams WITHOUT depending on C3/C4a/C5, so a capture-component failure cannot cancel memory. Each seam is a separate file in the harness shim dir: claude-code-z0intelligence (UserPromptSubmit/SessionStart additionalContext + .mcp.json z0-memory server), codex-z0intelligence (same, from C1), harness-adapters/hermes-z0intelligence/memory.py composed into the existing master pre_llm_call (one pre_llm_call, automatic + memory, single owner; MemoryPacketCache key rules from Hermes lane c84663880a; post_llm_call memory-use receipt), omp-extensions/z0-memory (context / before_agent_start; re-homes oh-my-pi #107 7fbdce67a/3ae2fe265 and ~/tmp/omp-ext-cognitive-state@ce151e5) plus an OMO senpi inject shim, dsh-z0intelligence/memory.mjs on agent/pre-step registered from index.mjs, and Grok pull-only (MCP). The Integrate phase merges these files with C4a/C5 changes in the same dirs. Each shim has memory_inject off|shadow|canary|on (default shadow): shadow computes the brief DETACHED (the hook never waits); canary/on use a hard deadline (300 ms) with native fallback and a persistent snapshot-keyed cache; injection into a non-local model endpoint needs allow_cloud_injection per harness (default false). Acceptance receipts: dsh, hermes, omo and omp emit z0eval.unified_memory_receipt.v0 rows that validate against z0evals fb14919 receipt.schema.json (its harness enum is exactly these four); claude-code, codex and grok emit z0int.memory_acceptance.v0 rows with the same A-F fields, called "z0 memory acceptance", not a #56 pass, until the gated z0evals study amendment C12 lands. Grok writes no global rules file by default (scope leak + every request goes to xAI); Grok case B is recorded UNSUPPORTED (pull-only stop condition) unless a PostToolUse additionalContext seam is proven (INFERRED). OMO B is UNSUPPORTED if the senpi API lacks a context hook.

**Reuse (never reimplement):**
- C7 memory surface (memory_brief, scrub, persistent cache, MCP memory profile, MemoryUseReceipt binding)
- src/z0int/claude_code.py additionalContext path (lines 108, 201-209)
- z0int master harness-adapters/hermes-z0intelligence pre_llm_call; Hermes turn_context.py:781-836 injection semantics (ephemeral user-message context)
- Hermes local lane ~/zer0/oss/hermes-unified-memory-56 @c84663880a agent/memory_packet_cache.py + evals/unified_memory/* (re-home into the plugin; no core dependency)
- oh-my-pi origin/feat/cognitive-state-shadow-rfc79 (7fbdce67a, 3ae2fe265; #107) + ~/tmp/omp-ext-cognitive-state@ce151e5; ~/.omp/agent/extensions/tencentdb-memory before_agent_start pattern (reference)
- kvnloo/deepseek-harness zer0.repo.yaml pre-step-admission seam (70215673) + PR #2 apps/cli/config/examples/mcp-memory/agentsview.cordis.yml
- kvnloo/z0evals main fb14919 studies/unified-memory-v0/{cohort.json,receipt.schema.json,question-contract.json} + origin/study/unified-memory-56-dsh-runner@dca8fd2 (runner reference)

**Red tests (write first, see them fail for the intended reason):**
1. per shim, shadow mode: with the resolver stubbed to sleep 5 s the hook returns in < 20 ms in-process; the harness-built model request is identical to off (fake endpoint records it); a MemoryUseReceipt with would_inject=true and memory_snapshot_id is written asynchronously
2. per shim, canary/on: the brief appears in the NEXT model-visible request (#56-B) and is not persisted to the transcript or session store (Hermes: session DB fixture unchanged); when the resolver exceeds the 300 ms deadline the turn gets native context and a counted timeout
3. persistent cache: a second process with the same snapshot id serves the brief without calling the resolver; a source revision change invalidates it
4. idempotency (#56-E): replaying the same turn_key never injects twice and has no side effect
5. single owner: with two injectors configured for one harness the second refuses and writes double_inject_guard; in Hermes, automatic and memory share one pre_llm_call; z0 briefs exclude TencentDB-sourced items when Hermes memory.provider is memory_tencentdb
6. fail-open: a resolver exception gives native context, a counted failure, and a completed turn
7. abstention (#56-D): with a required source removed, the brief states the gap and contains no invented evidence; supersession (#56-F): the newer fact is current and the history tool shows both
8. secret scrub at the seam: fixture secrets never appear in any model-visible request, receipt or MCP response
9. cloud egress: canary/on refuses to activate for a harness whose model endpoint is non-loopback unless allow_cloud_injection=true for that harness (default false), and records cloud_injection_blocked
10. receipts: eval rows for dsh/hermes/omo/omp validate against receipt.schema.json at z0evals fb14919; for claude-code/codex/grok the eval command emits z0int.memory_acceptance.v0 and refuses to label them z0eval.unified_memory_receipt.v0, asserting that the pinned enum is [dsh, hermes, omo, omp]
11. MCP entries for every harness use server name z0-memory and the memory profile only (no route_worker), and do not collide with existing z0intelligence servers
12. OMO: the senpi inject shim loads with a fake senpi API; with before_agent_start/context present the brief reaches the next request; without it the eval records B=UNSUPPORTED (stop condition) instead of passing
13. Grok: no rules file is written by default; `z0int memory rules --harness grok` requires --scope project and --owner-approved and writes only scrubbed, project-scoped content; Grok B is recorded UNSUPPORTED (pull-only) by default
14. DSH: memory.mjs uses only the z0 memory surface; no code path opens /workspace/hermes-home or any Hermes state.db

**Isolated e2e.** Homes under /mnt/zer0models/z0-wt/wiring/homes/C8-memory-wiring/, each with a request-recording stub on a private port in 11540-11559. CC: `claude -p` with a scratch CLAUDE_CONFIG_DIR, ANTHROPIC_BASE_URL pointing at an Anthropic-Messages stub and a dummy key, --plugin-dir <wt>/harness-adapters/claude-code-z0intelligence (no subscription, no network). Hermes: git-archive venv of the fork (as C4a), HERMES_HOME=<home>/hermes-home, `hermes chat -q` against an OpenAI-compatible stub. OMP: `PI_CODING_AGENT_DIR=<home>/omp omp -p --no-extensions -e <wt>/omp-extensions/z0-memory` against the stub. DSH: node --test with a mock host; the llm-replay headless e2e only with owner approval (never the dsh binary). Codex, Grok, OMO: fixtures plus an MCP stdio round-trip; their case B is marked live-only/UNVERIFIED and is NOT counted toward G-MEM56/G-MEMZ0. Run the 6-case frozen cohort per harness against the synthetic AgentsView fixture and a fake TencentDB; write results only under the home dir; validate rows. Run under flock -s.

**Activation.** Needs owner approval: yes. Risk: medium: model-visible context changes only after per-harness canary with explicit cloud-egress approval; shadow is detached so it adds no turn latency

Steps:
1. Prerequisite A6 (AgentsView upgraded, resynced, sync timer on) and `z0int memory doctor` green except the semantic layer, which may be UNAVAILABLE
2. A8a: back up each file, then add z0-memory MCP entries: CC plugin .mcp.json (plugin update from the pinned marketplace), Codex plugin, ~/.omp/agent/mcp.json, ~/.omo/agent/mcp.json, every ~/.dsh/profiles/*/cordis.patch.yml, ~/.grok/config.toml
3. A8b: set memory_inject: shadow in every shim config (Hermes plugin settings, CC/Codex env, OMP extension config, DSH plugin config); allow_cloud_injection stays false
4. A8c: per harness, run the acceptance eval live; the owner reviews A-F; the owner sets allow_cloud_injection for that harness and flips canary, then on, separately for each harness
5. A8-sem (optional, owner): install/run the TencentDB gateway on 127.0.0.1:8420 with the key supplied by the owner from their secret store as an env var at process start (never read by tests or this plan); doctor then reports the semantic layer reachable

Touches live:
- ~/.omp/agent/mcp.json
- ~/.omo/agent/mcp.json
- ~/.dsh/profiles/*/cordis.patch.yml
- ~/.grok/config.toml
- ~/.codex/config.toml
- Claude plugin
- active Hermes profile plugin settings
- optional: TencentDB gateway process (owner)

Rollback: memory_inject: off in every shim (immediate); restore each config from .bak-z0wiring-20261003; stop the gateway if A8-sem was done

### C4b-hermes-scorer-move: Move the bend-native API-attempt scorer/evaluator, score_worker/worker, bridge and `hermes z0` runtime CLI into hermes-z0intelligence (P-1 service half, P-5, P-6)

- **Repo:** kvnloo/z0intelligence  **Clone:** `/mnt/zer0models/z0-wt/z0intelligence`
- **Base:** `feat/wire-hermes-capture-20261003 (C4a head)`  **Branch:** `feat/wire-hermes-scorer-20261003`
- **Worktree:** `/mnt/zer0models/z0-wt/wiring/wt/C4b-hermes-scorer-move`  **Homes:** `/mnt/zer0models/z0-wt/wiring/homes/C4b-hermes-scorer-move/`
- **Build now:** no  **Gated by:** C4a verified; next build run (the 8-component cap); owner confirms the degenerate #386 lane is still wanted
- **Depends on:** C4a-hermes-capture
- **Harnesses:** hermes
- **Acceptance refs:** hermes-agent#386/#387; SYNTHESIS P-1/P-5/P-6; owner 10-03 plugin layout (a)

**Purpose.** Completes owner layout (a) without putting the capture critical path at risk. Moves stack/observer/shadow_api_failure.py (#386), evaluate_shadow.py (#387), score_worker.py, worker.py, bridge.py, the `hermes z0` CLI parts of commands.py and skills/z0-stack into the C4a plugin, deleting vendored z0int copies. The #386 question is DEGENERATE (0/653), so this is housekeeping, not a learning dependency; nothing in C6 or C8 depends on it.

**Reuse (never reimplement):**
- kvnloo/bend-native@e85e65e5 stack/{observer/shadow_api_failure.py,observer/evaluate_shadow.py,score_worker.py,worker.py,bridge.py}, commands.py, skills/z0-stack, stack/STACK.md
- C4a plugin layout and tests

**Red tests (write first, see them fail for the intended reason):**
1. P-1: no default service port; `hermes z0 runtime`/score refuse to run without an explicit port; 11501 is refused unless allow_live_service=true; a socket guard shows no hook path connects
2. P-5: score passes HF_HUB_OFFLINE and the checkpoint/device env to the child; a child failure surfaces a bounded traceback summary
3. P-6: evaluate selects by question id; verification_needed works; an unsupported question returns n and promotion_ready=false explicitly
4. the moved CLI and skill load from the plugin with no import from bend-native or a vendored z0int copy

**Isolated e2e.** Homes under /mnt/zer0models/z0-wt/wiring/homes/C4b-hermes-scorer-move/; git-archive Hermes venv as C4a; run `hermes z0 score` and `hermes z0 evaluate` against synthetic packets with a private port in 11540-11559 and HF_HUB_OFFLINE=1.

**Activation.** Needs owner approval: yes. Risk: low

Steps:
1. Ships inside the same plugin install as A3 once built; no extra live step

Touches live:
- nothing

Rollback: Reinstall the plugin at the previous SHA

### C9-bend-native-z0-removal: bend-native keeps Bend proof verification only; remove the z0 stack (moved to z0int); fix Bend-proof bugs P-4/P-8/P-12

- **Repo:** kvnloo/bend-native  **Clone:** `/mnt/zer0models/z0-wt/ro/bend-native`
- **Base:** `main (e85e65e5, v0.4.0)`  **Branch:** `chore/remove-z0-stack-20261003`
- **Worktree:** `/mnt/zer0models/z0-wt/wiring/wt/C9-bend-native-z0-removal`  **Homes:** `/mnt/zer0models/z0-wt/wiring/homes/C9-bend-native-z0-removal/`
- **Build now:** no  **Gated by:** C4a + C4b parity green (the z0int Hermes plugin reproduces CONTRACT H1/H2/H6 and the E2E join results bend-native had)
- **Depends on:** C4a-hermes-capture, C4b-hermes-scorer-move
- **Harnesses:** hermes
- **Acceptance refs:** owner 10-03 plugin layout (a); SYNTHESIS P-4/P-8/P-12; hermes-agent#389/#390 regression packets

**Purpose.** Owner layout (a): delete stack/, the stack hooks from plugin.yaml/__init__.py, the `hermes z0` command, skills/z0-stack, stack/vendor/z0int and the stack_* config. Keep bend_verify, `hermes bend` and the Bend workflow skill. Document the optional dependency on the z0int Hermes plugin. TDD fixes for P-4, P-8 and P-12. Regression guards: existing Bend workflow tests and smoke 7/7.

**Reuse (never reimplement):**
- bend-native tests/test_native_workflow.py, scripts/smoke.py, receipts.py, schemas/receipt.schema.json
- exp/aodl-20261002, exp/b390-20261002, exp/e2e-20261002 packets as regression fixtures

**Red tests (write first, see them fail for the intended reason):**
1. plugin.yaml provides_hooks no longer lists the 12 z0 hooks; with the plugin enabled Hermes dispatch has no bend pre_tool_call gate
2. P-4: a FAIL receipt with kernel_sha256_after=null passes read_receipt and replays
3. P-8: a missing name binding returns dependency_missing (reachable), still fail-closed
4. P-12: the receipt schema requires dependency_* fields; replay never stores a success last_receipt before replay_mismatch

**Isolated e2e.** HERMES_HOME=<home> with the git-archive fork venv; install the plugin from the worktree; run scripts/smoke.py plus the exp/b390 corpus against a private bend binary; no plugin-data/bend/z0 dir is created.

**Activation.** Needs owner approval: yes. Risk: low: deletion plus three Bend fixes

Steps:
1. Only if the owner uses bend live: hermes plugins install kvnloo/bend-native --ref <sha>, after hermes-z0intelligence is active

Touches live:
- active Hermes profile plugins/bend (only if installed)

Rollback: Reinstall bend-native at e85e65e5

### C10-hermes-jev-skills-dsh-removal: hermes-jev-skills keeps only the Jev decision layer; the DSH plugin moved to z0int

- **Repo:** kvnloo/hermes-jev-skills  **Clone:** `/mnt/zer0models/z0-wt/ro/hermes-jev-skills`
- **Base:** `origin/hermes-jev/omp-adapter (9ea5777; dsh/ is absent on main 30f20b1)`  **Branch:** `chore/move-dsh-plugin-to-z0int-20261003`
- **Worktree:** `/mnt/zer0models/z0-wt/wiring/wt/C10-hermes-jev-skills-dsh-removal`  **Homes:** `/mnt/zer0models/z0-wt/wiring/homes/C10-hermes-jev-skills-dsh-removal/`
- **Build now:** no  **Gated by:** C5 parity green (DSH capture and shadow plane reproduced in dsh-z0intelligence)
- **Depends on:** C5-dsh-plugin
- **Harnesses:** dsh
- **Acceptance refs:** owner 10-03 plugin layout (b)

**Purpose.** Owner layout (b): delete dsh/plugin/* and dsh/bridge/shadow_decide.py (+selftests). Keep jevkit, skills, the Hermes Jev routing plugin, evals, and dsh/bridge/jev-evaluate if evals still use it (INFERRED; check before deleting). Add a pointer to harness-adapters/dsh-z0intelligence.

**Reuse (never reimplement):**
- hermes-jev-skills tests/ and evals/ suites

**Red tests (write first, see them fail for the intended reason):**
1. no remaining import references dsh/plugin or shadow_decide.py
2. a grep test finds no defaults for /workspace/hermes-home or ~/.omp/.env in the remaining DSH-facing code

**Isolated e2e.** Run the repo test suite in the worktree under flock -s; nothing live.

**Activation.** Needs owner approval: no. Risk: low

Steps:
1. None: the live DSH bundle entry is removed in A4

Touches live:
- nothing

Rollback: not applicable

### C11-el-verified-loop-v1-prereg: evolution-lab verified_loop v1 pre-registration with harness and cohort strata

- **Repo:** kvnloo/evolution-lab  **Clone:** `/mnt/zer0models/z0-wt/evolution-lab`
- **Base:** `origin/exp/verified-loop-v0 (1b80a4e)`  **Branch:** `prereg/verified-loop-v1-strata-20261003`
- **Worktree:** `/mnt/zer0models/z0-wt/wiring/wt/C11-el-verified-loop-v1-prereg`  **Homes:** `/mnt/zer0models/z0-wt/wiring/homes/C11-el-verified-loop-v1-prereg/`
- **Build now:** no  **Gated by:** Owner approval of the v1 prereg text (pre-registration discipline)
- **Depends on:** C6-learning-tick
- **Harnesses:** all
- **Acceptance refs:** el#24; z0int#56 M1 cohort rule; R1 section 4.5

**Purpose.** The v0 prereg says any change is a new registration. Multi-harness rows need a v1 with harness x cohort strata, the same thresholds per stratum, no pooling, a `--stratum` filter, and host provenance from `z0int loop merge`. The text needs owner sign-off before any non-CC data is analysed.

**Reuse (never reimplement):**
- evolution_lab/verified_loop.py
- docs/prereg/verified-loop-v0.md

**Red tests (write first, see them fail for the intended reason):**
1. verified_loop --stratum hermes/interactive analyses only matching rows; a pooled table without --stratum is refused when the manifest has more than one harness
2. sufficiency thresholds per stratum equal v0; INSUFFICIENT_DATA is reported per stratum independently

**Isolated e2e.** Run verified_loop on C6 e2e multi-harness fixture tables in a scratch dir.

**Activation.** Needs owner approval: yes. Risk: low

Steps:
1. Register the prereg hash in ~/.z0int/config/loop.json (stratum -> prereg) after owner approval

Touches live:
- ~/.z0int/config/loop.json

Rollback: Remove the stratum registration (the tick reverts to prereg_missing)

### C12-z0evals-memory-study-harnesses: z0evals unified-memory study v1: add claude-code, codex and grok to the frozen study (harness enum + README scope)

- **Repo:** kvnloo/z0evals  **Clone:** `/mnt/zer0models/z0-wt/ro/z0evals`
- **Base:** `main (fb14919)`  **Branch:** `study/unified-memory-v1-harnesses-20261003`
- **Worktree:** `/mnt/zer0models/z0-wt/wiring/wt/C12-z0evals-memory-study-harnesses`  **Homes:** `/mnt/zer0models/z0-wt/wiring/homes/C12-z0evals-memory-study-harnesses/`
- **Build now:** no  **Gated by:** Owner approval to amend the frozen z0evals study (z0evals owns protected evals)
- **Depends on:** C8-memory-wiring
- **Harnesses:** claude-code, codex, grok
- **Acceptance refs:** z0evals#56; G-MEMZ0 -> G-MEM56

**Purpose.** studies/unified-memory-v0/receipt.schema.json limits harness to [dsh, hermes, omo, omp] and the README scopes the study to them. A frozen study is not edited in place: add unified-memory-v1 (same cohort and question contract, enum extended) so z0 memory acceptance rows from CC/Codex/Grok can be re-validated as #56 receipts.

**Reuse (never reimplement):**
- studies/unified-memory-v0/{cohort.json,receipt.schema.json,question-contract.json,README.md}

**Red tests (write first, see them fail for the intended reason):**
1. a claude-code receipt fails v0 validation and passes v1 validation; v0 files are byte-unchanged
2. v1 cohort.json hash equals v0 (same frozen questions)

**Isolated e2e.** Validate C8 e2e acceptance rows from every harness against v1 in a scratch dir.

**Activation.** Needs owner approval: yes. Risk: low

Steps:
1. None live; after merge, C8 eval emits #56 rows for CC/Codex/Grok against v1

Touches live:
- nothing

Rollback: not applicable

---

## Appendix B: Integrate phase instructions (for the build script)

1. `git -C /mnt/zer0models/z0-wt/z0intelligence worktree add /mnt/zer0models/z0-wt/wiring/wt/integrate -b integrate/wiring-20261003 origin/feat/shadow-loop-v0`.
2. `git merge --no-ff <head>` for each VERIFIED z0intelligence component in this order: C1, C2, C6, C3, C4a, C5, C7, C8. Skip unverified ones and list them. Never rebase or force.
3. Expected conflicts: `harness-adapters/hermes-z0intelligence/__init__.py` (C4a capture hooks vs C8 memory.py registration: one pre_llm_call composing automatic + memory, capture hooks unchanged), `harness-adapters/dsh-z0intelligence/index.mjs` (C5 capture registration vs C8 memory.mjs registration), `harness-adapters/{claude-code,codex}-z0intelligence/.mcp.json` and hooks.json. Resolve in the merge commit and add a test that both behaviours are registered exactly once.
4. Run the full suite under `flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock` and the cross-harness e2e from this tree with homes under `/mnt/zer0models/z0-wt/wiring/homes/integration/`.
5. Publish pushes `integrate/wiring-20261003` as a new branch (no PR, no force, --no-follow-tags). A0 pins activation to its SHA.

