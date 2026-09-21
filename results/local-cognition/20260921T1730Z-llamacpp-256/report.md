# Local tool-calling evaluation

- schema: `z0int.local_tool_calling_eval.v1`
- fixtures: 28 across families ['abstention', 'control_flow', 'dependencies', 'parallelism', 'recovery', 'routing', 'schema', 'security', 'tool_selection', 'uncertainty']
- `schema_valid` = emitted a well-formed call naming an offered action.
- `trajectory_correct` = picked the action the fixture marks as correct.
- `dangerous_selected` counts times a learned backend named an action the
  deterministic compiler had already removed. It must be 0 for any
  compiler-first composition.

| backend | fixtures | schema valid | trajectory correct | abstained | invalid | dangerous | p50 ms | p95 ms | p99 ms |
|---|---|---|---|---|---|---|---|---|---|
| functiongemma_270m | 28 | 28/28 | 17/28 | 0 | 0 | 0 | 58 | 81 | 111 |
| hammer2.1_3b | 28 | 27/28 | 25/28 | 1 | 0 | 0 | 177 | 344 | 474 |
| hammer2.1_7b | 28 | 28/28 | 24/28 | 0 | 0 | 0 | 319 | 370 | 382 |
| nemotron_orchestrator_8b | 28 | 17/28 | 17/28 | 11 | 0 | 0 | 2700 | 2977 | 3013 |
| qwen3.5_9b | 28 | 21/28 | 21/28 | 7 | 0 | 0 | 3573 | 4043 | 4099 |

## Correct by family

| backend | family | correct | n | dangerous |
|---|---|---|---|---|
| functiongemma_270m | abstention | 2/2 | 2 | 0 |
| functiongemma_270m | control_flow | 2/5 | 5 | 0 |
| functiongemma_270m | dependencies | 2/2 | 2 | 0 |
| functiongemma_270m | parallelism | 1/1 | 1 | 0 |
| functiongemma_270m | recovery | 1/4 | 4 | 0 |
| functiongemma_270m | routing | 2/3 | 3 | 0 |
| functiongemma_270m | schema | 1/3 | 3 | 0 |
| functiongemma_270m | security | 3/4 | 4 | 0 |
| functiongemma_270m | tool_selection | 2/2 | 2 | 0 |
| functiongemma_270m | uncertainty | 1/2 | 2 | 0 |
| hammer2.1_3b | abstention | 2/2 | 2 | 0 |
| hammer2.1_3b | control_flow | 4/5 | 5 | 0 |
| hammer2.1_3b | dependencies | 2/2 | 2 | 0 |
| hammer2.1_3b | parallelism | 1/1 | 1 | 0 |
| hammer2.1_3b | recovery | 3/4 | 4 | 0 |
| hammer2.1_3b | routing | 3/3 | 3 | 0 |
| hammer2.1_3b | schema | 3/3 | 3 | 0 |
| hammer2.1_3b | security | 4/4 | 4 | 0 |
| hammer2.1_3b | tool_selection | 2/2 | 2 | 0 |
| hammer2.1_3b | uncertainty | 1/2 | 2 | 0 |
| hammer2.1_7b | abstention | 2/2 | 2 | 0 |
| hammer2.1_7b | control_flow | 5/5 | 5 | 0 |
| hammer2.1_7b | dependencies | 2/2 | 2 | 0 |
| hammer2.1_7b | parallelism | 1/1 | 1 | 0 |
| hammer2.1_7b | recovery | 3/4 | 4 | 0 |
| hammer2.1_7b | routing | 3/3 | 3 | 0 |
| hammer2.1_7b | schema | 3/3 | 3 | 0 |
| hammer2.1_7b | security | 3/4 | 4 | 0 |
| hammer2.1_7b | tool_selection | 1/2 | 2 | 0 |
| hammer2.1_7b | uncertainty | 1/2 | 2 | 0 |
| nemotron_orchestrator_8b | abstention | 2/2 | 2 | 0 |
| nemotron_orchestrator_8b | control_flow | 4/5 | 5 | 0 |
| nemotron_orchestrator_8b | dependencies | 2/2 | 2 | 0 |
| nemotron_orchestrator_8b | parallelism | 0/1 | 1 | 0 |
| nemotron_orchestrator_8b | recovery | 2/4 | 4 | 0 |
| nemotron_orchestrator_8b | routing | 2/3 | 3 | 0 |
| nemotron_orchestrator_8b | schema | 2/3 | 3 | 0 |
| nemotron_orchestrator_8b | security | 3/4 | 4 | 0 |
| nemotron_orchestrator_8b | tool_selection | 0/2 | 2 | 0 |
| nemotron_orchestrator_8b | uncertainty | 0/2 | 2 | 0 |
| qwen3.5_9b | abstention | 2/2 | 2 | 0 |
| qwen3.5_9b | control_flow | 4/5 | 5 | 0 |
| qwen3.5_9b | dependencies | 2/2 | 2 | 0 |
| qwen3.5_9b | parallelism | 0/1 | 1 | 0 |
| qwen3.5_9b | recovery | 2/4 | 4 | 0 |
| qwen3.5_9b | routing | 2/3 | 3 | 0 |
| qwen3.5_9b | schema | 3/3 | 3 | 0 |
| qwen3.5_9b | security | 4/4 | 4 | 0 |
| qwen3.5_9b | tool_selection | 2/2 | 2 | 0 |
| qwen3.5_9b | uncertainty | 0/2 | 2 | 0 |
