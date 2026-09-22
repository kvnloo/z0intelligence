# Steal sweep — nanojev vs hammer3b, matched candidate sets

`evidence_class = exploratory_beta`. n=28 tasks. Not promotion evidence.

## What this measures

For each of the 28 frozen Phase 2 tasks, nanojev is offered **exactly** the
`legal_actions` the deterministic compiler offered hammer3b in the Phase 1B run.
The same menu, not a re-derived one.

That matters. The J1 shadow pilot reported 48.1% agreement that turned out to be a
mismatched candidate set (matched → 100%). This sweep was built with that failure in
mind, and it caught the same class of error in my own earlier NanoJev measurement:
the 43.6% I reported in the beta sprint was an artifact of the wrong candidate set
and state shape. On matched menus it is 73–94%.

## Result

baseline **compiler+hammer2.1_3b: 25/28 = 0.893**

nanojev alone, latency p50 **34.7 ms**, 0 network calls:

| threshold | coverage | success\|covered | wrong | escalated |
|---|---|---|---|---|
| 0.0 | 26/28 (92.9%) | 73.1% | 7 | 2 |
| 0.5 | 21/28 (75.0%) | 76.2% | 5 | 7 |
| **0.6** | **17/28 (60.7%)** | **94.1%** | **1** | **11** |
| 0.7 | 10/28 (35.7%) | 100% | 0 | 18 |
| 0.9 | 3/28 (10.7%) | 100% | 0 | 25 |

cascade `nanojev → hammer3b`, end-to-end against hammer3b alone:

| threshold | hammer calls eliminated | cheap-ok | escalated-ok | end-to-end | vs hammer |
|---|---|---|---|---|---|
| **0.6** | **17/28 = 60.7%** | 16 | 10 | **26/28 = 0.929** | **+0.036** |
| 0.7 | 10/28 = 35.7% | 10 | 16 | 26/28 = 0.929 | +0.036 |
| 0.9 | 3/28 = 10.7% | 3 | 23 | 26/28 = 0.929 | +0.036 |
| 0.95 | 0/28 | 0 | 25 | 25/28 = 0.893 | 0 |

**At threshold 0.6 the cascade removes 60.7% of hammer3b calls and end-to-end
accuracy goes up, not down** (0.893 → 0.929): nanojev answers one task correctly
that hammer3b gets wrong, and hammer3b covers the remaining 11.

## Caveats

* Two tasks have a single legal action. nanojev refuses them
  (`choice requires 2..255 options`), which is correct — a one-option menu is not a
  decision, and those 2 are the permanent floor on any coverage number here.
* Risk in this sweep is **wrong answers, not danger**. The corpus is compiler-first
  only, so `dangerous_selected` is false on all 420 episodes by construction. There
  is no safety signal to sweep and none is implied. Three declared dangerous options
  exist across the fixtures; none was ever selected.
* n=28, gold labels only, no second human audit beyond the sealed 15.
