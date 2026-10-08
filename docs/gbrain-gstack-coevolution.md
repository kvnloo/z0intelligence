# GBrain × GStack × z0intelligence co-evolution

This is the working contribution strategy for the integration. The repos keep their
own product boundaries; integration pressure should improve native seams before it
creates cross-repo coupling.

## Two lanes

### Upstream micro lane

Contribute small changes that are useful to GBrain/GStack on their own and also
make a later z0 integration less invasive.

Rules:

- start from an existing upstream problem whenever possible
- one invariant per PR
- reuse upstream abstractions instead of adding z0-specific vocabulary
- preserve contributor/reporter credit
- prefer additive fields, typed filters, receipts, budget controls, and regression tests
- no z0 runtime dependency upstream
- no promotion claim without the upstream repo's own proof

### Downstream integration/eval lane

z0intelligence may move faster and hold the full cross-repo experiment:

- adapters and protocol joins
- AODL correlation
- shadow policies
- counterfactual / receipt joins
- budget + attention experiments
- synthetic contract fixtures first, then real joined outcomes
- candidates may stay downstream indefinitely if evidence is weak

Promotion upstream happens only after a narrow native seam is clear.

## Wave 1

| Native problem | Upstream move | Downstream use |
|---|---|---|
| GBrain bounded recall mixes semantic kinds before the limit/budget | garrytan/gbrain#5571 -> PR #5971: typed `kind` filter before LIMIT/budget | lets z0 request standing preferences/commitments without paying for unrelated hot facts |
| Per-call bounded generation must override expensive global inference settings | garrytan/gbrain#5331 -> PR #5972: pin existing generic providerOptions override for Anthropic rather than add a second API | gives z0 a stable resource-policy seam for strict bounded decisions |
| GStack context accounting measures cost but not benefit | garrytan/gstack#2889 -> PR #3021: optional author-estimated savings + saving/cost ratio | creates the native measurement vocabulary needed for z0 benefit/cost policy experiments |
| GBrain ambient context must not become execution authority | kvnloo/z0intelligence#112 -> PR #111 | GBrain `context_pack`/`delta` -> ContextPacket -> AODL -> shadow receipts |

## Downstream promotion gates

A downstream result is not an upstream proposal until all relevant gates hold:

1. **Native utility:** explain the change without mentioning z0intelligence.
2. **Boundary:** no memory fact, scorer confidence, or model output widens authority.
3. **Counterfactual:** compare against the current native behavior, not a straw baseline.
4. **Outcome:** attach a verifier/world outcome where the question permits one.
5. **Cost:** record token/latency/attention spend separately from declared budgets.
6. **Privacy:** no private widening, raw transcript promotion, or sensor persistence by default.
7. **Attribution:** preserve observation -> diagnosis -> invariant -> proof provenance.
8. **Small patch:** promote the seam, not the downstream orchestration stack.

## Immediate experiments

The first deterministic GBrain shadow suite lives in
`benchmarks/gbrain_shadow/`. It pins the safety frontier before any learned
attention policy:

- complete empty wake -> `IGNORE`
- delivered change -> `OBSERVE`
- open thread -> `PREPARE`
- partial/degraded wake -> `OBSERVE`, never a false negative
- never `ACT` or `SURFACE`
- never grant authority

Next: join candidate IDs to real decision/outcome receipts and estimate precision,
human-attention avoided/spent, latency, and token cost. Only then evaluate a
`SURFACE` policy and Ripple.
