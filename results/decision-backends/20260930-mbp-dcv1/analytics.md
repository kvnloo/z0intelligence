# Decision bench analytics

## Reliability

- rows total: 99
- rows ok: 99
- rows unavailable: 0
- rows error: 0

## Quality by capability

### context_compress_needed
- n=18 accuracy=0.3888888888888889 dangerous=0.1111111111111111

### retry_or_escalate
- n=18 accuracy=0.5555555555555556 dangerous=0.3333333333333333

### rlm.worker_needed
- n=27 accuracy=0.4444444444444444 dangerous=0.14814814814814814

### tool_family_select
- n=18 accuracy=0.4444444444444444 dangerous=0.0

### verification_needed
- n=18 accuracy=0.6111111111111112 dangerous=0.0

## Measurement coverage

```
SYSTEM TOKENOMICS COVERAGE · 7d

Traces
────────────────────────────────────────
Traces                       99
Events                       297

Physical usage
────────────────────────────────────────
Incremental provider-metered 33
Zero local (metered)         0
Aggregate-only               0
Unmetered                    66
Physical coverage            33.3%

Counterfactuals
────────────────────────────────────────
Paired measured              0
Estimated                    0
Missing                      99
Counterfactual coverage      0.0%

Outcomes
────────────────────────────────────────
Verified/gold                48
Negative                     51
Execution-only               0
Unknown                      0
Verified coverage            48.5%

Measurement levels
────────────────────────────────────────
M3                           0
M2                           33
M1                           0
M0                           66

By mechanism
────────────────────────────────────────
routing          traces   99  metered   33  baseline_cov 0.0%
other            traces   54  metered   18  baseline_cov 0.0%
rlm              traces   27  metered    9  baseline_cov 0.0%
context          traces   18  metered    6  baseline_cov 0.0%
omp              traces   18  metered    6  baseline_cov 0.0%

FRONTIER COMPUTE (trace rollup — not mechanism sum)
────────────────────────────────────────
Actual frontier tokens       3874
Measured avoided             0
Estimated avoided            0
Unknown baseline traces      99

FLOW (separate lane)
────────────────────────────────────────
Prepare events               0
Net prepare value            0.0 ms
Speculation overhead         0.0 ms

Highest-value measurement gaps
────────────────────────────────────────
rlm          rlm.worker_needed      n=27   baseline_cov=0%  EIV=high
routing      rlm.worker_needed      n=27   baseline_cov=0%  EIV=high
other        tool_family_select     n=18   baseline_cov=0%  EIV=high
routing      tool_family_select     n=18   baseline_cov=0%  EIV=high
other        retry_or_escalate      n=18   baseline_cov=0%  EIV=high
routing      retry_or_escalate      n=18   baseline_cov=0%  EIV=high
other        verification_needed    n=18   baseline_cov=0%  EIV=high
routing      verification_needed    n=18   baseline_cov=0%  EIV=high

Period 2026-09-23T15:41:49.642569+00:00 → 2026-09-30T15:41:49.642569+00:00
Coverage does not alter savings totals.
```
