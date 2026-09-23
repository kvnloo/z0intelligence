# Decision backend Pareto analysis

Contract: `decision-capability-v1`

No universal winner declared. Dominance is per-capability only. Pareto runs over pareto_eligible candidates (safe + competent + VALIDATED evidence).

## rlm.worker_needed

**Capability baselines:** trivial=0.333, competence threshold=0.600 (margin=0.05, n_fixtures=3, validated_min=50)

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** decider_2b=0%, laya_421m=0%, local_mb=0%, nanojev_06b=0%, openjev_06b=0%, openjev_4b=0%, reflex=0%, system_one_4b=0%
**Excluded (dangerous false > 0):** nanojev_06b, openjev_06b
**Measured but ineligible:** laya_421m, decider_2b, openjev_4b

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|
| decider_2b | 3 | 0.667 | 0.333 | 0.600 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 20.858747000033873 | 0.4949 | measured_but_ineligible |
| laya_421m | 3 | 0.333 | 0.333 | 0.600 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 249.91936099999634 | 0.6354 | measured_but_ineligible |
| nanojev_06b | 3 | 0.333 | 0.333 | 0.600 | 0.333 | PROVISIONAL | excluded_unsafe (dangerous_false) | 41.42678799996702 | 0.8947 | excluded_unsafe |
| openjev_06b | 3 | 0.667 | 0.333 | 0.600 | 0.333 | PROVISIONAL | excluded_unsafe (dangerous_false) | 29.61104600001363 | 0.6786 | excluded_unsafe |
| openjev_4b | 3 | 0.667 | 0.333 | 0.600 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 64.28620200006208 | 0.2936 | measured_but_ineligible |

## tool_family_select

**Capability baselines:** trivial=0.125, competence threshold=0.175 (margin=0.05, n_fixtures=2, validated_min=50)

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** decider_2b=0%, laya_421m=0%, local_mb=0%, nanojev_06b=0%, openjev_06b=0%, openjev_4b=0%, reflex=0%, system_one_4b=0%
**Measured but ineligible:** laya_421m, decider_2b, nanojev_06b, openjev_06b, openjev_4b

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|
| decider_2b | 2 | 1.000 | 0.125 | 0.175 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 93976.49905800003 | 0.0380 | measured_but_ineligible |
| laya_421m | 2 | 1.000 | 0.125 | 0.175 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 273.5147559999973 | 0.2538 | measured_but_ineligible |
| nanojev_06b | 2 | 0.000 | 0.125 | 0.175 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 41.59153749998268 | 0.9791 | measured_but_ineligible |
| openjev_06b | 2 | 0.000 | 0.125 | 0.175 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 31.14854800003286 | 1.5919 | measured_but_ineligible |
| openjev_4b | 2 | 1.000 | 0.125 | 0.175 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 66.09755249996851 | 0.0728 | measured_but_ineligible |

## retry_or_escalate

**Capability baselines:** trivial=0.250, competence threshold=0.500 (margin=0.05, n_fixtures=2, validated_min=50)

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** decider_2b=0%, laya_421m=0%, local_mb=0%, nanojev_06b=0%, openjev_06b=0%, openjev_4b=0%, reflex=0%, system_one_4b=0%
**Excluded (dangerous false > 0):** laya_421m, decider_2b, nanojev_06b, openjev_06b, openjev_4b

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|
| decider_2b | 2 | 0.500 | 0.250 | 0.500 | 0.500 | PROVISIONAL | excluded_unsafe (dangerous_false) | 20.186130999945817 | 0.5385 | excluded_unsafe |
| laya_421m | 2 | 0.500 | 0.250 | 0.500 | 0.500 | PROVISIONAL | excluded_unsafe (dangerous_false) | 212.04048549999754 | 0.8889 | excluded_unsafe |
| nanojev_06b | 2 | 0.500 | 0.250 | 0.500 | 0.500 | PROVISIONAL | excluded_unsafe (dangerous_false) | 44.41792249997434 | 0.5467 | excluded_unsafe |
| openjev_06b | 2 | 0.500 | 0.250 | 0.500 | 0.500 | PROVISIONAL | excluded_unsafe (dangerous_false) | 27.883037000037802 | 0.9999 | excluded_unsafe |
| openjev_4b | 2 | 0.500 | 0.250 | 0.500 | 0.500 | PROVISIONAL | excluded_unsafe (dangerous_false) | 62.03273350001837 | 0.4424 | excluded_unsafe |

## context_compress_needed

**Capability baselines:** trivial=0.333, competence threshold=0.383 (margin=0.05, n_fixtures=2, validated_min=50)

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** decider_2b=0%, laya_421m=0%, local_mb=0%, nanojev_06b=0%, openjev_06b=0%, openjev_4b=0%, reflex=0%, system_one_4b=0%
**Measured but ineligible:** laya_421m, decider_2b, nanojev_06b, openjev_06b, openjev_4b

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|
| decider_2b | 2 | 0.500 | 0.333 | 0.383 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 19.767197000021497 | 0.6148 | measured_but_ineligible |
| laya_421m | 2 | 0.000 | 0.333 | 0.383 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 202.58450550001328 | 0.7104 | measured_but_ineligible |
| nanojev_06b | 2 | 0.500 | 0.333 | 0.383 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 35.237161999987165 | 0.7717 | measured_but_ineligible |
| openjev_06b | 2 | 0.500 | 0.333 | 0.383 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 26.74680500001614 | 1.0000 | measured_but_ineligible |
| openjev_4b | 2 | 0.500 | 0.333 | 0.383 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 60.885736999978235 | 0.5897 | measured_but_ineligible |

## verification_needed

**Capability baselines:** trivial=0.500, competence threshold=0.550 (margin=0.05, n_fixtures=2, validated_min=50)

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** decider_2b=0%, laya_421m=0%, local_mb=0%, nanojev_06b=0%, openjev_06b=0%, openjev_4b=0%, reflex=0%, system_one_4b=0%
**Measured but ineligible:** laya_421m, decider_2b, nanojev_06b, openjev_06b, openjev_4b

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|
| decider_2b | 2 | 0.500 | 0.500 | 0.550 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 20.121458999938113 | 0.4879 | measured_but_ineligible |
| laya_421m | 2 | 1.000 | 0.500 | 0.550 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 197.3226469999929 | 0.3654 | measured_but_ineligible |
| nanojev_06b | 2 | 0.500 | 0.500 | 0.550 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 44.76687449999872 | 0.6039 | measured_but_ineligible |
| openjev_06b | 2 | 0.500 | 0.500 | 0.550 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 28.26229900006183 | 0.9807 | measured_but_ineligible |
| openjev_4b | 2 | 0.500 | 0.500 | 0.550 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 62.32142499999327 | 0.6742 | measured_but_ineligible |

