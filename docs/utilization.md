# z0 utilization (north-star)

Status: v0, 2026-09-30. Code: `src/z0int/utilization.py`. CLI: `z0int utilization [--range 7d|24h] [--json]`.

The metric answers "how often is z0 being used?" More precisely, it asks how often z0's cognition
changes what happens in the agent work the user actually runs. Counts of installs, hook firings or
records written don't answer that, because a shadow record changes nothing.

## Headline definition (`z0_utilization_v0`)

```
z0 utilization = U / N

N = frontier-bound work units in the interactive + agent cohorts, across every harness the
    user runs (Claude Code, Hermes, OMP, Codex), whether or not z0 is installed there
U = units in N where z0 did at least one of:
    displaced  a completed offload whose result reached the parent in that unit
               (route_worker result ok=true with non-empty output; PARENT_ONLY does not count)
    shortened  ObservationPack withheld tool output from that unit's context, net of z0obs
               recalls in the same unit (withheld chars - recalled chars > 0)
    improved   an enforced z0 decision on that unit (routing context actually delivered on the
               prompt, shadow off) whose outcome a verifier passed
```

A **work unit** is one prompt (user or harness message) followed by at least one model call. In
Claude Code this is a turn from `claude_code_tokenomics.split_turns`, counting root and subagent
transcripts separately. For other harnesses it is a user message followed by at least one
assistant message.

### Why this definition

- **The denominator is the user's whole agent traffic.** If N were only the turns z0 sees, z0
  could look well used just by being installed in fewer places. Turns z0 cannot see count against
  it, so installing the Hermes/OMP layers is a measurable lever.
- **The numerator requires a counterfactual change, not presence.** Each of the three arms changes
  what the frontier model actually processed or decided:
  - displaced: the work ran elsewhere;
  - shortened: fewer tokens entered the context;
  - improved: the decision differed and a verifier says it was better.

  Shadow DecisionOpportunities, Stop-hook billing and SessionStart firing are coverage, not
  utilization.
- **State Packet / offload-hint injection is influence, not utilization.** It changes model input,
  but its per-turn effect is not verified. The paired z0evals result (lean×packet −61% vs stock)
  is an average over eval tasks and is not re-measured per live turn, so it is reported one level
  down the funnel. It becomes utilization once a per-turn verifier or a live paired arm exists.
- **"Improved" is gated on a verifier, not on model confidence or gate agreement.** Today every
  DecisionOpportunity is shadow (`shadow: true`; no harness acts on the gate), so this arm is 0 by
  construction. The gate-vs-observed agreement is reported beside it but never counted.
- **Eval arms are excluded.** z0evals/probe sessions (`/tmp/cc-*`, `/tmp/claude-N/*`, any
  `/tmp` or `~/.cache` cwd) and programmatic worker receipts that no transcript links to (shadow
  effect-inference probes, dogfood) are reported in the `eval` cohort and never enter N or U.
  Otherwise a busy benchmark night would read as the user's adoption.
- **A displaced unit counts even when the token estimate is negative.** The inline counterfactual
  (worker output minus the delegation args the parent wrote) can be below 0 for short answers over
  long pasted context. The work still left the frontier, so the unit counts as utilized. The token
  figure is reported separately as an estimate and never netted into the rate.

## Funnel reported beside the headline (never substituted for it)

| level | definition | meaning |
|---|---|---|
| coverage | units where z0 was live, divided by N | z0 could have acted |
| influence | units whose model input z0 changed, divided by N | packet/hint carried in context, ObservationPack, worker result, or prompt context |
| utilization | U / N | headline |
| utilization within seen | U / seen | headroom left once z0 already sees the traffic |

**Seen** is an evidence-based lower bound. A unit counts as seen when one of these holds:
- its prompt id has a shadow DecisionOpportunity (subagent turns carry the parent prompt id);
- the unit carries a z0 marker (ObservationPack, route_worker, z0 hook context);
- a timestamped z0 signal appeared earlier in the session.

The Stop hook's first run backfills a whole transcript, including turns from before z0 was
installed. Being billed that way is reported as `unseen_but_billed_after_the_fact`, not as seen.
For harnesses without a z0 layer, seen comes only from that layer's own turn records
(`~/.z0int/state/hermes/outcomes.jsonl`, OMP bridge `turn_open` receipts). Those are 0 until they
are installed.

## Per harness × cohort fields

Each row reports:
- **Volume:** `units` (`units_root` / `units_subagent`), `sessions`, `frontier_api_calls`,
  `frontier_prompt_tokens` / `frontier_output_tokens` (Claude Code, provider-observed).
- **Model input:**
  - `seen_units`, `input_changed_units`, `context_carried_units`;
  - `context_injections` and `context_injected_tokens_est`, the size of what z0 put in (chars/4);
  - `prompt_context_injections`;
  - `obspack_units`, `obspack_compactions`, `obspack_tokens_withheld_est`, `obspack_recall_tokens_est`.
- **Decisions:**
  - `decisions_shadow`: DecisionOpportunity records plus automatic dispatches not executed;
  - `decisions_enforced`: prompt context delivered plus dispatches executed;
  - `decisions_linked_to_outcome`, `decisions_gate_agrees_with_observed`.
- **Offloads:**
  - `offloads_attempted`, `offloads_completed`, `offloads_parent_only`;
  - `offloads_verified`: a receipt outcome carrying `verified` or `verifier_passed` true;
  - `frontier_tokens_displaced_est`: `displacement_estimate`, estimated;
  - `frontier_tokens_displaced_measured`: always `null` until a paired measurement exists.
- **Utilization:** `utilized_units` split into `utilized_displaced`, `utilized_shortened` and
  `utilized_improved`.
- **Rates:** `rates.{coverage, influence, utilization, utilization_within_seen}`.
- **Breakdowns:** `seen_basis`, `decisions_by_gate`, `dispatch_routes`, `worker_receipts`,
  `displacement_counterfactual`, `session_source`.

## Measurement honesty

- Claude Code units, decisions and offload counts are **observed**. They come from transcripts and
  z0 state, reading only counts, lengths, ids and timestamps; no prompt, tool or model text is
  kept.
- Hermes, OMP and Codex units are **estimated** from local stores. The sources are Hermes
  `state.db` (`sessions` plus message `role`/`timestamp`), OMP session row types and timestamps,
  and Codex rollout `task_started` events. Message content is never selected or read.
- `headline.measurement_state` uses tokenomics vocabulary:
  - `complete` when every unit in N is observed;
  - `partial` when N includes estimated units from harnesses z0 does not observe.
- `totals` mirrors `tokenomics.report.v1`:
  - `actual_frontier_tokens`;
  - `measured_tokens_avoided`, which is 0 because no paired live measurement exists;
  - `estimated_tokens_avoided`, with its per-mechanism split;
  - `measurement_state` and `authoritative: false`.

  Measured and estimated tokens are never collapsed into one number.
- `tokenomics_fields` flattens the headline and the per-harness rates into `utilization.*` keys,
  for embedding in a tokenomics report extension block.

## Levers this metric is built to move

1. Coverage: install the Hermes decisions plugin and the OMP bridge (built, not installed) so
   their units can be seen at all.
2. Influence → utilization: put a verifier on enforced decisions, and on packet-carried turns via
   a live paired arm, so injection can graduate from influence to "improved".
3. Displaced: route_worker under OFFLOAD/RESERVE posture. Under BURN, about 0 offloads is the
   correct behaviour (docs/engagement.md M3). Read the displaced arm together with
   `posture`.
