# Bend2 / z0 memory authority law (BEND2-Z0INT-MEMORY)

**Ownership:** #22 (memory), #66 (contract), #199 (Bend2), #195 `OPT1-SYNTHESIS`. This child is stacked only on `context/bend2-laws@232d73dd9f1399c2536bdaf13fd2e6429d09b10d`; it does not edit #197 capture, #198 freshness, #194 read-only MCP, or earlier L/W/I/O Bend branches. Original EventLog, OptMemTree, BitemporalClaim, MemorySnapshot, State Packet and AODL/harness policy remain the owners of their existing responsibilities. Victor Taelin and the Bend contributors retain their provenance.

## Implemented first law: memory is evidence, never permission

* `MODEL.bend`: a pure **finite abstraction** of the added `memory_contract.project_memory_evidence` constructor/projection seam. `MemAbsent`, `MemAdmitted`, `MemRetrieved`, `MemReplayed` plus the attempted instruction/approval bit model all variants of adding, retrieving and replaying already-admitted data. Trusted `explicit` and `policy` inputs **originate outside memory**.
* `LAWS.bend`: nine quantified propositions over the *actual* `MODEL.project` function: stage-independent capability, permanent false instruction capability, independent explicit/policy denial, positive trusted permission, and positive evidence retention after admission/retrieval/replay (not an all-refuse implementation).
* `PROOF.bend`: nine `def Laws.<name>` witnesses importing **the exact `LAWS.bend`**. A matching checker pass means the Bend-emitted book satisfied those propositions. It does NOT prove compiler/source equivalence, Python equivalence, native source authenticity, account authority, actual injection, or harness execution.
* `memory_contract.py`: no replacement contract/store/bridge; a pure, narrow `MemoryEvidenceProjection` of already-admitted `BitemporalClaim`s. Rejects unknown request scope, ignores global-origin or sibling records at this strictly scoped seam, deduplicates identical replay by claim ID, refuses mismatched ID/content, preserves exact claim values, IDs, provenance and supersession, rejects instruction capability, and returns a bound computed **only** from two caller-supplied booleans. This returned bound is an observation, NOT a consent token or permission to effect a tool call. The caller must revalidate current grants and AODL.
* `tests/test_memory_authority_law.py`: 28 finite Python configurations, scope negatives, source-backed *synthetic only* D1/D2 history, verified constructor denial, synthetic native EventLog restart and exact OptMemTree.zoom. These are NOT live capture or original-source attestations.
* `scripts/verify_bend2_memory_authority.py`: required real `bend PROOF.bend --verdict`, exhaustive 28-vector Bend/Python printed-output parity, and a deliberate *law-breaking* Bend mutation that ORs an untrusted injected-approval bit into capability; checker MUST reject it. Mutant/proof checks use disposable source copies. A type/syntax crash is not counted as a law failure. CI does not pretend ordinary Python unit tests constitute a proof.

**Proof/checker trust:** `toolchain.json` pins historically observed Bend 2.0.34 wrapper SHA, Base source, BendTT Lean source, previous built kernel and Lean version/commit from the existing #183 recorded receipt and #199 OMP inventory. Critically, **the host-specific actual `bend.bin` SHA is not in those sources**. It remains `null`, and the verifier intentionally fails *before* running/accepting a proof until a human verifies and pins that real payload digest in this manifest. GitHub-hosted CI also has no preinstalled qualified Bend toolchain. Do not invent a release URL, install script, checksum or success. Required CI will be red/blocked until an explicitly provisioned runner can supply the listed verified paths and a reviewed runtime digest. Even after a kernel PASS, `source_semantics_attested=false`. Existing historical receipt is **not a test of this new code**.

Run from the repo root in a qualified environment:

```bash
PYTHONPATH=src python -m unittest -v tests.test_memory_authority_law tests.test_memory_contract tests.test_memory_event_log tests.test_memory_optmem_tree
# After pin review and explicit toolchain provisioning:
BEND2_BIN=/absolute/path/bend BEND2_RUNTIME_BIN=/absolute/path/bend.bin \
BEND2_BASE_SOURCE=/absolute/path/Base.bend \
BEND2_KERNEL_SOURCE=/absolute/path/bendtt.lean \
BEND2_KERNEL_BIN=/absolute/path/pinned-bendtt \
PYTHONPATH=src python scripts/verify_bend2_memory_authority.py
# The script runs: bend PROOF.bend --verdict; Bend finite parity; malformed authority mutation --verdict.
```

The required workflow `.github/workflows/bend2-memory-authority.yml` runs the Python tests and invokes the real verifier (never mocks its verdict). Without the documented toolchain, it fails with `TOOLCHAIN_BLOCKED`; no green claim, no implicit required-branch-protection status, no runtime activation.

## Design matrix — next two owner-reviewed laws (NOT implemented)

| Law & responsible files | Bend2 proposition / concrete boundary | Proof obligation | CI check (proposed, not present) | Concolic *symbolic-path* plan (not executed) |
| --- | --- | --- | --- | --- |
| **M1 implemented:** evidence never permission. `bend/memory_authority/{MODEL,LAWS,PROOF}.bend`, `src/z0int/memory_contract.py`, `tests/test_memory_authority_law.py` | For **all** explicit/policy bits and all modeled admission/retrieval/replay states, `bounded(project(e,p,m)) = bounded(project(e,p,Absent))` and instruction capability is false. Positive case `e=p=true` remains true; admitted evidence stays visible. | Nine proof witnesses of the exact LAWS; mutant `bounded = explicit && policy || memory.claimed_authority` must fail. | Current required Bend workflow, Python finite table, actual Bend finite parity, proof, deliberately broken source checker rejection; blocked on toolchain as above. | Proposed symbolic execution from Python admission stage through scope/constructor and policy booleans, driving branch constraints on malformed flag, empty evidence, duplicate UID and classifier branches; replay concrete counterexamples, no concolic engine or coverage claimed. |
| **M2 planned:** unknown scope never global. Own only future `bend/memory_scope/{MODEL,LAWS,PROOF}.bend`, scoped read projection tests by #66/#194 owner. | Model scopes as `Unknown | ExplicitGlobal | User(u) | Project(u,p) | Repo(u,p,r) | Task(u,p,r,t)`, with explicit binding evidence. Unknown cannot transform into Global, and sibling project/repo/task or private material never appears in returned packet, including cached summary/hydration. | Quantify `scope_visible` over request/source scope constructors, visibility relation and source ACL/trust before rank; show no broadening by summary/zoom/cache transition. Require an explicit authenticated `Global` token input distinct from missing scope. | Future `bend PROOF.bend --verdict` + full Python scope table, stale/unversioned-cache, missing/unknown scope and sibling tests through **actual** read/snapshot seam. | Symbolic path conditions for missing tenant, project fallback, sibling task, stale cache and private scope crossing ranking/hydration. Concrete replay across admitted EventLog and MCP. **Not ordinary fuzzing; not yet implemented.** |
| **M3 planned:** sourced D1→D2 correction. Own only future `bend/memory_correction/{MODEL,LAWS,PROOF}.bend`; #197 claim reducer/capture owner retains `src/z0int/memory/claims.py` and native adapter. | `D2` can supersede `D1` **only** if explicit `correction_of=D1`, same subject+predicate/key, same exact non-global scope, and independently attested native user source identity. Both D1 and D2 must remain in history; source recorded time alone doesn't make D2 trusted. | Prove unique projected winner, history monotonicity, no false supersession for cross-key/cross-scope/assistant/synthetic newer assertion and no replacement of verified primary evidence by synthetic claim; treat contradiction as visible unless correction attested. | Future `bend PROOF.bend --verdict` + consumer parity + native persisted source replay and fresh secondary-client injection/source check in #22/z0evals#56. | Symbolic paths for identical UID/different hash, replay reorder, explicit vs absent correction link, native user vs injected/model row, same-key/same-scope vs sibling; follow path constraints through admission/reducer. **Not implemented.** |

### D1/D2 correction acceptance matrix (plan only)

| D2 observation | Exact key? | Exact scope? | Authenticated user correction with `correction_of`? | Expected state |
| --- | --- | --- | --- | --- |
| Independent native user says D2 explicitly supersedes D1 | Yes | Yes | Yes | D2 current; D1 recoverable as superseded; original source refs retained |
| Newer model/tool prose asserts D2 | Yes | Yes | No | D1 remains primary; untrusted assertion separate/contradicted, no automatic winner |
| Real user D2 for another task/repo | Yes | No | Yes | No change to D1 in this scope; both views isolated |
| Real user D2 about another key | No | Yes | Yes | No supersession of unrelated key |
| Synthetic event impersonates user or replaces original primary evidence | Yes | Yes | No | Reject promotion; original source still authoritative for what it recorded |
| No D2 link or missing verified native source | Yes | Yes | No | D1 remains; mark new assertion provisional/contradiction; do not overwrite |

**Security limits:** these are not proofs of source→BendTT translation, native source truth, correction semantic quality, user identity, runtime grants, scheduler or AODL. This branch does not run tests on behalf of OMP, turn on memory/adapters, alter original evidence, merge or write upstream. Next in order: (1) OMP verify/pin Bend executable+kernel on MBP, (2) run real proof, parity and mutation, (3) #197/#198/#194 owners integrate and test native capture+fresh scoped use, (4) M2 scope law, (5) M3 correction law, (6) #22 real two-client independent outcome acceptance.
