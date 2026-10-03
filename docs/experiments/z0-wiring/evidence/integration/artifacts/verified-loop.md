# Verified loop v0 -- result

Pre-registration: `docs/prereg/verified-loop-v0.md`. Table `0.1.0`, feature schema `69d7eef44ba1707e`. Metrics and counts only.

## Decision: **INSUFFICIENT_DATA** (analysis is descriptive only)

| sufficiency check | observed | passed |
|---|---|---|
| rows>=300 | 0 | False |
| not_success>=30 | 0 | False |
| groups>=10 | 0 | False |
| k==5_and_every_test_fold_has_both_classes | k=None | False |

Input rows 4; analysis rows 0; excluded {'unverified_or_no_label': 4}.


Sensitivity (unverified counted as not-success, 4 rows, not decisional): G 1.000 vs L_cov 1.000, AUROC n/a.

## Data-volume projection

- unavailable: no manifest.sweep (run z0int outcomes export --sweep-since 7d)
