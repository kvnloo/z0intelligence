# Shared contextual memory: status and ownership

Audit date: **2026-10-04** (America/Chicago). This is a dated repository/evidence audit, not a live host health report.

**Canonical plan, priority order and end-to-end acceptance: [issue #22](https://github.com/kvnloo/z0intelligence/issues/22).** This document is the versioned evidence index for that issue, not another architecture proposal. Older issue descriptions and design prose do not override the reconciled ownership in #22. Preserve historical measurements rather than rewriting them as current results.

## Read the status words literally

- **Merged:** source exists at the audited default-branch revision.
- **Branch-only:** source exists on a named, pinned development line; installation from `master` does not include it.
- **Host-reported:** a dated operator receipt describes a particular configuration and check. It is not a fresh audit of every endpoint.
- **Verified use:** source-backed evidence reached the actual answering model, was used correctly, and passed an independent check. Indexing, retrieval, injection and task success are separate observations.
- **Proposed:** an issue or branch name without a qualified implementation.

These states are not interchangeable. An issue can be closed for a bounded setup check while end-to-end continuity remains unproven.

## Audited revisions and limits

| Scope | Revision / evidence | Interpretation |
| --- | --- | --- |
| Default branch | [`master` at `0159808f64a8e8d7f7ae36a5a9a238b1cd2d03d6`](https://github.com/kvnloo/z0intelligence/tree/0159808f64a8e8d7f7ae36a5a9a238b1cd2d03d6) | Baseline inspected for this audit. |
| Core memory integration | [PR #109](https://github.com/kvnloo/z0intelligence/pull/109), squash `b345aa2bef199779cac8672b08d535cb28526fd7` | Integrated EventLog, scoped memory contract, structural OptMem, State Packet and other foundations. Does not imply live activation. |
| Shared surface and adapters | `integrate/wiring-20261003`, compared 96 commits ahead of the audited default branch | Branch-only memory surface, capture, hook/MCP and harness work. Reconcile the relevant content; do not rebuild it from old pseudocode or merge unrelated work wholesale. |
| `ctx` + DSH pull | [Draft PR #121](https://github.com/kvnloo/z0intelligence/pull/121), head `158931b1a6429bfc70fd1c87d80d6e8ee8ebb966`; base wiring revision `05e50151ec04ea40892be46645fb208aff2a1f70` | Current integration direction under #116; supersedes the isolated #117 path. Not merged into the audited default branch. |
| Host-related fixes | `feat/salvage-stack-metrics` at `d9a3615487ec54a2ca613ac6819809725328fa17` | Published branch, not an unpushed local branch. Contains more than memory work; no blanket merge authorization. |
| Host setup acceptance | [#122 close receipt](https://github.com/kvnloo/z0intelligence/issues/122#issuecomment-5983241590), reporting earlier branch revision `5578792` | Setup/cache/tunnel/import checks, not a scored cross-client memory study. |
| Fresh-turn OptChat | #120; `feat/optchat-harness-v0` compared identical to audited `master` | Proposed experiment. A branch name does not establish a semantic compactor or chat harness. |

The historical [October 2 integration audit](https://github.com/kvnloo/z0intelligence/blob/0159808f64a8e8d7f7ae36a5a9a238b1cd2d03d6/docs/pr-reconciliation-2026-10-02.md) remains evidence for its own revision and tests. Its test totals were not re-run during this documentation pass.

### The important OptMem correction

The merged [`OptMemTree`](https://github.com/kvnloo/z0intelligence/blob/0159808f64a8e8d7f7ae36a5a9a238b1cd2d03d6/src/z0int/memory/optmem_tree.py) explicitly implements a `deterministic_structural_baseline`. Coarse nodes contain event counts, source/type metadata, ranges and checksums, not semantic summaries of the conversations.

`zoom()` retrieves exact admitted event payloads. That does not establish that the model can discover a forgotten fact through a semantic overview. `cover()` uses token estimates and can raise `CoverBudgetExceeded`; arbitrary history is not guaranteed to fit an actual model-token budget. #120 owns the separate semantic-fidelity/fresh-turn experiment.

### The important host correction

The #122 receipt reports a restarted OMP process, completed local `ctx` import, gateway reachability and a seeded-cache outage check. It also explicitly reports **zero default-tenant search hits**, **missing AgentsView** on the checked host, and an **unscored** unified-memory study. Preserve the closed setup task without converting it into universal-memory success.

No current capture/injection/verification claim is established for every desired surface: ChatGPT/here, `mbp`, `0`, `grok-bot`, `muse` and `dots`. Resolve the host/harness mapping and record each capability separately during #22 qualification.

## One source-ownership model

Native provider archives, files and repository revisions remain original evidence for their own records. The existing `EventLog` is canonical for admitted z0 events, notes and source references, not a new mandatory raw archive for every provider.

Temporal trees, claims, search indexes, summaries and State Packets are derived views. They retain source identity/revision and scope; they do not replace original evidence or confer truth/authority merely by being persisted. Missing capture cannot be recovered by compression alone.

Existing retrieval paths remain capabilities: AgentsView/FTS5 and QMD as controls where present, `ctx` read-only under #116, and TencentDB as actually configured. A gateway's name or availability does not establish embeddings, semantic extraction or useful coverage. No additional canonical memory database is selected by this reconciliation.

The [memory contract](https://github.com/kvnloo/z0intelligence/blob/0159808f64a8e8d7f7ae36a5a9a238b1cd2d03d6/docs/memory-control-plane-contract.md) describes `EventIdentity`, `MemoryScope`, `BitemporalClaim`, `MemorySnapshot` and `MemoryUseReceipt`. Its types are merged; complete runtime enforcement still requires the #66 proof. Reuse existing `StatePacket`, context resolution and `DecisionReceipt.extra` instead of inventing equivalent APIs or stores.

```text
evidence != claim/state != authority != action != outcome
```

AODL/policy and the harness retain execution authority. An old approval in memory is not new authorization. Later assertions do not automatically supersede better evidence; explicit corrections must be interpreted in their scope while retaining contradictions and historical provenance.

## Push, pull and cache are different paths

The [#121 surface](https://github.com/kvnloo/z0intelligence/blob/158931b1a6429bfc70fd1c87d80d6e8ee8ebb966/src/z0int/memory/surface.py) keeps temporal/lexical/TencentDB in its existing default layer set and adds `ctx` to explicit pull/MCP. `ctx` is not added to the automatic push budget. This is a statement about the pinned branch, not a global default activation.

The branch uses source/scope/policy-aware snapshots and explicit layer failures. It bypasses brief caching where source generations cannot safely be reused, including the `ctx` path and reachable unversioned TencentDB data. A lower-level requesting-host last-known cache, such as the one exercised by #122, is not equivalent to a current revision-qualified State Packet.

`ctx` read-only evaluation preserves `--refresh off`, `CTX_LOCAL_USAGE_ENABLED=false` and `CTX_ANALYTICS_ENABLED=false`; the historical test found refresh suppression alone did not prevent usage/install writes. No retrieval hook should start an import, indexing job, model download or unapproved remote semantic execution.

## Historical evidence is not a backend verdict

The [#116/#117 26-question local report](https://github.com/kvnloo/z0intelligence/issues/116#issuecomment-5974628650) reported:

| Path | Supported answers | Median cold / warm latency | Median injected tokens |
| --- | --- | --- | --- |
| QMD + AgentsView FTS5 control | 6/26 | 697 / 628 ms | 590 |
| `ctx` lexical | 0/26 | 218 / 180 ms | 395 |
| `ctx` + exact hydration | 3/26; three missed by control | 1518 / 1015 ms | 1895 |
| `ctx` hybrid | Not run | — | — |

#121 contains caller-neutral search/query-shaping changes, but this audit did not re-run their real-history quality evaluation. Do not retire a control, enable semantic indexing or claim a default winner from these historical rows.

## Issue map

| Issue | Role after reconciliation |
| --- | --- |
| [#22](https://github.com/kvnloo/z0intelligence/issues/22) | Canonical continuity plan, current-work State Packet and end-to-end handoff acceptance |
| [#23](https://github.com/kvnloo/z0intelligence/issues/23) | Broader capability fabric; alternative backend experiments are optional |
| [#63](https://github.com/kvnloo/z0intelligence/issues/63) | Existing admitted ledger and temporal projection |
| [#66](https://github.com/kvnloo/z0intelligence/issues/66) | Runtime identity/scope/claim/snapshot enforcement |
| [#116](https://github.com/kvnloo/z0intelligence/issues/116) | Read-only `ctx` pull, provider coverage and matched quality evaluation; implementation #121 |
| [#120](https://github.com/kvnloo/z0intelligence/issues/120) | Optional semantic compression and fresh-turn OptChat experiment |
| [#122](https://github.com/kvnloo/z0intelligence/issues/122) | Closed bounded host-setup receipt; not a second product roadmap |
| #53 / #54 / #62 | Broader decision/state/credit and cross-harness identity; reused, not duplicated |
| #28 / #106 | Optional context-selection experiments; preserve negative results and promotion gates |
| #100 / #112 / #113 / #114 / #118 | Narrow provenance, external capability, instrumentation and consumer integration tracks |

Issue numbers in this table refer to `kvnloo/z0intelligence`. The existing [cross-harness evaluation issue](https://github.com/kvnloo/z0evals/issues/56) remains the shared evaluation owner.

## Next proof, not another architecture

#22 defines the full acceptance checklist. The first product proof is a real decision and correction captured in client A, received at the model-input boundary of a fresh client B on the same workstream, and used correctly without a manual recap. Include stale/missing evidence, duplicate delivery, source outage, unrelated workstream and private-scope negative controls.

Measure re-explanations per handoff, correction propagation, stale/irrelevant injections, actual or explicitly estimated context tokens, source reads, cold/warm latency and cache behavior. Keep each workstream's active intent separate. One new universal master chat, a new backend or a semantic-compaction daemon is not required before this handoff can be tested.

Private raw history stays local. Sanitized receipts can be published; summaries inherit source privacy restrictions. Tree-cache rebuilding is not user-data erasure. Bend remains a separate verifier experiment (#48), not the memory protocol. Existing transport, authority and free-only policies remain unchanged.

## Keeping this from drifting again

A memory-related PR should link #22 and its narrow owning issue, name the exact implementation/source revisions, and state whether it changes merged capability, activation, host coverage or verified-use status. Update this evidence index when those facts change; do not silently convert a branch plan into a default-install instruction. Record new measurements separately from historical cohorts.

This documentation branch targets `preview` under the repository's day-pass convention. The audited default branch and implementation branches above are separate source pins; this doc does not claim `preview` contains their runtime code. No runtime branch was merged and no local/private host tests were run by this audit.
