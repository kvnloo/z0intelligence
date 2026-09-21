# True composition evaluation (Phase 1B section D)

- schema: `z0int.true_composition_eval.v1`
- Every stage receives the previous stage's output; the upstream artifact is
  embedded in the downstream prompt and its digest is recorded.
- `stable_correct` requires *every* repetition to be correct; one lucky draw is not a chain.
- `ablation_incremental_value` = correctness with the upstream artifact minus
  correctness of the same final stage run without it. This is the only honest
  measure of whether an earlier component helped rather than added overhead.

| chain | runs | fixtures | correct runs | stable correct | ever correct | dangerous | terminated early | upstream consumed | ablation n | incremental value | helped | hurt | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline_hammer3b | 28 | 28 | 10 | 10/28 | 10/28 | 0 | 2 | 26 | 0 | 0 | 0 | 0 | 68 | 149 |
| baseline_qwen4b | 28 | 28 | 18 | 18/28 | 18/28 | 0 | 2 | 26 | 0 | 0 | 0 | 0 | 13801 | 17025 |
| baseline_qwen9b | 28 | 28 | 19 | 19/28 | 19/28 | 0 | 2 | 26 | 0 | 0 | 0 | 0 | 14686 | 24275 |
| compiler_jev_qwen4b | 28 | 28 | 13 | 13/28 | 13/28 | 0 | 2 | 26 | 26 | -8 | 0 | 8 | 15738 | 19440 |
| compiler_hammer3b_qwen4b | 28 | 28 | 6 | 6/28 | 6/28 | 0 | 2 | 26 | 26 | -13 | 0 | 13 | 27506 | 33247 |
