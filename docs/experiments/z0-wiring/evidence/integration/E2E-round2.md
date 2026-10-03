# Integration e2e, round 2: integrate/wiring-20261003 @ b99bc31 (2026-10-03)

## Verdict

Round 2 merged C7-memory-core and C8-memory-wiring into integrate/wiring-20261003 and re-ran the cross-harness e2e in isolation.

**Passing:**

- **Capture still works with memory merged in.** The round-1 capture e2e passes 63/63 for all 7 harnesses: joined capture, inert shadows, no text in rows, and the learner runs once.
- **Memory e2e passes 45/45.** It ran the C8 round-2 drivers on the integrate tree.
- **Round-2 additions pass 13 of 17 checks.** The remaining 4 fail and are listed below.
- **Unified memory reads work from every harness seam over one synthetic substrate.** Provenance and scope are enforced, the secret scrub holds against planted fake keys, and MemoryUseReceipt rows are present for every harness.
- **Cloud injection is off by default.**
- **Suite:** no new failures compared with the 6fee859 base.

**Not met (4 FAIL):**

1. **Harness-generic verify for all 7 harnesses: BLOCKED.**
   - `z0int outcomes verify --harness codex` fails with `unrecognized arguments`.
   - This is a C2-loop-consumers feature. C2 is not verified and was not merged.
2. **Harness-generic export for all 7 harnesses: FAIL (honest zeros).**
   - `outcomes export` gives 4 rows for claude-code and 0 for each of the other six.
   - The other six harnesses do have captured rows. The C1 frozen bundle counts them per harness: claude-code 11, codex 8, grok 24, hermes 16, omp 8, omo 8, dsh 12. These rows are unlabelled; every non-CC outcome is `missing_verifier`.
   - This also needs C2.
3. **Learning tick through its own entry point: BLOCKED.**
   - `z0int loop tick` does not exist. `loop tick --help` falls through to the `outcomes export` usage.
   - C6-learning-tick was never built (there is no branch).
   - The learner (evolution-lab verified_loop @ 1b80a4e) was run ONCE by hand, as in round 1. It reports `INSUFFICIENT_DATA` and fails every sufficiency criterion (rows 0/300, not_success 0/30, groups 0/10, K=5). No promotion request was written.
4. **MemoryUseReceipt in capture opportunity records: UNMET.**
   - SPEC §2 and DoD D3 say every memory use lands in `opportunity_record.memory`.
   - C7 built the binding: `harness_capture.opportunity_record` validates `ctx['memory']`, which the C7 merge test proves.
   - C8 never passes a receipt into the capture ctx. Every opportunity row in the run therefore has 0 `memory` fields (claude-code 0/4, codex 0/2, grok 0/6, hermes 0/3, omp 0/2, omo 0/2, dsh 0/3).
   - The receipts exist, but in `state/memory/seam/<h>.jsonl` and in the acceptance rows, not in the opportunity rows.
   - Not fixed here: that would add a new C8 behaviour (joining the detached memory child with the detached capture child per turn_key), which is beyond merge-conflict resolution. It is an open item for C8.

## Merges (no-ff, no rewrite, not pushed)

Worktree `/mnt/zer0models/z0-wt/wiring/wt/integrate`. Start: e02bcf5 (C1, C3, C4a, C5 plus the round-1 C3 fix).

- **Order:** dependency order restricted to verified components. C2 and C6 were skipped as not verified; C6 has no branch at all.

| merge | component | head | conflicts |
|---|---|---|---|
| aaa4f8f | C7-memory-core | 772ba7d | 1, `src/z0int/harness_capture.py` (`opportunity_record` return) |
| b99bc31 | C8-memory-wiring | 41aec0f | none (textual) |

- Final HEAD **b99bc316e2d23e3f0d901b5fe7542d4ce1058060**. The tree is clean.
- Both merges are authored by lesseradmin and carry the Co-Authored-By trailer.
- The tree of b99bc31 equals C8 41aec0f plus the new merge test. Nothing else differs.

### Conflict 1 (C7): `harness_capture.opportunity_record`

- **The two sides:**
  - C4a (in integrate) added P-9 `packet_text`, which redacts State Packet text.
  - C7 added the validated `MemoryUseReceipt` row field, which rejects instruction authority.
- **Resolution:** keep both. This is byte-identical to the resolution C8 already used in its base merge 3028e0c; git rerere replayed it.
- **Test:** `tests/test_integrate_wiring.py::test_opportunity_row_keeps_packet_text_redaction_and_memory_receipt`. It uses a synthetic repo with a canary commit subject.
  - On C7 alone (772ba7d): `TypeError: unexpected keyword argument 'packet_text'`.
  - On integrate alone (e02bcf5): `AssertionError`. The raw receipt object is passed through unvalidated.
  - On the merge: passes. All 8 tests pass, together with `test_memory_receipt_binding.py`.
  - Evidence: `round2/merge-c7-test-red-green.txt`.

### Appendix B expected conflicts (Hermes pre_llm_call, DSH index.mjs, .mcp.json/hooks.json)

- They did not appear at this merge, because C8 had already merged integrate e02bcf5 into its own base (3028e0c) and composed these seams on its branch.
- The "registered exactly once" behaviours are covered by C8's own tests, which are green in the integrate suite:
  - `test_automatic_capture_and_memory_share_one_pre_llm_call` (Hermes);
  - `index.mjs registers exactly one memory pre-step` (DSH, node);
  - `test_every_harness_has_exactly_one_z0_memory_server_on_the_memory_profile`;
  - `test_claude_code_keeps_its_route_worker_server_unchanged`;
  - `test_memory_push_seam_is_its_own_user_prompt_submit_entry`.
- I added no new test for these, because there was no conflict to resolve.

## Pinned venv

- `/mnt/zer0models/z0-wt/wiring/venv-integrate` was re-installed from the worktree: `uv pip install --torch-backend cpu -e 'wt/integrate[test]'`, under the shared lock.
- Log: `scratch/venv-integrate-install-r2.log`. Only `openjev-phase1` (the editable integrate package) was rebuilt.
- `/mnt/zer0models/z0-wt/venv-claude-code` was not touched.

## Full suite (shared quiet lock, env -i, isolated homes, socket guard)

Runner: `round2/scripts/suite-r2.sh`. It is round-1 `suite.sh` with two changes: node now runs every `tests/*.mjs` (which includes C8's `test-dsh-memory.mjs`), and the homes live under `homes/integration-r2`.

| tree | pytest | node | bun |
|---|---|---|---|
| base 6fee859 (round 1) | 11 failed, 964 passed, 4 skipped | n/a | 12 pass |
| integrate e02bcf5 (round 1) | 11 failed, 1065 passed, 8 skipped | rc 0 | 28 pass, 1 skip |
| **integrate b99bc31** | **11 failed, 1224 passed, 8 skipped, 1 xfailed** | **34/34, rc 0** | **37 pass, 1 skip, 0 fail** |

- The FAILED set is identical to the 6fee859 base set (diff is empty): the 11 environment failures in `test_functions_verify` (live), `test_quota_budget` and `test_provider_saturation`.
- The xfail is C8's strict xfail documenting the Hermes host `api_content` sidecar.
- No socket-guard violation.
- Raw output: `round2/suite-head-b99bc31{,-node,-bun}.raw.txt`.

## Isolated e2e (homes under `/mnt/zer0models/z0-wt/wiring/homes/integration-r2/`)

Three stages, each under `flock -s quiet-lane.lock`. Synthetic data only.

### A. Capture: round-1 drivers, unchanged

- **Run:** `round2/scripts/e2e_capture_r2.sh` (round-1 `e2e_all.sh` with the root moved to `integration-r2/e2e`). Log: `round2/e2e-capture-run.log`. Verdict: `round2/capture-e2e-check.json`, **63/63 OK**.
- **Hosts and isolation:** real `claude -p` 2.1.288, `codex exec`, `omp -p`, `omo -p` and `hermes chat -q`, plus Grok fixtures and the DSH mock host. Every model is a loopback stub. The CLIs run under `hostless unshare -rn` or bwrap, with the Hermes and z0int live homes masked.
- **Per-harness opp / out / joined:** claude-code 4/4/4 (2 interactive, 2 agent), codex 2/2/2, grok 6/6/6, omp 2/2/2, omo 2/2/2, hermes 3/3/3, dsh 3/3/3.
- **Inertness:** inert checks hold for all 7.
- **Memory shadow ran by default during capture.**
  - New since round 1: with C8 merged, the CC, Codex and Hermes memory seams now run in their default `shadow` mode during the capture legs. The seam rows are claude-code 2, codex 2, hermes 3 shadow, plus 1 error from the `unavailable` arm.
  - Model requests stayed byte-identical between the on and off arms.
  - The capture kill switch (`Z0INT_CAPTURE=0`) does not turn the memory shadow off; its own switch is `Z0INT_MEMORY_INJECT=off`. Recorded, by design.
- **Stale round-1 labels:** the round-1 memory probe and the checker's static verdict string are out of date. The probe now says PARTIAL because it does not know the `omp-extensions/z0-memory` location. Stages B and C below are the authoritative memory e2e.

### B. Memory: C8 round-2 drivers on the integrate tree

- **Run:** `round2/scripts/e2e_memory_r2.sh` (C8 `round2/scripts/e2e.sh` with the root moved to `integration-r2/memory`; `WT` is the integrate worktree, SHA b99bc31). Log: `round2/e2e-memory-run.log`. Verdict: `round2/memory-e2e-check.json`, **45/45 OK**.
- **Substrate:** one synthetic substrate for every harness:
  - an AgentsView fixture DB seeded with the frozen z0evals fb14919 cohort, a secret-probe session and a sibling project;
  - an EventLog ledger;
  - the C7 **fake** TencentDB gateway on 127.0.0.1:11548, with a fake bearer.
- **Live services not touched:**
  - The real gateway at 127.0.0.1:8420 and its `default` tenant were not contacted.
  - The live AgentsView v0.44 data dir was not opened.
- **Push seams.** Real CLIs, arms off / shadow / canary on each of 6 cohort questions:
  - CC, Hermes, OMP: shadow requests are identical to off; the canary brief appears in the next model request; seam rows `would_inject` with a snapshot id.
  - DSH (mock cordis host): off and shadow admit the native batch only.
  - Codex: hook arm.
- **Pull seams:** the z0-memory MCP over stdio for CC, Codex, Grok, OMP, OMO and DSH serves memory tools only, and `route_worker` returns an error.
- **Scope, egress and scrub:**
  - The sibling-project canary is never in a model request, a DSH brief or a seam row.
  - In the cloud arm, a real CC run with a non-loopback base URL is blocked even though the generic env says loopback.
  - The secret probe is scrubbed in model-visible requests and in seam rows.
- **Acceptance rows, all written:**
  - dsh, hermes, omo and omp validate against z0evals fb14919 `receipt.schema.json`.
  - claude-code, codex and grok are labelled `z0int.memory_acceptance.v0`, and the study label is refused for them.
  - B: VERIFIED for dsh, hermes, omp and claude-code; UNVERIFIED for codex and omo (live-only); UNSUPPORTED for grok (pull-only). As planned, none of these are counted toward G-MEM56/G-MEMZ0.
- **Persistence:** matches the recorded C8 deviations. OMP is clean. Hermes `api_content`, the CC transcript and the DSH durable pre-step persist the brief by host design; this is an owner decision. The echo guard keeps re-indexed briefs out of the evidence.

### C. Round-2 additions

- **Run:** `round2/scripts/e2e_r2_extra.py` under `hostless unshare -rn` and the socket guard, over the stage B home and the stage A learn outputs. Log: `round2/e2e-extra-run.log`. Verdict: `round2/extra-check.json`, **13/17**.

| check | result | detail |
|---|---|---|
| memory.codex_push_seam_shadow_default | PASS | real Codex hook command, mode unset, loopback endpoint: stdout empty, 1 `shadow` row with a receipt |
| memory.cloud_injection_default_off | PASS | CC/Codex with mode unset and the public API base URL: default mode `shadow`, nothing printed; canary with the public URL gives `cloud_injection_blocked`, endpoint_loopback false; `allow_cloud_injection` defaults to false for all 7 |
| memory.no_injection_into_a_non_loopback_endpoint_anywhere | PASS | 0 injected rows with a non-loopback endpoint across all 7 harnesses |
| memory.same_substrate_every_mcp_seam_same_evidence | PASS | all 6 MCP seams return the identical 4-item evidence set for one question |
| memory.same_substrate_push_receipts_share_evidence | PASS | CC, Hermes, OMP, Codex and DSH receipts cite shared event uids (9 distinct in total) |
| memory.provenance_canonical_locators_and_event_uids | PASS | every evidence ref is `agentsview:<sid>#<ord>` or `eventlog:<seq>`, and every would-inject receipt carries `evidence_event_uids` |
| memory.scope_no_sibling_project_in_receipts | PASS | 0 |
| memory.secret_scrub_planted_fake_keys_never_leave | PASS | planted api_key, aws, bearer, bws and pem fakes (all 5 confirmed in the substrate DB) appear in 0 files under the z0 home, results, stub request logs, CC config dir or OMP agent dir |
| memory.memory_use_receipt_rows_present_push_seams | PASS | valid MemoryUseReceipt seam rows: claude-code 21, codex 8, grok 6, hermes 29, omp 20, omo 6, dsh 17 |
| memory.acceptance_rows_every_harness | PASS | 6 rows per harness, all 7 harnesses |
| memory.opportunity_record_memory_joined | **FAIL** | 0 `memory` fields in the capture opportunity rows (see Verdict 4) |
| learn.harness_generic_export_nonzero_rows_all_7 | **FAIL** | export rows: claude-code 4, every other harness 0 (C2 not merged) |
| learn.harness_generic_verify_all_7 | **FAIL / BLOCKED** | `--harness` is not an option (C2) |
| learn.frozen_capture_rows_nonzero_all_7 | PASS | unlabelled capture rows per harness: cc 11, codex 8, grok 24, hermes 16, omp 8, omo 8, dsh 12 |
| learn.tick_entry_point_runs_once | **FAIL / BLOCKED** | no `z0int loop tick` (C6 never built) |
| learn.learner_once_reports_sufficiency_honestly_never_promotes | PASS | `INSUFFICIENT_DATA`; no promotion_request |

## Privacy and isolation

- **Isolation:**
  - Every home is under `homes/integration-r2/`. Hermes and the memory legs ran inside bwrap with `/workspace/hermes-home` and `~/.z0int` masked by empty tmpfs (`masks-inside`: 0 entries each).
  - The CLI legs ran in private network namespaces with loopback only.
  - Socket-guard logs are empty.
- **Untouched:** no live z0 service (11501), no live TencentDB gateway or tenant, no live AgentsView data dir, no systemd unit, no dsh binary.
- **Evidence scan:**
  - The round-2 evidence holds none of the 5 fake secrets.
  - `~` appears only in two driver scripts, as binary paths (bun, claude), as in round 1.
  - Capture privacy checks pass: no marker text in the shared Z0INT_HOME, the exports or the frozen bundle; no paths in exported or frozen rows.

## Open problems

1. **C2-loop-consumers is not verified.** Without it there is no `--harness` verify, no harness-generic export, no AgentsView join rules and no legacy import. So the non-CC labels for D1 and D2 are missing; the six non-CC harnesses export 0 rows.
2. **C6-learning-tick was never built** (it depends on C2). D2 continuous learning has no scheduled `loop tick`, no shadow slot and no per-stratum report. The learner was run by hand only.
3. **`opportunity_record.memory` is not wired by C8**, so DoD D3 ("MemoryUseReceipt rows appear in ... opportunity records") is UNMET. The receipts live in the seam rows and acceptance rows.
4. **C8 persistence deviations** for Hermes, Claude Code and DSH (the brief is persisted by host design) are still an owner decision. The spec clause holds for OMP only.
5. **Memory shadow defaults to on.** The memory seam defaults to `shadow` independently of capture. Activating capture (A1, A3, A5) therefore also starts memory shadow children unless `Z0INT_MEMORY_INJECT=off` or `memory.json` says otherwise. Shadow is inert and bounded at 4 children, but ACTIVATE.md should state it.
6. **#63 TencentDB provenance stays UNMET.** Gateway items carry no project scope, so they never enter a scoped brief.
7. **`publish/SUMMARY.md` does not exist** (the task referenced it). The publish dir holds ACTIVATE.md and the PR bodies only. Round 2 did not push anything.
8. **Round-1 checker labels are stale.** `e2e_memory_probe.py` and its static verdict string still say "memory BLOCKED". In this run they are superseded by the stage B and C verdicts.

## Reproduce

```
W=/mnt/zer0models/z0-wt/wiring; L=/mnt/zer0models/cua-lane-tmp/locks/quiet-lane.lock
flock -s $L $W/evidence/integration/round2/scripts/suite-r2.sh $W/wt/integrate head-b99bc31
flock -s $L $W/evidence/integration/round2/scripts/e2e_capture_r2.sh
flock -s $L $W/evidence/integration/round2/scripts/e2e_memory_r2.sh $W/wt/integrate
M=$W/homes/integration-r2/memory; C=$W/homes/integration-r2/e2e; X=$W/homes/integration-r2/extra; mkdir -p $X/{home,tmp,pycache}
flock -s $L /mnt/zer0models/github/cua-lanes/bin/hostless unshare -rn env -i PATH=$W/venv-integrate/bin:/usr/bin:/bin \
  LANG=C.UTF-8 HOME=$X/home TMPDIR=$X/tmp Z0INT_HOME=$M/z0 AGENTSVIEW_DATA_DIR=$M/av \
  PYTHONPATH=$W/scratch/integration/guard:$W/wt/integrate/src PYTHONPYCACHEPREFIX=$X/pycache \
  Z0INT_SOCKET_GUARD_LOG=$X/socket-violations.log Z0INT_PYTHON=$W/venv-integrate/bin/python \
  $W/venv-integrate/bin/python $W/evidence/integration/round2/scripts/e2e_r2_extra.py $W/wt/integrate $C $M \
  $W/evidence/integration/round2/extra-check.json
```
