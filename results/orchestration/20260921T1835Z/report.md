# Multi-turn orchestration evaluation

- schema: `z0int.orchestration_eval_report.v1`
- The model picks one callable per turn; the world answers deterministically.
- `solved` = every required specialty was resolved before stopping.
- `wasted_calls` = dispatches that resolved nothing.
- `order_violations` = a dependency dispatched before its prerequisite.
- `optimal_greedy` is the cheapest-covering-set control.

| backend | scenarios | solved | correct stop | premature stop | budget out | wasted | order viol | invalids | errors | cost units | turns | precision | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| optimal_greedy | 12 | 11/12 | 12/12 | 1 | 0 | 0 | 2 | 0 | 0 | 35 | 16 | 1.00 | 0 | 0 |
| nemotron_orchestrator_8b | 12 | 6/12 | 7/12 | 6 | 0 | 3 | 0 | 0 | 0 | 30 | 25 | 0.71 | 11475 | 24539 |
| hammer2.1_3b | 12 | 0/12 | 1/12 | 12 | 0 | 0 | 0 | 0 | 0 | 0 | 12 | - | 314 | 4838 |
| hammer2.1_7b | 12 | 0/12 | 1/12 | 12 | 0 | 0 | 0 | 0 | 0 | 0 | 12 | - | 511 | 6743 |
| qwen3.5_9b | 12 | 9/12 | 7/12 | 3 | 2 | 6 | 0 | 0 | 0 | 42 | 31 | 0.70 | 5063 | 14466 |
| qwen3.5_4b | 12 | 9/12 | 5/12 | 2 | 5 | 8 | 0 | 0 | 0 | 53 | 33 | 0.57 | 4740 | 12310 |
| functiongemma_270m | 12 | 0/12 | 1/12 | 12 | 0 | 0 | 0 | 0 | 0 | 0 | 12 | - | 148 | 2817 |
