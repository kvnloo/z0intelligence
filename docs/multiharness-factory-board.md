# Cross-harness factory — Evolution Lab-led Git strategy board
**Planning-only branch:** `coord/multiharness-strategy-board-20261009` (from master `5873723eb33bd5c09e5220b3619e25e01efacd64`)  
**Initialized:** 2026-10-09. **Owners:** Claude Code, Hermes Agent, ChatGPT Codex. **Scope:** our forks; no upstream publication, rollout or default-branch merge authorized.

## Goal

**Correction, 2026-10-09:** We **use** the recently forked `ai-data-extraction` project directly as an external local tool. We do **not** rewrite its parser, copy its files into another repository, create another corpus database, or make z0intelligence the owner of skill generation/training.

**Canonical responsibility split:**
- **`kvnloo/ai-data-extraction`** — executable, standalone source-history extraction (Claude, OMP, Codex etc.) and `corpus_to_skills.py`. Keep the library a separate checkout and invoke its existing CLI.
- **`ctx`** — canonical *search/retrieval and provenance* for existing coding-agent history; independently compare extractor output rather than replace ctx; `ctx sift` remains the existing tool-output optimization capability.
- **Evolution Lab** — *primary owner* of conversation-derived improvement experiments: recurring failure/workflow mining, generated skill hypotheses, environment/curriculum synthesis, optional model training, comparisons to deterministic and prior skill baselines, promotion research. Start from [Evolution Lab #23](https://github.com/kvnloo/evolution-lab/issues/23), particularly its Agent0-style training methodology.
- **z0evals** — *independent evaluation authority*: [#93](https://github.com/kvnloo/z0evals/issues/93) for capability/challenge falsification and [#92](https://github.com/kvnloo/z0evals/issues/92) for SFT/replay evaluation. Hold out examples by originating session/task, dedup branches, and reject leakage.
- **z0intelligence** — *narrow downstream consumer/bridge*: EventLog/DecisionOpportunity/receipts, existing `ctx` retrieval integration, verified tool-selection/routing/cache/skill adoption controls and fallback/deoptimization. [#144](https://github.com/kvnloo/z0intelligence/issues/144) now tracks the downstream interface, NOT ownership of the experiment engine; [#143](https://github.com/kvnloo/z0intelligence/issues/143) is the runtime training interface.
- **OMP, Hermes and Codex** — native execution and receipt surfaces, not independent capability truth. **Tokenomics** measures consumption and cost.

**Mission:** produce one measured, independently checked `existing conversation logs → candidate reusable skill/strategy → held-out improvements → opt-in shadow runtime` path. A generated `SKILL.md` is a candidate to test, not a verified improvement or an automatic installation instruction.

This board is a **Git issue/branch coordination index**, not another scheduler, memory database, corpus authority or inference gateway. An unattended 3–4-hour shift requires a **host-local supervisor** with real time/cost limits, heartbeats and approvals; prompts or this file alone cannot guarantee hours of work.

## The already-forked executable (use it, do not rebuild it)

- Project: [sybil-solutions/ai-data-extraction](https://github.com/sybil-solutions/ai-data-extraction)
- Our runnable fork: [kvnloo/ai-data-extraction](https://github.com/kvnloo/ai-data-extraction), created 2026-10-08 02:10:44 UTC.
- The existing `extract_omp.py`, `extract_claude_code.py`, `extract_codex.py` and `extract_all.sh` create JSONL under the **current working directory's `extracted_data/`**, which is gitignored in the fork. **Run from a private checkout/filesystem**, never a public-worktree branch with raw logs. The all-sources script makes a combined corpus and could be very large; prefer bounded source-specific probes initially.
- OMP parser supports `python3 extract_omp.py --all-branches` (ancestry preservation), optionally `--prompt-history` (read-only SQLite snapshot); inspect specific paths and permissions before broad export.
- **Execute the existing skill generator**, with a locally available OpenAI-compatible model and filtered private JSONL; its source accepts `--base-url`, `--model`, `--skills`, `--max-conversations`, `--corpus-char-budget`, `--seed`, `--output-dir`. A concrete example, run from the existing fork root after verifying a local model is served:

```bash
# Read-only source discovery and a bounded initial extraction:
python3 extract_omp.py --all-branches
# Inspect / minimize ./extracted_data/*.jsonl privately.
# Supply the actual served local model; never point unreviewed private logs at a remote endpoint.
python3 corpus_to_skills.py /path/to/private/filtered_corpus.jsonl \
  --base-url http://127.0.0.1:8000/v1 --model YOUR_ACTUALLY_SERVED_LOCAL_MODEL \
  --skills 2 --max-conversations 30 --corpus-char-budget 20000 --seed 0 \
  --output-dir /path/to/private/review-only-candidate-skills
```

The output is provisional; `corpus_to_skills.py` validates Agent Skills formatting, **not correctness, representativeness, safe tool use or actual performance**. Evolution Lab owns candidate experiments and z0evals owns held-out judgment before adoption. `ctx` retains the queryable source history and source-native identity; never re-import private raw extracts to public Git or turn them into another authoritative database.

- **License boundary:** GitHub currently reports no explicit upstream repository license. **This does not require copying any code to achieve the requested workflow:** use the existing fork in a separate local checkout as-is, not vendoring/relicensing/transplanting its source into z0intelligence, Evolution Lab or z0evals. Confirm terms before redistribution or other integration that would require broader rights.
- Local corpora, sensitive paths, prompts, outputs and generated candidate skills stay private; push only independently sanitized synthetic fixtures, hashes/receipts and original implementation of the *integration boundary*, not the source library's files.

## Work queue — owners are domain repos, harnesses are workers

| ID | Priority | Work owner / execution lane | Current evidence | Canonical task | Next verifiable deliverable |
| --- | --- | --- | --- | --- | --- |
| EL-23-X | **P0** | **Evolution Lab / Claude Code** | External fork exists, no full local run claimed | [Evolution Lab #23](https://github.com/kvnloo/evolution-lab/issues/23); [z0int #144](https://github.com/kvnloo/z0intelligence/issues/144) interface | Run `extract_omp.py` from *its own* checkout against bounded local inputs. Compare per-session/tool/diff/branch coverage to `ctx`; preserve source identities and private outputs. NO extractor rewrite |
| EL-23-S | **P0** | **Evolution Lab / Hermes** | Skill generator exists; performance unproven | [Evolution Lab #23](https://github.com/kvnloo/evolution-lab/issues/23) | Invoke actual `corpus_to_skills.py` with local model and filtered examples; independently audit draft skills against source facts and failure trajectories; publish private candidate receipts, not installed skills |
| ZE-93-V | **P0** | **z0evals / Codex** | Existing evaluation contracts | [z0evals #93](https://github.com/kvnloo/z0evals/issues/93) and [#92](https://github.com/kvnloo/z0evals/issues/92) | Freeze held-out baseline-versus-candidate behavior tests, contamination/scope negatives, task-success/tool-call counts, replay grouping; independently fail ungrounded candidate skills |
| ZI-137-I | **P0 independent** | **z0intelligence / Codex** | First Hermes→Claude/Codex→OMP→Hermes functional D2 proof exists; shared source branches diverged | [z0int #137](https://github.com/kvnloo/z0intelligence/issues/137) and [z0evals PR #94](https://github.com/kvnloo/z0evals/pull/94) | Reconcile Claude `686db4d`, Hermes `c71b4e`, and Codex `532a444` in isolated worktrees; strict launcher verdict remains separate from functional pass |
| EL-23-C | **P1** | **Evolution Lab / Claude + Hermes** | Agent0-style curriculum track defined, unqualified for this corpus | [Evolution Lab #23](https://github.com/kvnloo/evolution-lab/issues/23), [z0evals #93](https://github.com/kvnloo/z0evals/issues/93) | From independently validated skill/capability hypotheses synthesize discriminating task environments, compare no-skill baseline against candidate; frozen ref + exact model configuration |
| ZI-144-R | **P1 after eval** | **z0intelligence / Codex** | Existing routing and `ctx`/receipt capabilities | [z0int #144](https://github.com/kvnloo/z0intelligence/issues/144), [#143](https://github.com/kvnloo/z0intelligence/issues/143), [#20](https://github.com/kvnloo/z0intelligence/issues/20) | Minimal opt-in consumer of verified results: retrieval/skill candidate registry and tool-use optimization shadow ablation. Do not auto-install or silently promote generated skills |
| ZI-20-S | **P1 parallel** | **z0intelligence / Claude** | Shadow extension exists, actual native timeline absent | [z0int #20](https://github.com/kvnloo/z0intelligence/issues/20); [OMP fork #148](https://github.com/kvnloo/oh-my-pi/issues/148) | Genuine local SLM shadow + OMP session/tool correlation and coverage evidence; OMP generic browser trace remains design-first |
| ZI-139-M | **P1 parallel** | **z0intelligence / Hermes** | Historical timing and offline-screen audit exist | [#139](https://github.com/kvnloo/z0intelligence/issues/139) | Neutral wall/token/tool effectiveness report with missing coverage explicit, verification/savings uninflated |

**Task owners represent repository responsibility, not agents already running.** Agents work only their assigned files/worktrees. Share actual **CLAIM/READY** comments and refs through the existing Git issue, not competing board edits. Each lane should select another unclaimed eligible task if blocked while the local supervisor has remaining permitted budget.

## Git coordination contract (read this every autonomous cycle)
1. **Observe before claiming.** `git fetch --all --prune`, inspect `AGENTS.md` and this board, list newest commits, open fork PRs and the linked issue's newest comments. Verify exact current refs and avoid duplicating a running peer's files. No assumption that a historical status is still current.
2. **Claim through the existing issue**, not by editing a shared board file: append a compact `CLAIM` update with `task_id, harness, owner, branch, base_sha, paths, source_refs, worktree, blocked_by`. Read back other claims first; overlapping active claims must defer. Assign only one active write set per lane. A claim reserves files within our agreed workflow, **not** GitHub/execution authority.
3. **Work in a separate branch/worktree per task**: `factory/<harness>/<issue>-<slice>`. Never share a writable checkout, commit another lane's modifications, force-push or merge `master`/`dev`. Day-pass PRs target `preview`; unattended/nightly candidates target `nightly` per Verified OSS Loop. Do not open upstream PRs, change default runtime settings or deploy unreviewed work.
4. **Checkpoint every substantive verified slice**, not every model turn: push bounded code/tests/docs to the lane branch when permitted, append a short `READY` issue comment identifying exact SHA, RED/GREEN commands, exit status, external evidence/hash refs, reviewer/consumer, omissions and next dependency. Do not duplicate the same report across multiple issues. No unverified claims of production, deployment or CI.
5. **Choose next work without waiting for a new user prompt**: select the highest-value task within your lane that is unclaimed, not permission-blocked, testable on available inputs and non-overlapping. Prioritize passing a failing end-to-end gate and eliminating repeated manual handoffs over adding a new framework. If one task is blocked, continue an independently testable task; never spin forever on a locked approval.
6. **Stop/escalate honestly:** explicit user approval remains necessary for irreversible operations, external publication, license-sensitive copying, credentials, paid usage, broad memory ingestion or live tool dispatch. Preserve exact error/approval histories. On budget end, blocked conditions, or lack of safe work, publish one final receipt and exit.

For long autonomous sessions the *host launcher* (to be qualified separately) should enforce a **configurable 3–4 hour wall budget**, bounded per-turn calls, heartbeat/idle watchdog, fixed free-first or explicitly approved inference budget, process restart/resume from Git, and clean shutdown. This strategy board by itself **does not start a supervisor or guarantee hours of runtime**. Count only processes genuinely executing; do not report fake worker concurrency or request model-invented time spent.

## Required behavioral improvement chain — direct existing-tool execution

```text
existing ai-data-extraction scripts (source-native, local; not copied)
    ↘ compare output with ctx provenance/retrieval
        → private, sanitized and scope-pinned conversation/episode samples
            → existing corpus_to_skills.py (local served model)
            → Evolution Lab proposed skill + capability hypothesis
            → z0evals locked, independent baseline vs candidate evaluation
            → Evolution Lab optional curriculum / training / ablations
            → REVIEWED opt-in shadow trial via z0intelligence + OMP/Hermes
            → measured outcome and tool-call / context / cost consequences
            → keep, reject or deoptimize; maintain source credit
```

**First vertical slice:** run a real existing OMP extractor and a real local corpus-to-skills invocation with a **small private filtered corpus**, then use z0evals to falsify at least one generated skill in a locked fixture. A generated skill that merely repeats prior conversation or coincides with parent behavior **does not** qualify as improvement. Compare **same task / baseline no skill / candidate skill**, include false tool calls, tool argument correctness, retries, latency, cache inputs, total tokens, cost, verification, uncertainty.

**Do not** change `ctx` index, z0int runtime defaults or OMP tool dispatch merely because extraction succeeded. Promotion requires independent outcomes and explicit adoption approval; generated instructions are untrusted text. Original source logs and author's credit must remain inspectable through provenance, but raw bodies must stay private.

## Scoreboard measures (no synthetic measured savings)
- **Engineering:** ready slices/commits with exact-head tests, regression cases, independent review, and merge eligibility; active file collisions and rework.
- **Ingestion:** source-native event coverage, provenance, branch/subagent ancestry, tool result/diff preservation, missing/duplicate/secret incidents.
- **Capability mining:** candidate precision, rejected spurious skills, evidence support, temporal scope, false novelty/reuse, contamination and author attribution.
- **Performance:** wall time to independently verified completion; total requests; model wait; cached/uncached/reasoning tokens; local and provider cost; known versus missing measurements.
- **Reliability:** safety/stale-scope bypass negatives, approval binding, replay/idempotency, incomplete observations and genuinely blocked work.

No single composite intelligence score replaces these measures. Cross-harness effectiveness claims require measured pairs or clearly labelled observational evidence.

## Handoff for future coordinator
Apply the Evolution Lab-led ownership above, use the runnable fork's CLI unchanged, then read this file from `coord/multiharness-strategy-board-20261009`, refresh the linked Git issues and branches, preserve already executed evidence, then prepare **three autonomous supervisor-backed multi-hour prompts** with non-overlapping write ownership. The coordinator may update this planning file in a dedicated branch/PR after reviewing claims; individual agents should primarily update existing issue threads and their own Git branches to avoid a shared-file merge bottleneck.
