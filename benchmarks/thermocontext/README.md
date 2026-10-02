# ThermoContext Phase A

Tracking: #106

Status: **MODIFY**

This directory is an isolated experiment. It does not change routing, authority,
production context resolution, or promotion semantics.

## What was run

A synthetic contrastive-evidence cohort compares:

- full-context champion;
- relevance top-k;
- one-step marginal-energy greedy;
- exhaustive exact search for `N <= 16`;
- colored block-Gibbs thermodynamic sampling.

The current prototype uses a small JAX block-Gibbs kernel that mirrors THRML's
colored-block semantics. It is **not yet an import of the THRML package** because
the execution environment used for the first run could not install
`equinox` / `jaxtyping` / `thrml`. Replacing this kernel with the exact
THRML API is the next implementation step before making any Extropic-specific
performance claim.

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

## Current decision

**MODIFY, not GO / KILL.**

What survives:

1. There is a measurable global-search benefit on sparse pairwise synergy
   landscapes.
2. There is no advantage on easy monotone selection.
3. The sparse graph representation remains substrate-friendly (max degree 4).
4. The budget controller is currently the bottleneck.

## Next experiment

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
