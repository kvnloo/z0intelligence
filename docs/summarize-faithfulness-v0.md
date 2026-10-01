# Summarize faithfulness v0: decision #20

**Verdict: groot qwen3-8b does not qualify for live `summarize_tool_output` offload.** Both pre-registered
non-inferiority tests against Haiku fail by a wide margin, so no capability entry is added. The class keeps
only its v0 shadow annotation.

Pre-registration: `benchmarks/summarize_faithfulness/PREREG.md`, committed (`2870516`) before any model
output on the set existed. Aggregates, run ids and run-file sha256 values are in
`benchmarks/summarize_faithfulness/results.v0.json`. Items, outputs and judge rows are in `~/.cache/z0-summ-faith`.

## Set

There are 160 new items, all real invocations on z0 repos and CI. None of the 60 v0 items is reused.

| kind | items |
|---|---|
| pytest | 40 (on mutated z0intelligence; 30 failing, 7 passing, 3 collection errors) |
| git | 40 (`show --stat`, `diff --stat` ranges, patches) |
| build | 40 (ruff, mypy, compileall syntax errors, uv build) |
| gh | 40 (`--log-failed` windows, `run view`, `run list`, `pr checks`, `pr view`) |

Across the set there are 403 deterministic critical facts. The largest context is 5,581 characters.

## Results (n = 160)

| arm | hallucinated items (judge) | contradicted items | critical-fact recall | format ok | p50 / p95 ms |
|---|---|---|---|---|---|
| **groot qwen3-8b** | **42/160 (26.3%)** | 23 | **0.816** (335/403) | 158 | 745 / 2233 (wall) |
| groot qwen3-14b (exploratory) | 35/160 (21.9%) | 17 | 0.860 (342/403) | 157 | 875 / 2914 (wall) |
| Haiku | 21/159 (13.2%) | 7 | 0.893 (364/403) | 160 | 6883 / 20375 (api) |
| Sonnet | 18/160 (11.3%) | 5 | 0.975 (392/403) | 136 | 1483 / 2054 (api) |

Decision rule: paired comparison, local8b minus Haiku, with one-sided 95% bounds from a paired bootstrap
(10,000 resamples).

| test | difference | bound | margin | result |
|---|---|---|---|---|
| hallucination | +12.6 pp | upper +18.9 pp | < +2 pp | **fail** |
| critical-fact recall | -7.7 pp | lower -10.5 pp | > -5 pp | **fail** |
| judge recall (sensitivity) | -7.1 pp | lower -10.0 pp | | agrees |
| contradicted claims only (robustness) | +9.4 pp | lower +5.0 pp | | 8b is worse |

The gap is real, not a matter of imprecision: the one-sided bounds exclude non-inferiority, and they also
exclude equality. On faithfulness, qwen3-8b is worse than Haiku.

Where it fails, by kind (local8b vs Haiku, hallucinated items and recall):

| kind | local8b | Haiku |
|---|---|---|
| pytest | 7/40, 0.99 | 3/40, 0.98 |
| build | 5/40, 0.87 | 3/40, 0.92 |
| git | 14/40, 0.67 | 10/40, 0.83 |
| gh | 16/40, 0.73 | 5/39, 0.85 |

On pytest, recall is fine. The pytest hallucinations are swapped expected/actual values and invented
assertion causes. On git, 8b invents line counts, outcomes ("the build succeeded", "the test case
failed") and characterisations ("all changes are additions"). On gh, it miscounts the runs and failures in
`run list` pages, invents failure causes, and drops failed jobs and workflows. The v0 format check (git stat
only) could not see any of this.

qwen3-14b (exploratory, decides nothing) is closer to Haiku but still fails both tests: hallucination is
+8.2 pp (upper bound +15.1 pp) and recall is -3.3 pp (lower bound -6.2 pp).

The deterministic secondary check (numbers, files and test names absent from the output) flags Haiku more
often than 8b (29 vs 15 items). Most of those flags are correct derived counts, which the judge accepts, so the
check does not separate the arms. It is reported, not used.

## What this changes

* No groot entry goes into `manifests/capabilities.v1.json`, and no live routing changes.
* `manifests/task_classes.v0.json` still marks `summarize_tool_output` as `speed_qualified` on the v0 format
  check. As a result, the shadow annotation keeps reporting `would_offload_for_speed: true` for this class.
  That record is now contradicted on faithfulness. Recommended follow-up (an owner decision, not made here):
  demote it to `not_equivalent`, or add a `faithfulness` record citing this result.

## Deviations

1. The first local14b attempt got HTTP 500 "failed to load" (38 calls) while qwen3-8b was resident on the
   shared 12 GB GPU. Those rows were set aside, and the arm was re-run after 8b idled out.
2. local14b was judged in a second, separate blinded pool, because its rows did not exist when the main pool
   ran.
3. One Haiku summary stayed unjudged after 3 attempts (invalid label) and was dropped from the paired
   hallucination comparison (1/160, under the 10% limit).

No usage-limit errors occurred.

## Limits

* There is a single judge (Sonnet), which is also an arm. It was blinded to the arm. The contradicted-only
  robustness result and the deterministic recall both point the same way.
* Mutation-made test failures are real pytest output, but they are not a sample of production failures.
