
---

## Addendum (2026-10-03): the full self-improving shadow loop

The owner's goal is a system that improves from ordinary use. Every decision opportunity is shadowed by a population of candidate policies. Candidates are scored against verified outcomes and mutated in Evolution Lab. A candidate that keeps winning is promoted, and is deoptimized when its validity stops holding. This RFC is where that loop closes (z0#15: protected evaluation → learned policy → **shadow evidence** → economics → compilation + deoptimization). Each stage below reuses existing code and stays in the repo that owns it in the z0 repo map; nothing here is a new framework.

### Stages and owners

| # | Stage | Owner (repo map) | Existing code / issue | Status |
|---|---|---|---|---|
| 1 | Capture a DecisionOpportunity + deterministic gate per turn, every harness | z0intelligence | #62, `decision_opportunity`, Claude Code / Hermes / OMP adapters | Claude Code: live, inert (non-repo capture fixed in v0); Hermes / OMP: adapters exist (`hermes_decisions`), automatic routing off on zer0 |
| 2 | Observed behaviour + verified outcome + credit join | z0intelligence | #54, `outcome_verifier`, `verification_density`, `loop_export` | v0 landed on `feat/shadow-loop-v0` |
| 3 | Privacy-safe training table (`z0int.loop.training_row.v0`; counts, features and hashed ids only) | z0intelligence → Evolution Lab | `z0int outcomes export` | v0 |
| 4 | **Shadow slot**: registered challengers decide counterfactually beside the champion at each opportunity, with no effect on the turn | z0intelligence runtime (proposed owner for the stage z0#15 leaves unowned) | #26 shadow SafetySentinel, Hermes #319 shadow decisions | new, small |
| 5 | Population search: mutate, recombine and select candidates offline | Evolution Lab | el#24 `verified_loop` (learned ACT gate), el#25 mechanism-neutral candidates, z0int#28 state-construction genome | el#24 v0 prereg'd and runs |
| 6 | Protected scoring; the optimizer never sees or edits its judge | z0evals | z0evals#72, #74 | RFC |
| 7 | Promotion through `[sanity, replay, shadow, promoted]`, carrying the contract fields above | z0intelligence (this RFC); owner approval | Routine/Cascade compilers, ABAB sealed gate | owner-approved only |
| 8 | Drift monitors force fallback and demotion | z0intelligence (this RFC) | Routine drift demotion, z0evals#74 drill | open |
| 9 | Cost and savings accounting: search, training, shadow and maintenance cost against frontier work removed | Tokenomics | usage receipts | open |
| — | Authority: a promoted policy still only *recommends*; AODL / deterministic authority can deny | AODL, #55 | — | unchanged |
| — | Placement of candidate SLMs (k8s lab, farm llama) | Kerdoios | #20 | unchanged |

### The genome (what mutates)

Mutation targets, cheapest and most data-efficient first:

1. **Gate thresholds per decision family**, using the #55 action set `ACT / OBSERVE_MORE / ASK / ABSTAIN / ESCALATE`. No transfer across families is assumed.
2. **State construction** (z0int#28): which evidence families to read and in what budget. This comes before question wording.
3. **Mechanism choice per family** (el#25): rule, cache, retrieval, small classifier, MB/fly, local SLM, frontier.
4. **Prompt / question wording**: last, and only with grouped holdouts.

Search runs in Evolution Lab over offline replay of the training table and frozen work-item groups. It uses niching per decision family, and the simple controls (deterministic gate, unconditional escalate) are always kept in the population. Selection reads only z0evals' score API.

### Safety and privacy invariants

- Shadows are inert. A challenger's decision is recorded and never shown to the model, the user or a tool.
- Promotion past `shadow` needs owner approval per candidate and decision family. Any auto-promotion class (for example, reversible routing within one family) would need its own later pre-registration plus owner sign-off.
- The judge sits outside the evolvable surface (z0evals#72). Contamination rotates the suite.
- Training rows never contain prompt, response, command, path or claim text.
- Deoptimization is mandatory. Invalidation forces fallback; the stale compiled result is never left looking calibrated.

### Sufficiency comes before search

Population search is not useful until the judge has data. Evolution Lab's pre-registered thresholds are `MIN_ROWS 300`, `MIN_NEG 30` and `MIN_GROUPS 10`, with every one of the 5 folds containing both classes. Below them, every comparison is `INSUFFICIENT_DATA`, and a population would only fit noise. Measured so far:

- **el#24 v0 run (2026-09-30, other host):** 6 analysis rows, `INSUFFICIENT_DATA`. Projected from interactive traffic alone: about 24 weeks to 30 not-success rows. Eval/probe sessions carry about 90% of verified turns but had no opportunity records.
- **This host (zer0, 7-day sweep to 2026-10-03):** 149 turns in 8 sessions. 10 resolved (5 success, 5 failure), 5 contested. 0 had opportunity records, because turns started outside a git repo were dropped. That is fixed in `feat/shadow-loop-v0` (6fee859).

So the critical path is **label volume and resolution, not search**. The milestones are ordered to match.

### Milestones

- **M0 (shipped, `feat/shadow-loop-v0`):**
  - capture on every Claude Code turn;
  - `z0int outcomes verify|export|join` on master;
  - `python -m evolution_lab.verified_loop --table …` compares the champion (deterministic gate) with the learned gate;
  - the output is a report for the owner. Nothing is promoted.
- **M1, label volume:**
  - merge the privacy-safe tables across hosts by `turn_key`;
  - add opportunity records for eval/probe and workflow-subagent sessions as their own `cohort`, never pooled with interactive traffic;
  - add resolution oracles where `unverified` dominates.
  - Exit when el#24 sufficiency passes on at least one cohort.
- **M2, shadow slot (stage 4):** load registered challenger artifacts read-only; record their decisions per opportunity. The added latency is measured and off the hot path, as `emit_opportunity_async` already is.
- **M3, population (el#24 → el#25):** mutate along the genome above under a Tokenomics budget; select through the z0evals score API; keep every negative result.
- **M4, promotion (this RFC):** a candidate that wins on protected eval *and* in live shadow is proposed to the owner with the full contract (operating region, calibration, invalidators, fallback, cost vector, lineage). Canary, then promotion.
- **M5, deoptimization drill (z0evals#74):** inject drift and confirm demotion plus fallback happen and are recorded.

### Falsification for the loop

- If label resolution stays too low to reach sufficiency for any cohort within 8 weeks of M1 (proposed bound), stop search work. Put the effort into outcome oracles (#54) instead.
- If el#24 returns `NO_IMPROVEMENT` over the deterministic gate on a sufficient table, keep the deterministic gate. Narrow the learned surface to the families where it does win.
- If promoted candidates rarely stay valid long enough to repay search, training and shadow cost (Tokenomics), the same narrowing applies as in the original falsification above.

### Non-goals (loop)

- automatic promotion without owner approval;
- live mutation of production config or prompts;
- capturing raw user text into any training or evolution artifact;
- the optimizer reading protected labels or rewriting its judge.
