# Multi-turn orchestration evaluation

- schema: `z0int.orchestration_eval_report.v1`
- The model picks one callable per turn; the world answers deterministically.
- `solved` = every required specialty was resolved before stopping.
- `wasted_calls` = dispatches that resolved nothing.
- `order_violations` = a dependency dispatched before its prerequisite.
- `optimal_greedy` is the cheapest-covering-set control.
- `recovered` = the world returned an error/empty result and the task still got solved.
- `needless_escalation` = dispatched a costlier callable when a cheaper affordable one
  covered the same specialty.
- `failure_to_escalate` = stopped with work outstanding while an affordable callable
  that could have resolved it was never dispatched.

| backend | cohort | scenarios | solved | correct stop | premature stop | budget out | wasted | order viol | invalids | errors | cost units | turns | precision | recovered | needless esc | failed to esc | optional taken | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| optimal_greedy | expansion | 28 | 24/28 | 28/28 | 4 | 0 | 4 | 0 | 0 | 0 | 90 | 42 | 0.92 | 4/5 | 0 | 0 | 0 | 0 | 0 |
| optimal_greedy | regression | 12 | 11/12 | 12/12 | 1 | 0 | 0 | 0 | 0 | 0 | 35 | 15 | 1.00 | 0/0 | 0 | 0 | 0 | 0 | 0 |
| qwen3.5_4b | expansion | 28 | 22/28 | 18/28 | 3 | 6 | 38 | 0 | 0 | 0 | 128 | 99 | 0.57 | 4/5 | 0 | 1 | 0 | 3555 | 6469 |
| qwen3.5_4b | regression | 12 | 9/12 | 5/12 | 2 | 5 | 8 | 0 | 0 | 0 | 53 | 33 | 0.57 | 0/0 | 1 | 2 | 0 | 4315 | 11628 |
| qwen3.5_9b | expansion | 28 | 22/28 | 20/28 | 3 | 6 | 34 | 0 | 0 | 0 | 133 | 97 | 0.59 | 5/5 | 2 | 1 | 1 | 4508 | 9015 |
| qwen3.5_9b | regression | 12 | 9/12 | 7/12 | 3 | 2 | 6 | 0 | 0 | 0 | 42 | 31 | 0.70 | 0/0 | 0 | 2 | 0 | 5085 | 13745 |
| nemotron_orchestrator_8b | expansion | 28 | 17/28 | 19/28 | 11 | 0 | 12 | 0 | 0 | 0 | 88 | 69 | 0.69 | 2/5 | 0 | 7 | 1 | 7174 | 15799 |
| nemotron_orchestrator_8b | regression | 12 | 6/12 | 7/12 | 6 | 0 | 3 | 0 | 0 | 0 | 30 | 25 | 0.71 | 0/0 | 0 | 5 | 0 | 9763 | 22888 |
| hammer2.1_3b | expansion | 28 | 0/28 | 4/28 | 28 | 0 | 0 | 0 | 0 | 0 | 0 | 28 | - | 0/5 | 0 | 24 | 0 | 196 | 521 |
| hammer2.1_3b | regression | 12 | 0/12 | 1/12 | 12 | 0 | 0 | 0 | 0 | 0 | 0 | 12 | - | 0/0 | 0 | 11 | 0 | 284 | 4911 |
