# ThermoContext: real THRML and sparse budget experiments

Tracking: #106

Current decision: **KILL the current cohort's case for adding THRML to Hermes.**

Real THRML reproduces the small-N signal, but the tested sparse budget controller
fails at N=32. A host-repaired THRML arm succeeds, yet identically repaired random
proposals match it. A deterministic component solver reaches the same exact
frontier with lower measured CPU overhead. This rejects the present promotion
case, not thermodynamic computation in general. Broader real-evidence usefulness
remains UNKNOWN; no sampler is activated in Hermes.

## Reverification and native Hermes follow-up

The October 2 round-three rerun preserves the decision above. The original
432 rows and all 2,520 small-N comparison rows match their prior non-timing
outputs; all 2,628 retained trace/auxiliary files have identical SHA-256 hashes.
This repeats the same tasks and seeds and does not increase independent sample
size. The separately contributed wave at `46d93b0` has 2,060 independently checked
selected outputs and remains a distinct experiment; its retained trajectories
are unavailable. Both N=32 stop decisions remain intact. See
`results/reverification-round3-20261002/audit/THRML_AUDIT_DECISION.json`.

Separately, native Hermes now has a verified factual development-task answer
through an explicitly priced Nous route. A later five-case context cohort passed
0/5 strict outcomes, and a separately frozen source-claim prompt clarification
passed 1/5. Neither smaller-context pair preserves verified success. No useful
consumed-token saving or Hermes speedup is established. These studies preserve
exact request bytes, failed work, independent checking and replay; THRML is not
on their execution path. See `hermes_phase_c/results/` and its README.

This directory is an isolated experiment. It does not change routing, authority,
production context resolution, or promotion semantics.

## Completed next wave — 2026-10-02

Actual `extropic-ai/thrml` 0.1.4 at
`dfb3bde16962f9637a1845ccb2bea80aaa19e4bd` was installed and exercised through
`IsingSamplingProgram` / `sample_states`. The original generator, objective and
verifier are unchanged. Candidate code and protocol were frozen in commit
`74af422e51ffab78d8a5521186cf50c9b8f2c042` before measurement.

Each stochastic cell below contains 12 tasks × 8 seeds. Gibbs arms use 64 chains,
160 sweeps and 5,120 retained draws per run. The initialization-only control uses
64 initial masks and zero transitions; random projection uses 5,120 independent
random proposals and zero Gibbs sweeps. Deterministic methods run once per task.
These are empirical screening results, not certified population noninferiority.

| Arm | N=8 verified / feasible | N=12 | N=16 | N=32 |
|---|---:|---:|---:|---:|
| Frozen prototype | 96/96 | 96/96 | 95/96 | 0/96 |
| Real THRML, fixed price | 96/96 | 96/96 | 96/96 | 1/96 |
| THRML, safeguarded adaptive price | 96/96 | 96/96 | 96/96 | **57/96** |
| THRML, sparse slack/carry variables | 81/96 | 59/96 | 27/96 | NOT_RUN: failed small-N gate |
| Slack initialization alone | 81/96 | 59/96 | 27/96 | NOT_RUN |
| THRML + host budget projection | 96/96 | 96/96 | 96/96 | 96/96 |
| Prototype + identical projection | 96/96 | 96/96 | 96/96 | 96/96 |
| Random proposals + identical projection | 96/96 | 96/96 | 96/96 | 96/96 |
| Deterministic component DP | 12/12 | 12/12 | 12/12 | 12/12 |
| Top-k / one-step / pair-lookahead greedy | 0/12 each | 0/12 each | 0/12 each | 0/12 each |

The full-context champion verifies semantically but exceeds the token budget on
all tasks; it must not be counted as a feasible success. All three projected
arms and component DP reach the exact frozen-energy optimum at every tested N.
Adaptive THRML is exact through N=16, then has mean gap 0.128148 at N=32.
Fixed THRML's N=16 mean gap is 0.012478 versus the prototype's 0.015604.

The N=32 adaptive result fails both the ≥95% success gate and the ≥7/8
worst-task gate (observed worst task: 1/8). The preregistered rule stops the whole
ladder at that first failing stage. **N=64 and N=128 were not sampled in this
wave**, including for the surviving projected arm. No rescue samples were added.

### Failure diagnosis and attribution

- Adaptive pricing avoids literal empty and over-budget outputs, but loses the
  necessary three-item set. Of 39 failed N=32 runs, 15 never sample a valid pack;
  24 sample one but select a lower original-energy incorrect pack among the
  available draws. All 39 selected failures omit all three required items. The
  controller reaches its occupancy target and holds price fixed despite this
  semantic failure. The verifier is diagnostic only, never a reranker.
- Slack uses sparse ripple-carry/slack constraints, with full expanded maximum
  degree 8–9. It is nearly frozen: only one retained item state changes among
  1,474,560, and all 288 final packs exactly match initialization-only controls.
  Correct ground-state encoding does not establish useful mixing.
- Projection is deterministic, host-side delete-only repair. It is not a local
  constrained Gibbs kernel or evidence of budget enforcement on a TSU. Matching
  random-proposal controls remove the claimed incremental sampler benefit here.
- This generator has a six-node interacting core and disconnected noise pairs.
  Component enumeration plus token-knapsack DP exploits that structure without
  verifier facts. It is an explicit deterministic solver, not a hidden sampler
  oracle. Its applicability to large connected real-evidence graphs is unproven.

### Timing and evidence limits

| N | Component DP median total ms | THRML + projection median total ms | Random + projection median total ms |
|---:|---:|---:|---:|
| 8 | 0.657 | 41.375 | 34.836 |
| 12 | 0.958 | 46.208 | 37.991 |
| 16 | 1.296 | 51.251 | 42.265 |
| 32 | 2.668 | 77.103 | 62.231 |

These are contended CPU experiment timings, including selection, diagnostic
verification and trace-writing overhead. Compile costs and warm sampling times
are reported separately in every row and summary. Executable caches are shared
in the recorded arm order; zero newly incurred compilation is not zero cold-start
cost. Small-N measurement took 443.770 s; N=32 took 114.402 s.

Synthetic token weights are not consumed model tokens. No real Hermes efficiency,
TSU/Z1 latency, or energy savings follow from this simulation. Phase A samples
the required evidence too; the intended real integration would pin required
evidence outside the selector. That is a different experimental problem.

Independent audit passed all **3,156** new-wave rows and **3,212** declared
artifact hashes. It recomputed 36 small-N exhaustive optima, retained-sample
selection, first-valid counts and auxiliary constraints/motion. An independent
SciPy/HiGHS MILP certified all 12 N=32 reference optima to the reported tolerance
with zero solver gap. Original-cohort reproduction separately retains 432 rows.

Results, including negative outcomes and lossless traces:

- `results/next-wave/reproduction/original-20261002/`
- `results/next-wave/small-full-20261002/`
- `results/next-wave/scale32-20261002/`
- `next_wave_protocol.json`: frozen gates and resource limits.
- `hermes_phase_c/`: separate native-context admission and bounded real-provider
  work; it does not activate THRML or change the Phase A verifier.

## Reproduction

Use Python 3.12 and an isolated environment with
`pip install -r benchmarks/thermocontext/requirements-next-wave.txt`.
Experimental dependencies are separate from the core runtime. Run:

```sh
PYTHONPATH=src python -m pytest -q tests/test_thermocontext_*.py
python -m benchmarks.thermocontext.next_wave --mode full --sizes 8 12 16 \
  --tasks 12 --seeds 8 --out /new/create-only/small-results \
  --timing-mode contended --concurrent-jobs 'describe actual host contention'
python -m benchmarks.thermocontext.audit_next_wave /new/create-only/small-results \
  --out /new/create-only/small-results/independent-audit.json
```

Use the recorded affinity/thread limits for comparable CPU runs. Every result
directory records exact commands, source/configuration hashes and dependencies.
Audit and apply the frozen gates before each separate scale stage. Do not run a
larger N after a failure. The historical nine core-suite failures reproduce on
the untouched starting revision; they are separate from these experiment tests.

## Historical first prototype wave

A synthetic contrastive-evidence cohort compares:

- full-context champion;
- relevance top-k;
- one-step marginal-energy greedy;
- exhaustive exact search for `N <= 16`;
- colored block-Gibbs thermodynamic sampling.

The first prototype used a small JAX block-Gibbs kernel that mirrors THRML's
colored-block semantics. It did **not import the THRML package** because
the execution environment used for the first run could not install
`equinox` / `jaxtyping` / `thrml`. Replacing this kernel with the exact
THRML API was the next implementation step, completed in the wave above.

## Initial results — 2026-10-02

### Engineered pairwise-synergy cohort

12 tasks per size, 8 thermo seeds per task.

| N | arm | verified | mean tokens | exact hit |
|---:|---|---:|---:|---:|
| 8 | top-k | 0% | 143.3 | — |
| 8 | greedy | 0% | 105.0 | — |
| 8 | exact | 100% | 142.3 | 100% |
| 8 | thermo | **100%** | 142.3 | **100%** |
| 12 | top-k | 0% | 146.5 | — |
| 12 | greedy | 0% | 139.3 | — |
| 12 | exact | 100% | 147.2 | 100% |
| 12 | thermo | **100%** | 147.2 | **100%** |
| 16 | top-k | 0% | 154.9 | — |
| 16 | greedy | 0% | 149.4 | — |
| 16 | exact | 100% | 151.8 | 100% |
| 16 | thermo | **98.96%** | 151.1 | **89.58%** |

Median thermo sampling wall time after shape compilation was roughly:

- N=8: 7.0 ms
- N=12: 7.8 ms
- N=16: 8.8 ms

Interaction graph max degree: 4.

Interpretation: block Gibbs can cross the deliberately constructed pairwise
energy barrier that traps the one-step greedy arm. This is a **controlled signal,
not evidence of a production win**.

### Easy / monotone control

For `N = 8, 12, 16`, top-k, greedy, exact, and thermo all:

- verified at 100%;
- selected the same 81-token pack;
- reached the same energy.

Interpretation: there is no thermodynamic advantage when independent evidence
scores already expose the optimum.

### First scale test

At `N = 32 / 64 / 128`, a fixed token price produced no budget-feasible thermo
candidate. A naive proportional outer-loop `mu` controller reduced token load,
but pushed the minimum-energy solution toward the empty set.

At `N=32`, verifier-valid states still appeared in 50% of adaptive runs, but
they were not the objective minimum. At `N >= 64`, none appeared in the light
scale cohort.

Interpretation: the current token-budget formulation is **not scale-stable**.
Do not proceed to real evidence until this is fixed.

## Historical first-wave decision (superseded above)

**MODIFY, not GO / KILL.**

What survives:

1. There is a measurable global-search benefit on sparse pairwise synergy
   landscapes.
2. There is no advantage on easy monotone selection.
3. The sparse graph representation remains substrate-friendly (max degree 4).
4. The budget controller is currently the bottleneck.

## Historical first-wave next steps (superseded by the next-wave evidence)

Before any real z0 replay:

1. replace the prototype sampler with actual THRML;
2. separate semantic utility from budget pressure instead of letting `mu`
   collapse utility;
3. test a constrained sampler / slack-variable formulation against the current
   Lagrangian;
4. rerun the exact `N=8/12/16` oracle;
5. require parity with the small-N result before scaling again;
6. only then freeze real `ContextPacket` replay cases.

No TSU speed or energy claim is supported by these JAX results.


## Independent CPU qualification wave (2026-10-02)

A separately executed, frozen-source wave is preserved in [THRML_RESULTS_2026-10-02.md](THRML_RESULTS_2026-10-02.md), with [reproduction and lossless raw-data restoration](REPRODUCE.md). It independently recommends KILL for sampling investment on this engineered disconnected cohort. Its partial N32 cohort, paired-prefix stopping rule, and timings are distinct from the studies above; do not combine their counts. This addition preserves all existing Phase-C and native Hermes evidence unchanged.
