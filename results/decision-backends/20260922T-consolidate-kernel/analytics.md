# Decision bench analytics

## Reliability

- rows total: 88
- rows ok: 55
- rows unavailable: 33
- rows error: 0

## Quality by capability

### context_compress_needed
- n=10 accuracy=0.4 dangerous=0.0

### retry_or_escalate
- n=10 accuracy=0.5 dangerous=0.5

### rlm.worker_needed
- n=15 accuracy=0.5333333333333333 dangerous=0.13333333333333333

### tool_family_select
- n=10 accuracy=0.6 dangerous=0.0

### verification_needed
- n=10 accuracy=0.6 dangerous=0.0

## Measurement coverage

```
SYSTEM TOKENOMICS COVERAGE · 7d

Traces
────────────────────────────────────────
Traces                       88
Events                       231

Physical usage
────────────────────────────────────────
Incremental provider-metered 44
Zero local (metered)         0
Aggregate-only               0
Unmetered                    44
Physical coverage            50.0%

Counterfactuals
────────────────────────────────────────
Paired measured              0
Estimated                    0
Missing                      88
Counterfactual coverage      0.0%

Outcomes
────────────────────────────────────────
Verified/gold                29
Negative                     26
Execution-only               0
Unknown                      33
Verified coverage            33.0%

Measurement levels
────────────────────────────────────────
M3                           0
M2                           44
M1                           0
M0                           44

By mechanism
────────────────────────────────────────
routing          traces   88  metered   44  baseline_cov 0.0%
other            traces   48  metered   24  baseline_cov 0.0%
rlm              traces   24  metered   12  baseline_cov 0.0%
context          traces   16  metered    8  baseline_cov 0.0%
omp              traces   16  metered    8  baseline_cov 0.0%

FRONTIER COMPUTE (trace rollup — not mechanism sum)
────────────────────────────────────────
Actual frontier tokens       5687
Measured avoided             0
Estimated avoided            0
Unknown baseline traces      88

FLOW (separate lane)
────────────────────────────────────────
Prepare events               0
Net prepare value            0.0 ms
Speculation overhead         0.0 ms

Highest-value measurement gaps
────────────────────────────────────────
rlm          rlm.worker_needed      n=24   baseline_cov=0%  EIV=high
routing      rlm.worker_needed      n=24   baseline_cov=0%  EIV=high
other        tool_family_select     n=16   baseline_cov=0%  EIV=high
routing      tool_family_select     n=16   baseline_cov=0%  EIV=high
other        retry_or_escalate      n=16   baseline_cov=0%  EIV=high
routing      retry_or_escalate      n=16   baseline_cov=0%  EIV=high
other        verification_needed    n=16   baseline_cov=0%  EIV=high
routing      verification_needed    n=16   baseline_cov=0%  EIV=high

Period 2026-09-16T02:11:21.094189+00:00 → 2026-09-23T02:11:21.094189+00:00
Coverage does not alter savings totals.
```
