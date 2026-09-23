# z0 consolidation inventory — `consolidate/z0-kernel-20260922`

Base: `z0intelligence/master` @ `e2e8794` (branch created from the local default
branch, which is **61 commits ahead of `origin/master` @ `5e7cf4a`**).

This is the Step-1 deliverable (inventory overlap before editing) plus the
measured results of the consolidation slice. Every number below was produced by a
command in this session; nothing is inferred.

---

## 0. The finding that invalidates the ownership map

The brief described `contrastive_evidence`, `ResidentDecisionCache`, the four
decision backends and `cascade` as **unmerged**, living on donor branches. They
are not. All of them are **already on `master`**, byte-identical across the refs
that carry them:

| path | master | feat/resolve-context | feat/steal-sweep | network-cutover |
|---|---|---|---|---|
| `context_resolve.py` | `e71a8f` | `e71a8f` | `e71a8f` | `e71a8f` |
| `contrastive_evidence.py` | `2f712f` | — | `2f712f` | `2f712f` |
| `bridge/decision_cache.py` | `2f4a2f` | — | `2f4a2f` | `2f4a2f` |
| `cascade.py` | `b1f9b3` | `b1f9b3` | `b1f9b3` | `b1f9b3` |
| `aodl.py` | `3418df` | `3418df` | `3418df` | `3418df` |
| `receipt.py` | `65ee5a` | `65ee5a` | `65ee5a` | `65ee5a` |

(`git ls-tree <ref> -- <path> | awk '{print substr($3,1,6)}'`)

So "port the backends + residency" was already done. The real divergences are
narrow — `backends/{base,registry,laya,decider}.py` and `bridge/runtime.py`.

**The map was built from branch names, not from content.** `feat/resolve-context`
and `integrate/z0int-future-stack` do not contain `context_resolve.py` at all;
`master` got it separately via `74b5b0f`.

## 1. Overlap matrix

| concept | canonical implementation | strongest invariants | duplicates | verdict |
|---|---|---|---|---|
| context packets | `z0int/context_resolve.py` (596) | `InformationNeed`, `EvidenceRef`, `ContextPacket`, `ResolutionRecipe`, scope fingerprint, source epochs, AODL projection | `tools/statepack.py` (**was** an independent second stack: own classifier, router, FTS fan-out, reducer) | **resolver kept; statepack reduced to a wrapper** |
| retrieval providers | `tools/recall.py` (626) — the proven 6-adapter union | per-source isolation, `mode=ro` + `query_only`, AND-of-quoted-terms, provenance per hit | none other; but it lived outside the repo and was unreachable from the kernel | **ported into `z0int/context_providers.py`** |
| semantic caches | `context_resolve` recipe cache | `policy_revision` in signature, `scope_fingerprint` validation | **`MemoryPacketCache`** (hermes-agent `memory-packet-cache` @ `3e762e9d`, `agent/memory_packet_cache.py` 362 + tests 443, **unmerged**) | **semantics folded in, no new cache** |
| decision backends | `backends/base.py` (268) | `DecisionRequest/Result/DecisionBackend`; master's `BackendCapabilities` is the *newer superset* (`kind`, `description`, `batch_questions`) | Evolution Lab `openjev-track-b` re-implements it | **master kept; EvoLab refused** |
| cascade / routing | `cascade.py` (469, compiler) + `cognition/cascade.py` (645, runtime) | threshold search on `dev`, credit on `sealed`, non-inferiority | `cognition/escalation.py` (294) is a policy table, not a router | **not duplicates — compiler vs runtime; no new router** |
| evidence verification | `contrastive_evidence.py` (523) | leave-one-out necessity, abstain on removal | `tools/state_gate.py` (surface confidence) | **`contrastive_evidence` canonical; `state_gate` stays a negative control** |
| model residency | `bridge/decision_cache.py` (318) `ResidentDecisionCache` | load once, prewarm, observational status never triggers load | `cognition/server.py` (387, GGUF supervisor) + `cognition/serving.py` (190, process lifecycle) | **three layers, three jobs — not merged** |
| receipts | `receipt.py` (730) `z0int.decision_receipt.v1` | harness/EvoLab contract | `cognition/receipts.py` (359) `CognitionReceipt` | **not duplicates — contract vs training receipt** |
| AODL bindings | `aodl.py` (937) | intent/constraints/authority/legal actions | AODL repo `docs/searchable-policy-kernel-20260920` = **docs only, +310/−4, zero code** | **take nothing** |
| experiment/promotion | `autoresearch/` (2556) + `abab.py` (522) + `backends/bench/pareto.py` | promotion integrity, Pareto, bootstrap | Evolution Lab `feat/openjev-track-b` (+3319/−12) duplicates it with a 29-line promote | **refuse merge, take nothing** |

### Two decision interfaces — the real duplicate

`backends/base.py` owns `DecisionBackend` (typed boolean/choice/score), while
`cognition/adapters/local_slm.py` owns `ToolDecisionBackend` (tool selection over
a `LegalActionSet`). The brief's rule is that *all* model-backed bounded choices
go through `DecisionBackend`. **Unresolved** — flagged, not fixed, because
`cognition/*` is the tiered tool-selection plane and collapsing it needs its own
adversarial tests.

## 2. Patch series (this branch)

1. **Backends made visible and non-network** (`models_mgmt.py` +28,
   `backends/{laya,decider}.py` ±11 each, `backends/registry.py` +41).
   *Bug on master:* `laya.py`/`decider.py` resolved their model dir with
   `snapshot_download(...)` **without `local_files_only=True`** on the cache-miss
   path, and `health(load=False)` calls it — so `registry.backend_status()`, the
   fleet-inventory command, could start a multi-gigabyte download. Fixed with
   `require_cached_snapshot()` (opt in via `Z0INT_ALLOW_DOWNLOAD=1`).
   *Second bug:* `laya_421m` and `openjev_06b` shipped as working adapters but
   were **never registered**, so every roster, bench run, Pareto plot and shadow
   lane could not see them. Now registered.
2. **`z0int/context_providers.py` (new, 822)** — the six-provider registry plus
   `coverage.db` as a typed identifier provider, and the two-hop bridge.
3. **`context_resolve` rewired** (+320/−79) — provider registry replaces the qmd
   primary path; typed `InformationNeed` kinds are routed; the dead
   `exact_symbol` branch is fixed; cache write-side correctness folded in.
4. **`z0int/mcp_server.py` (new, 387)** — one harness-facing MCP surface
   (`resolve`, `orient`, `history`, `inspect`, `unknowns`, `verify`).
5. **`tools/statepack.py` 485 → 246** — compatibility wrapper over the kernel.

## 3. Measured results

### 3.1 The question→identifier capability

17-miss set from `coverage.db` `coverage_miss` (11 materialized, 6 not).
Metric: is the required specific present in the returned evidence, at K=8 per
provider, from **the question alone**. Same set, same K, same day.

| retrieval path | materialized 11 | not-materialized 6 |
|---|---|---|
| `tools/recall.py` (previous canonical union, AND semantics) | **1/11** | 0/6 |
| `context_providers`, lexical only | 6/11 | 1/6 |
| `context_providers`, lexical **+ two-hop identifier** | **7/11** | 1/6 |

- The two-hop bridge is **independently necessary**: it is the *only* path to
  miss #16 (`cleanup-cachyos.sh` → `/workspace/.files/scripts/cleanup-cachyos.sh`).
- It also reaches #17 (`setup-key`), the one item previously recorded as
  "genuinely absent from all structured layers".
- Median 1.9 s per query; the hermes provider was 12.4 s until it was changed
  from `order by m.timestamp desc` to `order by rank` (17-query wall: 191.9 s →
  34.7 s).

**Correction to the record.** The previously reported baseline of "9/17 (52.9%)"
is **not reproducible** and was not a question-alone measurement — it was
produced with derived keyword probes. Measured question-alone with the old code:
**1/11**.

**Two measurement bugs found and fixed in my own instrument:**
1. A `recall.py` baseline call silently skipped the `agentsview` adapter (wrong
   arity), which understated it. Corrected, it moves 0/11 → 1/11.
2. The fact hop read 0/11 until token-harvest **ordering** was fixed: short HTTP
   snippets filled the 32-token budget before the richer SQLite snippets that
   carry the identifiers were reached. Ordering is load-bearing.

### 3.2 Existing evaluations

| check | result |
|---|---|
| `pytest -q` (z0int) | **438 passed, 1 failed, 1 skipped** — identical to the pre-change baseline. The single failure is `test_nanojev_parity` → **CUDA out of memory**, environmental, not code. |
| `pytest tests/test_context_providers.py tests/test_context_resolve.py` | **29 passed** (new adversarial file) |
| `tools/recall_eval.py` (12 questions) | **22/22 PASS**, every query cross-harness (4–7 harnesses), 39–91 snippets, 0.5–3.3 s |
| `tools/acceptance.py` | **37/38** — see below |
| `python -m z0int.mcp_server selftest` | ok, 6 tools |

The one acceptance failure is
`sessions.db messages_fts covers every messages row: messages=349090 fts=349091`.
That store is **live**: it grew 349,090 → 349,097 during the run, and
`messages == messages_fts` (`delta_fts=0`) on immediate re-measurement. It is a
sync race with the running AgentsView daemon, not a regression — the new
provider code only ever opens these databases `mode=ro` with
`PRAGMA query_only=1`.

### 3.3 Failure injection (required probes)

Ported as tests in `tests/test_context_providers.py`, all passing:

- **source unavailable** — a dead `coverage.db` reports `ok=false` with
  `FileNotFoundError` in its own status entry; the lexical fan-out still returns.
  This was a real gap: the fact store was originally reached *after* a successful
  fan-out and an unguarded failure there discarded evidence already retrieved.
- **one provider dies** — a bogus `HERMES_ROOT` fails only `hermes`.
- **empty is not cached / failed is not cached** — `cache_write` is
  `skipped:empty-evidence` / `skipped:provider-error` and the cache state stays
  `MISS`.
- **stale, never silently deleted** — `invalidate_recipe_cache` leaves the entry
  readable and `recipe_cache_state` returns `STALE`; invalidation is scoped.
- **no silent empty** — a miss produces an explicit gap, never an invented value.

## 4. LOC accounting — the bar is NOT yet met

| surface | lines |
|---|---|
| `src/z0int` modified | **+332 / −79** |
| `src/z0int/context_providers.py` (new) | +822 |
| `src/z0int/mcp_server.py` (new) | +387 |
| `tests/test_context_providers.py` (new) | +274 |
| **z0int net** | **+1,736** |
| `tools/statepack.py` | **485 → 246 (−239)** |

Net is **positive on the z0int side**, which fails the "near-zero or negative"
bar the brief set. The offset exists and is measured (`wc -l`):

| remaining deletion | lines freed |
|---|---|
| `tools/recall.py` adapter bodies, lines 150–463 (duplicated by `context_providers`) | 314 |
| `tools/recall_mcp.py` → shim over `z0int.mcp_server`, then delete | 440 |
| `tools/statepack.py` → delete once no caller remains | 254 |
| `tools/state_gate.py` → **moved** under `tests/` as the negative control (not deleted) | 0 |
| **total actually deletable** | **1,008** |

1,008 against +1,736 leaves the branch **net +728** even after every listed
deletion. That is the honest position: this slice buys a measurable capability
(1/11 → 7/11 question-alone identifier resolution, and a `registry.backend_status()`
that no longer downloads) but it does **not** yet satisfy the near-zero bar. The
bar becomes reachable only when `context_providers.py` is treated as the *home*
of the adapter logic and `recall.py`'s copies are removed rather than wrapped —
i.e. the 822-line module has to be earned by ~700 lines of deletion elsewhere,
not by a wrapper.

These were **not** done in this pass because each rewires a live, currently-green
surface and the brief requires the replacement to be attacked first.
`tools/statepack.py` was the one surface with an already-green replacement and a
preserved CLI contract, so it was the one demoted.

## 5. Not done, deliberately

- **No large-branch merges.** `feat/resolve-context` was rejected on content: its
  `bridge/runtime.py` *reverses* master's decision and re-introduces Evolution Lab
  onto the live preflight path (+27/−90). `feat/steal-sweep`'s registry and
  no-download fixes were taken as a port; the rest of its 32 k lines were not.
- **No AODL port** — that branch is documentation (`+310/−4`, no code).
- **No Evolution Lab port** — it duplicates `autoresearch` + backends + promotion.
- **The GPU backend race was not run.** The 12 GB card is held by a live
  ComfyUI on `127.0.0.1:8188` (PID 1463894, 8,278 MiB, started 19:05). That is a
  user-launched process and was left alone. The brief sequences the race *after*
  the kernel is green, and the registration fix in patch 1 is what makes
  `laya_421m` / `openjev_06b` race-able at all.

## 6. Smallest next actions

1. Delete `tools/recall.py`'s adapter bodies; make it import `context_providers`.
2. Point `tools/recall_mcp.py` at `z0int.mcp_server`, then delete it.
3. Freeze an **out-of-sample** question→identifier set (split by project, session,
   task family, harness; no near-duplicate leakage) before tuning the bridge
   further — 7/11 is in-sample.
4. Resolve the `DecisionBackend` vs `ToolDecisionBackend` split.
5. Free the GPU, then run the backend race on
   `question → provenance-backed exact identifier`, starting at `linear` /
   `laya_421m` and escalating.
