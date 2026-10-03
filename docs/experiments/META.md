# META: session 2026-09-29 to 2026-10-03, every program on one page

Written 2026-10-03 at about 14:50 CDT for the owner to read at a meta level. Every row is a fact taken from a pushed README, SYNTHESIS, STATE or result file. Links go to the owner's forks only. Short SHAs are the fork tips, checked with `git ls-remote` when this page was written.

**Where to start**
- z0 family archive: [`kvnloo/z0intelligence@experiments/session-20261003:docs/experiments/`](https://github.com/kvnloo/z0intelligence/tree/experiments/session-20261003/docs/experiments) ([README](https://github.com/kvnloo/z0intelligence/blob/experiments/session-20261003/docs/experiments/README.md), [push inventory](https://github.com/kvnloo/z0intelligence/blob/experiments/session-20261003/docs/experiments/push-all/INVENTORY.md))
- CUA archive: [`kvnloo/cua@evidence/cua-lane-20261003-local:scripts/repro/handoff/lanes-artifacts-20261003/`](https://github.com/kvnloo/cua/tree/evidence/cua-lane-20261003-local/scripts/repro/handoff/lanes-artifacts-20261003) (`8bbdd1ce4`). Each lane's full packet sits on its own `exp/*` branch under `docs/experiments/<lane>/`.

## 1. Status board

| # | Program | Verdict now | Still running? |
|---|---|---|---|
| 1 | CUA RFC continuous loop (waves 0 to 7) | E1 met (pending publication), E2/E3 met except browser fill and the live layer, E5 staged, **E6 not met** | **yes**: wave 7 (FRESH-07 Phase 2 live; R2-07g packet just written) |
| 2 | CUA autoresearch harness, calibration 1 | 3 evaluator defects and a 1.7% rebuild layout bias found and fixed | no |
| 3 | Calibration 2 and R10c | Pre-registered verdict **FAIL** (R10). Owner-ruled rerun R10c **PASS**. Pilot v2 **NOT RUN** | no |
| 4 | CUA #107 (mirror vs scoped reads) | B BLOCKED, C mirror parked, D REVISE | no |
| 5 | CUA x Hermes x z0int stack (v1 + v2) | SMOKE and MULTISEAT accepted, SAMPLES not accepted (erratum only). v2 addressing fix works | no |
| 6 | Hermes x CUA x z0 x Bend integration | 5/5 lanes KEEP. The bend-native plugin itself is **REVISE** (12 bugs, unfixed) | no |
| 7 | Hermes upstream waves and promotion | 4 waves and #126847 posted. **All still open upstream** (checked 2026-10-03) | no (waiting on maintainers) |
| 8 | z0 shadow loop v0 + #56 RFC | **INSUFFICIENT_DATA**. The bottleneck is labels, not search | no |
| 9 | DSH self-learning audit | Done (read-only). No outcome-bearing DSH data exists | no |
| 10 | z0 wiring build rounds 1 to 3 | Round 2 integrated 6 components. C2 and C7 were REVISE | **yes**: round 3 (C6 verify, C2/C8 fixes) |
| 11 | AgentsView v0.39 to v0.44 + OMO | Done and live. 30-minute sync timer | no |
| 12 | TencentDB memory service + L1/L2/L3 model selection | Service e2e **PASS**. Model selection is mid-run, and the owner-named paid arms are **blocked** | **yes**: local candidates R1 |
| 13 | Bend perf triage (AODL gate, CPython vs Bend) | Interim: Bend is about 5x **slower** per decision on every parity-passing config | **yes**: batch rerun |
| 14 | Bend/z0 seam RFC | Draft. 3/3 judges ranked law-first first | no |
| 15 | z0 x Claude Code install (side work) | Installed. The study branch measured -34% tokens / -45% cost | no |

## 2. Programs

### 1. CUA RFC continuous loop
- **Goal:** find the Linux critical path for CUA browser and native actions and drive the untested share to under 5%, with one-source composition (E3), invariants (E4), staged deliverables for kvnloo/cua #10, #74 and the trycua 3963 rewrite (E5), then stability judges (E6). Canonical queue: kvnloo/cua#73 and #93.
- **Status:** 7 of 12 waves used, 0 stalled. 58 dispositions in `STATE.json`. Wave 6 accepted 8 of 8 lanes with no breach. Wave 7 is running: R2-07g, FRESH-07, R2-07f, FIX-04, B-09, DOC-3963b, PUB-04 and DOC-10-74b.
- **Key numbers:**
  - Browser click with feedback on: 1541.6 ms vs 23.5 ms with it off. 98% of that is the awaited cursor glide (R2-01 KEEP).
  - The glide is the largest single component: 2.4 to 3.0 s, 94 to 97% of BASE T. R2-10 speedup S drops from about 45 to 1.01 without it.
  - Native: cursor reveal 255 to 1416 ms, post-DoAction sleep about 51 ms, focus settle about 241 ms (IRREDUCIBLE).
  - Browser scripted untested share: toggle and modal 0.4%, fill 16.9%. The fill gap is an unstamped 10 ms verify poll; B-09 targets it.
  - Compiled replay deletes the live toggle provider decision (R2-07e, 29/29). Modal is REVISE. R2-07g (local, not yet verified): live modal fallback 0/3, and the quiet toggle block missed the +2.0 ms gate by 0.17 ms.
  - TypeSafe budget: 583 of 600 reached after wave 6, 17 left.
- **Code:** one branch per lane on [kvnloo/cua](https://github.com/kvnloo/cua/branches/all), for example [`exp/b-08-per-process-cold-b7-20261003`](https://github.com/kvnloo/cua/tree/exp/b-08-per-process-cold-b7-20261003) `49ae94590`, [`exp/r2-07e-modal-gate-phase-l-20261003`](https://github.com/kvnloo/cua/tree/exp/r2-07e-modal-gate-phase-l-20261003) `67b99ddc6` and [`exp/fix-03-file-input-toctou-session-routing-20261003`](https://github.com/kvnloo/cua/tree/exp/fix-03-file-input-toctou-session-routing-20261003) `e300edbd3`. Drafts: [`docs/rfc3963-rewrite-draft-20261003`](https://github.com/kvnloo/cua/tree/docs/rfc3963-rewrite-draft-20261003), [`docs/accounting-10-queue-74-20261003`](https://github.com/kvnloo/cua/tree/docs/accounting-10-queue-74-20261003) and their `-r2-` versions.
- **Data:** [`r2/SYNTHESIS-w6.md`](https://github.com/kvnloo/cua/blob/evidence/cua-lane-20261003-local/scripts/repro/handoff/lanes-artifacts-20261003/r2/SYNTHESIS-w6.md) (and `-w1`…`-w5`), [`r2/loop/STATE.json`](https://github.com/kvnloo/cua/blob/evidence/cua-lane-20261003-local/scripts/repro/handoff/lanes-artifacts-20261003/r2/loop/STATE.json), `r2/loop/END_CONDITION.md` and `r2/drafts/` (not posted). The raw run dirs (3.1 GB) stay on the host.
- **Open decisions for the owner:**
  - **Published-branch privacy (5 fork branches already public):** `exp/r2-10-composition-20261002`, `exp/own-20g-guard-final-diff-a2-20261003`, `exp/n-03-native-closure-axfg-a3-20261003`, `exp/n-04-native-composition-rprime-20261003` and `exp/r2-10r-recert-a3-20261003`. They hold a private name list, the user name, or tmp session-bus paths. Clean candidates exist for four of them. Replacing a branch needs a force or delete-and-repush, which only the owner can approve.
  - **Budget:** raise the TypeSafe cap (live BASE vs COMP+CR S needs 120 or more reached, live recert 180 or more) or accept BLOCKED.
  - **Product policy:** browser glide on or off, native cursor reveal, the 100 ms insert_text settle, the B-02 endpoint re-proof, and process/session reuse (B-08: fill 10.6 ms, toggle 3.1 ms, modal 4.6 ms of per-process cold excess).
  - **Rulings:** #20 name-owner trigger, OWN-09R bounded wait and R8, the R2-07e fallback substitution, FIX-03 F4 `effect=unknown`, R2-09 WebKitGTK, and #78 S1.
  - **Out of reach on this host:** Hyprland seat rows (#16, #94) and macOS/Windows rows.
- **Deferred push:** `exp/fresh-07-main-9a2b1d99e-20261003` (local `b2ddd9c7b`, live master4 queue) and `exp/r2-07g-live-fallback-ln-20261003` (local `869896d57`, packet written in the last 15 min). Re-snapshot `evidence/cua-lane-20261003-local` after wave 7 publishes.
- **Note:** this push-all run published the wave-7 lane heads B-09 (`56284782d`), FIX-04 (`2b59a66f7`) and R2-07f (`2c0475522`) **before** the loop's own wave-7 verify step. Treat them as lane output, not as accepted results.

### 2. Autoresearch harness: calibration 1
- **Goal:** an automated propose, evaluate and keep loop for CUA fixes, calibrated on planted defects before any pilot.
- **Verdict:** calibration 1 caught 3 evaluator defects (dbus socket normaliser, LORD++ alpha below bootstrap resolution, a noisy trace-off check) and a 1.7% rebuild layout bias. All were fixed (F1 to F3) before calibration 2.
- **Code:** [`exp/ar-harness-20261002`](https://github.com/kvnloo/cua/tree/exp/ar-harness-20261002) `73ce589ea`. Proposer candidates: `ar/calib/*` and `ar/calib2/*` (19 each, pushed this run "for owner review") and `ar/dryrun/settle-watch-120`.
- **Data:** [`ar/`](https://github.com/kvnloo/cua/tree/evidence/cua-lane-20261003-local/scripts/repro/handoff/lanes-artifacts-20261003/ar) (`SYNTHESIS.md`, `CALIBRATION.json`, `FIX*.json`).

### 3. Calibration 2 and R10c
- **Verdict:** 11 of 12 rows passed. R3 real deletion KEEP 10/10. R10 failed its manipulation check because the burner count came from `nproc`=10 (capped by OMP_NUM_THREADS) on a 20-CPU host. R10b (30 burners) passed but cannot change the pre-registered verdict. The owner-ruled rerun R10c (prereg `3a1d10646` before trials) **PASSED**: 2/2 KEEP, G7 pass, PSI share 0.65/0.67 vs 0.0035. Pre-registered overall verdict: FAIL. Owner-ruled verdict: PASS. Cost: 16.6 min per candidate uncontended, 37.8 min observed.
- **Code and data:** [`exp/ar-harness-20261002`](https://github.com/kvnloo/cua/tree/exp/ar-harness-20261002) `73ce589ea` under `docs/experiments/ar-calibration2-2026-10-02/` (R10c commits `3a1d10646` and `73ce589ea`). The raw run dir (1.4 GB) stays on the host.
- **Watch out:** [`exp/ar-pilot2-20261002`](https://github.com/kvnloo/cua/tree/exp/ar-pilot2-20261002) `35a20c403` is **stale**. It still says "NOT RUN: calibration 2 FAIL on R10" and was not updated after R10c.
- **Next (pre-agreed):** fix F7 (flaky history tests false-REJECT 2/15 at G1) and F8 (a session start-up failure ends the evaluation), run an idle-host A/A, then the pilot on a fresh ledger.

### 4. CUA #107: live state mirror vs scoped reads
- **Verdict:** B BLOCKED (no producer-side scoped read). C mirror parked: 0 stale reads in 90,659 checks, but it deletes no work. D REVISE: the PR 4316 guard submitted to a decoy form 5/5 in DC06.
- **Code:** [`exp/i107-map-20261002`](https://github.com/kvnloo/cua/tree/exp/i107-map-20261002) `be68363bc` (prereg), plus `exp/i107-{ab,cshadow,d}-20261002`. **Data:** [`i107/SYNTHESIS.md`](https://github.com/kvnloo/cua/blob/evidence/cua-lane-20261003-local/scripts/repro/handoff/lanes-artifacts-20261003/i107/SYNTHESIS.md).

### 5. CUA x Hermes x z0int stack
- **Goal:** Hermes executes computer-use tasks on a local model in private desktop sessions, and z0int scores them in shadow. Directive: kvnloo/hermes-agent#319.
- **Numbers:** 106 real CU runs on local qwen2.5:7b. 4 concurrent sway agents with 0 cross-landings, 2.38x throughput. v1 CU success was low because of a Hermes element_index vs Driver addressing mismatch. The v2 element_token fix took gtk3 from 0/12 to 12/12 and browser from 1/12 to 11/12. julia_1 NOT_CONFIRMED. No z0int backend beat the prior on `verification_needed`.
- **Code:** kvnloo/hermes-agent [`exp/stack-integration-20261002`](https://github.com/kvnloo/hermes-agent/tree/exp/stack-integration-20261002) `0d60437`, `exp/stack-{addr,confirm,samples,multiseat,smoke}-20261002`. kvnloo/cua `exp/stack-*-20261002`. **Data:** [`stack/SYNTHESIS.md`](https://github.com/kvnloo/cua/blob/evidence/cua-lane-20261003-local/scripts/repro/handoff/lanes-artifacts-20261003/stack/SYNTHESIS.md). The raw data (2.3 GB) stays on the host.

### 6. Hermes x CUA x z0 x Bend integration
- **Verdict:** all 5 lanes KEEP. The plugin is **REVISE**: 12 unfixed bugs, including default `stack_service_port` 11501 (the live z0 service) and a `close()` that cannot stop a full-queue worker. Patched Bend 17db447a fixes the #389 mis-certification and can be promoted. #390 accepted as packaged. AODL 20/20.
- **Code:** [kvnloo/bend-native](https://github.com/kvnloo/bend-native) `exp/{aodl,b389,b390,contract,e2e}-20261002`, [kvnloo/bend `exp/b389-patchonly-20261002`](https://github.com/kvnloo/bend/tree/exp/b389-patchonly-20261002) `6e940a0b2`, and kvnloo/hermes-agent [`exp/bend-stack-integration-20261002`](https://github.com/kvnloo/hermes-agent/tree/exp/bend-stack-integration-20261002) `ad31bbf`. **Data:** [`bend-stack/SYNTHESIS.md`](https://github.com/kvnloo/cua/blob/evidence/cua-lane-20261003-local/scripts/repro/handoff/lanes-artifacts-20261003/bend-stack/SYNTHESIS.md).
- **Decision:** the owner's 2026-10-03 plugin-layout decision moves `hermes z0` out of bend-native into z0intelligence `harness-adapters/`. The 12 bugs still need a fix pass or a retirement call.
- **Lesson:** B389 ran unlocked CPU builds during the RFC loop's exclusive timing windows, so `EXTERNAL-INTERFERENCE.md` was added to three packets.

### 7. Hermes upstream waves and promotion
- **Goal:** land kvnloo work in NousResearch/hermes-agent through what maintainers actually accept: salvage and mechanical close waves, at most about 3 open PRs.
- **State (read-only check today):** waves #129760, #129761, #130139 and #130140 and the `/model` picker PR #126847 are **all open, with no maintainer action yet**. Earlier mechanical waves landed 9/9 within 1 to 34 hours. Theme waves were NOT_PLANNED 4/4.
- **Promotion queue (2026-10-01):** 120 candidates reproved: 73 READY, 24 superseded, 15 external-owned. Frontier: 14 staged branches (`staged/<id>`), none PROMOTION_READY.
- **Code and data:** kvnloo/hermes-agent [`claude/ledger`](https://github.com/kvnloo/hermes-agent/tree/claude/ledger) `47e05ca` (`factory/{frontier,promotion-readiness,salvage-wave}-*`). The kanban lane's 126 `wt/t_*` and related branches were pushed this run (list: [`push-all/hermes-oss-push-list.txt`](https://github.com/kvnloo/z0intelligence/blob/experiments/session-20261003/docs/experiments/push-all/hermes-oss-push-list.txt)).
- **Owner decisions:**
  - 15 local `ready/*` branches have diverged from newer fork copies (no force-push was done).
  - 16 kanban branches ([list](https://github.com/kvnloo/z0intelligence/blob/experiments/session-20261003/docs/experiments/push-all/hermes-oss-held-branches.txt)) were held for privacy: raw model responses, a `.env` lease file, a kanban.db with emails, and home paths. Choose sanitised re-push or keep local.
  - Whether to post the held wave-superseded-prs-2.
  - `upstream-drafts-2026-09-30` (204 MB) is still host-only, under `/mnt/zer0models/project-artifacts/hermes-agent/`.

### 8. z0 shadow loop v0 + #56 RFC
- **Goal:** z0 learns from ordinary use. Inert shadow policies are scored on verified outcomes, searched in evolution-lab, and promoted only with owner approval.
- **Verdict:** INSUFFICIENT_DATA twice.
  - 2026-09-30 (evolution-lab): 6 analysis rows, 1 not-success, 1 session.
  - 2026-10-03 (this host): 9 input rows, all `no_opportunity`, so 0 analysis rows. Over 7 days, 149 turns produced 10 resolved outcomes.
  - The gate needs 300 or more rows, 30 or more negatives and 10 or more groups.
- **Code:** z0int [`feat/shadow-loop-v0`](https://github.com/kvnloo/z0intelligence/tree/feat/shadow-loop-v0) `6fee859`, evolution-lab [`exp/verified-loop-v0-run`](https://github.com/kvnloo/evolution-lab/tree/exp/verified-loop-v0-run) `1b80a4e`. **Data:** [`shadow-loop/`](https://github.com/kvnloo/z0intelligence/tree/experiments/session-20261003/docs/experiments/shadow-loop).
- **Correction:** the RFC addendum **is already live** on kvnloo/z0intelligence#56 (body updated 2026-10-03 04:10Z, about 9.6k chars). The shadow-loop README called the bodies "local drafts"; that wording is corrected in the same commit as this page.
- **Next:** M1, label volume, before any population search. The wiring program (10) is the supply side.

### 9. DSH self-learning audit
- **Findings:**
  - `~/.dsh/jev/receipts.jsonl` holds 13,192 rows (09-21 to 09-30) in a private schema with no outcomes. The route never changed (244/244), and nothing consumes it.
  - The z0int DSH adapter is disabled.
  - There is no DSH DecisionOpportunity emitter (tracked in z0int#62).
  - Four divergent z0int trees serve DSH. The live service runs a closed-PR branch.
- **Rule from the owner:** no PRs into DSH core. Learning belongs in z0 plugins.
- **Data:** [`z0-wiring/prior-findings.md`](https://github.com/kvnloo/z0intelligence/blob/experiments/session-20261003/docs/experiments/z0-wiring/prior-findings.md). The raw research dumps contain real conversation text, so they were **not pushed** and stay on the host.

### 10. z0 wiring (capture, learning tick, unified memory), rounds 1 to 3
- **Goal:** capture on every harness (Claude Code, OMP/OMO, Hermes `clean`, DSH), continuously learning shadows, and cross-harness memory. TDD, blind verifiers, and nothing live without owner approval.
- **Status:** round 2 integrated C1, C3, C4a, C5, C7 and C8 at [`integrate/wiring-20261003`](https://github.com/kvnloo/z0intelligence/tree/integrate/wiring-20261003) `b99bc31`. Round-2 verdicts: C2 REVISE (`exit_code()` takes the first match) and C7 REVISE (TencentDB revision is a software revision, not a data revision). Round 3 is running.
- **Code:** `feat/wire-{loop-core,omp-capture,hermes-capture,dsh-plugin,memory-core}-20261003` (all match the fork) and the `-unverified` variants. **Data:** [`z0-wiring/`](https://github.com/kvnloo/z0intelligence/tree/experiments/session-20261003/docs/experiments/z0-wiring) (spec, round2, evidence, publish/ACTIVATE.md as of 14:20).
- **Deferred push (round 3 owns them):** C2 `feat/wire-loop-consumers-20261003` (local `9de0d53`), C8 `feat/wire-memory-harnesses-20261003` (local `73ff88e`, 12 commits ahead of fork `41aec0f`), C6 `feat/wire-learning-tick-20261003` (local `9b91b26`), and the refresh of `z0-wiring/{evidence,publish}` including C6 evidence and E2E-round3.
- **Owner decisions:**
  - Merge `integrate/wiring` into master after review (approved in principle as option (a)).
  - Switch Hermes `clean` to `memory.provider: memory_tencentdb` (owner-deferred).
  - Approve each per-harness memory-injection canary.

### 11. AgentsView v0.39 to v0.44 + OMO parser
- **Verdict:** live. dataVersion 113 = binary. No agents lost. Sessions: OMO 14, DSH 203, Claude Code 888. FTS and MCP OK. `require_auth` unchanged. Sync timer runs every 30 minutes. `go test`: 60 packages ok; the one failing test also fails on pristine v0.44.0.
- **Code:** [kvnloo/agentsview `build/v0.44.0-omo-20261003`](https://github.com/kvnloo/agentsview/tree/build/v0.44.0-omo-20261003) `987293f4` (it adds a local Codex-fork retry fix). **Data:** [`z0-wiring/ops-agentsview/`](https://github.com/kvnloo/z0intelligence/tree/experiments/session-20261003/docs/experiments/z0-wiring/ops-agentsview). The 148 MB binaries stay on the host.
- **Decision:** the OMO parser lives only on fork branches (`feat/omo-source` and this build branch), and so does the Codex-fork fix. Whether to offer either upstream is an open owner call.

### 12. TencentDB memory service + L1/L2/L3 model selection
- **Service:** the gateway on 127.0.0.1:8420 runs under the systemd user unit `tencentdb-memory`. The e2e test **PASSED** on an isolated tenant. Prefetch p50 3.1 ms, search p50 1.2 ms, conversation add p50 5.5 ms (the old blocker was 8 s). After a kill, it recovered on its own in 37 s.
- **Model selection** ([PREREG](https://github.com/kvnloo/z0intelligence/blob/experiments/session-20261003/docs/experiments/tencentdb-model-select/PREREG.md), amendments A1 and A2; synthetic corpus of 47 conversations):
  - The owner-named arms grok-4.7-low and luna-xhigh are **not callable**. Vercel returns a free-tier 403, and the OpenRouter key has a $0 limit. $0 was spent.
  - The incumbent nemotron-super-free (R1, 3 repeats) scored F1 0.860 ±0.038 with 4.2% hallucination but **FAILS the gates**: 2 secret leaks out of 21.
  - The local candidates are running R1 now: qwen3-8b-q6k, qwen3-14b-q4km and qwen3-8b-q4km, 3 repeats each. All 7 local models passed the route probe; qwen2.5-coder-7b emitted no tool call.
- **Code:** local repo only (`d49fad1`, no remote). Its committed snapshot is under [`tencentdb-model-select/`](https://github.com/kvnloo/z0intelligence/tree/experiments/session-20261003/docs/experiments/tencentdb-model-select). `corpus.json` was left out on purpose (synthetic credential traps); it rebuilds from `bench/corpus/*.py`.
- **Owner decisions:**
  - Top up Vercel, or raise the OpenRouter limit to $5 or more, so the paid arms can run. Otherwise the report will say they are untested.
  - Whether a dedicated LLM key is worth it for the gateway.
- **Deferred push:** `results/` and `runs/` (165 MB, raw), after R1 ends.

### 13. Bend perf triage
- **Question (owner):** "Bend should be much faster than Python for how we use it; is it misconfigured?"
- **Interim answer:**
  - All Bend configs pass byte-identical parity on 10,019 kernel-routed cases.
  - Single decision p50: CPython 40.9 µs; Bend 211 to 233 µs (**5.2 to 5.7x slower**). Most of it is kernel round-trip, about 160 to 180 µs.
  - Batch: CPython 21k decisions/s; Bend 3.8k to 6.6k decisions/s.
  - Threads and native builds do not change this.
  - The batch run 1 was noisy and a rerun is in progress, so this is not final.
- **Code:** local repo (`5844252` pushed as a snapshot, `4450e01` local) under [`bend-perf/`](https://github.com/kvnloo/z0intelligence/tree/experiments/session-20261003/docs/experiments/bend-perf). Gate: z0int `exp/bend-aodl-gate` `a0e9578`.
- **Deferred push:** `results/TABLE.md`, `single.json` and the batch rerun.
- **Meta implication:** this result and the law-first RFC (14) point the same way. Bend is not on the hot path.

### 14. Bend/z0 seam RFC
- **Verdict:** 3 designs and 3 judges. All three ranked **law-first** first. Bend becomes z0's law layer: the owner writes laws about a small pure monitor `admit_F(...) -> Verdict`, and bend-native checks the proofs. It is not a runtime component. Draft only, not implemented.
- **Data:** [`bend-seam/`](https://github.com/kvnloo/z0intelligence/tree/experiments/session-20261003/docs/experiments/bend-seam). **Decision:** accept, amend or reject the RFC.

### 15. z0 x Claude Code (side work)
- The z0intelligence and z0-obspack plugins are installed on this host (packet on, obspack packing Bash outputs over 6000 chars). The measurement is on [`study/claude-code-savings-v1`](https://github.com/kvnloo/z0intelligence/tree/study/claude-code-savings-v1) `0948bc1`: -34% tokens and -45% cost; the lean launch profile gives -32%.

## 3. Cross-cutting, for the meta level
- **The owner's queue is the critical path.** Most programs end in owner decisions, not in more agent work: CUA (privacy, budget, product policy), Hermes (diverged branches, held branches, upstream maintainers), TencentDB (paid route), wiring (merge, activation) and the Bend RFC.
- **Data, not search, is the learning bottleneck.** The shadow loop has 9 rows against a 300-row gate. The wiring program is the only producer. DSH has 13k rows but no outcomes.
- **Resource ceilings:** 17 TypeSafe requests are left. The paid model routes are blocked. One host is shared by many lanes, and lock contention, leaked stub servers and unlocked builds affected timing windows several times.
- **Process debt:** 2 host-desktop breaches (both ruled "continue" with guards), and privacy leaks onto 5 already-public fork branches. The wave-6 synthesis recommends making the PUB-03 per-commit scanner a mandatory pre-push gate.
- **Stale or contradictory artifacts to discount:** `exp/ar-pilot2-20261002` (pre-R10c), and the snapshot folders of the three still-running programs (10, 12, 13), which show their 14:20 state.

## 4. Still running and needing a follow-up push
1. CUA wave 7: push `exp/fresh-07-main-9a2b1d99e-20261003` and `exp/r2-07g-live-fallback-ln-20261003`, and re-snapshot `evidence/cua-lane-20261003-local`.
2. Wiring round 3: its own Publish pushes C2, C8 and C6. Then refresh `z0-wiring/{evidence,publish}` here by fast-forward.
3. TencentDB R1 local candidates: commit the results in the local repo, then copy `results/` here.
4. Bend-perf batch rerun: copy `results/TABLE.md` and `single.json` here.
