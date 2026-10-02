# ThermoContext: actual THRML qualification, 2026-10-02

## Decision: KILL sampling investment on this engineered cohort

Official THRML works and recovers verifier-valid packs. However, the frozen graph has a six-node connected core plus disconnected noise pairs/singletons. Exact component-knapsack dynamic programming reaches the same or better verified frontier at much lower local execution cost. This decision is specific to this cohort and these bounded formulations. It does not reject thermodynamic computing or establish a result on real evidence pools.

No production integration, paid model call, TSU/Z1 speed claim, hardware-energy claim, or formal statistical noninferiority claim is supported. The original objective, verifier, task generator, and prototype file are unchanged.

## Requested arms and stop decisions

- **Original prototype:** all36 tasks/288 sampler runs reproduce the published quality exactly. N8/N12 are exact; N16 success95/96, exact86/96, mean gap0.015604. Original kernel timings omit selection and are not end-to-end baselines.
- **Actual THRML fixed:** success288/288; exact at N8/N12, exact80/96 at N16, mean N16 gap0.012478. Energy/sign parity is exhaustively checked. Backend random-number trajectories differ; this is not bitwise sample parity.
- **Adaptive token price with nonempty safeguard:** both backends exact and verified288/288 across N8/12/16. N32 selected success falls to47/80 JAX and48/80 THRML. THRML task9 fails8/8 and triggers the declared stop. No selected empty or overbudget pack occurs. N32 is partial tasks0–9; N64/128 are not run.
- **Explicit token-prefix/slack:** N8 success96/96 each. THRML exact96/96; JAX95/96 with mean gap0.000226, above1e-5. The paired formulation stops on its conservative energy gate, not verifier failure. THRML itself did not fail N8 quality. Degree6 includes auxiliaries; domains147–168; up to1,920,080 input table bytes; median THRML decision539ms. N12 onward are not run after this paired stop.
- **Constrained cardinality buckets:** N8 success96/96 each. Exact90/96 JAX and89/96 THRML; gaps0.009875 and0.005535 fail the energy gate. Degree6, domain9, median THRML decision76ms. N12 onward are not run after this stop.
- **Exact/top-k/greedy/champion:** retained for the same attempted task IDs. Top-k/greedy verify0/12 at each small N and0/10 at N32. Champion verifies but is overbudget. Component-DP verifies46/46 attempted task optima within budget, including N32 tasks0–9 (median3.69ms). Its advantage depends on the engineered component structure.

Every new sampler run uses64 chains,160 updates,80 retained observations per chain. Eight penalty/price epochs share that budget; they do not each receive a full budget. No additional samples or outcome-driven parameter changes were made.

## N32 diagnosis

Some valid sample is insufficient. JAX finds any verifier-valid retained state in68/80 runs and THRML in73/80, but energy-only selection verifies47/80 and48/80. The sampler/controller never uses required IDs, expected facts, or verifier feedback to rank/propose. Other sampled feasible candidates can outrank a valid candidate when the exact good pack was not reached. Exact component-DP masks independently verify10/10, so the original optimum is not inherently invalid on these tasks.

## Evidence and metric interpretation

Six controls pass: actual THRML Ising energy on256 N8 states; DP versus36 exhaustive optima; label-free inputs/coloring; energy-only selection; both prefix energies on256 states; every prototype local conditional difference versus full energy.

The summary checker reconstructs2060 selected masks from task IDs and selected item IDs. Raw rows preserve seeds, tokens, energy, first-valid ordinal, prices, auxiliary consistency, degree, construction/lowering/compile/sample/decode/verifier timings. summary-reviewed.json includes per-seed outcomes/variance, first-valid p50/p95, warm p50/p95, compile costs, and categorical dimensions.

Decision cost includes optimization, decode/rank, and one final verification. The diagnostic first-valid scan is separate and excluded from the comparison; total row wall includes it. First-valid sample is an ordinal in retained samples×chains after batch execution, not measured early-exit latency. Public problem construction is common outside fixed-sampler/DP timers; the separately retained deterministic frontier includes it. Imports/process startup, the frozen record helper, and JSON serialization are outside row timings. The record helper performs one additional untimed verifier audit per row; raw verifier_calls excludes it, while summary-reviewed.json includes derived actual call counts. Total row wall is compute-stage wall, not full process/I/O time.

Compilation and cold shapes are reported separately, not amortized away. Input table bytes exclude XLA intermediates and total process memory. Shared-machine timings are observations, not isolated hardware benchmarks; lightweight concurrent checks and other work may add noise. Original kernel-only timings are not compared as full decision costs.

## Registration and limits

Protocol and pre-cohort addendum freeze seeds, initialization, state carry, degree6/arity3/domain255/table64MiB caps, and disclose one earlier N8 API smoke. A later clarification candidly records the stricter exact-gap scale gate and first-failing-small-N behavior already implemented before runs, though not explicit in the original JSON. N32 independently fails the declared zero-verifier-loss gate. The stop receipt field completed_N means attempted sizes; N32 is partial.

The issue architecture pins required evidence outside sampling, while the existing synthetic generator samples fixture-required bits. We preserve this mismatch instead of leaking oracle labels. Historical easy/monotone and old adaptive-scale code are absent, so those prose-only results are not reproduced. Full-family abstention/edit/invariance, real ContextPacket replay, frontier spend, and powered noninferiority remain unmeasured. General project tests needing large model dependencies were not run; this isolated benchmark uses a pinned CPU environment.

See REPRODUCE.md for commands. Raw evidence is under results/thermocontext/2026-10-02-thrml-wave1. Outputs are create-only.

## Numeric comparison

| Arm | N | Verified | Exact hits | Mean gap | Mean tokens | Median decision ms |
|---|---:|---:|---:|---:|---:|---:|
| fixed-thrml / component_dp | 8 | 12/12 | 12 | 0.000000 | 142.33 | 1.404 |
| fixed-thrml / thrml | 8 | 96/96 | 96 | 0.000000 | 142.33 | 11.998 |
| fixed-thrml / component_dp | 12 | 12/12 | 12 | 0.000000 | 147.17 | 1.709 |
| fixed-thrml / thrml | 12 | 96/96 | 96 | 0.000000 | 147.17 | 15.527 |
| fixed-thrml / component_dp | 16 | 12/12 | 12 | 0.000000 | 151.83 | 2.146 |
| fixed-thrml / thrml | 16 | 96/96 | 80 | 0.012478 | 151.10 | 16.840 |
| adaptive-price / prototype_jax | 8 | 96/96 | 96 | 0.000000 | 142.33 | 8.785 |
| adaptive-price / thrml | 8 | 96/96 | 96 | 0.000000 | 142.33 | 12.200 |
| adaptive-price / prototype_jax | 12 | 96/96 | 96 | 0.000000 | 147.17 | 10.052 |
| adaptive-price / thrml | 12 | 96/96 | 96 | 0.000000 | 147.17 | 15.740 |
| adaptive-price / prototype_jax | 16 | 96/96 | 96 | 0.000000 | 151.83 | 13.654 |
| adaptive-price / thrml | 16 | 96/96 | 96 | 0.000000 | 151.83 | 16.834 |
| adaptive-price / prototype_jax | 32 | 47/80 | 14 | 0.116538 | 149.05 | 25.180 |
| adaptive-price / thrml | 32 | 48/80 | 14 | 0.106800 | 148.89 | 30.886 |
| prefix-token / prototype_jax | 8 | 96/96 | 95 | 0.000226 | 142.33 | 404.313 |
| prefix-token / thrml | 8 | 96/96 | 96 | 0.000000 | 142.33 | 538.655 |
| prefix-cardinality / prototype_jax | 8 | 96/96 | 90 | 0.009875 | 142.20 | 68.758 |
| prefix-cardinality / thrml | 8 | 96/96 | 89 | 0.005535 | 142.14 | 76.048 |
