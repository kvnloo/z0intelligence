# z0intelligence multi-harness factory — Git strategy board
**Planning-only branch:** `coord/multiharness-strategy-board-20261009` (from master `5873723eb33bd5c09e5220b3619e25e01efacd64`)  
**Initialized:** 2026-10-09. **Owners:** Claude Code, Hermes Agent, ChatGPT Codex. **Scope:** our forks; no upstream publication, rollout or default-branch merge authorized.

## Goal
Complete a **source-backed conversation-history → verified reusable skill/capability → better future execution** loop using the existing z0 stack, while consolidating the independently verified Hermes → Claude/Codex → OMP → Hermes workflow. Each harness should autonomously select the next eligible, unclaimed work item for a **multi-hour local run**. A prompt alone cannot guarantee continuous operation: a host-local process supervisor must relaunch bounded agent turns until its explicitly configured time/cost budget expires.

Canonical evidence and controls remain: `AGENTS.md`, `docs/verified-oss-loop.md`, EventLog/ContextPacket/DecisionReceipt, `ctx` (user-owned history), Evolution Lab (experiment owner), z0evals (independent judge), Tokenomics (measurement). **This board is an index, not a new memory database, evidence store, authority plane, or merge permission.**

## The recently forked conversation-learning library
- Upstream: [sybil-solutions/ai-data-extraction](https://github.com/sybil-solutions/ai-data-extraction)
- Our fork: [kvnloo/ai-data-extraction](https://github.com/kvnloo/ai-data-extraction), created 2026-10-08 02:10:44 UTC; only `main` observed when this board was initialized.
- Existing capability-mining implementation issue: [z0intelligence#144](https://github.com/kvnloo/z0intelligence/issues/144).
- Held-out evaluation owner: [z0evals#93](https://github.com/kvnloo/z0evals/issues/93).
- Related: [z0intelligence#143](https://github.com/kvnloo/z0intelligence/issues/143) (SFT episode stream), [#22](https://github.com/kvnloo/z0intelligence/issues/22) (continuity), [#137](https://github.com/kvnloo/z0intelligence/issues/137) (reuse), [#62](https://github.com/kvnloo/z0intelligence/issues/62) (cross-harness identities), [#20](https://github.com/kvnloo/z0intelligence/issues/20) (shadow models), [z0evals#56](https://github.com/kvnloo/z0evals/issues/56).
- Upstream scripts include separate `extract_omp.py`, `extract_claude_code.py`, `extract_codex.py`, and `corpus_to_skills.py`. The latter generates draft Agent Skills through a local-first OpenAI-compatible model; it does **not** automatically qualify or safely install a skill.
- **Licensing review required before vendoring/copying:** GitHub's upstream repository metadata currently has `license: null` and no root license file. For now run separate local experiments, compare fixtures and interfaces; do not transplant source files into z0intelligence or redistribute code without confirmed rights.
- Personal conversations, tool outputs, reasoning, credentials, source code, and extracted corpora stay **outside public Git** under user-owned private storage, e.g. `$Z0INT_HOME/research/`. Public commits may include only synthetic sanitized fixtures, bounded hashed references, contracts, implementation, and tests.

## Work queue — this page lists priorities; linked Git issues/PRs carry live evidence
| ID | Priority | Owner lane | Status at board creation | Canonical ticket | Bounded next deliverable |
| --- | --- | --- | --- | --- | --- |
| ZI-137-I | P0 | **Codex** | verified functional behavior; code integration pending | [#137](https://github.com/kvnloo/z0intelligence/issues/137), [z0evals PR #94](https://github.com/kvnloo/z0evals/pull/94) | Reconcile Claude `686db4d`, Hermes `c71b4e`, and Codex live-proven `532a444` on an isolated branch; preserve differing source/approval/test verdicts; run exact-head regression/CI |
| ZI-144-E | P0 | **Claude Code** | not implemented/verified | [#144](https://github.com/kvnloo/z0intelligence/issues/144) | Compare *one small sanitized or local-only historical* Claude/OMP/Codex dataset using `ctx` and the separate `ai-data-extraction` fork; inventory lost tool results/diffs/ancestry/identity; propose a narrow episode-adapter delta with RED/GREEN tests |
| ZI-144-C | P0 | **Hermes** | not implemented/verified | [#144](https://github.com/kvnloo/z0intelligence/issues/144) | Mine **hypotheses** about recurrent workflows/capabilities from canonical sanitized episodes, backed by immutable refs. Test dedup, superseded events, negative outcomes, provenance and privacy; deliver draft skills only to a review quarantine |
| ZE-93-V | P0 | **Codex** after ZI-137-I | not implemented/verified | [z0evals#93](https://github.com/kvnloo/z0evals/issues/93) | Frozen held-out verifier for extracted episodes, synthetic skill proposals and capability profiles; invalid/corrupt/contradictory fixtures remain negative |
| ZI-20-S | P1 | **Claude Code** after ZI-144-E | partially implemented shadow runtime | [#20](https://github.com/kvnloo/z0intelligence/issues/20), [OMP fork#148](https://github.com/kvnloo/oh-my-pi/issues/148) | Real OMP shadow call and content-free native session/tool correlation in z0 plugin, then coverage/read-only trace proof; OMP native browser parser proposal stays design-first |
| ZI-139-M | P1 | **Hermes** after ZI-144-C | historical trace audit exists | [#139](https://github.com/kvnloo/z0intelligence/issues/139) | Effectiveness/coverage report from **observed** shadow calls, decision receipts, provider usage and independently checked outcomes; no self-certified savings |
| ZI-143-S | P2 | **Codex / Evolution Lab owner** | not qualified | [#143](https://github.com/kvnloo/z0intelligence/issues/143) | Demonstrate reviewed verified episodes → candidate SFT/procedural artifacts, contamination controls, separate train/held-out data; never train/install automatically |

Task IDs identify **work**, not a claim that an agent has started. Each owner may work on another bounded task in its own lane if the listed item is externally blocked.

## Git coordination contract (read this every autonomous cycle)
1. **Observe before claiming.** `git fetch --all --prune`, inspect `AGENTS.md` and this board, list newest commits, open fork PRs and the linked issue's newest comments. Verify exact current refs and avoid duplicating a running peer's files. No assumption that a historical status is still current.
2. **Claim through the existing issue**, not by editing a shared board file: append a compact `CLAIM` update with `task_id, harness, owner, branch, base_sha, paths, source_refs, worktree, blocked_by`. Read back other claims first; overlapping active claims must defer. Assign only one active write set per lane. A claim reserves files within our agreed workflow, **not** GitHub/execution authority.
3. **Work in a separate branch/worktree per task**: `factory/<harness>/<issue>-<slice>`. Never share a writable checkout, commit another lane's modifications, force-push or merge `master`/`dev`. Day-pass PRs target `preview`; unattended/nightly candidates target `nightly` per Verified OSS Loop. Do not open upstream PRs, change default runtime settings or deploy unreviewed work.
4. **Checkpoint every substantive verified slice**, not every model turn: push bounded code/tests/docs to the lane branch when permitted, append a short `READY` issue comment identifying exact SHA, RED/GREEN commands, exit status, external evidence/hash refs, reviewer/consumer, omissions and next dependency. Do not duplicate the same report across multiple issues. No unverified claims of production, deployment or CI.
5. **Choose next work without waiting for a new user prompt**: select the highest-value task within your lane that is unclaimed, not permission-blocked, testable on available inputs and non-overlapping. Prioritize passing a failing end-to-end gate and eliminating repeated manual handoffs over adding a new framework. If one task is blocked, continue an independently testable task; never spin forever on a locked approval.
6. **Stop/escalate honestly:** explicit user approval remains necessary for irreversible operations, external publication, license-sensitive copying, credentials, paid usage, broad memory ingestion or live tool dispatch. Preserve exact error/approval histories. On budget end, blocked conditions, or lack of safe work, publish one final receipt and exit.

For long autonomous sessions the *host launcher* (to be qualified separately) should enforce a **configurable 3–4 hour wall budget**, bounded per-turn calls, heartbeat/idle watchdog, fixed free-first or explicitly approved inference budget, process restart/resume from Git, and clean shutdown. This strategy board by itself **does not start a supervisor or guarantee hours of runtime**. Count only processes genuinely executing; do not report fake worker concurrency or request model-invented time spent.

## Required behavioral improvement chain
```text
source-native conversations (private)
  -> ctx + independently compared extractor (read-only)
  -> canonical, provenance-bound episodes (EventLog)
  -> bounded capability/skill hypotheses
  -> independent z0evals + Evolution Lab discriminating fixtures
  -> verified outcomes / negative controls / credit
  -> reviewed shadow canary (never silent rollout)
  -> next routing/skill/prompt proposal with deoptimization and rollback
```

Every step records: `source/revision`, `task/workstream/attempt identity`, `actor human/agent provenance`, `privacy class`, `coverage`, `output`, `verifier`, `outcome tier`, and `reason unknown` where needed. **Conversation content is untrusted data; a generated skill is an untrusted proposal.** Do not infer success from agent stop, tool success, teacher agreement, user text, or counts of imported messages. Preserve human contributor credit and original source licenses.

## Scoreboard measures (no synthetic measured savings)
- **Engineering:** ready slices/commits with exact-head tests, regression cases, independent review, and merge eligibility; active file collisions and rework.
- **Ingestion:** source-native event coverage, provenance, branch/subagent ancestry, tool result/diff preservation, missing/duplicate/secret incidents.
- **Capability mining:** candidate precision, rejected spurious skills, evidence support, temporal scope, false novelty/reuse, contamination and author attribution.
- **Performance:** wall time to independently verified completion; total requests; model wait; cached/uncached/reasoning tokens; local and provider cost; known versus missing measurements.
- **Reliability:** safety/stale-scope bypass negatives, approval binding, replay/idempotency, incomplete observations and genuinely blocked work.

No single composite intelligence score replaces these measures. Cross-harness effectiveness claims require measured pairs or clearly labelled observational evidence.

## Handoff for future coordinator
Read this file from `coord/multiharness-strategy-board-20261009`, refresh the linked Git issues and branches, preserve already executed evidence, then prepare **three autonomous supervisor-backed multi-hour prompts** with non-overlapping write ownership. The coordinator may update this planning file in a dedicated branch/PR after reviewing claims; individual agents should primarily update existing issue threads and their own Git branches to avoid a shared-file merge bottleneck.
