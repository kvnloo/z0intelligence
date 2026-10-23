# Bend2 / z0 memory authority law (BEND2-Z0INT-MEMORY)

**Ownership:** #22 (memory), #66 (contract), #199 (Bend2), #195 `OPT1-SYNTHESIS`. This child is stacked only on `context/bend2-laws@232d73dd9f1399c2536bdaf13fd2e6429d09b10d`; it does not edit #197 capture, #198 freshness, #194 read-only MCP, or earlier L/W/I/O Bend branches. Original EventLog, OptMemTree, BitemporalClaim, MemorySnapshot, State Packet and AODL/harness policy remain the owners of their existing responsibilities. Victor Taelin and the Bend contributors retain their provenance.

## Implemented first law: memory is evidence, never permission

* `MODEL.bend`: a pure **finite abstraction** of the added `memory_contract.project_memory_evidence` constructor/projection seam. `MemAbsent`, `MemAdmitted`, `MemRetrieved`, `MemReplayed` plus the attempted instruction/approval bit model all variants of adding, retrieving and replaying already-admitted data. Trusted `explicit` and `policy` inputs **originate outside memory**.
* `LAWS.bend`: nine quantified propositions over the *actual* `MODEL.project` function: stage-independent capability, permanent false instruction capability, independent explicit/policy denial, positive trusted permission, and positive evidence retention after admission/retrieval/replay (not an all-refuse implementation).
* `PROOF.bend`: nine `def Laws.<name>` witnesses importing **the exact `LAWS.bend`**. A matching checker pass means the Bend-emitted book satisfied those propositions. It does NOT prove compiler/source equivalence, Python equivalence, native source authenticity, account authority, actual injection, or harness execution.
* `memory_contract.py`: no replacement contract/store/bridge; a pure, narrow `MemoryEvidenceProjection` of already-admitted `BitemporalClaim`s. Rejects unknown request scope, ignores global-origin or sibling records at this strictly scoped seam, deduplicates identical replay by claim ID, refuses mismatched ID/content, preserves exact claim values, IDs, provenance and supersession, rejects instruction capability, and returns a bound computed **only** from two caller-supplied booleans. This returned bound is an observation, NOT a consent token or permission to effect a tool call. The caller must revalidate current grants and AODL.
* `tests/test_memory_authority_law.py`: 28 finite Python configurations, scope negatives, source-backed *synthetic only* D1/D2 history, verified constructor denial, synthetic native EventLog restart and exact OptMemTree.zoom. These are NOT live capture or original-source attestations.
* `scripts/verify_bend2_memory_authority.py`: required real `bend PROOF.bend --verdict`, exhaustive 28-vector Bend/Python printed-output parity, and a deliberate *law-breaking* Bend mutation that ORs an untrusted injected-approval bit into capability; checker MUST reject it. Mutant/proof checks use disposable source copies. A type/syntax crash is not counted as a law failure. CI does not pretend ordinary Python unit tests constitute a proof.

**Proof/checker trust:** `toolchain.json` now pins the independently downloaded official Bend 2.0.34 linux-x64 archive and executable (not the installed fork/wrapper), adjacent Base and BendTT Lean source, and official Lean 4.34.0 archive. `bootstrap.sh` verifies archive hashes before extraction into a new caller-owned directory. The verifier clears BENDTT and uses a fresh isolated HOME, forcing a fresh kernel build rather than accepting an existing host kernel. Host clang must precede Lean's bin directory on PATH. `source_semantics_attested=false` remains mandatory. This qualification has not run proofs, tests or builds; CI/parent execution is still required.

Run from the repo root on Linux x86_64 (on MBP use a Linux amd64 VM/container, not native macOS):

```bash
PYTHONPATH=src python -m unittest -v tests.test_memory_authority_law tests.test_memory_contract tests.test_memory_event_log tests.test_memory_optmem_tree
# Requires curl, GNU coreutils, tar, zstd, host clang, Python 3.11+.
# Optional BEND_ARCHIVE and LEAN_ARCHIVE absolute paths reuse offline archives.
# Destination must not exist; bootstrap never installs into the host HOME.
bash bend/memory_authority/bootstrap.sh /tmp/memory-bend-owned
env -u BENDTT BEND2_BIN=/tmp/memory-bend-owned/bend/bin/bend \
PATH="/usr/bin:/bin:$PATH:/tmp/memory-bend-owned/lean-4.34.0-linux/bin" \
PYTHONPATH=src python scripts/verify_bend2_memory_authority.py
# Runs real proof verdict, 28-case finite parity, and authority-breaking mutant.
```

The workflow installs those pinned releases and invokes the real verifier, never mocked verdicts. Missing/mismatched bytes fail closed; no green claim, branch-protection guarantee, source attestation or runtime activation follows from this source change. Original ChatGPT draft authorship and Bend contributor provenance are retained.

## Design matrix — next two owner-reviewed laws (NOT implemented)

| Law & responsible files | Bend2 proposition / concrete boundary | Proof obligation | CI check (proposed, not present) | Concolic *symbolic-path* plan (not executed) |
| --- | --- | --- | --- | --- |
| **M1 implemented:** evidence never permission. Existing model, exact owner-reviewed LAWS and proof witnesses | All explicit/policy bits and modeled stages leave capability independent of memory; instruction stays false; positive evidence and trusted grant survive. | Nine exact LAWS witnesses; authority-escalation mutant must fail. | Official toolchain installation, Python finite table, real proof, 28-vector Bend parity and genuine failed-proof mutant verdict; all execution pending. | Symbolic admission-path exploration remains proposed, not executed. |
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

**Security limits:** these are not proofs of source→BendTT translation, native source truth, correction semantic quality, user identity, runtime grants, scheduler or AODL. No runtime activation, merge or upstream publication is authorized. Next: parent runs official toolchain qualification, proof, parity, mutant and Python checks on Linux amd64 (MBP VM/container or CI); native capture/fresh scoped use remains with #197/#198/#194 owners. M2, M3 and real two-client acceptance are separate obligations.
