# Z20 decision-model hill-climb

Status: partial, no promotion. The offline baselines and audit are reproducible; the requested SLM distillation comparison is not complete. Evidence folder `E` is `/mnt/zer0models/workspace/zer0/data/operations/intent-analyzer-20261104/ayo-runs/exec-Z20`. All tables below cite `E` artifacts, not private chat history.

Latest recheck: [2026-11-12 verification and acceptance gaps](RECHECK-20261112.md). The new fleet run reproduced the existing evidence; it did not close the missing SLM or held-out Jev requirements.

## Frozen data and validity

- Original preregistration: `/mnt/zer0models/workspace/zer0/data/operations/z20-decision-hillclimb/PREREG.md:25-80`. D-B uses 2,512 train, 488 validation, and 3,764 test issues. Training IDs SHA-256 is `a238f38bfb18683f5b07c27448a38cba44e3f88a1bbb2030815cf6d568666daa`; fitted vocabulary is `9323ee6a56829113d7f9137e7d64eb44f5d544606bbb34d1b83438ef0289f68c`. Source: `E/artifacts/validation-final/summary.json:1037-1043` and `E/artifacts/test-final/summary.json:1-17`.
- D-B train/validation input SHA-256: `53bdea8b39720c6e924daf909f6bfb61c1daeb8f0b33224b0f22640942b2a007`. Test input SHA-256: `e3fb1bbc01b04bbf4b216e9768bad2b0a7cfff7d97197f6865bcb247790fa0d4`. The test was read in the prior attempt once, with a start/completion receipt. This execution checked that receipt and its linked result bytes, without reopening the test source. Sources: `E/sources/owner/data/freeze.json:115-120`, `E/artifacts/test-reads.jsonl:1-2`, `E/fleet-verify.log`.
- Legacy D-A has 86 selected items, 47 train and 39 test. Every item exposes `candidate_outcomes`; its scores are invalid as evidence of generalization. The old D-A and D-B "reversed" files preserve option order while changing instructions. Source: `E/verified/verification-audit/summary.json`, fields `audit` and `aa`.
- The create-only repaired D-A export removes outcome fields, uses full task identity for clustering, and actually reverses criteria while preserving instructions. Its original/reversed hashes are `b28efa88e82e9b887152e8b724664e7afdfc3ce39a46b4f52a11ad70f4eed35f` and `1d504b5aadca57cc83eec12b50d81f48123603729e1e8a90c3e54b8bb40a0086`. It is NOT relabelled or scored. Sources: `src/build_da_items.py:14-82`, `E/verified/verification-sanitized/freeze.da-sanitized.json`.

## A/A and validation arena

Teacher label flips: D-A 0/86, D-B component 71/3000, D-B priority 85/3000. The instruction-change comparisons are not option-order tests. Deterministic baseline repeats have 0/488 flips, but are not two training seeds. Source: `E/verified/verification-audit/summary.json`, fields `aa` and `deterministic_proxy_aa`.

The following BT estimates use correctness-derived pairwise outcomes, half-win ties, 0.5 pseudo-wins per direction, and 2,000 bootstrap draws. D-B resamples issues; legacy D-A resamples 11 task clusters. Ratings are centered within each candidate pool. Individual rating CIs are not paired mutation-gain CIs. Source: `src/audit_round.py:92-137`, `E/verified/verification-audit/summary.json`.

| Decision | Candidate | Accuracy | BT rating [95% CI] | ECE | p50 / p95 ms |
|---|---|---:|---|---:|---:|
| Component validation | Majority | 23.98% | -0.594 [-0.678, -0.516] | .011 | not measured |
| Component validation | TF-IDF gold | 63.52% | .236 [.175, .299] | .205 | .284 / .295 |
| Component validation | TF-IDF Jev hard targets | 61.48% | .194 [.138, .249] | .241 | .288 / .307 |
| Component validation | Jev cached | 60.04% | .164 [.094, .232] | .205 | 153 / 201 |
| Priority validation | Majority | 48.98% | -.103 [-.138, -.067] | .006 | not measured |
| Priority validation | TF-IDF gold | 72.13% | .368 [.290, .454] | .085 | .278 / .289 |
| Priority validation | TF-IDF Jev hard targets | 48.36% | -.116 [-.151, -.081] | .285 | .278 / .288 |
| Priority validation | Jev cached | 46.72% | -.149 [-.204, -.094] | .227 | 153 / 201 |

TF-IDF timings measure `predict_proba` on precomputed features, not tokenization, vectorization, or an end-to-end request. Jev latency is from historical calls; it was not remeasured. Source: `src/audit_round.py:182-225`.

## Previously opened held-out results

Validation selected TF-IDF gold for both questions. These saved test results were checked against row aggregates and the read receipt, not rerun against held-out gold. There are no Jev test labels, so there is no held-out student-versus-Jev result. TF-IDF is not an SLM. Source: `E/artifacts/test-final/summary.json:1-17`, `src/verify_evidence.py`.

| Decision | Candidate | Accuracy | BT rating [95% CI] | ECE |
|---|---|---:|---|---:|
| Component test | Majority | 15.65% | -.525 [-.549, -.501] | .072 |
| Component test | TF-IDF gold | 52.68% | .254 [.235, .272] | .117 |
| Component test | TF-IDF Jev hard targets | 53.51% | .271 [.250, .290] | .158 |
| Priority test | Majority | 68.25% | .003 [-.012, .016] | .198 |
| Priority test | TF-IDF gold | 68.84% | .014 [-.012, .042] | .063 |
| Priority test | TF-IDF Jev hard targets | 67.27% | -.017 [-.032, -.003] | .132 |

Source for all six rows: `E/artifacts/test-final/summary.json`, fields `arena` and `metrics`. Choosing a new winner from these test results is not allowed.

For completeness only, the INVALID legacy D-A diagnostic gives Jev and untrained d1-3B 28.21% accuracy each, BT -.206 [-.366, -.053]; the train-picked single model has 58.97%, BT .413 [.105, .732]. Do not use these leaked-input results to compare models. Source: `E/verified/verification-audit/summary.json`, key `D-A:LEAKED_LEGACY_TEST`.

## Cascade, not a deployment gate

These are uncalibrated validation-only descriptions, with cached Jev as fallback. They do not establish held-out savings at at most one percentage point loss. Sources: `src/audit_round.py:171-179`, `E/verified/verification-audit/summary.json`, field `cascade`.

| Decision and proxy | Threshold | Coverage | Accuracy | Jev decisions avoided |
|---|---:|---:|---:|---:|
| Component TF-IDF gold | 1.01 | 0% | 60.04% | 0/488 |
| Component TF-IDF gold | .7 | 7.58% | 61.07% | 37/488 |
| Component TF-IDF gold | .5 | 30.94% | 66.19% | 151/488 |
| Component TF-IDF gold | .1 | 100% | 63.52% | 488/488 |
| Priority TF-IDF gold | 1.01 | 0% | 46.72% | 0/488 |
| Priority TF-IDF gold | .7 | 29.51% | 60.86% | 144/488 |
| Priority TF-IDF gold | .5 | 90.16% | 70.90% | 440/488 |
| Priority TF-IDF gold | .1 | 100% | 72.13% | 488/488 |

## Verification and remaining blockers

`E/COMMANDS.md` records exact commands. `fleet-build` job `fleet-mbp-ee5d3416369d4a7a`, MBP host, one CPU and 2 GiB, exited 0: 21 regressions passed; 23 input hashes checked; saved accuracy, probability coverage, ECE, and Brier values recomputed; validation audit rerun with 2,000 bootstraps; sanitized D-A export reproduced byte-for-byte. `E/verified-comparison-tolerant.log` confirms the saved arena, A/A, cascade and training hashes match exactly; calibration differences are below 1e-10. The test checks consistency of saved correctness bits, not independent gold labels or historical latency.

There were 18,258 cached teacher decisions, and zero new paid calls. One D-B test pass needs 7,528 additional decisions, taking the total to 25,786, already 5,786 over the preregistered cap. Supplying cached labels or authorizing a separate paid round and a revised budget is a Kevin decision. Sources: `E/verified/verification-audit/summary.json`, `E/sources/owner/data/freeze.json:119-120`, original `PREREG.md:45`.

SLM hard/soft-target LoRA training, two-seed student A/A, a valid student option-order test, a held-out Jev comparison, calibrated held-out cascade, and the conditional Claude gold slice are NOT RUN here. No routing, services, credentials, or public state changed. Ledger rows are prepared as drafts in `E/LEDGER-ROWS.draft.jsonl`, not appended or published. Research explanation and source links: [METHODS.md](METHODS.md).
