# Powered SLM leaderboard — negative result

**Not activated: no qualifying candidate.** Always guessing the training-majority tool is 70.25% wrong yet scores **4.239 s** under the same proxy, below Sol’s measured **7.999 s**. This is a counterexample to the proxy as a routing objective, not permission to deploy the majority rule. A fixed 6.034 s read-recovery charge omits frontier redo work and output-quality degradation. Economics status: **proxy-unvalidated** (recommendation; no RiskBar/compiler setting was changed).

## Evidence and accounting

Completed job `bg_80`: 16,722.90 s elapsed, six arms × 800 labels × two families = 9,600 decisions, followed by 54 variants × 800 tool labels = 43,200 decisions. All arm/variant holdouts have 400 labels; development also has 400. All recorded decisions/RPCs in the completed outputs have zero failures. `bg_382` adds the three deterministic controls on the exact same 400 tool holdout IDs. No new model was loaded: resident `qwen3-0.6b-q8` only.

Accuracy is unconditional teacher-next-action agreement, not task success. Wilson intervals are 95%; cost intervals are 1,000 session-cluster bootstrap resamples, conditional on fixed E/F. Search order and development-cost selection are recorded in `leaderboard-power.json`; these exploratory intervals are not simultaneous 54-way confidence guarantees. Independent DAG comparisons execute in batches; wall is observed elapsed decision critical path including queueing, not the sum of RPC durations.

Expected read proxy = measured call wall + (1 − correct/covered) × 6.034 s + abstain_fraction × 7.999 s; all-abstain uses call wall + 7.999 s. **Recovery is already included in the reported expected cost and must not be added twice.** Read recovery n=196, median6.034 s, mean7.543 s, p95 18.554 s; reversible-mutation n=13, median7.764 s, mean12.678 s, p95 64.339 s. These are whole recovery-message observations, not causal marginal wrong-pick costs. Source: RiskBar `local://risk-recovery-measurements.json`, aggregate-only numbers. No destructive-cost estimate exists. Local model billed API charges are $0; energy, GPU-active seconds and subscription marginal prices were not measured.

## Same-holdout deterministic controls

Majority is learned strictly before the earliest frozen tool evaluation label; narrowing-top1 uses the exact frequency top-three from the prior64 labels, then selects its top1; previous-repeat uses only the final preceding tool in frozen features. None reads the current label to predict. Database preparation is excluded from predictor call timing; deterministic call costs are approximately zero.

| Control | Correct/400; accuracy [Wilson95%] | Abstain | Read expected s [cluster95%] |
|---|---|---:|---|
| training_majority | 119/400; 29.75% [25.48, 34.41] | 0.0% | 4.239 [3.917, 4.568] |
| narrowing_frequency_top1 | 108/400; 27.00% [22.88, 31.55] | 0.0% | 4.405 [4.151, 4.628] |
| previous_tool_repeats | 117/400; 29.25% [25.01, 33.89] | 2.0% | 4.393 [3.950, 4.774] |

Training-majority is `read`. All three controls beat the development-selected pipeline (70/400,17.5%,5.772 s). The best holdout accuracy among all54 pipeline variants is only80/400=20%; the strongest tool SLM arm is BT88/400=22%. Thus this task framing supplies **no demonstrated SLM advantage over the trivial majority control**. Baseline29.75% is not an acceptable router: it is wrong70.25% of the time, exposing the economics proxy’s permissiveness. No finalist is run under the parent’s mechanism-neutral decision.

## Six matched arms: all holdout results

| Family | Arm | Correct/400; accuracy [Wilson95%] | Abstain | Call s/decision | Read expected s [cluster95%] |
|---|---|---|---:|---:|---|
| omp.path_kind | goal_binary | 100/400; 25.00% [21.01, 29.47] | 52.2% | 0.673 | 7.727 [7.115, 8.350] |
| omp.path_kind | bt | 131/400; 32.75% [28.33, 37.49] | 0.0% | 0.304 | 4.362 [4.078, 4.643] |
| omp.path_kind | hierarchical | 150/400; 37.50% [32.90, 42.34] | 34.2% | 0.659 | 5.991 [5.427, 6.583] |
| omp.path_kind | vote3 | 99/400; 24.75% [20.77, 29.21] | 52.2% | 2.001 | 9.087 [8.443, 9.692] |
| omp.path_kind | tournament3 | 185/400; 46.25% [41.42, 51.15] | 0.0% | 0.236 | 3.479 [3.178, 3.793] |
| omp.path_kind | history_bigram | 118/400; 29.50% [25.24, 34.15] | 57.5% | 0.000 | 6.445 [5.798, 7.137] |
| omp.tool | goal_binary | 68/400; 17.00% [13.64, 20.99] | 37.2% | 0.601 | 7.980 [7.430, 8.573] |
| omp.tool | bt | 88/400; 22.00% [18.22, 26.32] | 0.0% | 0.276 | 4.983 [4.715, 5.217] |
| omp.tool | hierarchical | 47/400; 11.75% [8.95, 15.28] | 57.5% | 0.583 | 9.548 [8.924, 10.167] |
| omp.tool | vote3 | 67/400; 16.75% [13.41, 20.72] | 36.5% | 1.748 | 9.110 [8.544, 9.686] |
| omp.tool | tournament3 | 81/400; 20.25% [16.60, 24.46] | 0.0% | 0.205 | 5.017 [4.756, 5.270] |
| omp.tool | history_bigram | 29/400; 7.25% [5.10, 10.22] | 78.8% | 0.000 | 10.275 [9.555, 10.980] |

## All 54 pipeline variants

Ordered by development expected cost only. C=category count, first/final=binary, pairwise BT, or constrained three-way tournament; margin is applied at stage selection. Each row has400dev+400holdout. All use the same resident model for both stages. Holdout is never used to select the variant.

| Dev rank | C | First / final | Margin | Dev expected s [95%] | Holdout correct/400; accuracy [95%] | Holdout call s | Holdout expected s [95%] |
|---:|---:|---|---:|---|---|---:|---|
| 1 | 5 | binary / threeway | 0 | 5.933 [5.742, 6.126] | 70/400; 17.50% [14.09, 21.53] | 0.794 | 5.772 [5.559, 5.987] |
| 2 | 5 | binary / binary | 0 | 5.974 [5.742, 6.203] | 79/400; 19.75% [16.14, 23.93] | 1.006 | 5.848 [5.601, 6.085] |
| 3 | 8 | binary / binary | 0 | 6.150 [5.922, 6.384] | 73/400; 18.25% [14.77, 22.33] | 1.290 | 6.223 [5.976, 6.474] |
| 4 | 8 | binary / threeway | 0 | 6.249 [6.036, 6.462] | 80/400; 20.00% [16.37, 24.20] | 1.219 | 6.047 [5.749, 6.332] |
| 5 | 5 | binary / bt | 0 | 6.486 [6.265, 6.715] | 49/400; 12.25% [9.39, 15.83] | 1.272 | 6.567 [6.356, 6.751] |
| 6 | 3 | bt / binary | 0 | 6.566 [6.394, 6.739] | 50/400; 12.50% [9.61, 16.10] | 1.089 | 6.369 [6.121, 6.610] |
| 7 | 3 | binary / binary | 0 | 6.805 [6.562, 7.019] | 65/400; 16.25% [12.96, 20.18] | 1.846 | 6.899 [6.642, 7.150] |
| 8 | 3 | binary / threeway | 0 | 6.858 [6.645, 7.043] | 51/400; 12.75% [9.83, 16.38] | 1.600 | 6.865 [6.629, 7.084] |
| 9 | 5 | bt / binary | 0 | 6.941 [6.784, 7.088] | 41/400; 10.25% [7.65, 13.61] | 1.299 | 6.715 [6.529, 6.890] |
| 10 | 8 | binary / bt | 0 | 6.953 [6.719, 7.198] | 72/400; 18.00% [14.55, 22.06] | 1.934 | 6.881 [6.635, 7.101] |
| 11 | 3 | bt / threeway | 0 | 6.957 [6.781, 7.127] | 39/400; 9.75% [7.21, 13.05] | 1.511 | 6.957 [6.719, 7.174] |
| 12 | 5 | bt / threeway | 0 | 7.387 [7.210, 7.550] | 26/400; 6.50% [4.47, 9.35] | 1.750 | 7.392 [7.208, 7.547] |
| 13 | 3 | binary / bt | 0 | 7.425 [7.278, 7.557] | 37/400; 9.25% [6.79, 12.49] | 1.754 | 7.230 [7.054, 7.389] |
| 14 | 5 | bt / bt | 0 | 7.700 [7.538, 7.842] | 13/400; 3.25% [1.91, 5.48] | 2.089 | 7.926 [7.809, 8.040] |
| 15 | 8 | bt / binary | 0 | 7.702 [7.558, 7.826] | 29/400; 7.25% [5.10, 10.22] | 2.061 | 7.658 [7.491, 7.811] |
| 16 | 3 | bt / bt | 0 | 7.722 [7.610, 7.836] | 21/400; 5.25% [3.46, 7.89] | 1.926 | 7.643 [7.497, 7.792] |
| 17 | 8 | bt / threeway | 0 | 7.898 [7.717, 8.078] | 31/400; 7.75% [5.51, 10.79] | 2.297 | 7.863 [7.697, 8.023] |
| 18 | 8 | bt / bt | 0 | 8.338 [8.204, 8.461] | 20/400; 5.00% [3.26, 7.60] | 2.546 | 8.278 [8.113, 8.431] |
| 19 | 8 | binary / threeway | 0.25 | 8.776 [8.307, 9.295] | 56/400; 14.00% [10.94, 17.74] | 1.046 | 8.580 [8.067, 9.082] |
| 20 | 3 | bt / threeway | 0.25 | 9.102 [8.765, 9.468] | 25/400; 6.25% [4.27, 9.06] | 0.906 | 9.337 [8.931, 9.720] |
| 21 | 5 | binary / threeway | 0.25 | 9.214 [8.766, 9.720] | 51/400; 12.75% [9.83, 16.38] | 0.862 | 8.650 [8.202, 9.187] |
| 22 | 8 | binary / binary | 0.25 | 9.264 [8.718, 9.859] | 54/400; 13.50% [10.50, 17.20] | 1.380 | 9.169 [8.652, 9.707] |
| 23 | 3 | binary / threeway | 0.25 | 9.587 [9.116, 10.021] | 26/400; 6.50% [4.47, 9.35] | 0.992 | 9.778 [9.322, 10.216] |
| 24 | 8 | binary / bt | 0.25 | 9.757 [9.328, 10.216] | 40/400; 10.00% [7.43, 13.33] | 1.311 | 9.772 [9.351, 10.221] |
| 25 | 5 | binary / binary | 0.25 | 9.878 [9.323, 10.450] | 51/400; 12.75% [9.83, 16.38] | 1.134 | 9.515 [8.972, 10.083] |
| 26 | 3 | binary / binary | 0.25 | 10.436 [9.865, 11.101] | 35/400; 8.75% [6.36, 11.93] | 1.350 | 10.500 [9.965, 10.999] |
| 27 | 5 | bt / binary | 0.75 | 11.058 [9.026, 15.104] | 0/400; 0.00% [0.00, 0.95] | 1.105 | 15.058 [14.953, 15.119] |
| 28 | 5 | binary / bt | 0.25 | 11.361 [10.942, 11.854] | 17/400; 4.25% [2.67, 6.70] | 1.209 | 11.385 [10.832, 11.868] |
| 29 | 5 | bt / threeway | 0.25 | 11.532 [11.078, 11.985] | 13/400; 3.25% [1.91, 5.48] | 1.275 | 11.307 [10.896, 11.728] |
| 30 | 3 | bt / binary | 0.25 | 11.963 [11.454, 12.487] | 26/400; 6.50% [4.47, 9.35] | 1.785 | 12.154 [11.580, 12.738] |
| 31 | 3 | bt / binary | 0.75 | 12.023 [8.446, 14.491] | 1/400; 0.25% [0.04, 1.40] | 0.505 | 11.481 [8.448, 14.524] |
| 32 | 5 | binary / threeway | 0.75 | 12.060 [11.358, 12.769] | 12/400; 3.00% [1.72, 5.17] | 0.652 | 12.272 [11.457, 13.066] |
| 33 | 8 | binary / binary | 0.75 | 12.146 [11.611, 12.717] | 20/400; 5.00% [3.26, 7.60] | 1.012 | 11.925 [11.152, 12.734] |
| 34 | 3 | binary / bt | 0.25 | 12.295 [11.942, 12.664] | 12/400; 3.00% [1.72, 5.17] | 1.252 | 12.251 [11.811, 12.696] |
| 35 | 5 | bt / binary | 0.25 | 12.474 [12.048, 12.942] | 9/400; 2.25% [1.19, 4.22] | 1.329 | 12.761 [12.234, 13.257] |
| 36 | 5 | binary / binary | 0.75 | 12.559 [11.892, 13.326] | 15/400; 3.75% [2.29, 6.09] | 1.039 | 12.262 [11.507, 13.116] |
| 37 | 8 | binary / threeway | 0.75 | 12.576 [11.919, 13.255] | 15/400; 3.75% [2.29, 6.09] | 1.056 | 12.398 [11.743, 13.130] |
| 38 | 8 | bt / threeway | 0.25 | 12.661 [12.253, 13.045] | 13/400; 3.25% [1.91, 5.48] | 1.884 | 12.573 [12.157, 12.988] |
| 39 | 3 | binary / binary | 0.75 | 12.699 [11.859, 13.572] | 7/400; 1.75% [0.85, 3.57] | 0.836 | 12.861 [11.948, 13.837] |
| 40 | 8 | bt / bt | 0.25 | 12.946 [12.583, 13.281] | 9/400; 2.25% [1.19, 4.22] | 1.935 | 12.951 [12.498, 13.376] |
| 41 | 5 | bt / bt | 0.25 | 13.184 [12.813, 13.599] | 5/400; 1.25% [0.54, 2.89] | 1.576 | 12.886 [12.464, 13.295] |
| 42 | 3 | bt / bt | 0.25 | 13.217 [12.787, 13.619] | 7/400; 1.75% [0.85, 3.57] | 2.129 | 13.238 [12.804, 13.644] |
| 43 | 8 | bt / binary | 0.25 | 13.434 [12.909, 13.931] | 14/400; 3.50% [2.10, 5.79] | 1.966 | 12.867 [12.374, 13.369] |
| 44 | 3 | binary / threeway | 0.75 | 13.446 [12.292, 14.403] | 2/400; 0.50% [0.14, 1.80] | 0.534 | 13.378 [11.976, 14.388] |
| 45 | 8 | binary / bt | 0.75 | 13.636 [12.986, 14.276] | 11/400; 2.75% [1.54, 4.86] | 1.799 | 13.526 [12.809, 14.216] |
| 46 | 5 | binary / bt | 0.75 | 13.748 [12.604, 14.540] | 4/400; 1.00% [0.39, 2.54] | 0.775 | 12.980 [11.599, 14.125] |
| 47 | 3 | bt / threeway | 0.75 | 14.522 [8.507, 14.559] | 0/400; 0.00% [0.00, 0.95] | 0.509 | 14.502 [8.486, 14.539] |
| 48 | 3 | bt / bt | 0.75 | 14.524 [8.547, 14.572] | 0/400; 0.00% [0.00, 0.95] | 0.572 | 14.585 [8.545, 14.608] |
| 49 | 5 | bt / threeway | 0.75 | 14.724 [13.944, 15.386] | 1/400; 0.25% [0.04, 1.40] | 1.713 | 14.943 [13.541, 15.630] |
| 50 | 3 | binary / bt | 0.75 | 14.941 [8.917, 15.001] | 1/400; 0.25% [0.04, 1.40] | 0.985 | 13.712 [10.882, 15.004] |
| 51 | 5 | bt / bt | 0.75 | 15.080 [9.095, 15.134] | 0/400; 0.00% [0.00, 0.95] | 1.141 | 15.074 [14.987, 15.140] |
| 52 | 8 | bt / binary | 0.75 | 16.086 [10.013, 16.147] | 0/400; 0.00% [0.00, 0.95] | 2.024 | 10.023 [9.944, 10.095] |
| 53 | 8 | bt / bt | 0.75 | 16.425 [10.373, 16.556] | 0/400; 0.00% [0.00, 0.95] | 2.525 | 16.538 [10.384, 16.694] |
| 54 | 8 | bt / threeway | 0.75 | 16.892 [10.770, 17.020] | 0/400; 0.00% [0.00, 0.95] | 2.828 | 16.841 [10.732, 16.949] |

## Risk-class comparison and no-routing decision

| Risk class | Counterfactual selected-pipeline cost | Frontier | Eligibility |
|---|---|---:|---|
| Read-only | 0.794 + 0.825×6.034 = **5.772 s**, CI5.559–5.987 | 7.999 s | No live routing: proxy-unvalidated; no output-quality evidence |
| Reversible mutation | Observed recovery median7.764 s is not a validated error cost; do not price dispatch from it | 7.999 s | k=0; frontier only |
| Destructive / external side effect | No measured safe wrong-pick cost | 7.999 s | k=0; frontier only |

The read-only figures apply the requested read-error proxy uniformly to replay decisions; they are not an observed class-specific deployed recovery measurement. The selected pipeline is wrong82.5% of the time. The proxy would rank majority4.239 s ahead of this SLM5.772 s and both ahead of frontier7.999 s, but cannot establish Kevin’s requirement that output does not degrade. No routing decision should rely on this proxy until real-session per-wrong-pick recovery, frontier redo, and output-quality judgments are measured in ABAB trials. Flag RiskBar/policy-compiler economics inputs `proxy-unvalidated` as a recommendation only.

## Scope caveat and withdrawal

All19 observed tool types occur in the training prefix; only18 recur afterward. `manage_skill` has one training label and zero post-training labels. It receives no metric/dispatch credit. The completed search had already loaded an earlier runner before the source-only exclusion landed, so its actual frozen categories still include `manage_skill`. **Do not relabel these results as an18-tool run.** The automatically exported19-tool candidate `methods-pipeline-0d4474b68b15b39dfc11c6935e274fb943ca84464103ad5f6df6b669dde29f33` was withdrawn from the private candidate catalogue and its local candidate artifact removed. Raw `winner_qualified:true` records the old numerical binary-incumbent interval gate only; `disposition.json` supersedes it for deployment. There is no qualifying candidate and no activation. Any future18-tool candidate must have new matched measurements; unseen `manage_skill` must fail closed to frontier.

## What could change the result — recommendations only

Next-tool prediction from bounded context may be the wrong target: majority29.75% beats every pipeline (best holdout20%) while the selected SLM costs0.794 s/call and is wrong82.5%. A narrower abstain-capable binary correctness/safety gate could instead answer an action-specific question with measured precision and explicit frontier escalation; current binary17% unconditional agreement and37.25% abstention do not establish such a gate. A larger *already-served/admitted* model is another experiment, not an assumed fix: this run measured only0.6B and supplies no1.7B accuracy. Before any routing experiment, measure per-wrong-pick recovery and ABAB output quality; neither a5.772 s proxy score nor a larger model name proves non-degradation. Mutation/destructive remain k=0.

## Artifacts and source gate

- `measured-power.json`: all9,600 arm rows, summaries, failure counts.
- `leaderboard-power.json`: all43,200 search rows, frozen training categories/digest,54variants.
- `sample-800-training19.json`: frozen exact IDs, development/holdout assignment and training cutoff.
- `baselines-power.json`: matched controls and expected-cost intervals.
- `disposition.json`: authoritative withdrawal and no-activation disposition.
- Production source gate from release owner: tested358ea4aad6f57ff5809e45712e4a34abb4a1e77b, fleet-mbp-cc6a2d557c98498f,537Python passed/2skipped,48native-extension passed. No checks rerun by the measurement owner. That source gate is not method qualification or activation.
- Operational handoff: `/mnt/zer0models/workspace/zer0/data/operations/policy-pipeline/SLM-RELEASE-HANDOFF.md`.
