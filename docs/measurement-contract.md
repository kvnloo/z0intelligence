# The measurement contract

One rule, learned three times at increasing cost:

> **Before judging a model, prove it received the same problem, under the same
> effective execution contract, scored in the same unit.**

Every correction in this repository's audit trail is an instance of that rule being
violated by our own tooling, not by a model.

| # | what differed silently | what it made us believe |
|---|---|---|
| 1 | **candidate set** — NanoJev scored against the full action space, Hammer3B against the compiler's legal set | "NanoJev is 43.6%, probably useless." With the menu matched: 60.7% of Hammer calls removed at 94.1% success. |
| 2 | **metric unit** — one frozen bundle reported per-episode (1.000) and per-step (0.839) | the promotion gate read the passing unit and promoted on it |
| 3 | **effective execution contract** — every receipt said `max_tokens: 1024`; four of six arms actually ran at 256 or 128 | generative models looking less capable than their reasoning traces indicate |

The shape is always the same: the measurement is real, the comparison is not, and
nothing in the artifact says which contract produced it.

## 1. Requested is not effective

For every inference request, four things are distinct and all four must be
recorded:

```
requested configuration      what the caller asked for
runtime/dialect constraint   the ceiling the runtime imposes
effective wire configuration min(requested, runtime) — what was actually sent
observed termination         how it ended
```

`received == requested` is an assumption. It has been false in this repo, for four
of six arms, for the whole of a frozen campaign.

**Required fields on every generative receipt:**

```yaml
requested_max_tokens:
runtime_max_tokens:
effective_max_tokens:

finish_reason:
truncated:

reasoning_present:
tool_call_emitted:
tool_call_parsed:
fallback_parser_used:

prompt_tokens:
completion_tokens:
```

`effective_max_tokens = min(requested_max_tokens, runtime_max_tokens)`. Record what
reached the wire. `resolve_max_tokens(requested, dialect)` in
`cognition/adapters/dialects.py` returns the triple so the clamp is a named,
testable resolution rather than an inline expression.

## 2. Wrong choice is not the same failure as cut off

An aggregate accuracy number collapses four distinct outcomes into one word:

```
gold identified in reasoning?   → the model's preference
tool call emitted?              → the budget reached the answer
tool call parseable?            → the parser recovered a truncated dump
selected action correct?        → the outcome
```

A model that names the gold action in its reasoning and is then truncated before
it can emit the call is **not** a model that chose wrong. Phase 1B scored both as
"incorrect". In the Nemotron case every completed call chose gold (91/91, 89/89,
89/89, 88/88) and all 203 truncated calls had already named the gold action in the
reasoning they had written — the aggregate hid a decode-budget problem inside a
capability claim.

**Future eval tables report the decomposition, not just top-1.** The columns above
are the minimum.

## 3. Legality and competence are two claims, not one

```yaml
legality_effect:      # structural, deterministic
  exposed_before:
  exposed_after:
  selected_before:
  selected_after:

competence_effect:    # empirical, per model × representation
  success_delta:
  abstain_delta:
  truncation_delta:
```

`legality_effect` needs no model and cannot be improved by a better one: the
illegal actions are absent from the compiled menu by construction. Verified —
legal-only menus show 0/140 dangerous exposure against 20/140 for all-actions.

`competence_effect` is model × representation dependent and has no general sign. In
one campaign the compiler appeared to help Hammer3B by +6 and hurt Nemotron by −9;
under a paired re-measurement the Nemotron gap did not replicate and filtering
slightly *cost* accuracy. The compiler's effect on accuracy is an empirical
question per model, and it must never be quoted as evidence for the legality claim
or vice versa.

The defensible sentence is:

> the compiler defines the legal boundary. Whether presenting only the legal set
> helps a particular model is empirical.

Not: "the compiler makes the models better."

## 4. Comparison contract

Two rows form a capability comparison only if these agree — or if the difference is
**explicitly named as the variable under test**:

```
state_hash          question_hash       candidate_set_hash
metric_name         metric_unit         aggregation_unit
split_id

requested_max_tokens
effective_max_tokens

runtime             model               revision            checkpoint_digest
```

`src/z0int/backends/comparability.py` enforces this. A mismatch returns
`INVALID_COMPARISON`, never a low score. `compare_receipts(a, b,
varying=("effective_max_tokens",))` is how a deliberate sweep declares its
independent variable: the pair becomes comparable *and* the difference is recorded
as the experiment rather than lost.

## 5. Gates must be able to discriminate

A promotion threshold compared against an arm is only meaningful if the slice can
tell the difference. If a trivial predictor clears the gate, the gate is absent,
not passed:

```
gate_status     = UNINFORMATIVE
promote_allowed = false
```

Metric units (`per_step`, `per_episode`) are part of gate identity, not metadata.
A one-class OOD slice cannot demonstrate competence — a constant predictor scores
1.000 on it.

## 6. Silent clamping is the general case

Token caps are one instance. Any parameter a runtime may clamp — context length,
candidate count, stop sequences, temperature, output schema mode — has the same
failure available: recorded as requested, applied as something else. When a clamp
exists, the resolution should be a named function that returns requested, cap and
effective, so the difference is representable and testable rather than implicit.
