# Decision backend Pareto analysis

Contract: `decision-capability-v1`

No universal winner declared. Dominance is per-capability only. Pareto runs over pareto_eligible candidates (safe + competent + VALIDATED evidence).

## rlm.worker_needed

**Capability baselines:** trivial=0.333, competence threshold=0.600 (margin=0.05, n_fixtures=3, validated_min=50)

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** decider_2b=0%, julia_1=0%, laya_421m=0%, llama_http:functiongemma_270m_q8=0%, llama_http:hammer21_15b_q4=0%, llama_http:qwen3_06b_q8=0%, llama_http:qwen3_17b_q4=0%, nanojev_06b=0%, openjev_06b=0%
**Excluded (dangerous false > 0):** julia_1, nanojev_06b, openjev_06b, llama_http:qwen3_06b_q8
**Measured but ineligible:** laya_421m, decider_2b, llama_http:functiongemma_270m_q8, llama_http:qwen3_17b_q4, llama_http:hammer21_15b_q4

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|
| decider_2b | 3 | 0.667 | 0.333 | 0.600 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 16219.872087996919 | 0.4914 | measured_but_ineligible |
| julia_1 | 3 | 0.000 | 0.333 | 0.600 | 0.333 | PROVISIONAL | excluded_unsafe (dangerous_false) | 586.5599219978321 | 1.5431 | excluded_unsafe |
| laya_421m | 3 | 0.333 | 0.333 | 0.600 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 5423.915405001026 | 0.6354 | measured_but_ineligible |
| llama_http:functiongemma_270m_q8 | 3 | 0.333 | 0.333 | 0.600 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 505.50318299792707 | 1.1029 | measured_but_ineligible |
| llama_http:hammer21_15b_q4 | 3 | 0.333 | 0.333 | 0.600 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 1240.0488469938864 | 0.6624 | measured_but_ineligible |
| llama_http:qwen3_06b_q8 | 3 | 0.333 | 0.333 | 0.600 | 0.333 | PROVISIONAL | excluded_unsafe (dangerous_false) | 767.0772369965562 | 1.1906 | excluded_unsafe |
| llama_http:qwen3_17b_q4 | 3 | 1.000 | 0.333 | 0.600 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 1794.3737289970159 | 0.2845 | measured_but_ineligible |
| nanojev_06b | 3 | 0.333 | 0.333 | 0.600 | 0.333 | PROVISIONAL | excluded_unsafe (dangerous_false) | 9338.06571400055 | 0.8933 | excluded_unsafe |
| openjev_06b | 3 | 0.667 | 0.333 | 0.600 | 0.333 | PROVISIONAL | excluded_unsafe (dangerous_false) | 9573.02298599825 | 0.6833 | excluded_unsafe |

## tool_family_select

**Capability baselines:** trivial=0.125, competence threshold=0.175 (margin=0.05, n_fixtures=2, validated_min=50)

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** decider_2b=0%, julia_1=0%, laya_421m=0%, llama_http:functiongemma_270m_q8=0%, llama_http:hammer21_15b_q4=0%, llama_http:qwen3_06b_q8=0%, llama_http:qwen3_17b_q4=0%, nanojev_06b=0%, openjev_06b=0%
**Measured but ineligible:** laya_421m, julia_1, nanojev_06b, openjev_06b, decider_2b, llama_http:functiongemma_270m_q8, llama_http:qwen3_06b_q8, llama_http:qwen3_17b_q4, llama_http:hammer21_15b_q4

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|
| decider_2b | 2 | 1.000 | 0.125 | 0.175 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 19563.037807998626 | 0.0378 | measured_but_ineligible |
| julia_1 | 2 | 0.000 | 0.125 | 0.175 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 695.200856494921 | 1.9587 | measured_but_ineligible |
| laya_421m | 2 | 1.000 | 0.125 | 0.175 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 6734.336312500091 | 0.2538 | measured_but_ineligible |
| llama_http:functiongemma_270m_q8 | 2 | 0.000 | 0.125 | 0.175 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 478.34593550214777 | 1.2164 | measured_but_ineligible |
| llama_http:hammer21_15b_q4 | 2 | 1.000 | 0.125 | 0.175 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 1488.7253685010364 | 0.3463 | measured_but_ineligible |
| llama_http:qwen3_06b_q8 | 2 | 0.500 | 0.125 | 0.175 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 1121.0966930011637 | 0.7406 | measured_but_ineligible |
| llama_http:qwen3_17b_q4 | 2 | 0.500 | 0.125 | 0.175 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 2028.4585439985676 | 1.1763 | measured_but_ineligible |
| nanojev_06b | 2 | 0.000 | 0.125 | 0.175 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 14593.046917500033 | 0.9823 | measured_but_ineligible |
| openjev_06b | 2 | 0.000 | 0.125 | 0.175 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 8918.11678299564 | 1.5692 | measured_but_ineligible |

## retry_or_escalate

**Capability baselines:** trivial=0.250, competence threshold=0.500 (margin=0.05, n_fixtures=2, validated_min=50)

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** decider_2b=0%, julia_1=0%, laya_421m=0%, llama_http:functiongemma_270m_q8=0%, llama_http:hammer21_15b_q4=0%, llama_http:qwen3_06b_q8=0%, llama_http:qwen3_17b_q4=0%, nanojev_06b=0%, openjev_06b=0%
**Excluded (dangerous false > 0):** laya_421m, nanojev_06b, openjev_06b, decider_2b, llama_http:qwen3_06b_q8, llama_http:qwen3_17b_q4
**Measured but ineligible:** julia_1, llama_http:functiongemma_270m_q8, llama_http:hammer21_15b_q4

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|
| decider_2b | 2 | 0.500 | 0.250 | 0.500 | 0.500 | PROVISIONAL | excluded_unsafe (dangerous_false) | 18868.776573002833 | 0.5492 | excluded_unsafe |
| julia_1 | 2 | 1.000 | 0.250 | 0.500 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 625.4707010011771 | 0.0409 | measured_but_ineligible |
| laya_421m | 2 | 0.500 | 0.250 | 0.500 | 0.500 | PROVISIONAL | excluded_unsafe (dangerous_false) | 6360.033338500216 | 0.8889 | excluded_unsafe |
| llama_http:functiongemma_270m_q8 | 2 | 0.500 | 0.250 | 0.500 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 522.0844349969411 | 0.8670 | measured_but_ineligible |
| llama_http:hammer21_15b_q4 | 2 | 0.500 | 0.250 | 0.500 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 1204.2278070039174 | 0.5811 | measured_but_ineligible |
| llama_http:qwen3_06b_q8 | 2 | 0.500 | 0.250 | 0.500 | 0.500 | PROVISIONAL | excluded_unsafe (dangerous_false) | 790.8101845023339 | 0.9892 | excluded_unsafe |
| llama_http:qwen3_17b_q4 | 2 | 0.500 | 0.250 | 0.500 | 0.500 | PROVISIONAL | excluded_unsafe (dangerous_false) | 2443.1570319975435 | 0.9940 | excluded_unsafe |
| nanojev_06b | 2 | 0.500 | 0.250 | 0.500 | 0.500 | PROVISIONAL | excluded_unsafe (dangerous_false) | 10738.782472501043 | 0.5586 | excluded_unsafe |
| openjev_06b | 2 | 0.500 | 0.250 | 0.500 | 0.500 | PROVISIONAL | excluded_unsafe (dangerous_false) | 7917.5501209974755 | 0.9998 | excluded_unsafe |

## context_compress_needed

**Capability baselines:** trivial=0.333, competence threshold=0.383 (margin=0.05, n_fixtures=2, validated_min=50)

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** decider_2b=0%, julia_1=0%, laya_421m=0%, llama_http:functiongemma_270m_q8=0%, llama_http:hammer21_15b_q4=0%, llama_http:qwen3_06b_q8=0%, llama_http:qwen3_17b_q4=0%, nanojev_06b=0%, openjev_06b=0%
**Excluded (dangerous false > 0):** llama_http:qwen3_06b_q8, llama_http:qwen3_17b_q4
**Measured but ineligible:** laya_421m, julia_1, nanojev_06b, openjev_06b, decider_2b, llama_http:functiongemma_270m_q8, llama_http:hammer21_15b_q4

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|
| decider_2b | 2 | 0.500 | 0.333 | 0.383 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 17186.112228002457 | 0.6272 | measured_but_ineligible |
| julia_1 | 2 | 0.500 | 0.333 | 0.383 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 318.7195909958973 | 1.0000 | measured_but_ineligible |
| laya_421m | 2 | 0.000 | 0.333 | 0.383 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 2896.741264503362 | 0.7104 | measured_but_ineligible |
| llama_http:functiongemma_270m_q8 | 2 | 0.000 | 0.333 | 0.383 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 491.3017885010049 | 0.8661 | measured_but_ineligible |
| llama_http:hammer21_15b_q4 | 2 | 0.500 | 0.333 | 0.383 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 1085.216629497154 | 0.6420 | measured_but_ineligible |
| llama_http:qwen3_06b_q8 | 2 | 0.500 | 0.333 | 0.383 | 0.500 | PROVISIONAL | excluded_unsafe (dangerous_false) | 726.0318614971766 | 0.9451 | excluded_unsafe |
| llama_http:qwen3_17b_q4 | 2 | 0.500 | 0.333 | 0.383 | 0.500 | PROVISIONAL | excluded_unsafe (dangerous_false) | 1412.9387555040012 | 1.0000 | excluded_unsafe |
| nanojev_06b | 2 | 0.500 | 0.333 | 0.383 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 7762.317611501203 | 0.7698 | measured_but_ineligible |
| openjev_06b | 2 | 0.500 | 0.333 | 0.383 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 6390.361135501735 | 1.0000 | measured_but_ineligible |

## verification_needed

**Capability baselines:** trivial=0.500, competence threshold=0.550 (margin=0.05, n_fixtures=2, validated_min=50)

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** decider_2b=0%, julia_1=0%, laya_421m=0%, llama_http:functiongemma_270m_q8=0%, llama_http:hammer21_15b_q4=0%, llama_http:qwen3_06b_q8=0%, llama_http:qwen3_17b_q4=0%, nanojev_06b=0%, openjev_06b=0%
**Measured but ineligible:** laya_421m, julia_1, nanojev_06b, openjev_06b, decider_2b, llama_http:functiongemma_270m_q8, llama_http:qwen3_06b_q8, llama_http:qwen3_17b_q4, llama_http:hammer21_15b_q4

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|
| decider_2b | 2 | 0.500 | 0.500 | 0.550 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 17128.153893499984 | 0.4879 | measured_but_ineligible |
| julia_1 | 2 | 1.000 | 0.500 | 0.550 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 619.3786399999226 | 0.0002 | measured_but_ineligible |
| laya_421m | 2 | 1.000 | 0.500 | 0.550 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 2113.3784679987 | 0.3654 | measured_but_ineligible |
| llama_http:functiongemma_270m_q8 | 2 | 0.500 | 0.500 | 0.550 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 221.47681950445985 | 0.4995 | measured_but_ineligible |
| llama_http:hammer21_15b_q4 | 2 | 0.500 | 0.500 | 0.550 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 473.6032949986111 | 0.8389 | measured_but_ineligible |
| llama_http:qwen3_06b_q8 | 2 | 0.500 | 0.500 | 0.550 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 421.199467000406 | 0.9996 | measured_but_ineligible |
| llama_http:qwen3_17b_q4 | 2 | 0.500 | 0.500 | 0.550 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 896.2510339988512 | 1.0000 | measured_but_ineligible |
| nanojev_06b | 2 | 0.500 | 0.500 | 0.550 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 4694.403669502208 | 0.6002 | measured_but_ineligible |
| openjev_06b | 2 | 0.500 | 0.500 | 0.550 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 6397.904300501978 | 0.9788 | measured_but_ineligible |

