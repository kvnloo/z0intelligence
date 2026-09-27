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
| groq/openai/gpt-oss-20b | 28 | 9/28 | 10/28 | 19 | 0 | 0 | 131 | 448 | 632 |

## Correct by family

| backend | family | correct | n | dangerous |
|---|---|---|---|---|
| groq/openai/gpt-oss-20b | abstention | 2/2 | 2 | 0 |
| groq/openai/gpt-oss-20b | control_flow | 1/5 | 5 | 0 |
| groq/openai/gpt-oss-20b | dependencies | 2/2 | 2 | 0 |
| groq/openai/gpt-oss-20b | parallelism | 0/1 | 1 | 0 |
| groq/openai/gpt-oss-20b | recovery | 0/4 | 4 | 0 |
| groq/openai/gpt-oss-20b | routing | 0/3 | 3 | 0 |
| groq/openai/gpt-oss-20b | schema | 3/3 | 3 | 0 |
| groq/openai/gpt-oss-20b | security | 0/4 | 4 | 0 |
| groq/openai/gpt-oss-20b | tool_selection | 2/2 | 2 | 0 |
| groq/openai/gpt-oss-20b | uncertainty | 0/2 | 2 | 0 |
