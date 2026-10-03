# z0 wiring (2026-10-03)

**What:** an agent-built plan and TDD build that wires z0intelligence's learning capture and unified memory into the owner's harnesses (Claude Code, OMP/OMO, Hermes `clean` profile, dsh). The work is split into components C1 to C8 plus an integration branch.

**When:** 2026-10-03. The spec was written 01:55 to 02:39 CDT, round 2 ran 07:00 to 07:47 and round 3 started around 12:05. Round 3 was still running when this snapshot was taken (about 14:20 CDT).

**How (workflows and agents):** the scripts in `workflows/` were run by the workflow harness.
- `z0-wiring-spec-*.js`: research agents wrote R1 to R4, then a synthesis step produced `SPEC.md` and `PLAN.json`.
- `z0-wiring-build.js`: round 1. One builder agent per component works under a strict TDD protocol (red on base, green on head, full suite, isolated e2e), then a blind verifier agent re-runs everything and returns PASS or REVISE.
- `z0-wiring-round2.js` and `z0-wiring-round3.js`: run fix-then-reverify loops on the components that got REVISE, then integrate and publish.

| Folder or file | Contents |
|---|---|
| `spec/` | `R1-learning-capture.md`, `R2-unified-memory.md`, `R3-harness-seams.md`, `R4-roadmap-gates.md` (research), plus `SPEC.md` and `PLAN.json` (components, dependencies, isolated e2e per component). The research scratch folder (`spec/scratch/`, 1.9 MB) is not included. |
| `prior-findings.md` | What had already shipped and been verified before this spec, from three read-only audits. |
| `round2/` | The last blind-verifier verdict for C2 and C7 at the end of round 2. Both say **REVISE**. C2's blocker: `exit_code()` takes the first exit-like match instead of the trailing OMP/OMO notice. C7's major issue: the TencentDB revision in `memory_snapshot_id` is a software revision, not a data revision. Round 3's verifier checks that each listed issue is fixed. |
| `c3verify-diff-{py,ts,tests}.patch` | The diffs the C3 verifier reviewed. |
| `workflows/` | The workflow scripts listed above, unchanged. They still contain the original `/mnt/zer0models/...` paths. |
| `evidence/`, `publish/`, `ops-agentsview/` | Per-component TDD evidence, PR bodies, the `ACTIVATE.md` runbook, and the AgentsView upgrade ops. These are snapshots from before round 3 finished. |

**Verdict at snapshot:** round 2 integrated C1, C3, C4a, C5, C7 and C8 into `integrate/wiring-20261003` at `b99bc31`. C2, C6 and the follow-up C8 commits were still in round 3. Activation is deferred to the owner, using `publish/ACTIVATE.md`.

**Code branches (kvnloo/z0intelligence):** `integrate/wiring-20261003`, the `feat/wire-*-20261003` branches and the `-unverified` variants. Their pushed state is in the table in `../README.md`.

`~` stands in for the home directory and `<local-host>` for the host name in the copied files. The content is otherwise byte-identical to the source in `/mnt/zer0models/z0-wt/wiring/`.
