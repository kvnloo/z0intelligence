# Decision bench analytics

## Reliability

- rows total: 693
- rows ok: 693
- rows unavailable: 0
- rows error: 0

## Quality by capability

### tool_family_select
- n=693 accuracy=0.30014430014430016 dangerous=0.0

## Measurement coverage

```
SYSTEM TOKENOMICS COVERAGE · 7d

Traces
────────────────────────────────────────
Traces                       693
Events                       2079

Physical usage
────────────────────────────────────────
Incremental provider-metered 231
Zero local (metered)         0
Aggregate-only               0
Unmetered                    462
Physical coverage            33.3%

Counterfactuals
────────────────────────────────────────
Paired measured              0
Estimated                    0
Missing                      693
Counterfactual coverage      0.0%

Outcomes
────────────────────────────────────────
Verified/gold                208
Negative                     485
Execution-only               0
Unknown                      0
Verified coverage            30.0%

Measurement levels
────────────────────────────────────────
M3                           0
M2                           231
M1                           0
M0                           462

By mechanism
────────────────────────────────────────
other            traces  693  metered  231  baseline_cov 0.0%
routing          traces  693  metered  231  baseline_cov 0.0%

FRONTIER COMPUTE (trace rollup — not mechanism sum)
────────────────────────────────────────
Actual frontier tokens       50940
Measured avoided             0
Estimated avoided            0
Unknown baseline traces      693

FLOW (separate lane)
────────────────────────────────────────
Prepare events               0
Net prepare value            0.0 ms
Speculation overhead         0.0 ms

Highest-value measurement gaps
────────────────────────────────────────
other        tool_family_select     n=693  baseline_cov=0%  EIV=high
routing      tool_family_select     n=693  baseline_cov=0%  EIV=high

Period 2026-09-23T17:14:20.833484+00:00 → 2026-09-30T17:14:20.833484+00:00
Coverage does not alter savings totals.
```
