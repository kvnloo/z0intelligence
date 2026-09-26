# Marginal-gain labels require paired execution

Q-Route's proposed learning target is the **expected marginal gain of escalating**:

```
gain(b ← s | x, c) = verified_utility(b, x, c) − verified_utility(s, x, c)
```

escalate when `expected gain > price of upgrading`.

This target is right, and it is not learnable from the data that was proposed to
feed it. That is a data-provenance constraint, not a modelling preference.

## The constraint

A marginal gain is a **difference between two arms on the same state**. It exists
only if both arms were executed against that state and both outcomes were scored
by the same verifier. An observe-only shadow lane executes one arm.

Concretely, on 2026-09-22:

| source | rows | arms per state | utility present | can support marginal gain |
|---|---|---|---|---|
| `results/phase1b/p1b-20260921T1430Z/observations.jsonl` | 1,102 | **13** (27 of 28 states; one has 19) | `utility_components`, `cost_usd`, `decision_ms`, `tokens_in/out` per row | **yes** |
| `~/.z0int/episodes/next_action.jsonl` | 76,970 | **1** | none — schema is `ts, session, user, prev, tool, family, tier` | **no**, not even approximately |
| `~/.z0int/shadow/cognition-shadow.jsonl` | 162 | **1** | none; `executed_action` is `None` on every row | **no**, by construction |

So the two proposals were in direct tension: change the target to marginal gain
(#24) **and** feed it from the observe-only shadow lane (#6). Observe-only lanes
cannot produce counterfactual labels. This is the nanojev candidate-set defect one
layer down — comparing two things that were never run against the same menu.

## The pipeline

```text
real traffic shadow            distribution discovery: where are the states?
        ↓
high-information / replayable state
        ↓
freeze a replay bundle         inputs, candidate set, compiler revision, verifier
        ↓
run candidate A in isolated replay
run candidate B in isolated replay
        ↓
the SAME verifier scores both
        ↓
Δutility = utility(B) − utility(A)      ← the only admissible Q-Route label
```

Shadow traffic tells us **where** the states are. Paired replay tells us
**whether escalation would have helped**. Neither substitutes for the other.

## Rules

1. A marginal-gain label must carry `label_source: paired_replay` and name the two
   arms it was computed from. A label whose provenance is not explicitly paired is
   not a marginal-gain label.
2. Both arms must share a `contract_hash` (see
   `src/z0int/backends/comparability.py`) — same state, same ordered candidate
   set, same question semantics, same metric unit, same split. Otherwise the
   difference is `INVALID_COMPARISON`, not a gain.
3. The verifier must be identical for both arms and must not be either arm.
4. Do not train a marginal-gain model until labels carry paired provenance. A
   model fitted on single-arm data would learn "what the deployed arm did", which
   is the opposite of the intended signal.
5. Phase 1B already satisfies (1)–(3): it is a genuine multi-arm design. It is
   currently the only corpus that does.

## Status

* Documented here; not yet wired.
* The replay harness (freeze → isolated execute → verify) is **not implemented**.
  What exists today is the corpus-side evidence that the 28-state design supports
  the target.
* `z0int.backends.comparability` enforces (2) at the receipt level.
