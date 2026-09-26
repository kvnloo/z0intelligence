# Decision bench analytics

## Reliability

- rows total: 11
- rows ok: 11
- rows unavailable: 0
- rows error: 0

## Quality by capability

### context_compress_needed
- n=2 accuracy=0.5 dangerous=0.0

### retry_or_escalate
- n=2 accuracy=0.5 dangerous=0.5

### rlm.worker_needed
- n=3 accuracy=0.3333333333333333 dangerous=0.3333333333333333

### tool_family_select
- n=2 accuracy=0.0 dangerous=0.0

### verification_needed
- n=2 accuracy=0.5 dangerous=0.0

## Measurement coverage

```
SYSTEM TOKENOMICS COVERAGE · 7d

Traces
────────────────────────────────────────
Traces                       11
Events                       33

Physical usage
────────────────────────────────────────
Incremental provider-metered 0
Zero local (metered)         0
Aggregate-only               0
Unmetered                    11
Physical coverage            0.0%

Counterfactuals
────────────────────────────────────────
Paired measured              0
Estimated                    0
Missing                      11
Counterfactual coverage      0.0%

Outcomes
────────────────────────────────────────
Verified/gold                4
Negative                     7
Execution-only               0
Unknown                      0
Verified coverage            36.4%

Measurement levels
────────────────────────────────────────
M3                           0
M2                           0
M1                           0
M0                           11

By mechanism
────────────────────────────────────────
routing          traces   11  metered    0  baseline_cov 0.0%
other            traces    6  metered    0  baseline_cov 0.0%
rlm              traces    3  metered    0  baseline_cov 0.0%
context          traces    2  metered    0  baseline_cov 0.0%
omp              traces    2  metered    0  baseline_cov 0.0%

FRONTIER COMPUTE (trace rollup — not mechanism sum)
────────────────────────────────────────
Actual frontier tokens       0
Measured avoided             0
Estimated avoided            0
Unknown baseline traces      11

FLOW (separate lane)
────────────────────────────────────────
Prepare events               0
Net prepare value            0.0 ms
Speculation overhead         0.0 ms

Highest-value measurement gaps
────────────────────────────────────────
rlm          rlm.worker_needed      n=3    baseline_cov=0%  EIV=high
routing      rlm.worker_needed      n=3    baseline_cov=0%  EIV=high
context      context_compress_needed n=2    baseline_cov=0%  EIV=high
omp          context_compress_needed n=2    baseline_cov=0%  EIV=high
other        verification_needed    n=2    baseline_cov=0%  EIV=high
other        retry_or_escalate      n=2    baseline_cov=0%  EIV=high
routing      verification_needed    n=2    baseline_cov=0%  EIV=high
routing      retry_or_escalate      n=2    baseline_cov=0%  EIV=high

Period 2026-09-14T08:10:46.012016+00:00 → 2026-09-21T08:10:46.012016+00:00
Coverage does not alter savings totals.
```
