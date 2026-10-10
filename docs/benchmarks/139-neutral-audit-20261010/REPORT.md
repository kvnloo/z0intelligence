# ZI-139-M: bounded neutral audit

Status: observational measurement, not independently verified task success or measured savings. This lane consumes existing derived artifacts only; it does not repeat D2, launch OMP/models, change runtime dispatch, or qualify generated skills.

## Slice 1 — immutable inventory and coverage

The original SHA256SUMS verifies all 16 artifacts. The report hash is `43cb5b6e2f156b3fd08686c58fe39a4a36e07d5f2e534d148ab66beb7dd99c42`; the call-table hash is `33a29a2f2373f3a119e5ba1739a2ac628cf31dda628d5761e43c45b28fabfcbd`. The CSV hash is `5691f62b84385e9645d60c1765acfec060f3e6441580de79426618b69ba26f81`. Neither originals nor their manifest were rewritten.

Current bounded hash verification of the 52 referenced source snapshots: 49 exact full-file matches, three append-only extensions with exact original-length prefix hashes. Current whole-file bytes of those three do NOT match their captured hashes; the preserved historical prefixes do. No newly appended records were ingested. This is integrity of captured bytes, not coverage of current sessions.

| Observation | Count / boundary |
| --- | --- |
| Existing derived observations | 282 |
| OMP / Codex / Hermes | 269 / 9 / 4 |
| Before original native identity reduction | 283 |
| Original copied observation removed | 1 |
| Remaining duplicate call identities | 0 |
| No-completed-usage attempts, separate | 5 |
| Identity overlap between calls and those attempts | 0 |
| CSV rows | 282 (count agreement only) |
| Known request duration rows | 267; 15 missing |
| Native event ID present | 278; four missing |
| Native response ID present | 278; four missing |
| Native request ID present | four; 278 missing |
| Observed OMP session headers version 3 | 13 |
| Existing adapter/source pin comparisons | 16 of 16 matched at capture |

The supported aggregate input schema is exactly `z0.latency_call.v1`. Version-3 OMP headers describe these captured sessions, not universal parser compatibility. The reducer's OMP call identity uses provider-label plus native response ID, falling back to session plus native event ID when unavailable. Its merge reconciles known provider/model/token fields and unions source references; the historical summary records no conflicts. No identity strings are exported here. Unique derived call identities do not independently prove cross-harness requests are disjoint, nor complete root/subagent coverage.

The preserved verification records 279 native event-hash checks, two retained aborted records, 267 timed calls, and prior r15 receipt/Codex-turn token reconciliations. These are attributed historical checks, not rerun native event ingestion. Current reconciliation verifies the derived table, manifest and captured snapshot bytes, not every historical semantic conclusion.

## Mandatory retained qualifications

Claude's [two-upstream correction](https://github.com/kvnloo/z0intelligence/issues/137#issuecomment-6092268035) invalidates historical r13b-versus-r15 and r15-versus-r16/r17 latency conclusions. r12 changed strata within its run. Response-ID shape is a stratification feature, not proof of a named upstream provider. Pair 1 is same-stratum n=1, not causal generalization. Its published 215-request cohort is not automatically this table's 269 OMP observations.

Requested `low` thinking does not prove wire-effective effort. Read/write next actions do not prove needless reasoning or safe offload. Missing matched prompt-prefix, tool-schema and cache warmness block causal cache diagnosis. Dispatch-to-result tool intervals may contain human approval; absent explicit approval events, waiting is unknown, not tool compute or lost time.

Wall windows and cumulative model/tool spans are not interchangeable. Ambient execution completion, canonical consumption, strict launcher acceptance and independent verified_success are separate. Historical invalid/unresolved outcomes remain intact. The existing offline 16.622-second eligible historical-call total remains an addressable upper bound, not savings.

## Verification and ownership

Pristine configured base: `11e1454b4d5af60a4edccfc65bc53715568131e7`. Read-only Python 3.11.17/pytest 8.4.2 libraries; owned checkout `src` first on PYTHONPATH, with `z0int.__file__` confirmed in the owned checkout. No environment install or modification.

Pristine full-suite `python -m pytest -q`: exit 2, collection error at `tests/test_jevlike_smoke.py`, missing `torch`. Torch installation/download is not permitted. Retain the exact baseline and candidate failures in the private operation evidence. This is partial validation and remains DRAFT. Raw published checksums pass; `benchmarks/verify_published.py` verifies all 69 summary claims. Documentation-only RED and mutation are n/a, not fabricated regression evidence.

Scope is restricted to this report directory and, if needed, a small stdlib aggregate consumer/tests in the corresponding benchmark directory. Claude owns shadow/runtime; Codex owns reconciliation and independent evaluation. Preserve Claude `a90e3be` and Codex `199a849` without changes. [Evolution Lab PR35](https://github.com/kvnloo/evolution-lab/pull/35) is producer evidence, not independently qualified skill performance. EL-23-C remains blocked on independent validation. No issue closure, default merge, routing promotion, privacy-clearance or skill-installation claim.

Next selected slice: behavior-tested neutral response-shape distributions with explicit missing-data denominators, safe labels and copied-identity refusal, using only existing derived tables.

## Slice 2 — response-shape distributions

The stdlib consumer `benchmarks/139-neutral-audit-20261010/aggregate.py` accepts only the existing derived `z0.latency_call.v1` schema. It emits fixed metric names, allowlisted harness/run aliases and response-ID **shape**, never response IDs, call IDs, model/source strings, action arguments or body hashes. Unknown labels remain unknown. Malformed/nonfinite/negative/bool measurements, fractional tokens, inconsistent duration/TTFT relations, invalid reasoning subsets and copied identities are refused with fixed messages. Synthetic behavior tests are authored independently, not copied source rows. Initial RED: missing consumer module, collection exit2; GREEN: 36 synthetic cases pass. Mutation=n/a.

Percentiles use nearest rank on known measurements, not interpolated quantiles. All sums are explicitly observed-value sums with known/missing denominators. After-first-token duration is NOT measured reasoning time. Output throughput is output divided by known positive after-first-token duration, not wall throughput. Reasoning is subtracted from total output only with an explicit true subset label and known counts. Missing reasoning is not zero.

This captured table is **not Claude's later 215-request correction cohort**. Its 269 OMP rows comprise 141 32hex and 128 UUID; 126 of the UUID rows are timed, two are not. No r16/r17-labelled rows are present in this historical table. Do not fill absent runs or reconcile differing cohorts by guessing. The correction's invalidation of comparisons is retained independently.

| OMP captured stratum | Rows | Timed | Duration p50 / p95 (s) | TTFT p50 / p95 (s) | After-first-token p50 / p95 (s) | Output p50 / p95 | Reasoning known |
| --- | ---: | ---: | --- | --- | --- | --- | --- |
| 32hex | 141 | 141 | 11.744 / 70.190 | 3.548 / 7.371 | 7.268 / 63.909 | 322 / 2706 | 141 / 141 |
| UUID | 128 | 126 | 7.046 / 26.412 | 2.411 / 3.927 | 3.912 / 23.217 | 841 / 5105 | 0 / 128 |

32hex observed sums: duration 2802.529 s = TTFT 565.387 s + after-first-token 2237.142 s; output 99,881 including 78,366 reported reasoning and 21,515 nonreasoning. UUID timed sums: duration 1212.394 s = TTFT 353.179 s + after-first-token 859.215 s; output counts across 128 rows sum to 179,921, with reasoning unknown on every row. These cross-run sums are NOT a critical path, wall-time total or matched speed comparison. Output/after-first-token throughput p10/p50/p90: 32hex 35.638/45.228/53.265 tok/s (n=141); UUID 175.638/219.878/291.475 tok/s (n=126). Shape does not prove a specific provider.

Per-run strata retained in the private aggregate projection:

| Public run alias / shape | Rows (timed) | Duration sum (s) | TTFT sum (s) | After-first-token sum (s) | Output sum | Reasoning known / sum |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| r11 / 32hex | 24 (24) | 504.297 | 93.393 | 410.903 | 17,804 | 24 / 13,725 |
| r12 / 32hex | 5 (5) | 71.933 | 12.701 | 59.232 | 2,530 | 5 / 2,115 |
| r12 / UUID | 30 (30) | 406.241 | 82.205 | 324.035 | 64,668 | 0 / unknown |
| r13b / UUID | 73 (73) | 577.145 | 204.621 | 372.524 | 80,233 | 0 / unknown |
| r14 / 32hex | 8 (8) | 394.216 | 33.470 | 360.745 | 16,723 | 8 / 15,104 |
| r15 / 32hex | 11 (11) | 191.211 | 54.630 | 136.582 | 6,243 | 11 / 3,829 |

r12 explicitly retains its five 32hex and 30 UUID observations instead of pretending a whole-run upstream. Remaining labels are pooled unknown aliases, NOT one run: 93 32hex and 25 UUID OMP rows plus 13 other-harness observations. These unknown aliases are omissions in public run attribution, not dropped observations. Codex has nine usage observations (2,919 output, 769 reasoning) but no request duration/TTFT here; four Hermes records bind request-body observations without usage or duration. Neither supports a cross-harness latency comparison.

Paired known-field cache ratios, defined only as `sum(cache_read)/(sum(cache_read)+sum(input_uncached))`, are 83.790% for 32hex (n=141) and 76.079% for UUID (n=128). Cache-write tokens are reported zero in both. These are descriptive ratios, not effective-cache/warmness equivalence or cache savings. Stable prompt/tool-schema matching and wire-effective reasoning remain unknown.

Next selected slice: strengthen accounting refusal controls for unknown-run pooling, rates-versus-additive-time, native tool/approval uncertainty and sum-versus-wall boundaries; audit existing native summary and receipt aggregates without rebuilding timelines.

## Slice 3 — native accounting and semantic refusals

Ten new controls first failed (36 existing controls still passed); after minimal implementation, all 46 pass. Rate distributions now explicitly have `additive=false` and no sum: adding request throughputs is not a meaningful aggregate throughput. Unknown run aliases explicitly carry `pooled-unknown` attribution. Internally comparing existing run identity labels without exporting them finds eight unmapped OMP runs, two of which contain multiple response shapes. Thus r12 is not claimed to be the only mixed run in the captured cohort. The pooled-unknown groups are never treated as one experiment.

`account_native_summary` consumes supplied native summary fields, not raw events or new intervals. It refuses invalid numeric/union bounds. Cumulative model time may exceed wall and is flagged, never clipped or silently renamed wall. An uncovered wall interval requires an explicitly supplied combined interval union; a model sum plus tool union does not supply that union. Tool compute, explicit approval wait and verified_success stay unknown. No approval inference argument is accepted. A finite-input rate that overflows is refused, rather than serialized as Infinity.

| Existing native summary | First-request-to-last-response window (s) | Model-duration sum (s) | Dispatch-to-result tool union within window (s) | Historical subtraction remainder (s) |
| --- | ---: | ---: | ---: | ---: |
| r11 | 637.580 | 504.297 | 131.381 | 1.902 |
| r12 | 2411.161 | 478.174 | 1884.122 | 48.865 |
| r13b | 614.320 | 577.145 | 25.498 | 11.677 |
| r14 | 397.218 | 394.216 | 0.898 | 2.104 |
| r15 | 219.948 | 191.211 | 26.000 | 2.737 |

The last column preserves the prior reducer's subtraction result; it is NOT independently demonstrated union idle, tool compute, approval time, savings or lost time. This consumer leaves combined union/uncovered wall unknown because those fields are absent in the derived summary. r15's model-sum/window ratio is descriptively 86.9349%, preserving the earlier rounded 86.93%; it is not an independently verified time-to-success claim. r14 is invalid completion evidence; r15's historical archived validator/strict qualification remains unresolved. Newer canonical functional verification/consumption in #137 does not rewrite these historical verdicts.

The existing tool table has 399 dispatch-to-result records, zero duplicate `(session, native tool-call)` identities, 109 error-labelled results and 34 reuse-gate-denied results. Their elapsed sum is 2380.191 s across runs, not wall or active tool compute. **Zero rows carry an explicit approval event label.** Successful write intervals can include waiting; the historical r11 approval hypothesis remains unproven. Tool errors, gate denials and provider errors are different categories; no provider-error count is manufactured from the tool table.

Across available native response ID sets, observed OMP–Codex, OMP–Hermes and Codex–Hermes overlaps are each zero. Different or missing source identities do not prove absence of cross-harness causal duplication; no overlap exclusion/savings credit follows. All 282 parent-session fields are absent; no complete subagent/parallel-path claim follows. Served-model identity is absent in all 282 rows; effective-thinking, stable-prefix and tool-schema fields are absent in 278, including every OMP/Codex usage row. Four Hermes request-body observations do not close the OMP wire-effective-effort or matched-prompt gap.

Receipt inventory is not request-count evidence: 379 matched decision revisions map to 14 identities; 11 matched outcome revisions map to nine identities; matched Tokenomics identities are zero. One decision identity has 26 revisions (the prior r15 receipt warning), and another has 150. Nine recorded outcome dictionaries say execution_completed=true; only two also carry verified_success/test_pass/verifier_ok=true. The seven others remain missing/unknown, not successes. These are recorded metadata, not fresh independent adjudications or joins to every one of the 282 call rows. Do not add decision/outcome versions together or reinterpret the planner's 37.733 ms as provider latency. Cost/$ and subscriber marginal price remain unmeasured.

Next selected slice: inspect the existing offline helper's interface without executing it, validate immutable stored gate aggregates and retain all negative-control/authority boundaries; rerun only if a genuine create-only output interface exists.
