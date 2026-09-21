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
| compiler_jev_qwen4b | 28 | 28 | 5 | 5/28 | 5/28 | 0 | 2 | 26 | 26 | 0 | 0 | 0 | 4465 | 4783 |
| compiler_hammer3b_qwen4b | 28 | 28 | 5 | 5/28 | 5/28 | 0 | 2 | 26 | 26 | 0 | 0 | 0 | 14971 | 25040 |
| compiler_jev_hammer3b_qwen4b | 28 | 28 | 5 | 5/28 | 5/28 | 0 | 2 | 26 | 26 | 0 | 0 | 0 | 14778 | 15556 |
| compiler_hammer3b_qwen9b_fallback | 28 | 28 | 8 | 8/28 | 8/28 | 0 | 2 | 26 | 26 | -2 | 2 | 4 | 18362 | 26819 |
