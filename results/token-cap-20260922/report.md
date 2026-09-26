# Token-cap sensitivity on the Phase 1B 28-state suite

_generated 2026-09-22T05:30:38.632581Z_

Measured calls used: **354** (0 invalid override rows excluded, 6 warmups excluded). 28 states x 3 reps per cell, compiler-first arm, seed 42, temperature 0.0, context 4096.

## Summary verdicts

| model | caps | accuracy % by cap | paired delta hi-lo (pp) | truncated % lo->hi | gold-in-reasoning % lo->hi | verdict |
|---|---|---|---|---|---|---|
| nemotron_orchestrator_8b | 256..1024 | 57.1 -> 82.1 -> 85.7 | 28.6 ((0.1429, 0.4643)) | 42.9 -> 0.0 | 100.0 -> 100.0 | cap-limited: preference flat, emission improves |
| hammer2.1_3b | 256..1024 | 89.3 -> 89.3 -> 89.3 | 0.0 ((0.0, 0.0)) | 0.0 -> 0.0 | 89.3 -> 89.3 | no cap effect (never approached the cap) |
| hammer2.1_7b | 256..512 | 85.7 -> 85.7 | 0.0 ((0.0, 0.0)) | 0.0 -> 0.0 | 85.7 -> 85.7 | no cap effect (never approached the cap) |
| functiongemma_270m | 128..256 | 60.7 -> 60.7 | 0.0 ((0.0, 0.0)) | 0.0 -> 0.0 | 60.7 -> 60.7 | no cap effect (never approached the cap) |
| qwen3.5_4b | 256..1024 | 71.4 -> 89.3 | 17.9 ((0.0357, 0.3214)) | 21.4 -> 0.0 | 100.0 -> 100.0 | cap-limited: preference flat, emission improves |
| qwen3.5_9b | 1024..2048 | 88.9 -> 88.9 | 0.0 ((0.0, 0.0)) | 11.1 -> 11.1 | 100.0 -> 100.0 | no accuracy effect at these caps |

## 0. Override reached the wire

The clamp was bypassed by rebuilding each model's resolved dialect with a larger `max_tokens` (`dataclasses.replace(resolved, max_tokens=cap)`) and passing it to `LocalSLMBackend`; every receipt records the literal `max_tokens` it POSTed (`wire_max_tokens`).

| model | native cap (frozen) | max wire cap sent | max completion_tokens | calls > native cap |
|---|---|---|---|---|
| nemotron_orchestrator_8b | 256 | 1024 | 871 | 19 |
| hammer2.1_3b | 256 | 1024 | 80 | 0 |
| hammer2.1_7b | 256 | 512 | 63 | 0 |
| functiongemma_270m | 128 | 256 | 39 | 0 |
| qwen3.5_4b | 1024 | 1024 | 558 | 0 |
| qwen3.5_9b | 1024 | 2048 | 2048 | 1 |

## 1. Cap sensitivity per model

### nemotron_orchestrator_8b (native 256)

| cap | n | accuracy % (95% CI) | truncated % | hit-cap % | gold-in-reasoning % | reasoning-implied acc % | call emitted % | call parsed % | mean completion tok | max tok |
|---|---|---|---|---|---|---|---|---|---|---|
| 256 | 28 | 57.1 (39.1-73.5) | 42.9 | 42.9 | 100.0 | 82.1 | 60.7 | 57.1 | 218.4 | 256 |
| 512 | 28 | 82.1 (64.4-92.1) | 7.1 | 7.1 | 100.0 | 78.6 | 92.9 | 92.9 | 268.7 | 512 |
| 1024 | 28 | 85.7 (68.5-94.3) | 0.0 | 0.0 | 100.0 | 89.3 | 100.0 | 100.0 | 291.4 | 871 |

- cap 512 vs native 256: paired delta **25.0 pp** (95% CI (0.1071, 0.4286)), pairs=28, hi-only-correct=7, lo-only-correct=0
- cap 1024 vs native 256: paired delta **28.6 pp** (95% CI (0.1429, 0.4643)), pairs=28, hi-only-correct=8, lo-only-correct=0

### hammer2.1_3b (native 256)

| cap | n | accuracy % (95% CI) | truncated % | hit-cap % | gold-in-reasoning % | reasoning-implied acc % | call emitted % | call parsed % | mean completion tok | max tok |
|---|---|---|---|---|---|---|---|---|---|---|
| 256 | 28 | 89.3 (72.8-96.3) | 0.0 | 0.0 | 89.3 | None | 96.4 | 96.4 | 23.5 | 55 |
| 512 | 28 | 89.3 (72.8-96.3) | 0.0 | 0.0 | 89.3 | None | 96.4 | 96.4 | 25.0 | 80 |
| 1024 | 28 | 89.3 (72.8-96.3) | 0.0 | 0.0 | 89.3 | None | 96.4 | 96.4 | 23.4 | 55 |

- cap 512 vs native 256: paired delta **0.0 pp** (95% CI (0.0, 0.0)), pairs=28, hi-only-correct=0, lo-only-correct=0
- cap 1024 vs native 256: paired delta **0.0 pp** (95% CI (0.0, 0.0)), pairs=28, hi-only-correct=0, lo-only-correct=0

### hammer2.1_7b (native 256)

| cap | n | accuracy % (95% CI) | truncated % | hit-cap % | gold-in-reasoning % | reasoning-implied acc % | call emitted % | call parsed % | mean completion tok | max tok |
|---|---|---|---|---|---|---|---|---|---|---|
| 256 | 28 | 85.7 (68.5-94.3) | 0.0 | 0.0 | 85.7 | None | 100.0 | 100.0 | 21.1 | 63 |
| 512 | 28 | 85.7 (68.5-94.3) | 0.0 | 0.0 | 85.7 | None | 100.0 | 100.0 | 19.6 | 24 |

- cap 512 vs native 256: paired delta **0.0 pp** (95% CI (0.0, 0.0)), pairs=28, hi-only-correct=0, lo-only-correct=0

### functiongemma_270m (native 128)

| cap | n | accuracy % (95% CI) | truncated % | hit-cap % | gold-in-reasoning % | reasoning-implied acc % | call emitted % | call parsed % | mean completion tok | max tok |
|---|---|---|---|---|---|---|---|---|---|---|
| 128 | 28 | 60.7 (42.4-76.4) | 0.0 | 0.0 | 60.7 | None | 100.0 | 100.0 | 13.4 | 39 |
| 256 | 28 | 60.7 (42.4-76.4) | 0.0 | 0.0 | 60.7 | None | 100.0 | 100.0 | 13.0 | 39 |

- cap 256 vs native 128: paired delta **0.0 pp** (95% CI (0.0, 0.0)), pairs=28, hi-only-correct=0, lo-only-correct=0

### qwen3.5_4b (native 1024)

| cap | n | accuracy % (95% CI) | truncated % | hit-cap % | gold-in-reasoning % | reasoning-implied acc % | call emitted % | call parsed % | mean completion tok | max tok |
|---|---|---|---|---|---|---|---|---|---|---|
| 256 | 28 | 71.4 (52.9-84.7) | 21.4 | 21.4 | 100.0 | 89.3 | 82.1 | 82.1 | 204.3 | 256 |
| 1024 | 28 | 89.3 (72.8-96.3) | 0.0 | 0.0 | 100.0 | 89.3 | 100.0 | 100.0 | 225.9 | 558 |

- cap 256 vs native 1024: paired delta **-17.9 pp** (95% CI (-0.3214, -0.0357)), pairs=28, hi-only-correct=0, lo-only-correct=5

### qwen3.5_9b (native 1024)

| cap | n | accuracy % (95% CI) | truncated % | hit-cap % | gold-in-reasoning % | reasoning-implied acc % | call emitted % | call parsed % | mean completion tok | max tok |
|---|---|---|---|---|---|---|---|---|---|---|
| 1024 | 9 | 88.9 (56.5-98.0) | 11.1 | 11.1 | 100.0 | 88.9 | 100.0 | 100.0 | 331.8 | 1024 |
| 2048 | 9 | 88.9 (56.5-98.0) | 11.1 | 11.1 | 100.0 | 88.9 | 100.0 | 100.0 | 445.6 | 2048 |

- cap 2048 vs native 1024: paired delta **0.0 pp** (95% CI (0.0, 0.0)), pairs=9, hi-only-correct=0, lo-only-correct=0

## 2. Nemotron 256 -> 1024 recovery

Paired (state, rep) cells with both 256 and 1024: **28**. At 256, **12** produced no parsed call and **11** had an empty content field with no native tool call (the exact frozen-corpus failure signature); **11** of those empty ones became a parsed tool call at 1024 and **8** became correct. At 256, **12** hit/truncated at the cap; **12** became a parsed call at 1024 and **8** became correct.

Accuracy at 256 on the paired set was 12 incorrect; **8** flipped to correct at 1024. Gold named in reasoning: 28/28 rows at 256 and 28/28 at 1024 — the *preference* is present at both caps; only the ability to emit changes.

Reasoning-implied ceiling at 1024 (correct when gold is named in reasoning): **85.7%**.


## 3. Hammer 3B / 7B

### hammer2.1_3b (native 256)

| cap | n | accuracy % (95% CI) | truncated % | hit-cap % | mean tok | max tok |
|---|---|---|---|---|---|---|
| 256 | 28 | 89.3 (72.8-96.3) | 0.0 | 0.0 | 23.5 | 55 |
| 512 | 28 | 89.3 (72.8-96.3) | 0.0 | 0.0 | 25.0 | 80 |
| 1024 | 28 | 89.3 (72.8-96.3) | 0.0 | 0.0 | 23.4 | 55 |
- 512 vs 256: paired delta **0.0 pp** (95% CI (0.0, 0.0)), pairs=28
- 1024 vs 256: paired delta **0.0 pp** (95% CI (0.0, 0.0)), pairs=28

### hammer2.1_7b (native 256)

| cap | n | accuracy % (95% CI) | truncated % | hit-cap % | mean tok | max tok |
|---|---|---|---|---|---|---|
| 256 | 28 | 85.7 (68.5-94.3) | 0.0 | 0.0 | 21.1 | 63 |
| 512 | 28 | 85.7 (68.5-94.3) | 0.0 | 0.0 | 19.6 | 24 |
- 512 vs 256: paired delta **0.0 pp** (95% CI (0.0, 0.0)), pairs=28

## 4. Qwen3.5-9B 1024 -> 2048

| cap | n | accuracy % (95% CI) | truncated % | hit-cap % | mean tok | max tok |
|---|---|---|---|---|---|---|
| 1024 | 9 | 88.9 (56.5-98.0) | 11.1 | 11.1 | 331.8 | 1024 |
| 2048 | 9 | 88.9 (56.5-98.0) | 11.1 | 11.1 | 445.6 | 2048 |

Paired 1024/2048 cells: 9. At 1024, 1 hit the cap, 1 of those were correct at 2048; 1 were incorrect at 1024 and 0 recovered at 2048; 0 were correct at 1024 but wrong at 2048. Paired delta: **0.0 pp** (95% CI (0.0, 0.0)).

### Nemotron per-state detail (correct reps / 3, truncated reps)

| state | gold | 256 acc | 256 trunc | 512 acc | 512 trunc | 1024 acc | 1024 trunc |
|---|---|---|---|---|---|---|---|
| cheap_model_sufficient | json.extract | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| continue_work | continue | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| credential_required_action | ask_user | 0/1 | 1/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| destructive_action | ask_user | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| diamond_dependency_graph | fetch | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| escalate_action | escalate | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| expensive_model_unnecessary | shell.wc | 0/1 | 1/1 | 0/1 | 1/1 | 1/1 | 0/1 |
| hard_dependency_a_then_b | db.migrate | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| jev_uncertain | ask_user | 0/1 | 1/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| no_relevant_tool | abstain | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| one_obvious_tool | fs.read | 0/1 | 1/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| orchestrator_should_abstain | abstain | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| permission_denied | deny | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| publish_send_deploy_action | ask_user | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| replan | replan | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| required_argument_missing | ask_user | 0/1 | 1/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| retry_action | retry | 0/1 | 1/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| slm_uncertain | replan | 0/1 | 1/1 | 0/1 | 0/1 | 0/1 | 0/1 |
| specialist_required | db.migrate | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| stop_now | stop | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| tool_fails | escalate | 0/1 | 1/1 | 0/1 | 0/1 | 0/1 | 0/1 |
| tool_returns_malformed_data | replan | 0/1 | 1/1 | 0/1 | 0/1 | 0/1 | 0/1 |
| tool_succeeds_after_retry | fs.read | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| tool_times_out | escalate | 0/1 | 1/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| two_independent_parallel_actions | fs.list | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 | 0/1 |
| two_similar_tools | fs.list | 0/1 | 1/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| unknown_tool | abstain | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 |
| wrong_argument_type | ask_user | 1/1 | 0/1 | 1/1 | 0/1 | 1/1 | 0/1 |

## 5. Completion-vs-cognition decomposition (incorrect rows per cell)

Categories: `cutoff_with_cognition` (gold named in reasoning, no call parsed), `cognition_but_other_call` (gold named, a different call parsed), `emitted_unparsed` (a call emitted but not parsed), `wrong_choice` (a different legal call parsed, gold not named), `silent_no_call` (nothing emitted, gold not named).

| model | cap | incorrect | cutoff_with_cognition | cognition_but_other_call | emitted_unparsed | wrong_choice | silent_no_call |
|---|---|---|---|---|---|---|---|
| nemotron_orchestrator_8b | 256 | 12 | 12 | 0 | 0 | 0 | 0 |
| nemotron_orchestrator_8b | 512 | 5 | 2 | 3 | 0 | 0 | 0 |
| nemotron_orchestrator_8b | 1024 | 4 | 0 | 4 | 0 | 0 | 0 |
| hammer2.1_3b | 256 | 3 | 0 | 0 | 0 | 2 | 1 |
| hammer2.1_3b | 512 | 3 | 0 | 0 | 0 | 2 | 1 |
| hammer2.1_3b | 1024 | 3 | 0 | 0 | 0 | 2 | 1 |
| hammer2.1_7b | 256 | 4 | 0 | 0 | 0 | 4 | 0 |
| hammer2.1_7b | 512 | 4 | 0 | 0 | 0 | 4 | 0 |
| functiongemma_270m | 128 | 11 | 0 | 0 | 0 | 11 | 0 |
| functiongemma_270m | 256 | 11 | 0 | 0 | 0 | 11 | 0 |
| qwen3.5_4b | 256 | 8 | 5 | 3 | 0 | 0 | 0 |
| qwen3.5_4b | 1024 | 3 | 0 | 3 | 0 | 0 | 0 |
| qwen3.5_9b | 1024 | 1 | 0 | 1 | 0 | 0 | 0 |
| qwen3.5_9b | 2048 | 1 | 0 | 1 | 0 | 0 | 0 |

Totals across all cells: **cognition_but_other_call**=15, **cutoff_with_cognition**=19, **silent_no_call**=3, **wrong_choice**=36.

## 6. Frozen-corpus reproduction

| model | rows | effective cap | rows at cap | max completion tok |
|---|---|---|---|---|
| nemotron_orchestrator_8b | 171 | 256 | 64 (37.4%) | 256 |
| hammer2.1_3b | 171 | 256 | 0 (0.0%) | 80 |
| hammer2.1_7b | 87 | 256 | 0 (0.0%) | 63 |
| functiongemma_270m | 87 | 128 | 0 (0.0%) | 39 |
| qwen3.5_4b | 171 | 1024 | 0 (0.0%) | 558 |
| qwen3.5_9b | 171 | 1024 | 30 (17.5%) | 1024 |

