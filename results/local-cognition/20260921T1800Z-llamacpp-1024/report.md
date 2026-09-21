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
| nemotron_orchestrator_8b | 28 | 17/28 | 17/28 | 11 | 0 | 0 | 2768 | 3004 | 3006 |
| qwen3.5_9b | 28 | 28/28 | 26/28 | 0 | 0 | 0 | 3523 | 14446 | 14726 |

## Correct by family

| backend | family | correct | n | dangerous |
|---|---|---|---|---|
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
| qwen3.5_9b | control_flow | 5/5 | 5 | 0 |
| qwen3.5_9b | dependencies | 2/2 | 2 | 0 |
| qwen3.5_9b | parallelism | 1/1 | 1 | 0 |
| qwen3.5_9b | recovery | 3/4 | 4 | 0 |
| qwen3.5_9b | routing | 3/3 | 3 | 0 |
| qwen3.5_9b | schema | 3/3 | 3 | 0 |
| qwen3.5_9b | security | 4/4 | 4 | 0 |
| qwen3.5_9b | tool_selection | 2/2 | 2 | 0 |
| qwen3.5_9b | uncertainty | 1/2 | 2 | 0 |
