# Erratum — Phase 1B execution contract (`p1b-20260921T1430Z`)

Status: **superseding interpretation. The frozen run is not modified and no
historical measurement is withdrawn.** Machine-derived from
`results/phase1b/p1b-20260921T1430Z/analysis/execution-contract-audit.json` and
`.../claim-status.json`.

## What was wrong

Every one of the 1,102 Phase 1B receipts records `max_tokens: 1024`. That was the
**requested campaign setting**. It was not the value that reached the wire.

`LocalSLMBackend.decide` sends `min(request.max_tokens, dialect.max_tokens)`, and
the dialect is chosen by `dialect_for(tool_parser, tool_call_template)` — which
tests `tool_parser` **first** and returns before ever consulting the template. So
`tool_parser="hermes"` resolves to `HERMES_DIALECT` (256) and the per-model
`tool_call_template` is never read.

The part that is worse than a config error: **`NEMOTRON_DIALECT` exists with
`max_tokens=1024`, and its own comment names this exact failure** —

> "Nemotron-Orchestrator emits a `<think>` block before its call; the measured
> failure mode at 256 tokens was an EMPTY content field with the whole budget
> spent on reasoning."

The fix was written and was never reached, because `tool_parser="hermes"` won the
lookup first.

## Recovered contract, per arm

| model | recorded requested | dialect | **effective** | draws at the cap | max observed |
|---|---|---|---|---|---|
| `nemotron_orchestrator_8b` | 1024 | `hermes` | **256** | **64 / 171 (37.4%)** | 256 |
| `hammer2.1_3b` | 1024 | `hermes` | **256** | 0 / 171 | 80 |
| `hammer2.1_7b` | 1024 | `hermes` | **256** | 0 / 87 | 63 |
| `functiongemma_270m` | 1024 | `functiongemma` | **128** | 0 / 87 | 39 |
| `qwen3.5_4b` | 1024 | `qwen` | 1024 | 0 / 171 | 558 |
| `qwen3.5_9b` | 1024 | `qwen` | 1024 | **30 / 171 (17.5%)** | 1024 |

Evidence: dialect lookup in `dialect_for()`, cross-checked against observed
completion-token histograms over the frozen corpus. Confidence **high** on every
row — the source-derived cap and the observed maximum agree in each case.

## Blast radius — narrower than the discrepancy suggests

The recorded contract was false for **four of six** generative arms, but a wrong
contract only invalidates a measurement where the contract **binds**. It bound for
one arm:

* **Nemotron** — 64 draws finished at exactly 256. Its numbers are affected.
* **Qwen3.5-9B** — its cap was recorded *correctly* (requested == effective == 1024)
  but bound on 30 draws. Not a contract defect; the requested cap was simply too
  small. Raising it may move its number.
* **Hammer3B, Hammer7B, FunctionGemma** — recorded a contract they never came
  close to. Hammer3B's maximum completion across 171 draws was **80 tokens**
  against a 256 cap. Their published numbers are true measurements of the models
  as run; only the historical description of their contract was wrong.
* **Qwen3.5-4B** — unaffected in both senses.

## Claim status

| claim | status |
|---|---|
| Hammer3B 75/84 = 0.893 compiler-first | `VALID_AS_EXECUTED` |
| Hammer3B 69/84 unfiltered | `VALID_AS_EXECUTED` |
| Hammer7B 72/84 | `VALID_AS_EXECUTED` |
| FunctionGemma 51/84 | `VALID_AS_EXECUTED` |
| Nemotron 52/84 and 61/84 | `REQUIRES_CORRECTED_RERUN` |
| Qwen3.5-4B 75/84 | `UNCHANGED` |
| Qwen3.5-9B 78/84 | `REQUIRES_CORRECTED_RERUN` |
| Qwen9B-vs-Qwen4B ranking (3-draw margin) | `REQUIRES_CORRECTED_RERUN` |
| compiler-first vs unfiltered accuracy delta | `INVALID_COMPARISON` |
| compiler removes dangerous **exposure** | `VALID_AS_EXECUTED` |
| compiler prevents dangerous **selection** | `INVALID_COMPARISON` |
| NanoJev cascade 0.893 → 0.929 | `VALID_AS_EXECUTED` |
| NanoJev threshold 0.6 / coverage 0.607 / success\|covered 0.941 / p50 34.7 ms | `UNCHANGED` |
| composition qwen4b+jev 13/28, hammer3b+qwen4b 6/28 | `VALID_AS_EXECUTED` |

Two of those deserve spelling out.

**The compiler's accuracy delta is not a comparison at all.** The A/B/C/D design
was rank-deficient: `ToolDecisionRequest` carries no framing field, so "compiler
framing" and "unfiltered framing" render the same template. Hash-verified — A≡C
and B≡D on all 28 states. The 2×2 had one level on that axis. What survives is the
structural half: the compiler defines the legal boundary, and legal-only menus
show zero dangerous *exposure* against 20/140 for all-actions. Dangerous
*selection* was 0 in every cell including all-actions, so "the compiler prevents
dangerous selections" is not supported by the fresh data — the 6 selections in the
frozen corpus do not replicate.

**The NanoJev cascade stands as executed.** Its Hammer3B baseline ran at an
effective 256, not the 384 that was assumed, and 256 never bound (max 80 tokens).
So `0.893 → 0.929` is a real comparison of NanoJev against Hammer3B **as actually
executed**. The correct phrasing is:

> against the measured Hammer3B baseline as executed, NanoJev eliminated 60.7% of
> Hammer3B calls and improved end-to-end accuracy.

Not yet claimed: that NanoJev improves accuracy over Hammer3B at a true 1024-token
budget. That requires the cap rerun, because — counter-intuitively — the token
savings result could get *stronger* while the accuracy delta disappears:

```
Hammer@1024        = 27/28
NanoJev→Hammer@1024 = 27/28
61% of Hammer calls gone, accuracy equal
```

which is still the result worth having.

## What is not being done

The frozen run is not mutated, not re-scored, and no historical number is deleted.
The corpus keeps `max_tokens: 1024` as the record of what was requested; the
audit sits beside it as the record of what was applied.

---

## Addendum — the cap rerun, preliminary

> **Rep 0 only, n=28 per cap — not the final cells.** The schedule is rep-major, so
> at the checkpoint these were read only the first repetition had finished. The
> final cells will be n=84 (28 states × 3 reps) and the accuracies below will move.
> The **mechanism** is not in doubt; the numbers are. An earlier version of this
> addendum described them as complete at n=28 × 3, which was wrong — labelling a
> rep-0 slice with a contract it was not run under is the same defect this erratum
> is about.

The override is verified effective: Nemotron reached **871 completion tokens** at a
wire cap of 1024, and 19 calls exceeded the old 256 ceiling.

### Nemotron — the defect damaged one number, and it was this one

| effective cap | accuracy | truncated | parsed | max completion |
|---|---|---|---|---|
| 256 (as frozen) | **57.1%** (rep 0) | 42.9% | 57.1% | 256 (12/28 at cap) |
| 512 | 82.1% | 7.1% | 92.9% | — |
| 1024 | **85.7%** (rep 0) | 0.0% | 100% | 871 |

`gold_mentioned_in_reasoning = 1.00 at every cap.` The model's preference never
changed — only its ability to emit a parsable call. Paired 256→1024: 12 of 28
states produced no parsed call at 256; **12/12 became real parsed tool calls**;
8 of those 12 flipped wrong→correct. Delta **+28.6 pp (95% CI +14.3 … +46.4)**.

At a truthful contract Nemotron is **85.7% at rep 0 — exactly its reasoning-implied
ceiling** — it is correct whenever gold appears in its reasoning. The frozen
`52/84 = 0.619` measured a decode budget, not a model. It should not be quoted as
a capability result, and 85.7% is statistically level with Hammer3B's 89.3% on 28
states.

### Hammer — exact null, as the audit predicted

| model | 256 | 512 | 1024 | truncated | paired delta |
|---|---|---|---|---|---|
| `hammer2.1_3b` | 89.3% | 89.3% | 89.3% | 0% at every cap | 0.0 pp [0, 0], 28/28 ties |
| `hammer2.1_7b` | 85.7% | 85.7% | — | 0% | 0.0 pp |

Max completion tokens 55 / 80 / 55. It never approached the cap, so its recorded
contract was wrong and its measurement was never affected.

**Pending the full cells, this points to strengthening the NanoJev cascade rather than
weakening it.** The one thing
that could have undermined the comparison — a capped Hammer baseline — was tested
and ruled out. Hammer3B is cap-invariant, so `0.893 → 0.929` is a comparison
against a verified baseline, not merely a valid-as-executed one.

### One parser caveat, recorded rather than credited

The production parser's step-5 substring fallback parses Hammer and FunctionGemma
output that the dialect patterns miss. `parse_source` is recorded per call so a
salvaged parse is visible instead of silently counted as a clean one. This is the
`fallback_parser_used` field, and it is why the completion-vs-cognition
decomposition has four columns and not three.

Still running: FunctionGemma (128/256), Qwen3.5-4B (256/1024), Qwen3.5-9B
(1024/2048).
