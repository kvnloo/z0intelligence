# SLM improvement methods

## Conclusion

**Pairwise BT is not the best measured SLM process, but lower accuracy does not automatically make it uneconomic.** With exact reconstructed native context and batched comparisons, tool BT costs231.5ms/decision and has expected recovery-proxy cost5511ms, below binary6065ms despite only3/24 correct. A real three-choice tournament is better here:10/24 correct,126.7ms,425tokens/decision, expected proxy3647ms. Path-kind BT loses. These are small, noisy counterfactual samples, not authorization or generalization to every SLM.

No candidate is promoted. Main subsequently authorized measured recovery **proxy** E=6034 ms and Sol frontier F=7999 ms; these are used in the updated pipeline leaderboard, not a claim of measured causal recovery cost. No active release was modified.

## Matched real-label experiment

`measure.py` reads `~/.z0int/loop/shadow-learning.sqlite` with SQLite `mode=ro`. Tool labels are the native exact joins in `labels`; path-kind labels are exact `(session,call,family)` jobs from `family_jobs`. Seed 20261025 selects 24 rows per family. Every method receives the same selected rows and candidates. Candidates use strictly earlier labels, never the current gold. Method execution order is randomized per row. Raw trace contents are not exported. `measured.json` contains opaque join IDs, aggregate accounting, and correctness only.

Model: existing z0-farm `qwen3-0.6b-q8`, no model reload or serving configuration change. Evidence class: historical `replayed_L1`, not prospective task success. Tool projection is a history-only reconstruction of the goal-binary scoring primitive, **not the original live goal-context payload**. Therefore do not compare its percentage directly to the live 414/1763 figure.

BT uses two-option yes/no log-odds, seeded randomized pair orientation and round robin; larger shortlists use a connected bounded comparison graph. Strengths use regularized Bradley–Terry MM. Hierarchy first selects one of at most three stable semantic categories, then one of at most three members. Vote3 uses three temperature-0.8 samples with different seeds, majority including abstention. Goal binary uses the existing `score_choices` implementation and .25 margin. History-bigram uses the existing previous-history transition vote with .8 confidence threshold. All options are narrowed to at most three before these comparisons. Candidate recall limits all methods.

Accuracy below is unconditional correct / 24 (abstentions count as incorrect); intervals are Wilson 95%. Token and wall columns are **totals for 24 rows**, not means. Every row had zero transport/parser exceptions.

| Family | Method | Correct | Accuracy [95% CI] | Abstain | Tokens | Wall ms |
|---|---|---:|---|---:|---:|---:|
| tool | goal binary | 7/24 | 29.2% [14.9,49.2] | 33.3% | 10,851 | 2,068.4 |
| tool | BT | 3/24 | 12.5% [4.3,31.0] | 0% | 12,498 | 2,505.3 |
| tool | hierarchy | 8/24 | 33.3% [18.0,53.3] | 20.8% | 11,591 | 2,232.8 |
| tool | vote3 | 7/24 | 29.2% [14.9,49.2] | 33.3% | 32,553 | 6,010.1 |
| tool | history bigram | 1/24 | 4.2% [0.7,20.2] | 91.7% | 0 | 3.1 |
| path kind | goal binary | 15/24 | 62.5% [42.7,78.8] | 16.7% | 12,771 | 2,032.4 |
| path kind | BT | 0/24 | 0% [0,13.8] | 0% | 14,496 | 2,800.6 |
| path kind | hierarchy | 9/24 | 37.5% [21.2,57.3] | 50.0% | 11,847 | 1,929.2 |
| path kind | vote3 | 15/24 | 62.5% [42.7,78.8] | 16.7% | 38,313 | 6,492.0 |
| path kind | history bigram | 15/24 | 62.5% [42.7,78.8] | 33.3% | 0 | 3.8 |

### Expected cost, not accuracy promotion

Let E be the measured marginal read-only error cost and F frontier cost, in the same pace-adjusted latency units as the call cost. Use RiskBar's `c_call + (1-p)*E + abstain*F`, with p the accuracy conditional on answering. In the lambda=0 latency special case the measured coefficients are:

| Family | Method | Expected cost per decision (ms units for E,F) |
|---|---|---|
| tool | goal binary | 86.18 + .5625 E + .3333 F |
| tool | BT | 104.39 + .875 E |
| tool | hierarchy | 93.03 + .57895 E + .20833 F |
| tool | vote3 | 250.42 + .5625 E + .3333 F |
| tool | history bigram | .127 + .5 E + .91667 F |
| path kind | goal binary | 84.68 + .25 E + .16667 F |
| path kind | BT | 116.69 + E |
| path kind | hierarchy | 80.38 + .25 E + .5 F |
| path kind | vote3 | 270.50 + .25 E + .16667 F |
| path kind | history bigram | .158 + .0625 E + .33333 F |

All-abstain has no conditional p: use F, not an invented p. These expressions follow the requested rule literally; a decision-frequency-weighted loss would instead multiply the conditional error term by coverage, so those two interpretations must not be conflated.

RiskBar reported no measured deployment costs. Its observed read-recovery proxy is median 6034 ms / p95 18554 ms, n=196, **whole recovery-message latency, not marginal error cost** (`local://risk-recovery-measurements.json`). It is not silently substituted for E. `decision-policy/src/z0int/policy/compile.py` uses `CompilerConfig.option_capacity_costs[risk]` (`cost_call`, `cost_frontier`, `cost_error`, `abstain_rate`, `measured`, `finding_ids`). Kevin's score additionally needs measured burn, remaining, reset, and calibrated acceptance: latency × (1+lambda·pace) / p_accept. This replay supplies wall time/tokens and teacher agreement, not those missing quantities.

Hierarchy gains one tool answer while losing six path-kind answers. Vote3 adds approximately 3x tokens without an accuracy gain. BT is not a justified candidate winner. History-bigram trades additional escalation for almost free calls; whether that wins depends on F and E.

## Methods inventory and source status

| Method | Observed result / limitation | Source |
|---|---|---|
| Binary per-candidate yes/no logit scoring and deterministic narrowing | Current live tool arm: 414/1763 correct (23.5%), reported by owning learner; cap3. This replay gives 7/24 tool and 15/24 path-kind. | `src/z0int/cognition/shadow_binary.py`; `operations/shadow-learner/repair-live-hour.json` in owning integration |
| Choice-capacity ladder | Prior retained finding: Qwen2.5-0.5B k2 29/29, k3 27/28, k5 20/25, k8 16/27, k12 4/19, k32 1/23, k64 1/16; synthetic tasks, not present exact-label replay. Original report unavailable in this checkout; numbers remain historical, not reverified here. | prior `operations/choice-ladder`; active ChoiceLadderV2 experiment |
| Context injection: none, memory, few-shot, summary, skill relevance, combination | Frozen replay protocol verified; memory and skill arms had empty retrieved packets and cannot demonstrate injection efficacy. Numeric outcome report absent from this checkout. | `/mnt/zer0models/z0-wt/slm-context/operations/slm-context/method.json` |
| Retrieval-only kNN (k1/3/5), margin cascade, training-majority comparator | Included in the context experiment protocol; development chooses k and cascade threshold, test only evaluates; fresh frontier latency was not measured. Numeric outcomes unavailable here. | same `method.json`, `additional_parent_requested_policies` |
| History transitions / bigram | Measured here; highly abstaining tool heuristic, useful path-kind agreement. Not a semantic task-success judge. | `learning_families._baseline`, `learning.predict`, this replay |
| History-width / recent-session projections / few-shot retrieval | Automatic portfolio has history-1/history-3/recent-sessions/few-shot-3 strategies; scope-preserving evaluation and automatic search exist. No per-strategy causal improvement claimed here. | `learning_search.DEFAULT_STRATEGIES`, `learning_runtime._configs` |
| Bradley–Terry pairwise SLM ranking | New measured arm loses to binary on both families; no payoff established. | `slm_methods.py`, `measured.json` |
| Hierarchical semantic categories | New measured arm: +1 correct tool decision, -6 path-kind decisions; not a general winner. | same |
| Self-consistency majority (three samples) | Identical accuracy to binary in both families at roughly triple tokens. | same |
| Jev evidence-grounded pairwise BT | Historical positive ranking control, not an SLM result. Pairwise used 482432 input +7194 output tokens across218 calls; absolute fit-evidence used36360+1163 across27 calls. Partial349/350 run; ranking must not become an accept gate. | `/mnt/zer0models/z0-wt/salvage/agentrun-trial/pairwise/RESULTS.md` |
| Phone-goal BT / knockout / flat hybrids | **Additional located prior trial:** 46 decisions,26 recorded S22 plus20 synthetic, three repeats. Jev flat112/138 correct vs flat+BT122/138, but wrong answers rise6→9; median149→160.5ms; calls1→3.1. Not SLM evidence. | `/mnt/zer0models/hermes-phone-goal/tests/eval/RESULTS.md`, `goalkit/pairwise.py` |
| Shadow-offload families, slm-opt, slm-router | Named historical experiment families, not proven improvements by their names. Results artifacts unavailable in inspected worktrees; must not assign invented numerical outcomes. | historical `operations/shadow-offload`, `operations/slm-opt`, `operations/slm-router` |
| Semantic family skill selector | Historical reported `decider_2b family scores`, not promoted; current result artifact unavailable. | branch `exp/semantic-skill-select` (historical reference) |
| SFT replay reconstruction | Historical branch exists by prior reference; reconstruction is not proof that training improved deployed decisions. No verified accuracy result available here. | `feat/sft-replay-reconstruct` (historical reference) |

## Integration status / missing prerequisites

Predictors are reusable but are **not deployed**. Owner ShadowRepair-2 explicitly froze the already-green active release `fd2297b`; it requested a separately gated next release with immutable method/arm scope and MBP tests before OneLiveCopy consumption. Existing portfolio publication must never relabel accumulated binary observations as another method. Automatic selection must retain counterfactual replay vs prospective evidence separately.

Remaining acceptance gates: independently tested next release; unavailable historical inventory numerical results; GPU admission for nonresident model sizes. E/F are now supplied as explicitly authorized observed proxies, not causal deployment measurements. No hand promotion is permitted.

## Expanded scheduling and context experiment

The first table above is retained as the initial historical-control experiment, not the final protocol. Subsequent `measured-dag.json` submits all independent BT comparisons as one parallel batch, with shared context prefix and `cache_prompt=true`. The next `measured-context.json` reconstructs exact pre-call native goal context through session parentId ancestry, excluding sibling branches, current arguments, and results. All methods receive the identical bounded context. Missing exact context fails the run instead of substituting history-only input.

The expanded `search.py` uses actual earlier tool co-occurrence to learn balanced 3/5/8-category partitions, independently scores categories as yes/no or BT, then scores members as yes/no, pairwise BT, or three-way tournament. Three-way tournaments preserve groups≤3 and feed winners into dependent waves, fitting BT across observed outcomes. Search spans margins0/.25/.75 and only resident model sizes (never loads a new model without admission). Every call is real; no cached experimental RPC latency is mistaken for critical-path timing. `leaderboard.json` separates development and holdout labels, reports category accuracy separately, and sorts on development expected proxy cost. It is not production authorization.

Absolute context: local calls incur **$0 billed API charges**; energy, depreciation and GPU-active seconds are unmeasured, not zero. Measured critical-path wall is reported rather than summing parallel RPC times. Compare with measured Sol median7999ms/turn and TTFT2284ms; a token multiple by itself is not a rejection criterion. E uses observed read recovery median6034ms (mean7543,p9518554,n196,+1call), explicitly a proxy. Formula: measured call-wall + conditional error probability×6034 + abstention rate×7999. Pace-adjusted lambda/burn inputs remain unmeasured.

The proposed portfolio source includes immutable `methods-hierarchy-v1` scope and automatic re-evaluation through the existing strategy catalogue. It is a challenger, not a hand-selected champion. Receipt writes now persist the bounded original goal-context allowlist in the proposed source; the active release is untouched. Tests were added for BT ordering, training-only category partition and branch-aware reconstruction privacy; the integration owner must run them on the requested MBP gate.

## Final native-context measurement (supersedes the historical control)

Source `measured-context-final.json`, 24 identical labels per family for all methods. Same bounded exact native ancestor-chain context for every arm. Wall is actual elapsed critical path, including queueing, not summed RPC durations. Three-way now asks one constrained numeric-slot question per group of at most3 (not three independent binary questions), and fits BT using observed conditional pair odds from those group logits.

| Family | Arm | Correct; Wilson95% | Abstain | Tokens/decision | Wall ms/decision | Expected proxy ms/decision | Failed decisions |
|---|---|---|---:|---:|---:|---:|---:|
| tool | binary | 8/24;18.0–53.3% |33.3%|1121.5|381.3|6064.7|0|
| tool | pairwise BT |3/24;4.3–31.0%|0%|1272.5|231.5|5511.3|0|
| tool | three-way tournament |10/24;24.5–61.2%|0%|425.2|126.7|3646.6|0|
| tool | hierarchy |3/24;4.3–31.0%|62.5%|1071.9|334.3|9356.4|0|
| tool | vote3 |9/24;21.2–57.3%|29.2%|3364.5|1036.6|6209.1|0|
| tool | history bigram |2/24;2.3–25.8%|87.5%|0|0.1|9010.6|0|
| path kind | binary |4/24;6.7–35.9%|70.8%|655.5|161.4|8413.4|10|
| path kind | pairwise BT |0/24;0–13.8%|41.7%|751.4|110.1|9477.0|10|
| path kind | three-way tournament |4/24;6.7–35.9%|45.8%|234.1|68.2|7911.8|11|
| path kind | hierarchy |10/24;24.5–61.2%|54.2%|637.9|152.5|5033.8|11|
| path kind | vote3 |5/24;9.2–40.5%|66.7%|1966.5|482.8|8078.3|0|
| path kind | history bigram |15/24;42.7–78.8%|33.3%|0|0.1|3043.6|0|

All local arms have $0 billed API charges; GPU-active seconds and energy costs were not measured. Frontier comparison: measured Sol median7999ms and TTFT2284ms. Tool three-way uses about127ms rather than8seconds, making its lower agreement potentially worthwhile under the explicitly requested recovery-proxy loss. This is not a dollar comparison with unknown subscription marginal pricing.

Path failures remain in the denominator; they are not suppressed. The process logged transport/predictor failures, and one failed path row could later be scored successfully during diagnosis; the aggregate remains the actual failed run, not a silently repaired measurement. Concurrent search/server queue contention is a possible cause, not established. Vote3 currently aggregates sample abstentions but does not propagate constituent failure counts, so its zero top-level errors must not be read as zero failed internal RPCs.

### Real decision-space finding

The read-only database census observed15316 exact native tool labels and **19 distinct used tools**. Kevin authorized using this observed vocabulary: deterministic narrowing119→19 removes roughly100 never-used options before model execution. The category search clusters actual preceding tool co-occurrence, not a hand-written taxonomy, and does not fabricate120 labelled tools. A requested tool outside the frozen observed vocabulary must route to the frontier with reason `tool_not_in_observed_vocab`; the gold tool is never injected to repair model candidate recall.

## Powered replay protocol

`sample-800-training19.json` freezes 800 labels per family, split into 400
development and 400 holdout labels, after a training prefix in which all
19 observed tools have appeared. The old `sample-800.json` remains unchanged
as a historical manifest. The six-arm runner uses the new same manifest for
binary, pairwise BT, hierarchy, vote3, three-way tournament, and history bigram.
Missing exact ancestor chains are scored as failed abstentions for every arm;
they are never dropped or replaced with history-only prompts. The old manifest
contained two path-kind joins with unavailable native ancestor `ec5af60f`.

The pipeline search freezes categories once before the earliest evaluated
tool label. Its report stores the exact categories, training digest, row count,
and exclusive cutoff; publication copies this tested scope without refitting.
The initial old-manifest run was stopped before completion: its earliest
sample allowed only 14 preceding labels and nine tools. The new train-prefix
protocol avoids this reduced-vocabulary surrogate and future-label leakage.
Only 18 tool labels recur after the all-19-tools prefix; the exhausted stratum
cannot supply new development/holdout labels and is not synthetically filled.

The powered output paths are `measured-power.json` and
`leaderboard-power.json`. The latter explores 54 category/stage/margin
variants per resident final-stage model. Development expected proxy cost
alone selects a candidate. Publication additionally requires a matched,
complete binary incumbent, at least 400 unique holdout labels, and a
holdout expected-cost interval wholly below the incumbent interval.
Overlapping or losing intervals block candidate export. Candidate
publication does not grant promotion or execution authority.

