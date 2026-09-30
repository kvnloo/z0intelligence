# Decision backend Pareto analysis

Contract: `decision-capability-v1`

No universal winner declared. Dominance is per-capability only. Pareto runs over pareto_eligible candidates (safe + competent + VALIDATED evidence).

## rlm.worker_needed

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** decider_2b=0%, julia_1=0%, laya_421m=0%, llama_http:functiongemma_270m_q8=0%, llama_http:hammer21_15b_q4=0%, llama_http:qwen3_06b_q8=0%, llama_http:qwen3_17b_q4=0%, nanojev_06b=0%, openjev_06b=0%

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|

## tool_family_select

**Capability baselines:** trivial=0.125, competence threshold=0.175 (margin=0.05, n_fixtures=77, validated_min=50)

**Pareto-optimal (eligible only):** laya_421m, openjev_06b, llama_http:qwen3_06b_q8, llama_http:qwen3_17b_q4, llama_http:hammer21_15b_q4
**Bootstrap Pareto inclusion:** llama_http:qwen3_06b_q8=100%, llama_http:qwen3_17b_q4=100%, llama_http:hammer21_15b_q4=86%, julia_1=3%, decider_2b=0%, laya_421m=0%, llama_http:functiongemma_270m_q8=0%, nanojev_06b=0%, openjev_06b=0%
**Measured but ineligible:** julia_1, llama_http:functiongemma_270m_q8

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|
| decider_2b | 77 | 0.403 | 0.125 | 0.175 | 0.000 | VALIDATED | pareto_eligible | 19519.355311000254 | 0.7859 | eligible |
| julia_1 | 77 | 0.104 | 0.125 | 0.175 | 0.000 | VALIDATED | measured_but_ineligible (below_competence_floor) | 953.4032899973681 | 1.5479 | measured_but_ineligible |
| laya_421m | 77 | 0.195 | 0.125 | 0.175 | 0.000 | VALIDATED | pareto_eligible | 6234.562204997928 | 1.0080 | optimal |
| llama_http:functiongemma_270m_q8 | 77 | 0.000 | 0.125 | 0.175 | 0.000 | VALIDATED | measured_but_ineligible (below_competence_floor) | 629.2374969998491 | 1.1374 | measured_but_ineligible |
| llama_http:hammer21_15b_q4 | 77 | 0.468 | 0.125 | 0.175 | 0.000 | VALIDATED | pareto_eligible | 4685.756614002457 | 0.7557 | optimal |
| llama_http:qwen3_06b_q8 | 77 | 0.364 | 0.125 | 0.175 | 0.000 | VALIDATED | pareto_eligible | 1152.5687180037494 | 0.8922 | optimal |
| llama_http:qwen3_17b_q4 | 77 | 0.571 | 0.125 | 0.175 | 0.000 | VALIDATED | pareto_eligible | 5067.826211998181 | 0.6655 | optimal |
| nanojev_06b | 77 | 0.182 | 0.125 | 0.175 | 0.000 | VALIDATED | pareto_eligible | 19652.289826000924 | 0.8396 | eligible |
| openjev_06b | 77 | 0.416 | 0.125 | 0.175 | 0.000 | VALIDATED | pareto_eligible | 6582.700762999593 | 0.8516 | optimal |

**Dominated by (among eligible):**
- `decider_2b` ← `openjev_06b`
- `nanojev_06b` ← `laya_421m`, `openjev_06b`, `decider_2b`

## retry_or_escalate

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** decider_2b=0%, julia_1=0%, laya_421m=0%, llama_http:functiongemma_270m_q8=0%, llama_http:hammer21_15b_q4=0%, llama_http:qwen3_06b_q8=0%, llama_http:qwen3_17b_q4=0%, nanojev_06b=0%, openjev_06b=0%

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|

## context_compress_needed

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** decider_2b=0%, julia_1=0%, laya_421m=0%, llama_http:functiongemma_270m_q8=0%, llama_http:hammer21_15b_q4=0%, llama_http:qwen3_06b_q8=0%, llama_http:qwen3_17b_q4=0%, nanojev_06b=0%, openjev_06b=0%

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|

## verification_needed

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** decider_2b=0%, julia_1=0%, laya_421m=0%, llama_http:functiongemma_270m_q8=0%, llama_http:hammer21_15b_q4=0%, llama_http:qwen3_06b_q8=0%, llama_http:qwen3_17b_q4=0%, nanojev_06b=0%, openjev_06b=0%

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|

