# Lifelong memory control-plane contract

Tracks [#66](https://github.com/kvnloo/z0intelligence/issues/66), extending
[#63](https://github.com/kvnloo/z0intelligence/issues/63).

## Boundary

The memory control plane does **not** introduce a new universal memory database.

```text
immutable evidence
      |
      v
 Event Ledger
      |
      +--> OptMem temporal projection
      +--> lexical/entity indexes
      +--> semantic memory backends
      |
      v
 claim/state compiler
      |
      v
 StatePacket / DecisionOpportunity
      |
      v
 action -> independent outcome -> credit
```

The shared invariant is:

```text
evidence != claim/belief != state != authority != action != outcome
```

AODL/policy remains the authority boundary. Memory can supply evidence; it
cannot mint authority.

## Stable event identity

`EventIdentity` separates durable source identity from local append order.

- `event_uid`: stable across replay/import.
- `ledger_seq`: local ordering only.
- `source_system/source_session/source_event_id/source_seq`: origin identity.
- `payload_hash`: detects conflicting source payloads without comparing raw text.

When a source-native event ID exists it wins over `source_seq`, so an export
that renumbers rows does not silently change identity.

## Scope before similarity

`MemoryScope` is hierarchical:

```text
global -> user -> project -> repo -> task
```

A request may consume eligible ancestor memories, but sibling project/repo/task
memory is rejected **before** semantic ranking.

This is a visibility contract, not an authorization system. Privacy and tool
authority still live in their existing policy boundaries.

## Bitemporal claims

`BitemporalClaim` preserves two distinct timelines:

- observation/recording: `observed_at`, `recorded_at`
- world validity: `valid_from`, `valid_to`

Lifecycle values remain aligned with the State Packet work:

```text
unknown -> provisional -> observed -> verified
                     \-> contradicted
                     \-> stale
```

Newer evidence must not destroy historical claims. Supersession is explicit via
`superseded_by`; contradiction remains inspectable.

`verified` claims require source event IDs. Confidence is metadata only.

## Memory is never instruction authority

Persisted or retrieved memory is data even if it originated from a tool result,
web page, worker transcript, or prior model output.

The contract carries:

- `origin_trust`
- `privacy_class`
- `derived_from_untrusted`
- `instruction_capability`

`instruction_capability=True` is rejected. If something needs instruction or
policy authority it belongs in the existing policy/AODL surface, not memory.

## Decision-time snapshots

`MemorySnapshot` content-addresses the memory/state inputs to a decision:

- scope
- state revision
- source revisions
- claim IDs
- evidence event IDs
- optional policy/ontology revisions

The snapshot ID intentionally excludes `created_at`; identical state produces
an identical ID. Any relevant source revision change produces a different ID.

This gives replay/evals a stable answer to: **what memory state did this
decision actually see?**

## Outcome linkage

`MemoryUseReceipt` does not create another receipt system. It projects into
the existing `DecisionReceipt.extra` field:

```json
{
  "extra": {
    "memory": {
      "schema": "z0int.memory_contract.v1",
      "snapshot_id": "mem_...",
      "capability_ids": ["fts5", "tencentdb"],
      "query_ids": ["q0"],
      "included_claim_ids": ["claim-1"],
      "excluded_claim_ids": [],
      "evidence_event_uids": ["evt_..."],
      "retrieval_latency_ms": 12.5,
      "input_tokens": 311,
      "raw_source_reads": 1,
      "tainted_evidence": false
    }
  }
}
```

That is the seam for #54 / Evolution Lab to measure whether a particular memory
choice helped or hurt the verified outcome.

## Step 1: bytes-to-evidence freshness and scoped read admission (#66)

The existing z0int.context_resolve.v1 resolver and recovered
z0int.architecture_reuse_packet.v0 (#140) are the bounded consumer paths.
There is no second index, memory database, store, registry or bridge.

- A file EvidenceRef.source_version is a sha256 digest of the **same verified
  bytes** used to produce its excerpt. The admitted canonical absolute
  path is opened component-by-component with directory-fd-relative
  O_NOFOLLOW (including the final file), followed by descriptor and anchored
  lstat identity checks before/after reading. Parent-directory or final
  symlink replacement cannot redirect the reader into a sibling/out-of-root
  file. An ordinary in-root symlink is eligible only when initial canonical
  resolution admits its stable target. Unsupported nofollow hosts and
  raced/missing/non-regular files fail closed; there is no mtime/size fallback.
  A later replacement is still detected by independent re-observation.
- FFF content hits are locators, not fresh source excerpts: the indexed
  line must still match the freshly hydrated source line before it is
  eligible. FFF path hits and eligible local QMD hits rehydrate current
  source bytes inside the trusted project root. QMD score, preview date and
  index epoch are not content versions. A stale/non-local snippet is not
  relabeled with a live file hash.
- Every InformationNeed supplies its known MemoryScope; the trusted
  caller supplies request_scope, **before** ranking, cache, search or
  source hydration. Only eligible ancestor scopes pass. Absent, empty
  global-by-default, sibling or otherwise excluded scope yields only a
  generic gap with no source name, evidence, snippet, locator, index
  metadata or source-derived fingerprint. The project root remains a hard
  local-filesystem boundary. A scope label from a model is not a grant:
  source-scope attribution, caller authentication and policy live at the
  existing host/read-facade admission boundary.
- MemorySnapshot.build is reused, not reimplemented. It binds the
  requested scope, workstream/state revision, source versions, policy
  revision, compiler/ontology revision and optional repository HEAD plus
  dirty/untracked content fingerprint. Unversioned workstream/compiler
  or missing snapshot inputs leave an explicit gap, never a claim of
  freshness. Git/watch epochs are supplementary, not proof of excerpt
  identity.
- build_reuse_packet only serializes candidates, test refs and novelty
  evidence already admitted by the scoped ContextPacket. An unknown
  snapshot is sanitized, not simply labeled OBSERVE while leaking metadata.
  observe_reuse_revisions(packet, fresh_context) independently reads
  candidate bytes **only among sources admitted by a fresh scoped
  resolution**, then the existing check_reuse_packet compares the
  observed map. Missing, raced, stale and unobservable dependencies return
  OBSERVE with implementation_allowed=false.
- implementation_allowed is archaeology/readiness **only**.
  authorizes_action=false and verified_success=null remain invariant.
  Neither an old approval nor retrieved evidence grants a write.

### Integration status and remaining ownership

The exact canonical `src/z0int/memory_contract.py` and
`tests/test_memory_contract.py` bytes from
`integrate/wiring-20261003@05e50151ec04ea40892be46645fb208aff2a1f70`
(#68/#109) are included in this Draft PR's preview branch. The resolver imports
the required `MemoryScope`/`MemorySnapshot` directly, using the canonical
`MemorySnapshot.build(scope, state_revision, source_revisions,
policy_revision, ontology_revision)` arguments. Known authorized paths remain
readable; an absent required revision leaves a gap and dependent reuse remains
OBSERVE. Git porcelain `-z` parses rename/copy secondary names as raw NUL
fields and hashes dirty/untracked bytes, with unknown or unreadable worktrees
unverifiable rather than omitted.

The optional resident `src/z0int/file_search.py` (#135) is still absent
on preview; FFF remains unavailable without disabling exact-path reading.
The `append_context_packet_event` helper retains its lazy import of the
existing `src/z0int/memory/event_log.py` (PR #197 owns EventLog, claims
and native capture). No second ledger or unrelated integration is added.

The actual #47 OMP tool-policy/write consumer must still obtain fresh
scope/policy/workstream/compiler inputs independently at the mutation
boundary and qualify all governed edit/shell routes. A model-supplied
version map, an index cache or the packet API alone cannot prove enforcement.
#136 retains per-need semantic sufficiency; #194 retains read-only native
memory facade/hydration; #22/#62 and z0evals#56 own cross-client
decision/correction, injection and verified outcome. No default activation,
host change, claimed test run or global cache invalidation occurs here.

The #66 Z03 race regression drives real resolve_context exact-path, FFF
and QMD hydration while replacing the leaf or parent after canonical admission.
It asserts no sibling payload/locator/version, explicit gaps and OBSERVE.
The earlier read-mutation regression now instruments descriptor-backed reads
rather than the removed Path.open path; it must still exercise a real mutation.

### Operator verification (exact PR head; no tests run by this worker)

From the repo root, using the exact PR head:

```bash
PYTHONPATH=src python -m pytest -q tests/test_memory_contract.py
PYTHONPATH=src python -m pytest -q tests/test_context_resolve_freshness.py
PYTHONPATH=src python -m pytest -q tests/test_reuse_packet_freshness.py
PYTHONPATH=src python -m pytest -q tests/test_memory_contract.py tests/test_context_resolve_freshness.py tests/test_reuse_packet_freshness.py
```

## Follow-on work

Beyond the bounded #66 consumer repair, the coordinated owners should:

1. make EventLog/AgentsView importers emit `EventIdentity`;
2. make the #22 reducer emit scoped `BitemporalClaim` records;
3. bind StatePacket/DecisionOpportunity to `MemorySnapshot`;
4. record memory-use data in real OMP/Hermes/DSH receipts;
5. add z0evals cohorts for stale memory, precision/noise, cross-scope leakage,
   contradiction, correction/forgetting, and taint propagation;
6. feed repeated **verified** episodes into the existing routine compiler rather
   than creating a second procedural-memory system.

No production memory migration or live authorization change is part of this
slice. Freshness is a decision-time observation, not a perpetual filesystem lease.
