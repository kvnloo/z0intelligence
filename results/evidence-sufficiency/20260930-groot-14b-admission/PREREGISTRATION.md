# Pre-registration: larger groot models vs the evidence_sufficiency admission bar

Written and committed on 2026-09-30, **before** any scored item was run on either candidate.
The results and the addendum are committed afterwards, as separate commits on this branch.
Nothing below changes after the pre-registration commit. Any deviation goes in the addendum
and is labeled as a deviation.

## Why
On the frozen 48-item evidence_sufficiency set, Jev scores 46/48. The best local model so far,
Qwen3-8B Q4_K_M on groot, scores 43/48 with 1 confident error at 90 ms per item. The
admission bar (≥44/48) was proposed at the same time as the bench, not before it, and the
z0evals study flagged that. This time the bar, the candidates, and the procedure are fixed
before anything is measured.

## Frozen inputs (base commit b072468, integrate/claude-code-z0-stack)
| Input | sha256 |
|---|---|
| `benchmarks/data/authored144.jsonl` (family evidence_interpretation, split test: 48 items) | `8162d1c73f925af64453f1ec05ef36d583b3815bf698e60f0d454bd11537e079` |
| `benchmarks/local_evidence_sufficiency.py` (unmodified) | `773d01d5d121c0794ab82a614c33bca8cba97787bfa02ef8f692b89363af90af` |
| `src/z0int/backends/llama_http.py` (unmodified) | `a65bd97dd48b2dfc43d04ab65c656a976ef018e6278caebcb884de12c9c86096` |
| Baseline: `~/.z0int/research/factory/groot-bench/groot_qwen3_8b_q4.jsonl` (43/48, 1 confident error) | `3b0574870dc6088e1f406a49113a8d9af646d1e310751b8c9b0bc5bfe874a888` |

This bench code has not changed since the 8B baseline run (last commit 12085dd).

## Candidates (both from the official Qwen GGUF repos, with pinned revisions)
| Arm (`llama_http:` id) | Router model id | File @ revision | HF LFS sha256 (to be verified on groot) |
|---|---|---|---|
| A. `groot_qwen3_14b_q4` | `qwen3-14b-q4km` | `Qwen/Qwen3-14B-GGUF@530227a7d994db8eca5ab5ced2fb692b614357fd` `Qwen3-14B-Q4_K_M.gguf` (9.00 GB) | `500a8806e85ee9c83f3ae08420295592451379b4f8cf2d0f41c15dffeb6b81f0` |
| B. `groot_qwen3_8b_q6` (optional) | `qwen3-8b-q6k` | `Qwen/Qwen3-8B-GGUF@7c41481f57cb95916b40956ab2f0b139b296d974` `Qwen3-8B-Q6_K.gguf` (6.73 GB) | `cb042ccd76795a8830d6be6bd4165245847cc68e41797b13bd61aed4c2cfbce6` |

B uses the same repo and revision as the 8B Q4_K_M baseline. It isolates the effect of
quantization, where A tests the effect of model size. If a file's sha256 on groot does not
match the value above, that arm is not run.

## Procedure (identical to the 8B baseline)
- Server: the existing `z0-farm-llama.service` router on groot, flags unchanged
  (`--models-max 2 -ngl 99 -c 8192 --parallel 2 --reasoning off -t 4 --sleep-idle-seconds 900`,
  llama.cpp b11270). The only farm change is two new GGUFs plus their router symlinks, then a
  service restart.
- Client: from the mbp, run
  `.venv/bin/python benchmarks/local_evidence_sufficiency.py --backend llama_http:<arm> --out <file>.jsonl`.
- Prompt: no prompt changes. Readout: direct next-token letter log-probabilities,
  `temperature 0`, `n_predict 1`, `n_probs 64`, `cache_prompt true`.
  `Z0INT_SLM_DEBIAS` stays at its default of 1 (forward + reversed option order averaged).
  Decoding is greedy, so no seed is involved, which also held for the baseline.
- There is no warm-up request. Row 1 is the cold load, as it was in the baseline.
- Each arm runs once, A first, then B. There are no reruns and no best-of.
  - Exception: an item that fails on transport (an HTTP error or timeout, which leaves no row) may
    be resumed once with the script's built-in resume. Rows already written stand. Any such
    resume goes in the addendum.
- Before each arm, check `nvidia-smi` on groot. If a non-farm process (for example Ollama) holds
  more than 1 GB of VRAM, wait for it to finish rather than evict it.

## Admission bar (each arm judged independently; all three must hold)
1. Three-way accuracy **≥ 44/48**.
2. **≤ 1 confident error**. A confident error is a row where `pred_id != gold_id` and
   `max(probabilities) >= 0.9`, using the script's own definition.
3. **Warm p50 ≤ 500 ms**, where warm p50 is the median `ms` over rows 2–48 in file order, excluding
   the cold row 1.

If both arms are admitted, the preferred one has higher accuracy, then fewer confident errors,
then lower warm p50.

## What admission does and does not do
- An admitted arm gets an evidenced $0 route in `~/.z0int/config/worker_routing.local.json`
  under provider `groot`, citing its results file by path and sha256 under the existing evidence
  rule. The file is backed up first. A routed completion is then verified.
- **`local_order` and the groot provider's default model are not changed**, whatever the outcome.
  Promoting to default is a separate decision.
- Admission does not replace Jev in live routing. It only records that the arm cleared the
  pre-agreed bar.
- Honest limits:
  - n = 48, so 44 vs 43 is a difference of one item and is not a statistically meaningful
    improvement over the 8B.
  - The bar is a pre-agreed gate, not a significance test.
  - Items were already seen by the baseline run. No model was tuned on them.

## Also reported (not gating)
- p90 latency, cold first-item time, and per-item disagreements with the 8B Q4_K_M baseline.
- VRAM (`nvidia-smi`) with each candidate loaded alone.
- **Co-residency with `qwen3-8b-q4km`**: after both scored runs, load the candidate and the 8B
  together (the router allows 2), then record whether both stay resident and answer one request
  each, the VRAM total, and any load failure or eviction.
  - Expectation, written down now: the 14B Q4_K_M (~9 GB of weights) cannot fit next to the 8B
    Q4_K_M (~5 GB) on a 12 GB card, while the 8B Q6_K alone fits.
  - This test runs after the scored runs, so it cannot affect them.
