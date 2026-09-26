# Decision backend Pareto analysis

Contract: `decision-capability-v1`

No universal winner declared. Dominance is per-capability only. Pareto runs over pareto_eligible candidates (safe + competent + VALIDATED evidence).

## rlm.worker_needed

**Capability baselines:** trivial=0.333, competence threshold=0.600 (margin=0.05, n_fixtures=3, validated_min=50)

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** nanojev_06b=0%
**Excluded (dangerous false > 0):** nanojev_06b

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|
| nanojev_06b | 3 | 0.333 | 0.333 | 0.600 | 0.333 | PROVISIONAL | excluded_unsafe (dangerous_false) | 33.37120200012578 | 0.8947 | excluded_unsafe |

## tool_family_select

**Capability baselines:** trivial=0.125, competence threshold=0.175 (margin=0.05, n_fixtures=2, validated_min=50)

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** nanojev_06b=0%
**Measured but ineligible:** nanojev_06b

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|
| nanojev_06b | 2 | 0.000 | 0.125 | 0.175 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 34.567631999379955 | 0.9791 | measured_but_ineligible |

## retry_or_escalate

**Capability baselines:** trivial=0.250, competence threshold=0.500 (margin=0.05, n_fixtures=2, validated_min=50)

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** nanojev_06b=0%
**Excluded (dangerous false > 0):** nanojev_06b

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|
| nanojev_06b | 2 | 0.500 | 0.250 | 0.500 | 0.500 | PROVISIONAL | excluded_unsafe (dangerous_false) | 39.258974997210316 | 0.5467 | excluded_unsafe |

## context_compress_needed

**Capability baselines:** trivial=0.333, competence threshold=0.383 (margin=0.05, n_fixtures=2, validated_min=50)

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** nanojev_06b=0%
**Measured but ineligible:** nanojev_06b

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|
| nanojev_06b | 2 | 0.500 | 0.333 | 0.383 | 0.000 | PROVISIONAL | measured_but_ineligible (provisional_evidence) | 33.178941997903166 | 0.7717 | measured_but_ineligible |

## verification_needed

**Capability baselines:** trivial=0.500, competence threshold=0.550 (margin=0.05, n_fixtures=2, validated_min=50)

**Pareto-optimal (eligible only):** (empty — no eligible candidates)
**Bootstrap Pareto inclusion:** nanojev_06b=0%
**Measured but ineligible:** nanojev_06b

| backend | n | acc | baseline | threshold | dang | evidence | eligibility | p50 ms | Brier | Pareto |
|---------|---|-----|----------|-----------|------|----------|-------------|--------|-------|--------|
| nanojev_06b | 2 | 0.500 | 0.500 | 0.550 | 0.000 | PROVISIONAL | measured_but_ineligible (below_competence_floor) | 30.17796350104618 | 0.6039 | measured_but_ineligible |

