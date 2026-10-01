# Speed-first offload v0 (shadow)

Resource posture (v1b) only reasons about cost. Under BURN it returns bounded work to the parent frontier,
even when a verified host-local model would return the same answer sooner. v0 adds a speed-first rule keyed
by **task class**, backed by a pre-registered local-vs-frontier measurement. It annotates plans and changes
no routing.

## Pieces

| piece | where |
|---|---|
| pre-registration (committed before measuring, `d716a38`) | `benchmarks/speed_offload/PREREG.md` |
| frozen-set builder, runner, scorer | `benchmarks/speed_offload/{build_sets,run,score}.py` |
| set counts and sha256 (the items stay in `~/.cache/z0-speed-offload/sets`) | `benchmarks/speed_offload/sets_manifest.json` |
| task-class registry with equivalence records | `manifests/task_classes.v0.json` |
| evidence file (aggregates, run ids, run-file sha256), cited by sha256 | `~/.z0int/evidence/speed-offload-v0.json` |
| policy | `src/z0int/speed_offload.py` |
| shadow hooks | `worker_routing.speed_annotate` (route_worker plans) and `intelligence.speed_annotate_route` (capability routes, including PARENT_ONLY) |

## Policy

| class status | BURN | OFFLOAD | BALANCED / RESERVE |
|---|---|---|---|
| speed_qualified | offload for speed | offload for speed | offload for speed |
| cost_eligible | posture (parent) | offload for cost | posture |
| not_equivalent | posture | posture | posture |
| unknown class | parent | parent | parent |

The class comes from conservative patterns on the task text. No match, or more than one class matching,
means `unknown`. A speed or cost decision also needs all four guards to pass: the evidence sha256 matches,
the recorded route (`groot/qwen3-8b-q4km`) is still a validated $0 route with a nonzero cap and is
available, and the context is no larger than the largest one measured for that class. If any guard fails,
the posture decision stands. The output is `plan['speed_offload']`: `action`, `would_offload_for_speed`,
`candidates`, `reason`, and `shadow: true`.

## Results (n = 48–60 per class; local = groot qwen3-8b-q4km, generative, temperature 0)

| class | local | haiku | sonnet | 95% lower bound of local − best frontier | p95 local / fastest frontier (API) | status |
|---|---|---|---|---|---|---|
| summarize_tool_output | **60/60** | 59/60 | 37/60 | +0.000 (vs haiku) | **893 / 2054 ms** (2.3×) | **speed_qualified** |
| classify_file_type | 48/60 | 59/60 | 59/60 | −0.267 | 370 / 1419 ms | not_equivalent |
| evidence_sufficiency | 31/48 | 47/48 | 48/48 | −0.479 | 146 / 1863 ms | not_equivalent |
| extract_json | 8/60 | 49/60 | 60/60 | −0.933 | 1794 / 1970 ms | not_equivalent |
| short_rewrite | 2/53 | 48/53 | 22/53 | −0.943 | 741 / 4224 ms | not_equivalent |

No class is `cost_eligible`. There were no local errors. GPU utilisation on groot during the local run was
14–84%, because another tenant was rendering. Two local cold starts happened (0.42 s and 0.30 s); the model was
already resident.

What to make of it:

* **Only one class qualifies, and the speed gain is about 2×, not 30×.** The figure of roughly 90 ms per item
  comes from the logprob decision mode. A generative worker call costs 0.1–1.8 s end to end. The frontier
  comparator is `duration_api_ms`, which excludes the CLI start and so favours the frontier. Measured by wall
  clock, `claude -p` took 4–6 s at p95.
* **summarize_tool_output passes a format-and-facts check, not a faithfulness check.** The check is: at
  most 40 words, the files-changed count written as digits, and at least one changed file named. Sonnet's
  37/60 is mostly that check's strictness: 21 of its 23 failures have no digit count, usually because the
  count is written as a word ("Two files changed") and sometimes because it is left out.
  Haiku, the quality comparator, scored 59/60.
* **evidence_sufficiency:** the generative text worker gets 31/48. The same model in logprob decision mode
  gets 43/48 at 90 ms (groot bench). Its errors are mostly "insufficient" given for "contradicted". If this
  class goes local, it should go as a typed decision function, not as a text worker. That route was not part
  of this pre-registration.
* extract_json and short_rewrite fail clearly. The 8B model hallucinates field values and does not shorten
  the text.
* Frontier latency was inflated after the session-limit reset (haiku p95 reached 27–55 s on two classes).
  The latency comparator is always the faster frontier arm, which in every class was sonnet at 1.4–4.2 s.

Deviation: the subscription session limit cut off part of the frontier arms. Those rows were re-run after the
reset, which is recorded in PREREG.md. Quality and latency rules are otherwise as registered.

## Why general work never reaches groot today

Coordinator report: in lean sessions, `route_worker` returned PARENT_ONLY every time. The tests in
`tests/test_speed_offload.py` pin down the causes:

1. `route_worker` (MCP) goes to `/v1/intelligence`, then to `intelligence.route`, which routes through
   `manifests/capabilities.v1.json`. That registry has **no groot entry**, and no non-Jev entry is
   `eligible`.
2. With `free_only`, a function name the registry does not know, or a function with no $0 entry, returns
   `free_only: no validated zero-cost candidate…` (an empty candidate list reaches the free_only check
   first). A known text function gets past that check and then returns `No available eligible execution
   candidate`.
3. Even past the registry, `plan_route` only uses `local_order` (groot first) for `local-only` tasks.
   General categories use `function_orders`/`fallback_order` (cerebras, groq, deepseek), and on this host
   none of those has a usable $0 route.

The speed annotation is keyed by task class, not by function or category. It is attached to both the
worker plan and the capability route, so a speed-qualified class records `groot/qwen3-8b-q4km` even when
the live answer is PARENT_ONLY. Acting on it would mean either enforcing the speed decision in those two
places or registering groot capability entries. Both are out of scope for v0, which is shadow only.

## Limits

The sets are small. The tasks are synthetic wrappers around z0 artefacts, not sampled `route_worker`
traffic. The class patterns are untested on real traffic, so the shadow annotations should be collected
before anything is enforced. Claude Code has no temperature control.
