# Z21 Opus budget and acceptance evidence

Status: incomplete. Existing evidence does not establish equal-pass Opus savings. The inspected after sample covers 1.6743 post-ship hours and attributes Opus usage to only 1 of 46 finished tasks. [A:136-164]

## Sources and checkout

Citations use repository-relative `path:line`. `S` means `operations/z21-opus-infinite/evidence/current-z21/sources`; `A` means `operations/z21-opus-infinite/evidence/expected-audit.json`.

The original shadow source is available at `/mnt/zer0models/z0-wt/z21-shadows`, revision `3b9de8dca9629d82685bf87d59be1f01f2302bf8`. The earlier source-unavailable claim is stale on this host. The main checkout has unrelated dirty files; this report uses a separate `ayo/Z21` worktree based on the clean shadow HEAD. [operations/z21-opus-infinite/evidence/source-discovery.log:4-21]

The module is a standalone JSONL CLI. It labels `effort` or `route`, calls an explicitly supplied HTTP endpoint, and appends decisions without acting on them. Its source search found no distinct production caller under `src`. Tests import the labeler and exercise the module through a subprocess. This does not establish live integration. [src/z0int/z21_shadow.py:14-28,31-70,85-121; tests/test_z21_shadow.py:10,90-103; operations/z21-opus-infinite/evidence/source-discovery.log:23]

## Historical token budget

These are saved aggregate totals, not a fresh seven-day measurement. The collector selects cumulative rows by `last_seen`, so the totals are not incremental window usage. The declared weights are an API-price proxy, not a measured subscription-meter conversion. [S/budget.py:2-6,15-24,33-42; S/REPORT.md:4-9]

| Component | Tokens | Proxy weight | Weighted share |
| --- | ---: | ---: | ---: |
| Input | 3,376,655 | 1 | 0.1605% |
| Output | 42,281,474 | 5 | 10.0461% |
| Cache read | 11,738,768,138 | 0.1 | 55.7830% |
| Cache write | 572,560,992 | 1.25 | 34.0104% |

Total: 12,356,987,259 raw tokens and 2,104,362,078.8 proxy-weighted tokens, across 65,614 calls and 3,514 session/model/task rows. One row straddles the boundary. Clean contributes 76.3211% in this aggregate. These figures do not prove savings. [A:3-26]

The separate turn sample assigns 63.7% of estimated call weight to execution, 10.1% to final text, 9.4% to reads/searches, 7.0% to edits, and 3.2% to polling. Its context-character shares include terminal output 40.2%, skill output 7.9%, and file reads 7.4%. These denominators differ from the aggregate budget and cannot be substituted for token savings. [S/turns-7d.json:19-45]

## Ranked levers

This preserves the original reachable-share ranking. Shares overlap. They are not measured savings, and Jev call count is not the optimization target. [S/REPORT.md:4,60-85]

| Rank | Lever | Historical reach estimate | Disposition |
| --- | --- | --- | --- |
| 1 | Route non-interactive work | 27% in original report | Reject current template-driven labeler; needs per-caller equal-pass A/B. [S/REPORT.md:66; S/SHADOW-RESULTS.md:17-26] |
| 2 | Trim tool output | About 52% context-based reach | Needs tested envelope handling and recall/pass checks; no adoption claim. [S/REPORT.md:67; S/AFTER-24h.md:61-81] |
| 3 | Compaction | 46% long-context proxy | Reject promotion without recall A/A and equal-pass A/B. [S/REPORT.md:68,78] |
| 4 | Per-turn effort | 10.0461% output share, not savings | Reject current labeler; workload labels fail calibration. [A:12-16; S/SHADOW-RESULTS.md:5-15] |
| 5 | Skill selection | 7.9% context characters; 2.7% estimated call weight | Needs frozen-task receipts. [S/turns-7d.json:19-34] |
| 6 | Supervision instead of Opus polling | 3.2% estimated call weight | Needs completed-task comparison. [S/turns-7d.json:19-27] |
| 7 | Skip no-op cron ticks | 0.95% silent plus 0.77% nothing-merged | Needs objective false-skip tests. [S/cron-ticks-7d.json:12-20; S/REPORT.md:54-58] |

## Shadows and equal-pass comparison

The historical labels came from local Qwen3.5-4b, not Liquid d1 or hosted Jev. Calibration covers 2,978 effort turns and 2,919 route sessions. Effort labels do not order workload; routing follows brief templates. Trim had not started in that summary. [S/SHADOW-RESULTS.md:1-26,41-42]

The recovered code explains the limited context: effort reads at most 2,000 characters of `record['text']` plus counters; route reads at most 4,000 characters. Output rows have ID, task, label, reason, latency, and model, but no UTC timestamp, objective pass, or incremental token fields. A label log alone cannot prove a full-day shadow or equal-pass Opus A/B. [src/z0int/z21_shadow.py:14-28,41-70]

The compact summaries contain 540 effort calls and 2,909 route calls, different populations from calibration. Their presence does not prove matched 24-hour coverage. [A:149-164]

| Required result | Inspected evidence |
| --- | --- |
| Three 24-hour shadows | Explicit coverage bounds and trim shadow missing. [A:155-164] |
| Frozen-task A/A | Missing. [A:155-164] |
| Equal-pass Opus A/B | Missing objective pass labels and complete usage attribution. [A:155-164] |
| Opus tokens per finished task | Existing figure uses one matched Opus task; 45 of 46 remain unattributed. [A:136-147] |

Neither saved before/after file is a disjoint experimental arm. Both cross the ship boundary, and the after-query changes token weights. Missing usage is not a zero-token success. No savings or live-promotion claim follows from these files. [A:123-164; S/z21-after.py:24-27,37-63,95-109]

## Verification and remaining boundary

Run `sh operations/z21-opus-infinite/verify.sh` from this repository root through `fleet-build` per the compute brief. The script checks source hashes, runs the existing synthetic HTTP and CLI tests, verifies all 15 frozen source hashes, and compares the recomputed audit byte-for-byte. It does not query private databases or perform model inference. [operations/z21-opus-infinite/verify.sh:1-13; tests/test_z21_shadow.py:14-112; operations/z21-opus-infinite/audit.sh:8-18,95-98]

This execution reproduced the 15-source aggregate audit byte-for-byte and verified the labeler/test hashes. These tiny file checks ran on groot, not model inference or a test suite. [operations/z21-opus-infinite/evidence/local-checks.log:1-13]

Fresh runtime verification is blocked by `FLEET_EXECUTORS_UNAVAILABLE`. MBP submission exited 1 before job allocation because the Kueue API refused connections. Both phone Python guests reported a missing Linux service; their admission loop was stopped without allocating a job. No service was restarted, no phone UI was used, and the test suite did not run locally. [operations/z21-opus-infinite/evidence/fleet.log:1; operations/z21-opus-infinite/evidence/kueue-check.log:1; operations/z21-opus-infinite/evidence/fleet-phone.log:1-4]

The remaining acceptance blocker is `SANITIZED_ACCEPTANCE_RECEIPTS_MISSING`. Supply privacy-reviewed, text-free receipts with UTC coverage, task/model/arm IDs, objective pass labels, matched frozen candidates, complete incremental token/cache counts, missing-data counts, and consistent declared weights. This request does not require raw chat, credentials, a paid call, or a restart. [A:155-164; operations/z21-opus-infinite/evidence/BRIEF.md:23-37]
