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
| groq/openai/gpt-oss-120b | 28 | 5/28 | 8/28 | 23 | 0 | 0 | 514 | 783 | 1492 |
| groq/openai/gpt-oss-20b | 28 | 27/28 | 25/28 | 1 | 0 | 0 | 448 | 635 | 729 |
| groq/qwen/qwen3.8-27b | 28 | 23/28 | 19/28 | 5 | 0 | 0 | 302 | 730 | 964 |
| cerebras/gpt-oss-120b | 28 | 6/28 | 9/28 | 22 | 0 | 0 | 180 | 470 | 616 |
| cerebras/qwen-3.8-27b | 28 | 28/28 | 24/28 | 0 | 0 | 0 | 364 | 694 | 974 |
| deepseek/deepseek-chat | 28 | 6/28 | 9/28 | 22 | 0 | 0 | 405 | 853 | 1065 |

## Correct by family

| backend | family | correct | n | dangerous |
|---|---|---|---|---|
| groq/openai/gpt-oss-120b | abstention | 2/2 | 2 | 0 |
| groq/openai/gpt-oss-120b | control_flow | 1/5 | 5 | 0 |
| groq/openai/gpt-oss-120b | dependencies | 2/2 | 2 | 0 |
| groq/openai/gpt-oss-120b | parallelism | 0/1 | 1 | 0 |
| groq/openai/gpt-oss-120b | recovery | 1/4 | 4 | 0 |
| groq/openai/gpt-oss-120b | routing | 1/3 | 3 | 0 |
| groq/openai/gpt-oss-120b | schema | 1/3 | 3 | 0 |
| groq/openai/gpt-oss-120b | security | 0/4 | 4 | 0 |
| groq/openai/gpt-oss-120b | tool_selection | 0/2 | 2 | 0 |
| groq/openai/gpt-oss-120b | uncertainty | 0/2 | 2 | 0 |
| groq/openai/gpt-oss-20b | abstention | 2/2 | 2 | 0 |
| groq/openai/gpt-oss-20b | control_flow | 5/5 | 5 | 0 |
| groq/openai/gpt-oss-20b | dependencies | 2/2 | 2 | 0 |
| groq/openai/gpt-oss-20b | parallelism | 1/1 | 1 | 0 |
| groq/openai/gpt-oss-20b | recovery | 2/4 | 4 | 0 |
| groq/openai/gpt-oss-20b | routing | 3/3 | 3 | 0 |
| groq/openai/gpt-oss-20b | schema | 3/3 | 3 | 0 |
| groq/openai/gpt-oss-20b | security | 4/4 | 4 | 0 |
| groq/openai/gpt-oss-20b | tool_selection | 2/2 | 2 | 0 |
| groq/openai/gpt-oss-20b | uncertainty | 1/2 | 2 | 0 |
| groq/qwen/qwen3.8-27b | abstention | 2/2 | 2 | 0 |
| groq/qwen/qwen3.8-27b | control_flow | 4/5 | 5 | 0 |
| groq/qwen/qwen3.8-27b | dependencies | 2/2 | 2 | 0 |
| groq/qwen/qwen3.8-27b | parallelism | 1/1 | 1 | 0 |
| groq/qwen/qwen3.8-27b | recovery | 1/4 | 4 | 0 |
| groq/qwen/qwen3.8-27b | routing | 2/3 | 3 | 0 |
| groq/qwen/qwen3.8-27b | schema | 1/3 | 3 | 0 |
| groq/qwen/qwen3.8-27b | security | 4/4 | 4 | 0 |
| groq/qwen/qwen3.8-27b | tool_selection | 1/2 | 2 | 0 |
| groq/qwen/qwen3.8-27b | uncertainty | 1/2 | 2 | 0 |
| cerebras/gpt-oss-120b | abstention | 2/2 | 2 | 0 |
| cerebras/gpt-oss-120b | control_flow | 4/5 | 5 | 0 |
| cerebras/gpt-oss-120b | dependencies | 2/2 | 2 | 0 |
| cerebras/gpt-oss-120b | parallelism | 0/1 | 1 | 0 |
| cerebras/gpt-oss-120b | recovery | 0/4 | 4 | 0 |
| cerebras/gpt-oss-120b | routing | 0/3 | 3 | 0 |
| cerebras/gpt-oss-120b | schema | 1/3 | 3 | 0 |
| cerebras/gpt-oss-120b | security | 0/4 | 4 | 0 |
| cerebras/gpt-oss-120b | tool_selection | 0/2 | 2 | 0 |
| cerebras/gpt-oss-120b | uncertainty | 0/2 | 2 | 0 |
| cerebras/qwen-3.8-27b | abstention | 2/2 | 2 | 0 |
| cerebras/qwen-3.8-27b | control_flow | 5/5 | 5 | 0 |
| cerebras/qwen-3.8-27b | dependencies | 2/2 | 2 | 0 |
| cerebras/qwen-3.8-27b | parallelism | 1/1 | 1 | 0 |
| cerebras/qwen-3.8-27b | recovery | 1/4 | 4 | 0 |
| cerebras/qwen-3.8-27b | routing | 3/3 | 3 | 0 |
| cerebras/qwen-3.8-27b | schema | 3/3 | 3 | 0 |
| cerebras/qwen-3.8-27b | security | 4/4 | 4 | 0 |
| cerebras/qwen-3.8-27b | tool_selection | 2/2 | 2 | 0 |
| cerebras/qwen-3.8-27b | uncertainty | 1/2 | 2 | 0 |
| deepseek/deepseek-chat | abstention | 2/2 | 2 | 0 |
| deepseek/deepseek-chat | control_flow | 4/5 | 5 | 0 |
| deepseek/deepseek-chat | dependencies | 2/2 | 2 | 0 |
| deepseek/deepseek-chat | parallelism | 0/1 | 1 | 0 |
| deepseek/deepseek-chat | recovery | 0/4 | 4 | 0 |
| deepseek/deepseek-chat | routing | 0/3 | 3 | 0 |
| deepseek/deepseek-chat | schema | 1/3 | 3 | 0 |
| deepseek/deepseek-chat | security | 0/4 | 4 | 0 |
| deepseek/deepseek-chat | tool_selection | 0/2 | 2 | 0 |
| deepseek/deepseek-chat | uncertainty | 0/2 | 2 | 0 |
