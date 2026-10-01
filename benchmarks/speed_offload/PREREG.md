# Speed-offload v0: pre-registration

Committed before any local-vs-frontier measurement on these sets. Everything below is fixed: if a rule
turns out to be wrong it is reported as a deviation, not silently changed.

## Question

For a given class of bounded worker task, is the host-local model (groot `qwen3-8b-q4km`) **non-inferior on
quality** to the frontier and **faster at p95**? If so the class is *speed-qualified*: offloading it is the
right call under every posture, BURN included, because it returns the same answer sooner. Classes that are
not speed-qualified keep today's posture behaviour (BURN returns them to the parent).

## Arms

| arm | how it is called | latency recorded |
|---|---|---|
| `local` | OpenAI-compatible `POST /v1/chat/completions` to `100.113.138.100:11530`, model `qwen3-8b-q4km`, temperature 0, server `--reasoning off`, one request at a time | wall clock on the mbp, request to parsed response (tailnet RTT included) |
| `haiku` | `claude -p --model haiku --output-format json --setting-sources "" --strict-mcp-config --tools "" --no-session-persistence --system-prompt SYSTEM`, at most 3 concurrent | `duration_api_ms` (primary; excludes CLI start, favours the frontier) and wall clock (reported) |
| `sonnet` | same as `haiku` with `--model sonnet` | same |

All arms get the same messages: the worker `SYSTEM` prompt from `z0int.worker_routing` and the same user
text (task + context). Output normalisation, identical for every arm: strip `<think>…</think>` blocks,
strip surrounding whitespace, strip one surrounding Markdown code fence. Nothing else.

## Classes, sets and deterministic checks

Sets are built by `build_sets.py` from z0's own artefacts into `~/.cache/z0-speed-offload/sets/` (never
committed). Only counts and sha256 values go to git (`sets_manifest.json`). Seeded (`seed=20260930`).

| class | n | source | pass iff |
|---|---|---|---|
| `evidence_sufficiency` | 48 | `benchmarks/data/authored144.jsonl`, family `evidence_interpretation`, split `test` (the frozen set Jev and the groot bench used) | lower-cased output equals the gold option id |
| `extract_json` | 60 | `~/.z0int/receipts/decisions.jsonl` (frozen copy), stratified by capability/status, every rare stratum included; receipt rendered as flattened `path = value` lines | output parses as a JSON object whose keys are exactly `capability_id, status, provider, model, latency_ms, ok` and every value equals the gold (numbers within 1e-6) |
| `classify_file_type` | 60 | 20-line excerpts (from line ≥ 5) of files tracked in this repo; 10 each of python, markdown, json, yaml, shell, typescript | lower-cased output equals the label |
| `summarize_tool_output` | 60 | real `git show --stat` output of commits in this repo | ≤ 40 words AND the number of files changed appears as a digit string AND at least one changed file's basename (or its stem) appears |
| `short_rewrite` | 53 | sentences from tracked `*.md` files with ≥ 1 backticked span, 18–70 words, feasible (span words + 2 ≤ limit) | ≤ ceil(0.6 × original words) words AND every backticked span appears verbatim AND output differs from input AND has no newline |

An arm error (timeout 120 s, HTTP error, `is_error`) counts as a **fail** for quality and is excluded from
latency percentiles; errors are reported per arm.

## Decision rules (per class)

* Frontier comparator for quality: the frontier arm (haiku or sonnet) with the **higher** pass rate on that class.
* Frontier comparator for latency: the frontier arm with the **lower** p95 `duration_api_ms` on that class.
  Both choices are deliberately the hardest bar for the local model.
* Quality, paired by item: d = pass_local − pass_frontier. One-sided 95% lower bound by paired bootstrap
  (10 000 resamples, seed 0, 5th percentile of the resampled mean d).
* **Speed-qualified** iff all of:
  1. lower bound of d > −0.10 (non-inferiority margin δ = 0.10);
  2. local pass rate ≥ 0.80 (absolute reliability floor);
  3. local warm p95 latency < frontier p95 latency (warm = excluding the first local call of the run, which
     may load the model; cold-start time reported separately);
  4. local error rate ≤ 5%.
* **Cost-eligible** (used only under OFFLOAD to widen offload) iff lower bound of d > −0.15 and local pass
  rate ≥ 0.70 and local error rate ≤ 5%, regardless of latency.
* A class meeting neither is `not_equivalent`.

No other margins, comparators or subsets will be reported as the result. Per-arm pass rates, p50/p95 and
discordant-pair counts are reported for every class whatever the outcome.

## Policy under test (shadow only)

`z0int.speed_offload` classifies a `route_worker` task text into one of the classes above by conservative
patterns (no match or several matches -> `unknown`). Then:

| class status | BURN | OFFLOAD | BALANCED / RESERVE / unavailable |
|---|---|---|---|
| speed-qualified | offload for speed (local route) | offload for speed | offload for speed |
| cost-eligible | posture decision (return to parent) | offload for cost | posture decision |
| not equivalent | posture decision | posture decision | posture decision |
| unknown class | parent | parent | parent |

A speed decision also requires: the recorded route is still a validated $0 route and available; the task's
context is within the size envelope the class was measured on; the evidence file's sha256 matches. Any
failure falls back to the posture decision (fail closed to today's behaviour). The plan is only annotated
(`plan['speed_offload']`, `would_offload_for_speed`); no live configuration changes in v0.

## Known limits, stated in advance

* Small n (48–60 per class; short_rewrite has only 53 qualifying sentences): the non-inferiority bound is wide; a class can fail to qualify while being
  equivalent in truth.
* groot's GPU is shared (another tenant was rendering at 98% utilisation at registration time). Local
  latency is measured under whatever load exists; GPU utilisation is sampled and reported.
* The frontier latency uses `duration_api_ms` from Claude Code headless, which still includes Claude Code's
  own request framing; an in-context parent answer would have a different (not necessarily smaller) latency.
* The set sources are z0 artefacts but the tasks are synthetic wrappers around them; they are not a sample of
  real `route_worker` traffic (there is too little of it to sample).
