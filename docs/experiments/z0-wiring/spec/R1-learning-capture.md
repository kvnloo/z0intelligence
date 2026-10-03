# R1: Cross-harness data collection and continuous shadow learning (survey)

Surveyor R1 of 4, read-only, 2026-10-03. This extends `/mnt/zer0models/z0-wt/wiring/prior-findings.md` and does not repeat
those audits. Anything not read directly from code, data or an issue body is marked **INFERRED**. Counts are from this host,
read today, and include no content.

Sources pinned:
- z0intelligence `origin/master` @ `0159808`, `origin/feat/shadow-loop-v0` @ `6fee859` (worktree /mnt/zer0models/z0-wt/shadow-loop)
- evolution-lab `origin/exp/verified-loop-v0` @ `1b80a4e`, `origin/experiment/q-route-v0` @ `4cf52bb`
- bend-native `main` @ `e85e65e5` (v0.4.0), synthesis /mnt/zer0models/github/cua-lanes/artifacts/bend-stack/SYNTHESIS.md
- Hermes fork worktree /mnt/zer0models/hermes-wt/bend-integration @ `ad31bbf079`, refs `fork/lab/z0int/*`
- hermes-jev-skills `origin/hermes-jev/omp-adapter` @ `9ea5777`; z0 @ `7933914`; z0evals @ `fb14919`; oh-my-pi @ `0e2411c0d`
- Issues read in full (body and comments): z0#15; z0int #14 #26 #28 #53 #54 #55 #56 (with the 10-03 addendum) #57 #58 #59 #60 #62 #66;
  el#20 #23 #24 #25 #27 #28; z0evals #72 #74 #75; tokenomics #19 #20; oh-my-pi#109 (and #80 titles/body); hermes-agent#319
  (all comments up to the 10-02/03 Bend-stack rows).

---

## 0. Answer in ten lines

1. **Only Claude Code captures the #62 record live today, and only barely.** This host has 7 opportunity rows, 19 observed
   and 9 verified. Every one of the 7 opportunity rows is a non-repo turn with `provenance.built_at = null`, so the
   records carry no timestamp.
2. **Hermes captures nothing on the live host.** The only z0 plugin enabled is `hermes-z0intelligence` (automatic routing),
   and `automatic.json` has `hermes: false`, so it does nothing. There is no `~/.z0int/state/hermes`. Hermes is still the
   largest interactive source: 1,789 sessions and 11,938 user messages in AgentsView up to 10-01.
3. **Two finished Hermes capture vehicles already exist, and they overlap.** One is z0int master's `z0int-decisions`
   plugin. The other is `bend-native` v0.4, which bundles a byte-identical copy of that plugin plus the #385 observer and
   the #386/#387 scorer and evaluator. **bend-native is the right vehicle** (it is a superset, and its shadow safety is
   proven), but it needs the P-1/P-2/P-3/P-7 fixes plus three fixes found in this survey (A1–A3, §3.2) before its records
   join the z0 loop.
4. **OMP's 7,077-row "cognition shadow" is almost entirely fail-open noise.** 6,900 of 6,902 answers from both
   nemotron_orchestrator_8b and functiongemma_270m are `transport_error` (no model server was running). There are only
   about 83 valid shadow readings in total. `selected_action` is null on every row, and the actual tool exists only as raw
   text inside `state`. OMP emits no DecisionOpportunity (#109). Its outcomes are 95% ambient `execution` tier (616/649).
5. **DSH's learning data lives in a private schema with no consumer.** It sits in a plugin on an unmerged hermes-jev-skills
   branch. No DSH DecisionOpportunity or outcome record exists. **Codex** has only an MCP route_worker plugin whose source
   is in no git repo. **OMO/opencode** have no capture, and their volume is small.
6. **"Shadow procs automatically learning at all times"** maps to stages 1–9 of the z0int#56 addendum. Today the pieces
   are: capture (stage 1, CC only), join and verify (stage 2, CC only, manual), table export (stage 3, CC only), offline
   comparison (stage 5, `verified_loop`, manual, INSUFFICIENT_DATA). **None of these is scheduled.** Stage 4 (the shadow
   slot that runs registered challengers per opportunity) **does not exist**. z0evals has no protected score API (stage 6).
7. **Sufficiency rules are fixed and registered:** `MIN_ROWS 300 / MIN_NEG 30 / MIN_GROUPS 10 / K=5`, with both classes in
   every fold (el prereg v0). Hermes #319 needs ≥20 joined decisions before any active-mode discussion. **Promotion past
   shadow is owner-approved, per decision family; there is no auto-promotion** (z0int#56 addendum, el prereg).
8. **No converter exists anywhere** from `cognition.shadow.receipt.v1`, from DSH jev receipts, or from
   `z0int.hermes_observer_event.v1` to the loop record. `loop_export` hard-filters to `z0int.claude_code.*` schemas, so
   Hermes opportunity records would be silently skipped. The #54 "episode/credit record" does not exist as a versioned
   schema. Its closest realisations are `z0int.claude_code.credit_join.v0` and `z0int.loop.training_row.v0`, both CC-only.
9. **Six trace-id conventions are in use** (§5.3). Without one canonical rule, cross-harness joins and #62 "stable IDs
   across replay" cannot be tested.
10. **Every harness has a usable extension seam, so no core change is needed:**

    | Harness | Seam |
    |---|---|
    | Claude Code | plugin hooks |
    | Hermes | plugin hooks |
    | OMP | `pi.on` extension, plus the z0int bridge worker |
    | DSH | Cordis plugin |
    | Codex | plugin with hooks (INFERRED from the 0.153.4 binary; `[features] hooks = true` is set) |
    | opencode | JS plugin dir (INFERRED) |

    Every gap above can be closed in z0int `harness-adapters/`, `omp-extensions/` or bend-native.

---

## 1. Owner-direction lens: which seam each harness offers and where the code lives today

| Harness | Extension seam (no core change) | z0 integration code today | Lives in a z0-owned repo? | Enabled on this host? |
|---|---|---|---|---|
| Claude Code | plugin: `hooks.json` (UserPromptSubmit / Stop / SessionEnd / SessionStart) + `.mcp.json` | z0int `harness-adapters/claude-code-z0intelligence` → `python -m z0int.claude_code` | yes (z0int) | yes. The venv is editable from the shadow-loop worktree, so capture runs **branch code, not master** |
| Hermes | native plugin: `register(ctx)` + `ctx.register_hook(...)`, `hermes plugins install` | (a) z0int `harness-adapters/hermes-z0int-decisions` (z0int-decisions 0.1.0); (b) bend-native v0.4 `stack/`; (c) z0int `harness-adapters/hermes-z0intelligence` (automatic only); (d) the #385–387 originals in the **Hermes fork** under `lab/z0_hermes_observer/` (`fork/lab/z0int/{full-observer,retry-shadow,shadow-eval}-v0`) | (a)(b)(c) yes; **(d) no, fork tree**, already superseded by the bend-native vendoring | only (c), which is inert (`automatic.json: hermes false`) |
| OMP | extensions in `~/.omp/agent/extensions/*` using `pi.on("input" / "before_agent_start" / "turn_end" / "agent_end" / "tool_call")` | z0int `omp-extensions/{z0int-bridge, local-cognition, z0int-intelligence, flyforge-*, openjev*, vllm-jev}` + resident `z0int.bridge` worker | yes (z0int) | yes, but every symlink points at **~/tmp/z0int-canonical** (closed PR #35 tree; its `z0int-bridge/index.ts` differs from master). Install script `scripts/omp_bridge_install.py` exists only on the unmerged branch `feat/omp-bridge-ready` @ `815c680`. oh-my-pi#80 says to move all z0 behaviour out of tree |
| DSH | Cordis plugin, listed in profile `package.json` + `cordis.patch.yml`: `ctx.on('llm/stream')`, `ctx.on('agent/request')`; MCP servers | (a) z0int `harness-adapters/dsh-z0intelligence` (automatic); (b) `hermes-jev-dsh`, linked from `~/zer0/oss/hermes-jev-skills/dsh/plugin` (branch `hermes-jev/omp-adapter` @ `9ea5777`, unmerged); MCP memory-agentsview; MCP intelligence-z0intelligence (stale `.work` tree) | (a) yes; (b) hermes-jev-skills is kvnloo-owned but not a z0 repo, and its branch is unmerged | yes. (a) is inert (`dsh: false`); (b) wrote receipts until 09-30 |
| Codex | plugin (`.codex-plugin/plugin.json`, `.mcp.json`, skills; binary strings show `plugin.json#hooks` and `hooks/hooks.json`) + `~/.codex/hooks.json` with `[features] hooks = true` | `z0intelligence@personal` (MCP `route_worker`). Source is `~/plugins/z0intelligence`, **not a git repo**. The shim imports `/mnt/zer0models/workspace/zer0/oss/.work/z0intelligence/src` and runs under `~/.hermes/hermes-agent/venv/bin/python` | **no** | yes (MCP only) |
| OMO / opencode | opencode JS plugins dir `~/.config/opencode/plugins/` (INFERRED seam; it holds only axi-* plugins today) | none (`receipts/executor-omo.json`: `router_client true, auto_hook false`) | n/a | no |

The NeMo Relay RFC (z0 `docs/architecture/rfc-nemo-relay-runtime-observation.md`, status proposed, z0#21) states the same
rule independently: "integrate only at each runtime's existing, owned observability/extension seam". It names
`SessionTelemetryBackend` / Cordis for DSH, the existing OTel spans for OMP, and native plugins for Hermes.

---

## 2. What #62 / #54 require (normative checklist)

z0int#62 scope, applied per harness and on "the same semantic record, not three harness-specific schemas"
(owner comment, 09-30):

| # | Field / behaviour | Source |
|---|---|---|
| R1 | stable DecisionOpportunity / trace / work-item / attempt IDs | #62, #54, #109 |
| R2 | pinned evidence/state revision | #62, #53 |
| R3 | candidate action set (legal) | #62, #55 |
| R4 | actual action | #62 |
| R5 | backend/policy revision (+ full distribution when available) | #62, #54, #14 |
| R6 | execution result | #62 |
| R7 | independent verifier/outcome, **distinct from execution_completed** | #62, #54 |
| R8 | retries/corrections/escalations | #62, #54 |
| R9 | measurement completeness | #62, #54, tokenomics#19 |
| R10 | provenance/privacy class; label source + confidence; credit level (observational/ablation/paired/causal) | #54 |
| F1–F7 | failure cases: restart/replay, duplicate event, timeout with uncertain execution, stale evidence, missing verifier, unsupported schema version, partial measurement | #62 |
| A1 | one fixture per harness round-trips through the same semantic record | #62 acceptance |
| A2 | z0evals can freeze the records without harness-specific reinterpretation | #62, #109 |
| A3 | kill switch keeps native behaviour; shadow-first | #109, #319 |

---

## 3. Per-harness: captured today, required, missing

### 3.1 Claude Code (the reference implementation)

**Captured today.** The writer is `z0int.claude_code` on `feat/shadow-loop-v0`. Files are under `~/.z0int/state/claude-code/`:

| File | Schema | When | Rows now |
|---|---|---|---|
| `opportunities.jsonl` | `z0int.claude_code.opportunity_record.v0`, wrapping `z0int.decision_opportunity.v0` + `gate` (deterministic_gate) | UserPromptSubmit; detached child (`emit_opportunity_async`) | 7 |
| `outcomes.jsonl` | `z0int.claude_code.turn_outcome.v0` (`label_kind: observed_behaviour_not_optimal`, asked_user, tool_calls) | Stop (root transcript only) | 19 |
| `outcomes_verified.jsonl` | `z0int.claude_code.turn_outcome_verified.v0` (signals, verification_state, label_class/confidence, measurement, privacy, credit, verifier 0.2.0) | **manual** `z0int outcomes verify` | 9 |
| (in memory) | `z0int.claude_code.credit_join.v0` | `outcome_verifier.credit_join` | — |
| export | `z0int.loop.training_row.v0` + `…training_table_manifest.v0` (`FEATURE_SCHEMA_SHA`, `TABLE_VERSION 0.1.0`) | **manual** `z0int loop` / `outcomes export` | — |
| `~/.z0int/tokenomics/events.jsonl` | `tokenomics.event.v0` via `tokenomics_emit.emit_provider_usage` | Stop | — |

**Against #62/#54.**

| Requirement | Status |
|---|---|
| R1 | yes: `prompt_id` → trace_id; `semantic_id` + `trace.opportunity_id` |
| R2 | yes for repo turns (`invalidation.source_revisions`); **empty for non-repo turns** |
| R3 | yes (`action_space` ACT/OBSERVE/ASK/ABSTAIN/ESCALATE with `legal` and `blocked_by`) |
| R4 | coarse only: ACT vs ASK, derived at export |
| R5 | **missing**: no model or policy revision in the opp or observed row; the model name only reaches Tokenomics |
| R6 | partial (tool_calls count) |
| R7, R8, R9, R10 | yes, in the verified row (correction cues, revert/fix-commit SZZ, CI, PR merge with self-merge downgrade) |
| F-cases | append-only, latest wins; schema filter in loop_export; no explicit "unsupported schema" error row |

**Missing.**
- No scheduler. Verify, export and the evolution-lab run are all manual.
- Subagent, workflow and eval/probe sessions emit no UserPromptSubmit, so they get no opportunity record. The el#24 run
  (commit `1b80a4e`) found these sessions "carry 90% of verified turns" (z0int#56 M1).
- **Bug (confirmed by reading code plus data):** `opportunity_record.v0` has no `recorded_at`, and non-repo turns have
  `provenance.built_at = null` (all 7 rows here). `loop_export.sweep_counts` computes
  `first_opp = min(built_at or '~')`, which is `'~'` when every opp is non-repo (`loop_export.py:303-304`). Then every
  `started >= '~'` is false, so `turns_after_first_opportunity` and `with_opportunity_after_first_opportunity` stay 0. The
  training row's `day` also falls back to `built_at` when no verified row exists (`loop_export.py:234`).
- `outcome_verifier` cites `docs/verified-outcomes.md`, which does not exist on the branch.

### 3.2 Hermes

**Live host.**
- `~/.hermes/config.yaml` `plugins.enabled` = `[hermes-handoff, dashboard_auth/basic, hermes-agent-cluster, kanban,
  hermes-z0intelligence]`. Neither `z0int-decisions` nor `bend` is enabled.
- `memory.provider: memory_tencentdb`.
- `~/.z0int/config/automatic.json` = `{omp: true, hermes: false, dsh: false}`. There is no `~/.z0int/config/hermes.json`
  and no `~/.z0int/state/hermes/`.
- **Result: zero learning capture.** `hermes-cluster.service` is live. The only Hermes-derived learning data is the
  09-17 offline mine `~/.z0int/episodes/next_action.jsonl` (76,970 rows, 4,921 gold, written by `z0int.compile`).
  `compile.py` reads `/workspace/hermes-home/state.db` directly, the live DB, so **the build plan must not reuse it**.

**Vehicle (a): z0int master `harness-adapters/hermes-z0int-decisions` + `src/z0int/hermes_decisions.py`.**
- 289 lines, stdlib only. Hooks: `pre_llm_call`, `post_llm_call`, `on_session_end`, `pre_approval_request`. All return
  `None`. A daemon thread and queue (max 1000) do the work. The child is spawned only for user turns, capped at
  `MAX_CHILDREN=4`.
- Writes `~/.z0int/state/hermes/opportunities.jsonl` (`z0int.hermes.opportunity_record.v0`, built by the child
  `python -m z0int.hermes_decisions opportunity`) and `outcomes.jsonl` (`z0int.hermes.turn_outcome.v0` with asked_user,
  escalated = delegate_task or approval, ended, hook_ms, wall_s).
- Needs a z0int interpreter: `Z0INT_PYTHON`, `$Z0INT_HOME/config/hermes.json` `{"python": …}`, or `z0int` on PATH.
- trace_id = raw `"<session>:<turn>"` string (`_key`).
- `z0int hermes decisions` reports the gate versus observed behaviour. It is not installed anywhere.

**Vehicle (b): bend-native v0.4 (`/mnt/zer0models/z0-wt/ro/bend-native` @ `e85e65e5`).**
- `stack/sources.json` pins the bundled files. The observer (#385), `shadow_api_failure` (#386) and `evaluate_shadow`
  (#387) come from Hermes fork `0d60437a`. `decision_opportunity`, `state_packet`, `context_resolve` and `paths` come from
  z0int `f80da337`; their sha256 equals current master. `stack/observer/decision_hooks.py` **is byte-identical to master's
  `hermes-z0int-decisions/__init__.py`**, but bend-native imports only `classify` and `turn_behaviour` from it.
- It registers 12 hooks. `stack_mode` and `stack_opportunities` both default off. Rows go to
  **`$HERMES_HOME/plugin-data/bend/z0/events.jsonl`** (`z0int.hermes_observer_event.v1`: identity
  `{harness_id, session_id, task_id, turn_id, trace_id = sha256(session\0turn), api_request_id, tool_call_id}`, scalar
  fields, usage, tool shape `{tool_name, arg_keys, status, error_type, duration_ms}`, subagent shape). `post_llm_call`
  rows carry `behaviour` and `label_kind`. Opportunity records go to
  **`$HERMES_HOME/plugin-data/bend/z0/opportunities.jsonl`** (`z0int.hermes.opportunity_record.v0`).
- CLI: `hermes z0 {status, report, state, opportunity, score, evaluate, runtime}`.

**Evidence that it works** (SYNTHESIS §4 and §6; #319 comments of 10-02/03):

| Check | Result |
|---|---|
| H1 | 0 Hermes-built prompt divergences across 36 runs; oracle 108/108 |
| H5 | no raw tool, reply or source content (except the opted-in opportunity request) |
| H6 | zero footprint when disabled |
| E2E joins | receipt→observer trace 17/17; opportunity records 40/40, all read-only, semantic_id stable |
| #319 SMOKE / SAMPLES / CONFIRM | 106 / 105 / 142 frozen ordinary turns, with joins via `close_observation` / `join_outcome` |

**Fitness as the Hermes capture vehicle.**
- **Verdict: use bend-native and propose nothing new**, but the plugin needs a REVISE PR first.
- Known bugs: P-1 to P-12 (SYNTHESIS §8). Those that matter for capture:
  - **P-1**: `stack_service_port` default 11501 is the live z0 service. Only explicit runtime commands use it, but the
    default must change.
  - **P-2**: `close()` cannot stop a full-queue worker.
  - **P-3**: drops are not persisted.
  - **P-7**: opportunity projection uses the process cwd, so the gate says ACT with every git fact unknown. The master
    plugin has the same flaw: it uses `TERMINAL_CWD` or `os.getcwd()`.
  - **P-9**: the State Packet persists README open-item text, commit subjects and branch names.
  - **P-11**: `enabled()` runs outside the try block; `__pycache__` is written into the plugin tree.
- Additional issues found in this survey:
  - **A1, records invisible to z0**: `Stack.home()` is hard-wired to `get_hermes_home()/plugin-data/bend/z0`
    (`stack/bridge.py:30-31`). The observer has a `Z0INT_HERMES_EVENT_PATH` override; the Stack has none. So
    `z0int hermes decisions` and `loop_export` never see these rows. The data also sits **inside the live Hermes home**,
    which this programme may not read. The fix needs a `Z0INT_HOME/state/hermes` sink, or an export command that writes
    z0int-shaped rows.
  - **A2, no observed-outcome rows**: there are no `z0int.hermes.turn_outcome.v0` rows. Observed behaviour exists only
    as `behaviour` inside `post_llm_call` observer rows. `ended`, `approval_requested`, `escalated` and `wall_s` (which
    the master plugin computes) are lost, and session-end gap filling is not ported.
  - **A3, trace-id mismatch**: bend-native uses `sha256(session\0turn)`; the master Hermes plugin uses the raw
    `"session:turn"`. The same Hermes turn gets two different trace_ids depending on the vehicle.
  - **A4, slow projection**: each opportunity projection is a synchronous ≤30 s subprocess inside the single drain
    thread (`bridge.py:96-101`), so event writes queue behind it. This is how 3,124 of 3,400 rows were dropped in the
    CONTRACT H3 test.
  - **A5, no live label source**:
    - `report` returns `verified_task_success: null`.
    - `evaluate` is hard-wired to `api.attempt_will_fail` (P-6). That lane is DEGENERATE: 0 positives in 299 (SMOKE),
      235 (SAMPLES) and 178 (E2E) joined attempts.
    - The only lane with signal is `verification_needed`. Its turn-example builder exists only in experiment packets
      (bend-native `exp/e2e-20261002` `docs/experiments/e2e-2026-10-02/harness/build_turn_examples_e2e.py`), and its
      labels come from **fixture oracles** that do not exist for ordinary live Hermes work.
  - **A6, coupling (INFERRED design concern)**: capture ships inside the Bend proof-tool plugin (`name: bend`), so
    upgrading capture means requalifying a Bend release (#389/#390).
- **Never enable both (a) and (b).** Both hook `pre_llm_call`, so turns would be double-captured.

**Missing for Hermes, beyond the vehicle fixes.**
- No live outcome verifier. The observer carries tool `status/error_type` but no command class or exit code. Hermes
  turns therefore cannot be labelled the way `outcome_verifier` labels Claude Code turns (tests/CI exit codes, commits,
  reverts, PRs, correction cues).
- Two options, both via seams:
  - (i) a content-free `post_tool_call` classifier in the plugin that records the check class (test/ci/git-commit/pr)
    and exit status;
  - (ii) an offline verifier over AgentsView's normalised Hermes rows: `sessions` (`hermes:<session_id>`, the same id
    format as the plugin's session_id), `messages`, `tool_calls.input_json`, `tool_result_events.status`. These are
    read-only.
- The turn-level join in (ii) needs a turn ordinal or timestamp rule (INFERRED; AgentsView has no Hermes `turn_id`).
  AgentsView sync has been stopped since 10-01, so (ii) also needs sync scheduled as a config step.
- No backend/policy revision or model distribution on the opportunity. The actual model is in observer `fields.model`.

### 3.3 OMP

**Captured today.** These are the pre-#53 "v1 spine" files from prior-findings; new characterisation below.

| File | Schema | Writer | Rows | Last write |
|---|---|---|---|---|
| `~/.z0int/receipts/decisions.jsonl` | `z0int.decision_receipt.v1` | `z0int.bridge.runtime.turn_open` (via `omp-extensions/z0int-bridge`), plus other producers | 3,582. By tag: OMP bridge meta-stamp 1,711, `omp` 122, codex* 1,377, dsh 46, hermes 34, omo 2, untagged 275 | 10-01 03:23 |
| `~/.z0int/receipts/outcomes.jsonl` | `z0int.outcome_join.v1` | `turn_close` / `agent_end` | 649. Tiers: execution 616, gold 23, soft 10. Sources: `bridge_turn_end` 625, `bridge_agent_end` 5 | 10-01 |
| `~/.z0int/shadow/cognition-shadow.jsonl` | `z0int.cognition.shadow.receipt.v1` | `omp-extensions/local-cognition` `pi.on("tool_call")` → bridge op `cognition_shadow` → `z0int.cognition.shadow.run_shadow` | 7,077 (09-21..10-01; 4,740 on 09-25) | 10-01 03:23 |
| `~/.z0int/shadow/preflight.jsonl`, bridge stream `bridge.jsonl` | preflight rows; the bridge row stores `prompt[:400]` (raw text) | `turn_open` | — | — |

**Cognition-shadow content (new).**
- Per-model answers:
  - nemotron_orchestrator_8b: 6,900 of 6,902 are `transport_error: URLError calling /v1/chat/completions`.
  - functiongemma_270m: the same, 6,900 of 6,902.
  - nanojev: 40 of 80 are `nanojev_06b is not served`.
  - hammer3b: 40 valid.
- That leaves **about 83 valid shadow answers out of 13,924**.
- Top-level `selected_action` is None in every row. `executed_action` is None by design (`shadow.py:504/526`).
- The actual tool appears only as text, `state = "OMP tool_call: <name>\ninput: <json…>"`. That parses for 6,915 rows,
  and it holds raw tool input, which is a privacy issue.
- `session_id` is present on 162 of 7,077 rows. There is no turn link.
- **Usable as learning data: effectively none.** As a fail-open proof it is fine.

**Against #109/#62.**
- R1: partial (trace per turn; a separate trace per tool call).
- R2: none (no state revision).
- R3: legal tool set exists in cognition-shadow (`legal_ids`) but not at turn level.
- R4: the tool name exists, unstructured.
- R5: shadow backend ids only.
- R6: ambient execution only.
- R7: 23 gold out of 649.
- R8–R10: retries none; measurement only in tokenomics.
- **No DecisionOpportunity.**

**Missing.**
- An opportunity and outcome emitter on the OMP turn. `turn_open` already receives `prompt`, so the z0-owned bridge could
  build the opportunity. The extension must also pass `cwd` (INFERRED; not in the current `turn_open` signature).
- Structured actual-action and outcome fields in the cognition shadow, and a "backend served" precondition so
  transport-dead runs do not mint rows.
- Repointing the extension symlinks from `~/tmp/z0int-canonical` to a master checkout. That is a config step,
  and `scripts/omp_bridge_install.py` is on the unmerged `feat/omp-bridge-ready`.
- `automatic.json omp: true` means OMP automatic routing is **live, not shadow**. This needs owner awareness;
  it is out of R1 scope.

### 3.4 DSH (DeepSeek Harness)

**Captured today.**
- `~/.dsh/jev/receipts.jsonl`: 13,192 rows from 09-21 to 09-30, last write 09-30 12:18 (prior-findings).
- Writer: Cordis plugin `hermes-jev-dsh` (`dsh/plugin/index.js`, `ctx.on('agent/request')`), plus the fan-out bridge
  `dsh/bridge/shadow_decide.py` (backends `decider_2b`, `nanojev` via `Z0INT_SRC=~/tmp/openjev/src`).
- Private types: `model_request`, `jev_decision` (route_changed false 244/244), `decision_receipt`,
  `decision_comparison`. Keyed by `turn_key`.
- The plugin hardcodes `JEV_ROUTING_CONFIG=/workspace/hermes-home/profiles/chiefstaff/jev/routing.json`, which is in
  **the live Hermes home**, and a `~/tmp/openjev` venv.
- `dsh-z0intelligence` (z0int master) does automatic routing only and is inert.
- 46 `decision_receipt.v1` rows are tagged `dsh` in decisions.jsonl.

**Against #62.** No DecisionOpportunity, no turn outcome, no verification, and no convergence onto z0 schemas (#62 tracks
DSH work because DSH Issues are disabled). DSH is absent from AgentsView. Sessions exist only as
`~/.dsh/sessions/**/session.v3.jsonl.zstd` (203).

**Missing.**
- Opportunity and outcome emission in `harness-adapters/dsh-z0intelligence`. `llm/stream` already sees the user turn
  (`last.role === 'user'`); for turn end, the NeMo RFC names DSH's `SessionTelemetryBackend` seam (INFERRED as usable).
- A converter or retirement decision for jev receipts.
- Moving the plugin off its unmerged hermes-jev-skills branch, and removing its dependency on the live Hermes home.

### 3.5 Codex

**Captured today.**
- Only `decision_receipt.v1` rows for MCP `route_worker` delegations (tags `codex` 199, `codex-ab` 626, `codex-eval` 552;
  capability `codex.delegated_text` 367).
- Plugin `z0intelligence@personal` v`0.1.0+codex.20260927001037`. Its source, `~/plugins/z0intelligence`, is not
  under git, and the shim imports the stale `.work/z0intelligence/src`.

**Against #62.** Nothing per turn. Codex has 1,690 sessions and 10,122 user messages in AgentsView (rollouts at
`~/.codex/sessions/`), so it is the second-largest source after Hermes.

**Seam.** `[features] hooks = true` with `~/.codex/hooks.json` already in use (SessionStart → axi tools). The installed
binary (0.153.4) contains `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PreCompact`, `plugin.json#hooks` and
`hooks/hooks.json`. **INFERRED:** a Codex plugin can carry UserPromptSubmit/Stop hooks with Claude-Code-like payloads,
so the `claude_code.py` pattern ports with a Codex rollout reader for the Stop and verification side.

**Missing.** Everything. The plugin source also has to move into z0int (`harness-adapters/codex-z0intelligence`).

### 3.6 OMO / opencode, and other active producers

- **OMO/opencode:** no capture. AgentsView shows omo with 13 sessions (last 09-27) and opencode with 54 (last 09-12).
  Low priority by volume.
- **Grok CLI:** 2,411 sessions (last 10-01). **Antigravity CLI:** 107. No z0 capture. Their seams were not surveyed;
  offline-only via AgentsView is INFERRED.
- **Flow** (OS-context predictor, kvnloo/.files `feat/workspace-copilot-flow`):
  - `~/.z0int/receipts/flow_predictions.jsonl` has 17,053 `flow_prediction.v1` rows (09-18 to 10-02, still writing).
  - **Every row has `actual`.** It is the only producer on this host that is continuously shadow-predicting *and*
    self-labelling.
  - Tokenomics `adapters.from_flow_prediction` consumes it. It is not a harness-turn record.
- **Agent Orchestrator** (z0int#60): `ao_bridge.py` on master produces a shadow spawn decision receipt and an outcome
  join, keyed `ao-spawn-<session-id>`. There are no AO receipts on this host.
- **AgentWeb/Emma** (z0int#49): `outcome_observation`, `verified_event_ingress` and `outcome_coverage` are on master
  (lab). No local data.

### 3.7 Summary matrix

| Harness | Opportunity (R1–R3) | Observed action (R4) | Backend rev (R5) | Exec vs verified (R6/R7) | Verifier | Periodic job | Shadow challengers recorded | Live on host |
|---|---|---|---|---|---|---|---|---|
| Claude Code | yes (v0) | coarse | no | yes / yes | `outcome_verifier` (manual) | no | gate only | yes, 7 opp |
| Hermes | code exists (a)/(b) | (a) yes, (b) embedded | no | (a)/(b) execution only | **none live** | no | (b) offline scorer only | **no** |
| OMP | **no** | tool name in text | shadow labels only | ambient / 23 gold | none | no | dead (97.5% transport_error) | yes (old tree) |
| DSH | **no** | model_request | jev lanes | no | none | no | jev lanes (private) | stopped 09-30 |
| Codex | **no** | no | route receipts | no | none | no | no | MCP only |
| OMO/opencode | no | no | no | no | none | no | no | no |

---

## 4. "Shadow procs automatically learning at all times": what it maps to in the RFCs

### 4.1 Stage map (z0int#56 addendum of 2026-10-03; z0#15 dependency DAG)

| Stage | Meaning | Owner | Exists today | "At all times" needs |
|---|---|---|---|---|
| 1 Capture | DecisionOpportunity + deterministic gate, every turn, every harness | z0int | CC only (branch) | always-on plugin per harness (§3) |
| 2 Join + verify | observed + verified outcome + credit join | z0int (#54) | CC only, **manual** | per-harness verifier, run on a schedule |
| 3 Training table | `z0int.loop.training_row.v0` (counts, booleans, hashed ids; `assert_private` refuses text keys) | z0int → EL | CC only, **manual** | harness-generic export; multi-host merge by `turn_key` (M1) |
| 4 **Shadow slot** | registered challengers decide counterfactually per opportunity, inert, off the hot path | z0int runtime (proposed; z0#15 leaves it unowned) | **does not exist.** Fragments: OMP cognition_shadow (dead), bend-native offline `hermes z0 score`, DSH jev lanes, `RoutineRegistry.decide_shadow` (`routines.py:555`) | registry of challenger artifacts loaded read-only, decisions recorded beside the champion (M2) |
| 5 Population search | mutate/recombine/select offline | Evolution Lab | `verified_loop` v0 (learned ACT gate; manual; INSUFFICIENT_DATA); q-route ridge (`experiment/q-route-v0`) | scheduled offline run on frozen tables; M3 genome |
| 6 Protected scoring | optimizer never sees its judge | z0evals (#72, #74) | **RFC only.** No score API in z0evals; `study/offload-and-loop-v0` is a frozen study of v0 | score API |
| 7 Promotion | `[sanity, replay, shadow, promoted]` (z0 `registry/lifecycles.yaml` evolution-lab-promotion) + #56 contract fields | z0int + **owner approval** | Routine/Cascade compilers carry candidate → credited → promoted → demoted | owner sign-off per candidate × family |
| 8 Drift / deopt | monitors force fallback/demotion | z0int | `RoutineCompiler` future-split demotion (`routines.py:591-608`) | z0evals#74 drill (M5) |
| 9 Economics | search/train/shadow/maintenance cost vs frontier work removed | Tokenomics (#19/#20) | adapters + reports | measured shadow cost |

### 4.2 Which candidates and learners

All are shadow-only, scored against verified outcomes, and use simple controls as mandatory baselines.

1. **Champion:** `decision_opportunity.deterministic_gate` (fixed priority ACT > ESCALATE > ASK > OBSERVE > ABSTAIN).
   Unconditional escalate is the second mandatory control (#55 falsification).
2. **Learned ACT gate** (el#24; prereg `docs/prereg/verified-loop-v0.md`). Two forms:
   - `L_cov`: L2 logistic, coverage matched to G.
   - `L_crc`: conformal risk control, α = 0.02.
   - Both may only **withhold** ACT and can never grant it (`legal.ACT == 1` is required).
3. **DecisionBackends** on registered questions (`backends/registry.py`: `laya_421m`, `julia_1`, `nanojev`,
   `decider_2b`, `openjev_06b`, `llama_http`).
   - Questions: `verification_needed` (decision-capability-v1) and `api.attempt_will_fail` (degenerate).
   - Status (#14 comment, 10-02): julia_1 NOT_CONFIRMED on CONFIRM n=142. No backend beats the LOO-family prior or base
     rate on discrimination.
4. **OMP tool-choice SLMs** (z0int#20, oh-my-pi#83): nemotron_orchestrator_8b, functiongemma_270m, hammer3b, nanojev.
   They never had a live server here.
5. **DSH jev lanes:** nanojev and decider_2b. Every route_changed was false.
6. **Genome** (#56 addendum): gate thresholds per family → state construction (z0int#28) → mechanism per family (el#25)
   → prompt wording (last).
7. **Other candidates:**
   - Routine/cascade candidates from `routines.py` and `cascade.py`.
   - The ridge-on-hashed-text next-action baseline (q-route `8a181e4`: ridge 0.6425 vs nanojev 0.2119 vs mushroom
     0.1429 vs majority 0.5139 on the Hermes `next_action` confirm tail).
   - SafetySentinel (#26) is **not implemented**; there is no `sentinel` in `src/`.

### 4.3 Data each learner consumes

| Learner | Input | Notes |
|---|---|---|
| Gate learners | `z0int.loop.training_row.v0` | features are prompt-time only; `observed` is post-decision and never an input; label `y_success` 1 = verified_success, 0 = verified_failure or contested, null = unverified (missing, not negative); `group` = hashed session; `cohort` ∈ interactive / agent / harness / unknown |
| Backends | per-question example rows: `z0int.hermes_shadow_example.v1` (bend-native, api failure), packet-local `verification_needed` builders | full distributions retained |
| Population search | frozen tables + frozen work-item groups | grouped event → trace → lineage → work item → family → harness (#14, el#20) |

### 4.4 Who runs them

- **Today: a human.** There is no timer. `systemctl --user list-timers` shows nothing z0-related; only
  `z0intelligence.service` (the live router) runs.
- **Intended:**
  - The z0int runtime runs stage 4 in shadow, off the hot path the way `emit_opportunity_async` is.
  - Evolution Lab runs stage 5 offline.
  - z0evals scores through its API.
  - The owner approves stage 7.

### 4.5 Sufficiency rules (registered)

- **el prereg v0:** `MIN_ROWS 300`, `MIN_NEG 30`, `MIN_GROUPS 10`, `K = 5`, and every test fold must contain y=0 and y=1.
  Otherwise the result is `INSUFFICIENT_DATA`, which is descriptive only. Each result also reports a data-volume
  projection.
- **#319:** ≥20 real joined decisions before any active-mode discussion (target 100+), and the set is frozen by hash
  before scoring.
- **#56 addendum:** M1 exits when el#24 sufficiency passes on at least one cohort. Eval/probe and workflow-subagent
  traffic must be its **own cohort, never pooled** with interactive. Stop search work if no cohort reaches sufficiency
  within 8 weeks of M1.
- **Implication:** pooling harnesses (Hermes + CC + OMP) into one table changes the distribution. Per the prereg rule
  "any change … is a new pre-registration (v1)", a harness-stratified **v1 prereg** is needed before multi-harness rows
  feed `verified_loop`. v0 has no harness column filter. **INFERRED from the prereg text.**

### 4.6 Promotion and approval rules

- **Outcomes:** `INSUFFICIENT_DATA | NO_IMPROVEMENT | SHADOW_CANDIDATE`. `SHADOW_CANDIDATE` requires all of:
  - coverage within 0.02;
  - Δ < 0 with CI upper bound < 0;
  - Δ < 0 in ≥3 of 5 folds;
  - L_crc joint risk ≤ α + 0.01;
  - zero illegal ACTs.
- **A positive result only earns shadow.** Going past shadow needs a separate registration with online shadow evidence,
  then **owner approval per candidate and decision family**. Any auto-promotion class needs its own pre-registration and
  owner sign-off (#56 addendum).
- **Contract fields every promoted mechanism must carry (#56):**
  - capability/family identity;
  - evidence/state version;
  - train/dev/sealed/future cohorts;
  - operating region;
  - calibration;
  - verifier contract;
  - cost vector;
  - invalidators;
  - drift monitors;
  - fallback;
  - demotion reason;
  - lineage.
- Promotion is per question family (#14, el#20). "Simplest candidate clearing the frozen gate wins."
- **Authority:** a promoted policy only *recommends*. AODL / deterministic authority can deny (#55). Model confidence
  never grants authority (`with_authority_grant` requires an identified user answer).
- AO (#60): `post-seed shadow → outcome joins → frozen eval → assist → … → bounded authority`.

---

## 5. Schemas that must be unified, and existing converters

### 5.1 Inventory

| Schema | Writer / file | Identity | Has opp / candidates / actual / backend rev / exec / verified / completeness / privacy | Consumers | Converter to the loop record? |
|---|---|---|---|---|---|
| `z0int.decision_receipt.v1` (`receipt.py`) | OMP bridge, Codex MCP, DSH/Hermes automatic → `~/.z0int/receipts/decisions.jsonl` | trace_id (+ session_id, meta stamp) | – / – / `action_taken`+`route` / provider+model / via `outcome` / tier / `measurement_state` / – | tokenomics `from_z0int_receipt`; AO; `memory_contract` (MemorySnapshot → `extra`, #66) | **no** (to tokenomics only) |
| `z0int.outcome_join.v1` | `receipt.join_outcome` / `close_turn` → `receipts/outcomes.jsonl` | trace_id | outcome tiers gold/negative/execution/soft; `normalize_outcome` demotes ambient closes | tokenomics summary | **no** |
| `z0int.cognition.shadow.receipt.v1` (`cognition/shadow.py`) | OMP local-cognition → `shadow/cognition-shadow.jsonl` | trace_id per tool call | legal_ids, shadow[] {backend, model, selected_action, abstained, invalid_call, latency, parse_error}; executed_action always None; raw `state` text | **none** | **none** |
| `z0int.cognition` receipts (`cognition/receipts.py`, z0int#20) | learned cognition decisions (state, eligible_candidates, chosen, quota, latency, prediction) | — | candidates + chosen | — | none |
| `z0int.decision_opportunity.v0` | `decision_opportunity.build_decision_opportunity` | `semantic_id` (harness-independent), `trace.opportunity_id` | intent (**raw request**), authority, state (claims, superseded, contradictions, unknowns), invalidation.source_revisions, scope, action_space, fallback, `expected_outcome.verifier=None` | wrapped by opportunity_record | — |
| `z0int.claude_code.opportunity_record.v0` / `z0int.hermes.opportunity_record.v0` | CC plugin / Hermes plugin (a) and bend-native (b) | (session_id, trace_id) | opp + gate | `loop_export` (**CC schema only**), `hermes_decisions.decisions_report` | CC: yes; **Hermes: no** (schema filter) |
| `z0int.claude_code.turn_outcome.v0` / `z0int.hermes.turn_outcome.v0` | CC Stop / Hermes plugin (a) | (session_id, trace_id) | observed behaviour (`observed_behaviour_not_optimal`) | loop_export (CC only), outcome_verifier | CC only |
| `z0int.claude_code.turn_outcome_verified.v0` | `outcome_verifier` | (session_id, trace_id) | signals, state, label class/confidence, measurement, privacy, credit level | loop_export, credit_join | CC only |
| `z0int.claude_code.credit_join.v0` | `outcome_verifier.credit_join` | — | opp → gate → observed → verified chain | summarize | the closest existing #54 record (CC only) |
| `z0int.loop.training_row.v0` + manifest | `loop_export` | `turn_key`, `group` | features / gate / observed / label / verifier / privacy | `evolution_lab.verified_loop` | — (it is the target) |
| `z0int.hermes_observer_event.v1` | bend-native observer → `$HERMES_HOME/plugin-data/bend/z0/events.jsonl` | sha256(session\0turn), api_request_id, tool_call_id | API/tool/subagent/session metadata, usage | `shadow_api_failure.joined_examples` | → `hermes_shadow_example.v1` only |
| `z0int.hermes_shadow_example.v1` / `z0int.hermes_shadow_eval.v1` | bend-native `hermes z0 report/score/evaluate` | trace/turn | question, distribution, label | evaluate | hard-wired to api.attempt_will_fail (P-6) |
| `z0int.allocation_observation.v1` | `adapters/hermes_z0int.close_observation` + `join_outcome` (master) | trace/turn | execution_completed vs verified_success | #319 packets | packet-local only |
| DSH jev receipts (`type`: model_request / jev_decision / decision_receipt / decision_comparison) | hermes-jev-dsh → `~/.dsh/jev/receipts.jsonl` | `turn_key` | route + shadow lane decisions; no outcomes | **none** | **none** |
| `z0int.task_snapshot.v1` / episodes `next_action` | `counterfactual.mine_omp_sessions` / `compile.compile_hermes` (live state.db) | session | historical; `user` text field | q-route experiments | historical importers only |
| `mutation-outcome/v0` (`cognition/mutation_outcome.py`) | separates cognition retries from world retries | — | the F3 "timeout with uncertain execution" semantics | — | — |
| `flow_prediction.v1` | Flow → `receipts/flow_predictions.jsonl` | prediction_id | prediction + actual | tokenomics | n/a (not turn-level) |
| `tokenomics.event.v0` | `tokenomics_emit`, tokenomics adapters | trace_id | usage + measurement completeness | tokenomics reports | exists (economics side) |

### 5.2 Converters and importers that already exist

| Converter | Location | Mapping | Limits |
|---|---|---|---|
| **tokenomics adapters** | `kvnloo/tokenomics` `packages/python/src/tokenomics/adapters.py` | `from_z0int_receipt`, `from_flow_prediction`, `from_omp_provider_usage`, `from_hermes_provider_usage`, … | economics side only |
| **loop_export** | z0int `feat/shadow-loop-v0` | CC opp + observed + verified → `training_row.v0` | CC-only constants `OPP_SCHEMA`/`OBSERVED_SCHEMA`/`VERIFIED_SCHEMA`/`HARNESS` (`loop_export.py:35-38`); reads `state/claude-code/` |
| **outcome_verifier** | z0int `feat/shadow-loop-v0` | Claude Code transcript turns (`turns_from_transcript`, `find_transcript`) → verified rows; `credit_join` | CC-only |
| **hermes_decisions.decisions_report** | z0int master | Hermes opp ⋈ observed → gate-vs-observed table | not a training-row export |
| **bend-native `joined_examples` / `score` / `evaluate`** | bend-native | observer events → api-failure examples → scored → Brier / log-loss | single question |
| **adapters/hermes_z0int** | z0int master | Hermes envelope → `allocation_observation.v1`, `join_outcome` | — |
| **receipt.normalize_outcome / effective_tier / scrub_contaminated_outcomes** | z0int `receipt.py` | sanitises ambient "gold" | — |
| **compile.py** | z0int | Hermes state.db → next_action episodes | historical; reads the live DB, so **do not reuse** |
| **counterfactual.py** | z0int | OMP sessions → task_snapshot | historical |
| **memory_contract** | z0int master, contract-only | memory-use snapshot → `DecisionReceipt.extra` | #66 says "do not create a second outcome/receipt system" |

**Not found anywhere** (searched z0int, all `ro/` clones, evolution-lab, both worktrees):
- `cognition.shadow.receipt.v1` → loop record;
- DSH jev → anything;
- `hermes_observer_event.v1` → `turn_outcome.v0`;
- `decision_receipt.v1` / `outcome_join.v1` → `training_row`;
- a harness-generic `opportunity_record` / `turn_outcome` / `turn_outcome_verified`;
- a #54 versioned episode/credit schema.

### 5.3 Trace-id conventions in use (must converge for F1/A1)

| Producer | trace_id rule |
|---|---|
| Claude Code opportunity/outcome | `prompt_id` (fallback: sha256(session\0transcript-size\0prompt)) |
| z0int `automatic.normalize` | sha256(parent_agent\0turn_id) |
| Hermes plugin (a) `z0int-decisions` | raw `"<session>:<turn>"` (or turn_id if it already embeds the session) |
| bend-native observer + opportunity | sha256(session\0turn) |
| OMP bridge | per-turn trace from the extension; per-tool-call trace for cognition shadow |
| DSH jev | `turn_key` (last message id or sha256 of messages) |
| AO | `ao-spawn-<session-id>` |

`harness_id.py` exists ("Cross-harness identity fields") but does not define a canonical turn key. **INFERRED:** that file
is the natural home for one.

---

## 6. Gaps, ordered for a TDD build plan

Everything here goes through seams, in z0-owned repos; enabling it is a config step. These are design proposals,
INFERRED, ready for the planner to accept or cut.

**G0. Canonical record and identity (z0int, pure code, tests first).**
1. `harness_turn_key(harness, session_id, turn_id)` in `harness_id.py`, plus an alias table for the six conventions in
   §5.3. Tests: replay gives the same key; a duplicate is detected; Hermes vehicles (a) and (b) give the same key.
2. Generalise `opportunity_record` / `turn_outcome` / `turn_outcome_verified` to harness-parameterised schemas. Option:
   keep the v0 names and add a `harness` field; `loop_export` then accepts the `z0int.<harness>.*.v0` family. Add
   `recorded_at` to `opportunity_record`. Fix the `first_opp '~'` bug (`loop_export.py:303`). Tests: one fixture per
   harness round-trips (#62 A1); a CC table is byte-identical before and after (regression).
3. Explicit failure rows: `unsupported_schema`, `missing_verifier`, `partial_measurement`, and `uncertain_execution`
   (reuse `mutation_outcome` semantics) instead of silent drops (#62 F-cases).

**G1. Hermes vehicle: a bend-native REVISE PR. No new plugin.**
- P-1: the default port must not be 11501, or the runtime must require an explicit port.
- P-2: bounded close. P-3: persisted drop counter. P-7: task-repo cwd, and no ACT when every fact is unknown.
- A1: z0 sink at `$Z0INT_HOME/state/hermes`, or a `hermes z0 export` that writes z0int-shaped rows.
- A2: emit `z0int.hermes.turn_outcome.v0`, porting `_outcome` / session-end gap filling from decision_hooks.
- A3: trace_id per G0.
- A4: move projection off the event drain thread.
- Packet regression fixtures already exist (`exp/contract-20261002`, `exp/e2e-20261002`).
- Enabling it is config only: `plugins.enabled += bend`, `stack_mode: shadow`, `stack_opportunities: true`, with
  `z0int-decisions` left disabled.

**G2. Labels for non-CC harnesses (the real bottleneck; #56 says "label volume and resolution, not search").**
- (i) A content-free check-class + exit-status signal in the Hermes `post_tool_call` hook and the OMP `turn_end`
  extension.
- (ii) An AgentsView-backed, read-only turn reader for `outcome_verifier`, covering Hermes, OMP, Codex, Grok and other
  agents. It reuses signal semantics (tests/CI, commit/revert/fix SZZ, PR, correction cues). It needs AgentsView sync
  scheduled (config) and a documented turn-join rule (INFERRED feasible: `hermes:<id>` session ids match).
- Neither may store text (`assert_private`).

**G3. OMP.**
- `turn_open` / `turn_close` in `z0int.bridge.runtime` also emit `z0int.omp.opportunity_record.v0` /
  `turn_outcome.v0`. The extension passes `cwd`.
- cognition_shadow: write a row only when the backend is served, otherwise a counted `backend_unavailable` marker;
  record the actual tool as a structured field; stop persisting raw tool input in `state`.
- Config step: repoint `~/.omp/agent/extensions/*` to a master checkout (`omp_bridge_install.py` from
  `feat/omp-bridge-ready`).

**G4. DSH.**
- `harness-adapters/dsh-z0intelligence` adds opportunity and outcome emission (`llm/stream` user turn; session end via
  the telemetry seam, INFERRED).
- Owner decision: convert `~/.dsh/jev/receipts.jsonl` (model_request / jev_decision → shadow decision rows; there are no
  outcomes, so they are unlabelled) or retire it.
- Remove the dependency on the live Hermes home routing config.

**G5. Codex.**
- Move `~/plugins/z0intelligence` into z0int `harness-adapters/codex-z0intelligence`. Add UserPromptSubmit/Stop
  hooks reusing the claude_code pattern. Add a Codex rollout reader for observed and verified rows.
- Install is a plugin marketplace entry. The MCP shim should import the master package, not `.work/`.

**G6. Scheduler ("at all times"): config, not a daemon written into a harness.**
- One `z0int loop tick` (new, z0int) that runs verify (per harness) → export (all harnesses, per cohort/harness) →
  manifest + sufficiency projection → optional `python -m evolution_lab.verified_loop` report. It never promotes and
  never calls the live service.
- Driven by a systemd user timer installed as a config step.
- **Note:** hard rules forbid starting services in this survey phase; a timer is a later owner-approved step.

**G7. Shadow slot (stage 4 / M2).**
- A read-only challenger registry. Start with: deterministic gate (champion), unconditional ESCALATE, el#24 learned gate
  artifacts, `RoutineRegistry.decide_shadow`, and DecisionBackends for `verification_needed`.
- It is evaluated in the detached opportunity child, so it adds no hot-path latency. It writes
  `z0int.<harness>.shadow_decision.v0` keyed by `opportunity_id`.
- Fail-open with an explicit `backend_unavailable` (lesson from OMP's 6,900 transport errors). It is never shown to the
  model.

**G8. Protected scoring and promotion.**
- A z0evals score API (#72), plus a `verified_loop` v1 prereg with harness/cohort strata.
- Promotion stays owner-approved (`[sanity, replay, shadow, promoted]`). No auto-promotion class without its own prereg.

---

## 7. Risks and constraints for the build

- **Privacy.**
  - Opportunity records store the raw request (`intent.request`). bend-native P-9 persists README/commit/branch text.
  - The OMP bridge stream stores `prompt[:400]`. OMP cognition-shadow `state` stores raw tool input.
  - `episodes/next_action.jsonl` has a `user` field.
  - Only `training_row.v0` is text-free (`assert_private`). Every cross-host or cross-repo export must go through it.
  - Training rows never contain prompt, response, command, path or claim text (#56).
- **Live-service safety.** Several things default to `127.0.0.1:11501` (the live z0 service): bend-native P-1,
  `automatic.post` (`Z0INT_SERVICE_URL` default), and the DSH/Hermes automatic plugins. Capture must never need the
  service. Today, opportunity building is local and needs no service.
- **#95 gate.** It covers governed dispatch and remote execution, not observe-only capture (prior-findings). Capture
  work is therefore not blocked by the OMP AODL canary.
- **Code drift.**
  - CC capture runs from the shadow-loop worktree (branch code).
  - OMP runs from `~/tmp/z0int-canonical`.
  - The Codex shim imports `.work/z0intelligence`.
  - DSH jev imports `~/tmp/openjev`.
  - Four different z0int trees serve live capture. The build plan should pin one installed z0int (master + merged
    shadow-loop) and repoint every seam to it by config.
- **Double capture.** Hermes vehicles (a) and (b) both hook `pre_llm_call`.
- **Unmerged shadow-loop.** `feat/shadow-loop-v0` is pushed with no PR. Every Stage-2/3 module (`outcome_verifier`,
  `verification_density`, `loop_export`, `claude_code_engagement`) is absent from master.

## 8. Owner decisions surfaced (not decided here)

1. Should Hermes capture use bend-native (recommended, after the REVISE PR), or should the capture stack be split out of
   the Bend plugin into its own z0-owned plugin repo?
2. DSH jev receipts: convert or retire? Which repo should host the DSH plugin long-term?
3. Does a `verified_loop` v1 prereg, with harness/cohort strata, precede multi-harness pooling?
4. Is a scheduled `loop tick` (systemd user timer) and AgentsView sync timer acceptable as the "always on" mechanism?
5. OMP `automatic: true` is live routing, not shadow. Is that intended?
