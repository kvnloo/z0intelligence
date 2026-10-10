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
