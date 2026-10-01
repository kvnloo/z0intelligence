# Pre-registration: summarize-faithfulness v0 (decision #20)

Registered before any model output on this set was produced or seen. Set counts and sha256 are in
`sets_manifest.json` (committed with this file); the items stay in `~/.cache/z0-summ-faith/sets/items.jsonl`.

## Question

speed-offload v0 (`docs/speed-offload-v0.md`) marked `summarize_tool_output` speed-qualified for
groot `qwen3-8b-q4km` on a format-and-facts check over 60 `git show --stat` items (local 60/60, haiku 59,
sonnet 37). That check did not test faithfulness. Decision #20: is groot qwen3-8b faithful enough to take
this class live, meaning it invents no more facts than Haiku and drops no more critical facts than Haiku?

## Set (fresh, n = 160, built by `build_sets.py`, seed 20261001)

All items are real tool invocations run on z0's own repos and CI. None of the 60 v0 items is reused.

| kind | n | source |
|---|---|---|
| pytest | 40 | `pytest -q` on test files of a detached z0intelligence worktree (under `~/.cache`) after one random source mutation of a module the tests import (operator flips, constant changes, or an injected missing import that breaks collection). Built: 30 failing, 7 passing, 3 collection errors (exit 1/0/2). |
| git | 40 | z0intelligence, z0, z0evals, evolution-lab, firstmate, kerdoios, o8, dash: 15 `git show --stat`, 15 `git diff --stat A..B` (2 to 6 commits), 10 unified `git diff` patches (1 to 4 files). |
| build | 40 | z0's own Python repos (kvnloo origins, upstream forks excluded: z0intelligence, evolution-lab, aodl, pii, tokenomics, hermes-mesh-keel): 15 `ruff check` (random rule set and target; 11 failing), 11 `mypy` on one file (8 failing), 8 `python -m compileall` on a copy of a file with one closing parenthesis removed, 6 `uv build` (one per repo, as they are). |
| gh | 40 | read-only: 16 `gh run view --log-failed` windows (40 lines before to 4 after the first `##[error]`), 8 `gh run view` summaries of failed runs, 8 `gh run list` pages, 4 `gh pr checks`, 4 `gh pr view` (z0intelligence). |

Each context is `$ <command>`, the output, and `[exit code N]`, at most 6000 characters (oversized outputs are
re-run with shorter tracebacks or skipped; `gh pr view` bodies are truncated with a marker).

Task text, the same for every item apart from `{what}` (checked to classify as `summarize_tool_output` with
`z0int.speed_offload.classify_task`):

> Summarize this {what} output in at most 40 words for an engineer who has not seen it. Keep the key facts:
> whether it succeeded or failed, the counts, and the names of what failed or changed. Return only the summary.

System prompt: `z0int.worker_routing.SYSTEM` (as in v0). `max_tokens` 120 for local arms.

## Gold critical facts (deterministic, from the shown text only)

* outcome (fail/pass) where the tool has one: exit code, CI conclusion, check states. Not for git.
* counts: failed tests, collection errors (or passed tests if nothing failed), ruff `Found N errors`, mypy
  `Found N errors`, CI `exit code N`, files changed (git stat), failed runs in a `gh run list` page, PR number.
* names: failing test functions, files with lint/type/syntax errors, changed files, failed jobs/steps/checks/
  workflows, wheel/sdist names, PR state. Up to 3 distinct names: each is its own fact. More than 3: one fact,
  satisfied by naming any of them. For git `--stat` items with more than 2 files: one "any changed file" fact.
* secondary (reported, not in the rule): insertions, deletions, passed count beside failures, rule codes,
  syntax-error line, error type, mypy file count.

## Arms

| arm | route | role |
|---|---|---|
| local8b | groot `qwen3-8b-q4km`, farm 100.113.138.100:11530, temperature 0, one request at a time | primary |
| haiku | `claude -p --model haiku` headless (no tools, no settings, no session) | comparator |
| sonnet | `claude -p --model sonnet` headless | reported |
| local14b | groot `qwen3-14b-q4km`, same settings | exploratory, no decision |

Only normalisation, applied to every arm: remove `<think>...</think>` blocks and strip whitespace.

## Metrics

1. **Hallucination (primary).** A blinded judge, Claude Sonnet via `claude -p` (`judge.py`), receives the tool
   output, one summary and the item's critical facts. It never receives the arm, model, run id or latency;
   summaries from all arms are pooled and judged in seeded random order. It splits the summary into atomic
   claims labelled SUPPORTED / UNSUPPORTED / CONTRADICTED (counting and simple arithmetic count as supported;
   unshown causes count as unsupported even if hedged). An item is **hallucinated** if any claim is
   UNSUPPORTED or CONTRADICTED. Hallucination rate = hallucinated items / judged items.
   Deterministic secondary check (reported, not in the rule): numbers, hashes, file names and `test_*` names
   in the summary that do not occur in the output.
2. **Critical-fact recall (primary).** Per item, the fraction of its critical facts present in the summary,
   matched deterministically (`score.py`): numbers as digits or number words (`one`/`a single` for 1); names
   case-insensitively as written, by basename, or by stem of at least 6 characters; names of 3 or more words
   also match when at least 60% of their content words (length >= 4) appear; outcome by fixed fail/pass
   regexes. Arm recall = mean over items. Sensitivity (reported, not in the rule): the judge's per-fact
   "conveyed" answers.
3. **Format.** Non-empty, at most 40 words, not cut off by the token limit.
4. Latency: local wall time per HTTP call; frontier `duration_api_ms` (as in v0).

## Decision rule

Paired by item, local8b minus haiku, one-sided 95% bounds from a paired percentile bootstrap (10,000
resamples, seed 20261001):

* hallucination non-inferior: upper bound of (rate_local8b - rate_haiku) **< +0.02**, and
* recall non-inferior: lower bound of (recall_local8b - recall_haiku) **> -0.05**.

groot qwen3-8b qualifies for live `summarize_tool_output` offload only if both hold. Otherwise the class
stays shadow-only. sonnet and local14b comparisons are reported and decide nothing.

Errors: a non-quota error in local8b counts as a failure for that item (recall 0, hallucinated). Items where
haiku errored are dropped from the paired comparison and counted. Judge parse failures are retried twice; an
item still unjudged for either arm is dropped from the hallucination comparison and counted. If more than 10%
of items are dropped, the verdict is "incomplete", not "qualifies".

## Operations

* At most 3 concurrent `claude` processes in total (arms and judge run one after another, `--jobs 3`).
* Usage limits: the first usage/session-limit error stops the run; it is recorded with its time. Those rows
  (and only those) are retried after the limit resets; any such retry is listed as a deviation.
* groot GPU may be shared: one request at a time, GPU utilisation sampled every 40 calls; qwen3-14b runs only
  after qwen3-8b has finished.
* Committed: code, this file, `sets_manifest.json`, and afterwards aggregates, run ids and sha256 values.
  Items, raw outputs and judge rows stay under `~/.cache/z0-summ-faith`.

## If it qualifies

Prepare (not merge) a commit on this branch adding a groot capability entry for this class to
`manifests/capabilities.v1.json` with an evidence receipt under `manifests/capability-evidence/`, and state
exactly which live behaviour would change.

## Known limits, stated in advance

* One judge model (Sonnet) that is also an arm; it is blinded to the arm but may favour its own style.
  The deterministic checks are reported beside it to expose disagreement.
* n = 160 gives a wide bound for a 2 pp margin; the rule can fail through imprecision while the arms are
  equivalent in truth. That is reported as "not shown non-inferior", not as "inferior".
* Mutation-generated test failures are real pytest output but not a sample of real failures.
* Deterministic name matching can miss paraphrases (any arm); the judge recall is the check on that.
