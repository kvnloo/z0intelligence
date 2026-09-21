# Phase 1B decision artifact -- densified evidence

- schema: `z0int.phase1b.decision_artifact.v1`
- run id: `p1b-20260921T1430Z` (frozen 2026-09-21T19:22:03.396551Z)
- observations: 1102

## Provenance

| repo | sha | branch | dirty |
|---|---|---|---|
| aodl | `1fa80da2b9d3` | feat/aodl-contract-importable | 0 |
| evolution-lab | `80ffebe9512a` | experiment/q-route-v0 | 0 |
| kerdoios | `8441c3cb4739` | feat/local-cognition-placement | 0 |
| openjev | `cf687a7ec56f` | feat/network-cutover-harness-adapters | 4 |
| tokenomics | `9af30cafd566` | feat/orchestration-telemetry | 0 |
| z0intelligence | `3430a5afd06e` | feat/local-cognition-portfolio | 1 |

| model | hf revision | quant | gguf sha256 |
|---|---|---|---|
| nemotron_orchestrator_8b | `26df4b9aad5a` | Q4_K_M | `1cc7077e20b3339d` |
| qwen3.5_9b | `c20223623576` | Q4_K_M | `d784ce9eda1a5a7b` |
| qwen3.5_4b | `851bf6e806ef` | Q4_K_M | `13c16f426047e2de` |
| hammer2.1_3b | `702ce4215e13` | Q4_K_M | `320b6114e54cdee0` |
| hammer2.1_7b | `c5692ee193b8` | Q4_K_M | `cd698cb286d02e40` |
| functiongemma_270m | `39eccb091651` | Q8_0 | `83940d4dd9676710` |
| nanojev_06b | `4a19595eada0` | bfloat16 | `-` |

## (state, arm) coverage

- cells: 370 over 28 states x 19 arms
- cells with n>=2: 370
- cells with n>=3: **362**
- cells with n>=10: 0
- cell-size histogram: {"2": 8, "3": 362}

## Per-arm benchmark

| arm | kind | n | states | success | 95% CI | dangerous | abstained | errors | p50 ms | p95 ms | decision p50 | load p50 | tokens out p50 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| coldprobe+functiongemma_270m | cold_probe | 3 | 1 | 3/3 | [0.44, 1.00] | 0 | 0 | 0 | 3093 | 3236 | 97 | 3003 | 17.0 |
| coldprobe+hammer2.1_3b | cold_probe | 3 | 1 | 3/3 | [0.44, 1.00] | 0 | 0 | 0 | 6310 | 13507 | 296 | 6005 | 33.0 |
| coldprobe+hammer2.1_7b | cold_probe | 3 | 1 | 0/3 | [0.00, 0.56] | 0 | 0 | 0 | 13550 | 19023 | 541 | 13009 | 19.0 |
| coldprobe+nemotron_orchestrator_8b | cold_probe | 3 | 1 | 0/3 | [0.00, 0.56] | 0 | 3 | 0 | 14924 | 15932 | 2916 | 12008 | 256.0 |
| coldprobe+qwen3.5_4b | cold_probe | 3 | 1 | 3/3 | [0.44, 1.00] | 0 | 0 | 0 | 10676 | 19378 | 1668 | 9008 | 153.0 |
| coldprobe+qwen3.5_9b | cold_probe | 3 | 1 | 3/3 | [0.44, 1.00] | 0 | 0 | 0 | 17241 | 22889 | 2231 | 15010 | 139.0 |
| compiler+functiongemma_270m | single_model | 84 | 28 | 51/84 | [0.50, 0.70] | 0 | 0 | 0 | 66 | 139 | 66 | 4005 | 14.0 |
| compiler+hammer2.1_3b | single_model | 84 | 28 | 75/84 | [0.81, 0.94] | 0 | 3 | 0 | 196 | 533 | 196 | 5004 | 21.0 |
| compiler+hammer2.1_7b | single_model | 84 | 28 | 72/84 | [0.77, 0.92] | 0 | 0 | 0 | 322 | 483 | 322 | 10508 | 19.0 |
| compiler+jev | single_model | 84 | 28 | 45/84 | [0.43, 0.64] | 0 | 6 | 6 | 31 | 32 | 31 | 0 | - |
| compiler+jev+qwen3.5_4b | cascade | 76 | 28 | 63/76 | [0.73, 0.90] | 0 | 0 | 0 | 1650 | 3552 | 1650 | 8006 | 204.0 |
| compiler+nemotron_orchestrator_8b | single_model | 84 | 28 | 52/84 | [0.51, 0.72] | 0 | 31 | 0 | 2518 | 2832 | 2518 | 12011 | 228.5 |
| compiler+qwen3.5_4b | single_model | 84 | 28 | 75/84 | [0.81, 0.94] | 0 | 0 | 0 | 2029 | 4274 | 2029 | 7506 | 204.0 |
| compiler+qwen3.5_9b | single_model | 84 | 28 | 78/84 | [0.85, 0.97] | 0 | 0 | 0 | 3643 | 13868 | 3643 | 15012 | 245.0 |
| deterministic.compiler_only | deterministic | 84 | 28 | 15/84 | [0.11, 0.27] | 0 | 78 | 0 | 0 | 0 | 0 | 0 | - |
| unfiltered+hammer2.1_3b | unfiltered_control | 84 | 28 | 69/84 | [0.73, 0.89] | 6 | 3 | 0 | 187 | 477 | 187 | 5507 | 21.0 |
| unfiltered+nemotron_orchestrator_8b | unfiltered_control | 84 | 28 | 61/84 | [0.62, 0.81] | 0 | 20 | 0 | 2460 | 3323 | 2460 | 13018 | 218.0 |
| unfiltered+qwen3.5_4b | unfiltered_control | 84 | 28 | 75/84 | [0.81, 0.94] | 0 | 0 | 0 | 2087 | 4627 | 2087 | 8005 | 215.0 |
| unfiltered+qwen3.5_9b | unfiltered_control | 84 | 28 | 78/84 | [0.85, 0.97] | 0 | 0 | 0 | 3330 | 13915 | 3330 | 15012 | 237.0 |

## Residency distributions (cold vs warm kept apart)

| class | n | p50 ms | p95 ms | mean ms |
|---|---|---|---|---|
| cold_load | 18 | 13928 | 20820 | 12231 |
| model_swap | 23 | 12927 | 21298 | 14131 |
| no_model_call | 168 | 0 | 32 | 104 |
| warm_invocation | 893 | 1763 | 5014 | 2014 |

## Dominance (interval-separated only)

| arm | n | success CI | p50 ms | dominated by | strict quality gap | faster |
|---|---|---|---|---|---|---|
| compiler+functiongemma_270m | 84 | [0.50, 0.70] | 66 | compiler+hammer2.1_3b | True | False |
| compiler+hammer2.1_7b | 84 | [0.77, 0.92] | 322 | compiler+hammer2.1_3b | False | True |
| compiler+jev | 84 | [0.43, 0.64] | 31 | compiler+hammer2.1_3b | True | False |
| compiler+jev+qwen3.5_4b | 76 | [0.73, 0.90] | 1650 | compiler+hammer2.1_3b | False | True |
| compiler+nemotron_orchestrator_8b | 84 | [0.51, 0.72] | 2518 | compiler+functiongemma_270m | False | True |
| compiler+qwen3.5_4b | 84 | [0.81, 0.94] | 2029 | compiler+hammer2.1_3b | False | True |
| deterministic.compiler_only | 84 | [0.11, 0.27] | 0 | compiler+jev | True | False |

## Section C pairwise comparisons

`indistinguishable` = the 95% intervals overlap at this n, which is not a loss.

| cheap | expensive | n (c/e) | success c/e | separated | p50 ratio | verdict |
|---|---|---|---|---|---|---|
| compiler+hammer2.1_3b | compiler+qwen3.5_4b | 84/84 | 0.89/0.89 | False | 0.10 | indistinguishable_at_this_n |
| compiler+hammer2.1_3b | compiler+qwen3.5_9b | 84/84 | 0.89/0.93 | False | 0.05 | indistinguishable_at_this_n |
| compiler+hammer2.1_3b | compiler+nemotron_orchestrator_8b | 84/84 | 0.89/0.62 | True | 0.08 | cheap_superior |
| compiler+hammer2.1_3b | compiler+hammer2.1_7b | 84/84 | 0.89/0.86 | False | 0.61 | indistinguishable_at_this_n |
| compiler+qwen3.5_4b | compiler+qwen3.5_9b | 84/84 | 0.89/0.93 | False | 0.56 | indistinguishable_at_this_n |
| compiler+hammer2.1_3b | compiler+functiongemma_270m | 84/84 | 0.89/0.61 | True | 2.96 | cheap_superior |

## Per-state optimum (highest measured success; latency breaks ties)

| arm | states won (ties counted for each tied arm) |
|---|---|
| compiler+qwen3.5_9b | 27 |
| compiler+hammer2.1_3b | 26 |
| compiler+qwen3.5_4b | 26 |
| compiler+hammer2.1_7b | 25 |
| compiler+jev+qwen3.5_4b | 24 |
| compiler+functiongemma_270m | 18 |
| compiler+nemotron_orchestrator_8b | 17 |
| compiler+jev | 16 |

## Raw Pareto membership (non-dominated in success_rate x p50_ms)

_raw (success_rate, p50_ms) non-domination among model arms; the deterministic compiler-only control is excluded because 0 ms would otherwise make it non-dominated everywhere_

| arm | states on frontier |
|---|---|
| compiler+jev | 28 |
| compiler+functiongemma_270m | 7 |
| compiler+hammer2.1_3b | 4 |
| compiler+qwen3.5_9b | 1 |

## State-family niches

| family | winner | success | arms compared |
|---|---|---|---|
| abstention | compiler+functiongemma_270m | 1.00 | 9 |
| control_flow | compiler+hammer2.1_7b | 1.00 | 9 |
| dependencies | compiler+functiongemma_270m | 1.00 | 9 |
| parallelism | compiler+functiongemma_270m | 1.00 | 9 |
| recovery | compiler+hammer2.1_3b | 0.75 | 9 |
| routing | compiler+hammer2.1_3b | 1.00 | 9 |
| schema | compiler+hammer2.1_3b | 1.00 | 9 |
| security | compiler+hammer2.1_3b | 1.00 | 9 |
| tool_selection | compiler+functiongemma_270m | 1.00 | 9 |
| uncertainty | compiler+functiongemma_270m | 0.50 | 9 |
