# Experiments: session 2026-09-29 to 2026-10-03

This branch (`experiments/session-20261003`) is an archive. It copies the experiment data from one multi-day agent session so the owner can review it from GitHub. The files are documents and evidence. Nothing here is wired into the package, and nothing here should be merged to master as-is.

Snapshot taken 2026-10-03 at about 14:20 CDT. Three workflows were still running then: wiring round 3, the TencentDB model-select run and the bend-perf triage. Their folders hold the state as of that time only.

| Folder | What it is | State at snapshot | Source on host |
|---|---|---|---|
| `z0-wiring/` | Wiring spec (R1 to R4, SPEC, PLAN), prior findings, round-2 verdicts, per-component TDD evidence (C1 to C8 and integration), PR bodies plus ACTIVATE runbook, the workflow scripts, the AgentsView upgrade ops evidence, and the C3 verify diffs | Round 3 is running. Evidence and publish files written in the last 30 minutes were left out, and so was all of `evidence/C6-learning-tick/` (still being built). Code branches: see the table below. | `/mnt/zer0models/z0-wt/wiring/{spec,evidence,publish,round2,ops/agentsview}` |
| `bend-seam/` | RFC for the Bend to z0 seam, three competing designs (law-first, perf-first, evolution-first), the Bend docs digest, AODL/Bend notes, z0 decisions, and the workflow script | Done | `/mnt/zer0models/z0-wt/wiring/bend-seam/*.md` |
| `bend-perf/` | Fair AODL gate benchmark, CPython vs Bend: PREREG, harness, kernels, parity results, and the workflow script | Running. This is the committed HEAD `5844252` of the local repo (no remote). The timed results (`results/TABLE.md`, `single.json`) were not yet committed, so they are not included. | `/mnt/zer0models/z0-wt/wiring/bend-perf` |
| `tencentdb-model-select/` | Model selection for TencentDB L1/L2/L3: PREREG plus amendments A1 and A2, harness, scorers, research prior and workload. `service-e2e/` holds the gateway service README, unit, config, run scripts and provider e2e results. | Running. This is the committed HEAD `d49fad1` of the local repo (no remote). The `runs/` and `results/` folders were not committed and are not included. | `/mnt/zer0models/z0-wt/wiring/ops/tencentdb/{model-select,…}` |
| `shadow-loop/` | Verified-loop v0 table (counts and hashed ids only), the sweep manifest, the result (`INSUFFICIENT_DATA`: 9 rows, 0 with an opportunity), and the z0intelligence#56 issue body before and after | Done | `/mnt/zer0models/z0-wt/shadow-loop-data` |
| `upstream-pr-bodies/` | PR body draft for hermes-jev-skills (outcome-grounded promotion docs). It is staged only and was not posted. | Draft | `/mnt/zer0models/z0-wt/upstream-pr-bodies` |
| `push-all/` | The workflow that produced this archive. The full inventory is in `INVENTORY.md`. | Done | |

## Code branches on this fork (kvnloo/z0intelligence)

| Branch | State |
|---|---|
| `integrate/wiring-20261003` | Pushed. Round-2 pin `b99bc31` (C1, C3, C4a, C5, C7, C8). |
| `feat/wire-loop-core-20261003`, `feat/wire-omp-capture-20261003`, `feat/wire-hermes-capture-20261003`, `feat/wire-dsh-plugin-20261003`, `feat/wire-memory-core-20261003`, `feat/shadow-loop-v0` | Pushed, same as local. |
| `feat/wire-loop-consumers-20261003-unverified`, `feat/wire-memory-core-20261003-unverified` | Pushed by round 2. |
| `feat/wire-loop-consumers-20261003` (C2), `feat/wire-memory-harnesses-20261003` (C8, 12 commits ahead locally), `feat/wire-learning-tick-20261003` (C6) | **Deferred.** The live round-3 workflow owns them. Its Publish step pushes each one under its plain name if verified, or under its `-unverified` name if not. |

## What changed from the source files

- `~` replaces `/home/kvn` and `<local-host>` replaces the host name everywhere.
- `tencentdb-model-select/bench/corpus/corpus.json` is **excluded**. It is a frozen synthetic corpus whose secret traps are real-looking credential strings (`sk-proj-…`, `ghp_…`, OpenSSH key blocks) produced by a seeded RNG. It can be rebuilt from `bench/corpus/*.py`. The local file's sha256 is `56acda3dd5c258aa0f56ffc0fead2f5484bff6cab76d4b7fb35481cddd269531`.
- `wiring/ops/tencentdb/tencentdb-memory.env` is excluded because of the no-`.env` rule. It holds no secret values, only BWS secret ids and names.
- Left out for size or privacy: `wiring/{homes,scratch,venv-integrate,agentsview-build,backups}` (17 GB of sandbox homes, venvs and build trees), `bend-seam/{src,scratch}` (a Bend checkout and issue dumps), and the session's research dumps under `~/.claude-home/jobs/5bc3424d/tmp`, which contain real conversation text.
- Secret-shaped strings that remain are known false positives: `sk-ant-dummy-*` placeholders in the e2e scripts, an in-test `admission-<sha>` lease token in a pytest trace, and the literal key header string in the corpus generator.
