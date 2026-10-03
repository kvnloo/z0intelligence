# Integration e2e: integrate/wiring-20261003 (2026-10-03)

## Verdict

The four verified components (C1, C3, C4a, C5) run together on one integration branch, in one isolated environment.

Passing:

- **Capture:** all 7 harness seams write records into one shared Z0INT_HOME. The seams are Claude Code, Codex, Grok, OMP, OMO, Hermes and DSH.
- **Inertness:** shadows are inert in every leg.
- **Privacy:** the records and exported rows carry no synthetic text.
- **Learning:** the learning chain runs from capture through verify, export and the frozen bundle to the learner. The learner reports `INSUFFICIENT_DATA` honestly.
- **Suite:** the full suite has no new failures compared with the base.
- **Integration defect:** the e2e found one defect (OMO print mode hangs). It is fixed with TDD on the owning branch, C3, and merged.

Not met:

- **Unified memory is BLOCKED.** No harness seam reaches a z0 memory surface. C7-memory-core and C8-memory-wiring are not verified and were not merged. Nothing was built in their place, because that would widen scope.
- **Labels for the other six harnesses are missing.** Verify and export for codex, grok, hermes, omp, omo and dsh are not available. These need C2-loop-consumers (AgentsView turn readers and harness-generic export), which is not verified.
- **No scheduled tick.** The continuous-learning job ran once, by hand. C6-learning-tick (the scheduled `z0int loop tick`) is not verified.

Verdict file: `artifacts/e2e-check.json` (63 checks: 62 OK, 1 FAIL = `memory.unified_memory_from_every_harness_seam`, status BLOCKED).

## Integration branch

- Worktree `/mnt/zer0models/z0-wt/wiring/wt/integrate`, branch `integrate/wiring-20261003`, from `origin/feat/shadow-loop-v0` @ 6fee859.
- The upstream tracking that `worktree add` set up was removed (`--unset-upstream`), so a bare `git push` cannot reach shadow-loop-v0.
- Not pushed.
- Final HEAD: **e02bcf5**. The tree is clean. Every commit is authored by lesseradmin and carries the Co-Authored-By trailer.

The merges are no-ff and nothing was rewritten. Order: C1, (C2 skipped), (C6 skipped), C3, C4a, C5, (C7 skipped), (C8 skipped), then the C3 fix.

| merge commit | component | head |
|---|---|---|
| cc5c82f | C1-loop-core | 914dc99 |
| ebb290d | C3-omp-omo-capture | fe077db |
| a0a9cf5 | C4a-hermes-capture | ee7cae0 (one conflict, resolved in the merge commit) |
| 3a3b6e9 | C5-dsh-plugin | 73fe3d7 |
| e02bcf5 | C3-omp-omo-capture fix | 3f6d139 (integration defect, see below) |

Components skipped as unverified: C2-loop-consumers, C6-learning-tick, C7-memory-core, C8-memory-wiring.

### Conflict (recorded)

- **Where:** `src/z0int/harness_capture.py`, in `begin_turn`.
  - C3 derives cohort `agent` from a bridge subagent payload (`parent_id` / `agent_kind: sub`).
  - C4a adds a shim-supplied capture-time `cohort=` parameter: Hermes `automated` sources and injected `harness` turns.
- **Resolution:** keep both. The cohort is the shim cohort when one is given; otherwise `agent` for a subagent payload; otherwise `interactive`. The local variable was renamed to `kind` so it no longer shadows the parameter.
- **Test:** `tests/test_integrate_wiring.py` (3 tests) is in the merge commit. It fails on either side alone:
  - On C3: 2 failures, `TypeError: unexpected keyword 'cohort'`.
  - On C4a: 1 failure, `'interactive' == 'agent'`.
  - On the merge: 3 passed.
  - Evidence: `merge-test-red-green.txt`.
- **Not seen:** none of the conflicts Appendix B predicted occurred: `hermes-z0intelligence/__init__.py`, `dsh index.mjs`, `.mcp.json`. All of them involve C8, which was not merged.

## Pinned e2e venv

- `/mnt/zer0models/z0-wt/wiring/venv-integrate`: CPython 3.13.13 from `uv venv`, plus `uv pip install --torch-backend cpu -e '<integrate>[test]'`. torch is the 2.10.0+cpu build, the same pin as the CI offline suite.
- uv cache: `scratch/uv-cache-integrate`. Install log: `scratch/venv-integrate-install.log`.
- `/mnt/zer0models/z0-wt/venv-claude-code` was not used or modified. I only listed its site-packages to see how the earlier suites had run.

## Full suite (shared quiet lock, env -i, isolated HOME/Z0INT_HOME/TMPDIR, socket guard on)

Runner: `scripts/suite.sh`. It runs pytest `tests/`, `node --test tests/test-dsh-plugin.mjs` and `bun test omp-extensions/`.

| tree | pytest | node (DSH contract) | bun (omp-extensions) |
|---|---|---|---|
| base 6fee859 | 11 failed, 964 passed, 4 skipped | n/a (file absent) | 12 pass |
| integrate 3a3b6e9 (before the C3 fix) | 11 failed, 1065 passed, 8 skipped | rc 0 | 27 pass, 1 skip |
| **integrate e02bcf5 (final)** | **11 failed, 1065 passed, 8 skipped** | **rc 0** | **28 pass, 1 skip** |

- The FAILED set is identical to the base. These are the 11 known environment failures in `test_functions_verify` (live) and `test_quota_budget` / `test_provider_saturation`.
- No socket-guard violation was logged.
- Raw output: `suite-{base,head,head-final}*.raw.txt`.

## Isolated environment

Root: `/mnt/zer0models/z0-wt/wiring/homes/integration/e2e`. It was wiped and rebuilt for the one recorded run (`scripts/e2e_all.sh`, log `e2e-run.log`). The whole run held `flock -s quiet-lane.lock`.

- **Shared capture home:** `z0home/` is the single Z0INT_HOME for every "shadows on" arm of every harness. `z0home-off/` takes every "off" arm.
- **Per-harness homes:** each harness has its own HOME / CLAUDE_CONFIG_DIR / CODEX_HOME / PI_CODING_AGENT_DIR / HERMES_HOME (`<root>/<harness>/`).
- **Fixtures:** synthetic git task repos and synthetic prompts only.
- **Model stubs:** recording stubs on private loopback ports 11540-11545. Port 11549 is a closed "dead backend". Every stub logs the full request body together with the arm label.
- **Network:** each real-CLI leg runs under `hostless unshare -rn` (private user and net namespace, loopback only). Hermes runs under `hostless bwrap --unshare-net` with `/workspace/hermes-home` and `~/.z0int` masked by empty tmpfs; the proof is `hermes/masks-inside.txt`. The Python socket guard (sitecustomize) and the node guard refuse port 11501 and any non-loopback address. Every guard log is empty.
- **Untouched:** no live z0 service, no AgentsView, no dsh binary or wrapper, no systemd.

## Per-harness legs

| harness | host driven | arms | opp / out / joined | cohorts | inert check |
|---|---|---|---|---|---|
| claude-code | real `claude -p` 2.1.288 with `--plugin-dir harness-adapters/claude-code-z0intelligence`, Anthropic-Messages stub that scripts one Task delegation | on x2 / off x2 (`Z0INT_CAPTURE=0`, same plugin) | 4 / 4 / 4 | interactive 2, agent 2 (subagents) | all 4 runs' 3 model requests identical after id/clock normalisation |
| codex | real `codex exec` 0.153.4; `codex-z0intelligence` installed from the integrate tree's local marketplace into an isolated CODEX_HOME; Responses stub | on x2 / off x2 | 2 / 2 / 2 | interactive 2 | all 4 runs identical |
| grok | fixtures (the real CLI is paid): payloads piped through the exact command strings of `grok-z0intelligence/hooks/z0-capture.json` with GROK_HOOK_EVENT / GROK_SESSION_ID | on / off, 3 turns each | 6 / 6 / 6 | interactive 3, agent 3 | hook stdout identical (all empty), all rc 0 |
| omp | real `omp -p` with `-e z0int-bridge -e local-cognition`; chat stub; local-cognition model at a dead port | on x2 / off x2 / none (no extension) x2 | 2 / 2 / 2 | interactive 2 | all 6 runs' requests byte-identical |
| omo | real `omo -p` 5.0.1 (senpi) with `-e z0int-bridge/omo.ts`, `--omo-senpi-disabled`, `models.json` in the isolated agent dir | on x2 / off x2 / none x2 | 2 / 2 / 2 | interactive 2 | all 6 identical; all exit 0 (after the C3 fix) |
| hermes | real `hermes chat -q` from the fork export (venv reused read-only from homes/C4a-hermes-capture), plugin = `git archive e02bcf5` of `hermes-z0intelligence` in an isolated HERMES_HOME | off x3 / shadow x3 / unavailable x1 | 3 / 3 / 3 | interactive 3 | the 6 off/shadow runs' 4 requests each are identical; the unavailable arm exits 0 with 7 `z0int_unavailable` failure rows and 8 persisted drops |
| dsh | mock DSH host (C5 driver): fake cordis ctx, real detached `python -m z0int.hook_adapter --harness dsh` spawns, fake z0 shadow service on 11545 (`/v1/plan`) | on / off | 3 / 3 / 3 | interactive 3 | host transcript (10 steps) identical; 3 `shadow_decision.v0` rows, each `executed: false`, `student_changed_execution: false` |

- The off arms wrote 0 opportunity or outcome rows for every harness.
- Identity fields are present on every row: harness, session_id, trace_id, turn_key, work_item_id, attempt_id, cohort, model_id, policy_revision, privacy_class and recorded_at.
- The privacy class is `content_free` everywhere, and `intent.request` is null.
- Every outcome that no verifier labels today carries an explicit `missing_verifier` row. This covers every non-CC harness.

Normalisation used by the inert check, and only this:

- uuids, hex ids, `toolu_/msg_/resp_` ids and the subagent id;
- ISO timestamps;
- two host wall-clock values: the CC subagent hand-back `duration_ms` and the Codex `turn_started_at_unix_ms`. These differ between two off runs just as much.

## Learning chain (shared Z0INT_HOME → learner)

1. **`z0int outcomes verify --no-gh`** (Claude Code; labels come from the isolated CLAUDE_CONFIG_DIR transcripts).
   - 4 turns, 4 rows appended, 4 with a full opportunity → observed → verified chain.
   - States: unverified 4. The synthetic turns run no tests or commits, so there are no signals. `transcript_missing` 2 (subagent turns).
   - `verify --harness codex` → `unrecognized arguments`. The per-harness verifier is part of C2, which is not verified.
2. **`z0int outcomes export` per harness state dir.**
   - claude-code: 4 rows, 2 groups, 0 resolved, `assert_private` passed.
   - codex, grok, hermes, omp, omo, dsh: 0 rows each. `loop_export` still filters on the claude_code schemas; harness-generic export is C2 red test 10, not verified.
3. **Cross-harness frozen bundle** (C1 `harness_capture.freeze`) over every `state/<harness>/` record file:
   - 86 text-free rows covering all 7 harness schemas;
   - `unsupported_schemas` empty;
   - rebuilding gives the same hash (bundle sha256 b55033eeb70c…).
4. **Learner, run once** under the shared lock: `python -m evolution_lab.verified_loop --table tables/claude-code.jsonl` from `/mnt/zer0models/z0-wt/evolution-lab-verified-loop` @ 1b80a4e44972a9069248fb58a82ca2926143cde6.
   - Result: **`INSUFFICIENT_DATA`**, descriptive only.
   - Sufficiency failed on every criterion: rows 0/300, not_success 0/30, groups 0/10, and K=5 with both classes not met. The analysis rows are resolved rows, and all 4 exported rows are unverified.
   - The projection reports `available: false` because there is no manifest.sweep.
   - Nothing was promoted, and no promotion request was written (not a SHADOW_CANDIDATE).
   - Artifacts: `artifacts/verified-loop.{json,md}`.

## Unified memory: BLOCKED

`scripts/e2e_memory_probe.py` checked each harness seam of the integrate tree. Result: `artifacts/memory-probe.json`.

- **MCP tools:** the only z0 MCP server is `z0intelligence` (`intelligence_mcp`). Its tools/list returns `delegate_worker, list_models, route_worker`, with no memory tools. Only the CC plugin `.mcp.json` declares it, and no shim declares `z0-memory`.
- **Memory seam files:** there are none in any shim directory (CC, Codex, Grok, Hermes, OMP/OMO, DSH).
- **Memory-use receipts:** 0 capture rows carry an `opportunity_record.memory` receipt.
- **Contract:** the canonical contract that C7/C8 bind to is present on the base: `memory_contract.MemoryUseReceipt`, `MemoryScope` and `EventIdentity`. It is a contract only.

So the e2e steps "unified-memory read from every harness seam (same substrate, provenance + scope enforced) → memory-use receipts written" could not be exercised. They need C7-memory-core and C8-memory-wiring verified and merged. The Appendix B conflicts (Hermes pre_llm_call composition, DSH index.mjs registration, `.mcp.json`) will appear only at that merge.

## Privacy

None of the 18 synthetic markers appears anywhere under the shared Z0INT_HOME, the exported tables or the frozen bundle. The markers include the prompts, the subagent task, the stub response, and the README / commit-subject / branch canaries.

- Exported and frozen rows contain no paths.
- Outcome and failure rows contain no paths.
- `loop_export.assert_private` accepts every exported row.
- Opportunity rows keep the task `repo` path in the private capture state, as designed. It is never exported.
- The evidence artifacts were scanned the same way: no markers, and no `~` paths.

## Integration defect fixed (TDD, owning branch C3)

- **Symptom:** real `omo -p` with the z0int-bridge `omo.ts` shim finished its turn (stub answered, rows written), then hung until `timeout 180` (exit 124). This happened in both the on and the off arm. The same run without the extension exited 0.
- **Cause:** the resident bridge worker's child process and its stdio pipes kept the host's event loop alive. OMP exits explicitly, but senpi print mode waits for the loop to drain. C3 had only exercised OMO against a fake senpi in-process, so this never showed up.
- **Branch:** `feat/wire-omp-capture-20261003`, worktree C3, new commits on top of fe077db with no rewrite:
  - **a6d38de `test(omp)`, red:** `omo.test.ts` runs one turn through a fake senpi host in its own bun process and requires the host to exit by itself and the worker to follow it out (stdin EOF). Before the fix it failed with `exited=false` after 8 s while the turn was done (`c3-fix-red.txt`).
  - **3f6d139 `fix(omp)`:** `unrefChild()` unrefs the worker child and its stdin/stdout/stderr at spawn. In-flight requests keep their own timers, so a turn still closes before the host exits. The worker exits on EOF (`for raw in sys.stdin`). OMP behaviour is unchanged.
- **Green:** `bun test omp-extensions/` gives 28 pass, 1 skip. `test_omp_capture`, `test_bridge_worker` and `test_cognition_shadow` give 42 passed, 2 skipped (`c3-fix-green.txt`).
- **Merged** into integrate as e02bcf5. In the e2e, all 6 omo runs and all 6 omp runs exit 0.

## Observations (not fixed; outside the verified components or by design)

- **Claude Code:**
  - `model_id` is `unknown` on CC rows: Claude Code 2.1.288's hook payloads in `-p` mode did not carry a model. The spec allows "value or explicit unknown".
  - `policy_revision` is `unknown` everywhere except Hermes (`hermes-z0intelligence@0.2.0`).
  - Every CC turn writes one `duplicate_event` failure row, because Stop and SessionEnd both run `stop`. This is the counted no-op that C1 designed.
  - With the kill switch on (the off arm), the CC plugin still writes its pre-existing tokenomics usage event and a `state/claude-code/<session>.json` holding only `message_ids`. There are no capture rows and nothing model-visible.
  - In export, the `claude -p` turns get cohort `agent` from the existing transcript-cohort rule (6fee859): an SDK/print session is not interactive. The capture-time cohorts were interactive 2 and agent 2.
- **Learning gaps:**
  - The non-CC exports return 0 rows silently, instead of counting `unsupported_schema` in the manifest. C2 is meant to fix this.
  - The learner projection needs `export --sweep-since`, which uses transcripts. It is not meaningful on synthetic volume.
- **OMO:** each omo run takes about 19.5 s, the no-extension arm included. That is the host's own PostHog telemetry timing out in the no-network namespace, not the shim.
- **Hermes:** the Hermes host venv and fork export were reused read-only from `homes/C4a-hermes-capture`. Pyc files were redirected, and HERMES_HOME, HOME and Z0INT_HOME were all integration-local. The plugin under test was `git archive` of the integrate HEAD.

## Definition-of-done mapping (this integration only; live proofs are owner activation steps)

| DoD item | status here |
|---|---|
| D1 capture (7 harnesses, joined opp+outcome, cohort, identity, inert, no text, harness diff 0, install/config only) | Met on synthetic and isolated turns for all 7 (Grok by fixture, DSH by mock host). The "REAL live turn" proof is activation A1-A5 and is not done here. |
| D1b legacy import | not exercisable (C2 not verified) |
| D2 continuous learning (timer tick, per-harness tables, sufficiency, learner) | Partial: one manual learner run on the CC table reports INSUFFICIENT_DATA. No tick (C6), no non-CC tables (C2). |
| D2b promotion | nothing promoted, and no promotion code path was reached |
| D3 unified memory | **BLOCKED** (C7/C8 not verified) |
| D4 control plane | not exercised (C7) |
| Hygiene | Not pushed yet (Publish phase). No harness-fork or non-git integration code. |

## Reproduce

```
flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock \
  /mnt/zer0models/z0-wt/wiring/evidence/integration/scripts/e2e_all.sh      # rebuilds homes/integration/e2e, runs the learner once
flock -s /mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock \
  /mnt/zer0models/z0-wt/wiring/evidence/integration/scripts/suite.sh <tree> <tag>
```

The recorded e2e ran once, in full (`e2e-run.log`). After that only `e2e_check.py` was re-run, with two checker-only corrections:

- the learner decision is read from `primary`;
- the two host wall-clock fields above were added to the normalisation.

The learner was not re-run. The probe runs that came before, in `homes/integration/e2e-probe`, never invoked the learner.
