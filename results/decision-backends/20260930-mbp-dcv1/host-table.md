### 20260930-mbp-dcv1  (fixtures: decision-capability-v1/examples.jsonl, n=11)

| backend | device | status | acc | mean Brier | ECE | p50 ms | p95 ms | cold load ms | peak RSS MB | peak VRAM MB | per-capability acc | frontier caps |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| laya_421m | cpu | ok | 6/11 (54.5%) | 0.577 | 0.191 | 5424 | 6790 | 69528 | 2375 | 574 | context_compress_needed 0/2; retry_or_escalate 1/2; worker_needed 1/3; tool_family_select 2/2; verification_needed 2/2 | - |
| julia_1 | cpu | ok | 5/11 (45.5%) | 0.966 | 0.512 | 587 | 1288 | 65163 | 1914 | 574 | context_compress_needed 1/2; retry_or_escalate 2/2; worker_needed 0/3; tool_family_select 0/2; verification_needed 2/2 | - |
| nanojev_06b | cpu | ok | 4/11 (36.4%) | 0.773 | 0.375 | 9338 | 14593 | 39731 | 3255 | 574 | context_compress_needed 1/2; retry_or_escalate 1/2; worker_needed 1/3; tool_family_select 0/2; verification_needed 1/2 | - |
| openjev_06b | cpu | ok | 5/11 (45.5%) | 1.013 | 0.478 | 7877 | 9753 | 8803 | 4611 | 574 | context_compress_needed 1/2; retry_or_escalate 1/2; worker_needed 2/3; tool_family_select 0/2; verification_needed 1/2 | - |
| decider_2b | cpu | ok | 7/11 (63.6%) | 0.443 | 0.304 | 17407 | 19883 | 5022 | 5072 | 574 | context_compress_needed 1/2; retry_or_escalate 1/2; worker_needed 2/3; tool_family_select 2/2; verification_needed 1/2 | - |
| llama_http:functiongemma_270m_q8 | vulkan0 | ok | 3/11 (27.3%) | 0.928 | 0.379 | 500 | 969 | 4815 | 2251 | 911 | context_compress_needed 0/2; retry_or_escalate 1/2; worker_needed 1/3; tool_family_select 0/2; verification_needed 1/2 | - |
| llama_http:qwen3_06b_q8 | vulkan0 | ok | 5/11 (45.5%) | 0.993 | 0.386 | 767 | 1121 | 5519 | 2251 | 1680 | context_compress_needed 1/2; retry_or_escalate 1/2; worker_needed 1/3; tool_family_select 1/2; verification_needed 1/2 | - |
| llama_http:qwen3_17b_q4 | vulkan0 | ok | 7/11 (63.6%) | 0.836 | 0.501 | 1523 | 2760 | 14230 | 2222 | 1707 | context_compress_needed 1/2; retry_or_escalate 1/2; worker_needed 3/3; tool_family_select 1/2; verification_needed 1/2 | - |
| llama_http:hammer21_15b_q4 | vulkan0 | ok | 6/11 (54.5%) | 0.619 | 0.212 | 1194 | 1489 | 10223 | 1731 | 1575 | context_compress_needed 1/2; retry_or_escalate 1/2; worker_needed 1/3; tool_family_select 2/2; verification_needed 1/2 | - |

