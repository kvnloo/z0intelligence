# Bootstrap Pareto stability

Inclusion probability is the fraction of fixture-resampled datasets in which a backend remained on the eligible Pareto frontier. At small n this can be unstable — treat provisional evidence seriously.

Bootstrap draws: 500 / 500 (seed=0)

## context_compress_needed

| backend | P(Pareto) | P(eligible) |
|---------|-----------|-------------|
| decider_2b | 0.000 | 0.000 |
| julia_1 | 0.000 | 0.000 |
| laya_421m | 0.000 | 0.000 |
| llama_http:functiongemma_270m_q8 | 0.000 | 0.000 |
| llama_http:hammer21_15b_q4 | 0.000 | 0.000 |
| llama_http:qwen3_06b_q8 | 0.000 | 0.000 |
| llama_http:qwen3_17b_q4 | 0.000 | 0.000 |
| nanojev_06b | 0.000 | 0.000 |
| openjev_06b | 0.000 | 0.000 |

## retry_or_escalate

| backend | P(Pareto) | P(eligible) |
|---------|-----------|-------------|
| decider_2b | 0.000 | 0.000 |
| julia_1 | 0.000 | 0.000 |
| laya_421m | 0.000 | 0.000 |
| llama_http:functiongemma_270m_q8 | 0.000 | 0.000 |
| llama_http:hammer21_15b_q4 | 0.000 | 0.000 |
| llama_http:qwen3_06b_q8 | 0.000 | 0.000 |
| llama_http:qwen3_17b_q4 | 0.000 | 0.000 |
| nanojev_06b | 0.000 | 0.000 |
| openjev_06b | 0.000 | 0.000 |

## rlm.worker_needed

| backend | P(Pareto) | P(eligible) |
|---------|-----------|-------------|
| decider_2b | 0.000 | 0.000 |
| julia_1 | 0.000 | 0.000 |
| laya_421m | 0.000 | 0.000 |
| llama_http:functiongemma_270m_q8 | 0.000 | 0.000 |
| llama_http:hammer21_15b_q4 | 0.000 | 0.000 |
| llama_http:qwen3_06b_q8 | 0.000 | 0.000 |
| llama_http:qwen3_17b_q4 | 0.000 | 0.000 |
| nanojev_06b | 0.000 | 0.000 |
| openjev_06b | 0.000 | 0.000 |

## tool_family_select

| backend | P(Pareto) | P(eligible) |
|---------|-----------|-------------|
| decider_2b | 0.004 | 1.000 |
| julia_1 | 0.028 | 0.028 |
| laya_421m | 0.000 | 0.616 |
| llama_http:functiongemma_270m_q8 | 0.000 | 0.000 |
| llama_http:hammer21_15b_q4 | 0.856 | 1.000 |
| llama_http:qwen3_06b_q8 | 1.000 | 1.000 |
| llama_http:qwen3_17b_q4 | 1.000 | 1.000 |
| nanojev_06b | 0.000 | 0.554 |
| openjev_06b | 0.000 | 1.000 |

## verification_needed

| backend | P(Pareto) | P(eligible) |
|---------|-----------|-------------|
| decider_2b | 0.000 | 0.000 |
| julia_1 | 0.000 | 0.000 |
| laya_421m | 0.000 | 0.000 |
| llama_http:functiongemma_270m_q8 | 0.000 | 0.000 |
| llama_http:hammer21_15b_q4 | 0.000 | 0.000 |
| llama_http:qwen3_06b_q8 | 0.000 | 0.000 |
| llama_http:qwen3_17b_q4 | 0.000 | 0.000 |
| nanojev_06b | 0.000 | 0.000 |
| openjev_06b | 0.000 | 0.000 |

# Measurement uncertainty

## decider_2b

- `tool_family_select`: n=77 acc=0.4025974025974026 CI95=[0.3002184586296985, 0.5142335066950848] dangerous_false=0.0 upper95=0.03896103896103896
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 0.03896103896103896

## julia_1

- `tool_family_select`: n=77 acc=0.1038961038961039 CI95=[0.053592282957973164, 0.19184570936281223] dangerous_false=0.0 upper95=0.03896103896103896
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 0.03896103896103896

## laya_421m

- `tool_family_select`: n=77 acc=0.19480519480519481 CI95=[0.12176294266340365, 0.2968532153542507] dangerous_false=0.0 upper95=0.03896103896103896
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 0.03896103896103896

## llama_http:functiongemma_270m_q8

- `tool_family_select`: n=77 acc=0.0 CI95=[3.469446951953614e-18, 0.047520088667220836] dangerous_false=0.0 upper95=0.03896103896103896
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 0.03896103896103896

## llama_http:hammer21_15b_q4

- `tool_family_select`: n=77 acc=0.4675324675324675 CI95=[0.36029890673598886, 0.5778517483722723] dangerous_false=0.0 upper95=0.03896103896103896
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 0.03896103896103896

## llama_http:qwen3_06b_q8

- `tool_family_select`: n=77 acc=0.36363636363636365 CI95=[0.26505270593089814, 0.47518004552379856] dangerous_false=0.0 upper95=0.03896103896103896
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 0.03896103896103896

## llama_http:qwen3_17b_q4

- `tool_family_select`: n=77 acc=0.5714285714285714 CI95=[0.46010331091803647, 0.6759652478437891] dangerous_false=0.0 upper95=0.03896103896103896
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 0.03896103896103896

## nanojev_06b

- `tool_family_select`: n=77 acc=0.18181818181818182 CI95=[0.11151152010047759, 0.28236489996048114] dangerous_false=0.0 upper95=0.03896103896103896
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 0.03896103896103896

## openjev_06b

- `tool_family_select`: n=77 acc=0.4155844155844156 CI95=[0.31209010264563286, 0.5271016006358461] dangerous_false=0.0 upper95=0.03896103896103896
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 0.03896103896103896

