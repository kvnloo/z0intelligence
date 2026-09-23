# Bootstrap Pareto stability

Inclusion probability is the fraction of fixture-resampled datasets in which a backend remained on the eligible Pareto frontier. At small n this can be unstable — treat provisional evidence seriously.

Bootstrap draws: 500 / 500 (seed=0)

## context_compress_needed

| backend | P(Pareto) | P(eligible) |
|---------|-----------|-------------|
| decider_2b | 0.000 | 0.000 |
| laya_421m | 0.000 | 0.000 |
| local_mb | 0.000 | 0.000 |
| nanojev_06b | 0.000 | 0.000 |
| openjev_06b | 0.000 | 0.000 |
| openjev_4b | 0.000 | 0.000 |
| reflex | 0.000 | 0.000 |
| system_one_4b | 0.000 | 0.000 |

## retry_or_escalate

| backend | P(Pareto) | P(eligible) |
|---------|-----------|-------------|
| decider_2b | 0.000 | 0.000 |
| laya_421m | 0.000 | 0.000 |
| local_mb | 0.000 | 0.000 |
| nanojev_06b | 0.000 | 0.000 |
| openjev_06b | 0.000 | 0.000 |
| openjev_4b | 0.000 | 0.000 |
| reflex | 0.000 | 0.000 |
| system_one_4b | 0.000 | 0.000 |

## rlm.worker_needed

| backend | P(Pareto) | P(eligible) |
|---------|-----------|-------------|
| decider_2b | 0.000 | 0.000 |
| laya_421m | 0.000 | 0.000 |
| local_mb | 0.000 | 0.000 |
| nanojev_06b | 0.000 | 0.000 |
| openjev_06b | 0.000 | 0.000 |
| openjev_4b | 0.000 | 0.000 |
| reflex | 0.000 | 0.000 |
| system_one_4b | 0.000 | 0.000 |

## tool_family_select

| backend | P(Pareto) | P(eligible) |
|---------|-----------|-------------|
| decider_2b | 0.000 | 0.000 |
| laya_421m | 0.000 | 0.000 |
| local_mb | 0.000 | 0.000 |
| nanojev_06b | 0.000 | 0.000 |
| openjev_06b | 0.000 | 0.000 |
| openjev_4b | 0.000 | 0.000 |
| reflex | 0.000 | 0.000 |
| system_one_4b | 0.000 | 0.000 |

## verification_needed

| backend | P(Pareto) | P(eligible) |
|---------|-----------|-------------|
| decider_2b | 0.000 | 0.000 |
| laya_421m | 0.000 | 0.000 |
| local_mb | 0.000 | 0.000 |
| nanojev_06b | 0.000 | 0.000 |
| openjev_06b | 0.000 | 0.000 |
| openjev_4b | 0.000 | 0.000 |
| reflex | 0.000 | 0.000 |
| system_one_4b | 0.000 | 0.000 |

# Measurement uncertainty

## decider_2b

- `context_compress_needed`: n=2 acc=0.5 CI95=[0.09452865480086614, 0.9054713451991339] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0
- `retry_or_escalate`: n=2 acc=0.5 CI95=[0.09452865480086614, 0.9054713451991339] dangerous_false=0.5 upper95=0.9054713451991339
- `rlm.worker_needed`: n=3 acc=0.6666666666666666 CI95=[0.20765495512648796, 0.9385096847238394] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0
- `tool_family_select`: n=2 acc=1.0 CI95=[0.34237195288961925, 1.0] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0
- `verification_needed`: n=2 acc=0.5 CI95=[0.09452865480086614, 0.9054713451991339] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0

## laya_421m

- `context_compress_needed`: n=2 acc=0.0 CI95=[0.0, 0.6576280471103807] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0
- `retry_or_escalate`: n=2 acc=0.5 CI95=[0.09452865480086614, 0.9054713451991339] dangerous_false=0.5 upper95=0.9054713451991339
- `rlm.worker_needed`: n=3 acc=0.3333333333333333 CI95=[0.061490315276160556, 0.792345044873512] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0
- `tool_family_select`: n=2 acc=1.0 CI95=[0.34237195288961925, 1.0] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0
- `verification_needed`: n=2 acc=1.0 CI95=[0.34237195288961925, 1.0] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0

## nanojev_06b

- `context_compress_needed`: n=2 acc=0.5 CI95=[0.09452865480086614, 0.9054713451991339] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0
- `retry_or_escalate`: n=2 acc=0.5 CI95=[0.09452865480086614, 0.9054713451991339] dangerous_false=0.5 upper95=0.9054713451991339
- `rlm.worker_needed`: n=3 acc=0.3333333333333333 CI95=[0.061490315276160556, 0.792345044873512] dangerous_false=0.3333333333333333 upper95=0.792345044873512
- `tool_family_select`: n=2 acc=0.0 CI95=[0.0, 0.6576280471103807] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0
- `verification_needed`: n=2 acc=0.5 CI95=[0.09452865480086614, 0.9054713451991339] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0

## openjev_06b

- `context_compress_needed`: n=2 acc=0.5 CI95=[0.09452865480086614, 0.9054713451991339] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0
- `retry_or_escalate`: n=2 acc=0.5 CI95=[0.09452865480086614, 0.9054713451991339] dangerous_false=0.5 upper95=0.9054713451991339
- `rlm.worker_needed`: n=3 acc=0.6666666666666666 CI95=[0.20765495512648796, 0.9385096847238394] dangerous_false=0.3333333333333333 upper95=0.792345044873512
- `tool_family_select`: n=2 acc=0.0 CI95=[0.0, 0.6576280471103807] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0
- `verification_needed`: n=2 acc=0.5 CI95=[0.09452865480086614, 0.9054713451991339] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0

## openjev_4b

- `context_compress_needed`: n=2 acc=0.5 CI95=[0.09452865480086614, 0.9054713451991339] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0
- `retry_or_escalate`: n=2 acc=0.5 CI95=[0.09452865480086614, 0.9054713451991339] dangerous_false=0.5 upper95=0.9054713451991339
- `rlm.worker_needed`: n=3 acc=0.6666666666666666 CI95=[0.20765495512648796, 0.9385096847238394] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0
- `tool_family_select`: n=2 acc=1.0 CI95=[0.34237195288961925, 1.0] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0
- `verification_needed`: n=2 acc=0.5 CI95=[0.09452865480086614, 0.9054713451991339] dangerous_false=0.0 upper95=1.0
  - Zero observed dangerous failures does not imply true rate is 0; upper bound ≈ 1.0

