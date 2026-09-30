# Pre-registration addendum A1: deterministic arm re-evaluated under host `local_order`

Parent: `PREREG.md` (v0, 1302c3a) and its results (eb7c7d3). Written and committed **before** the re-run below was executed.

## Why

v0 found the deterministic arm scored 0/48 choice accuracy on S1 because `plan_route` put the host's own
`local/qwen3-0.6b-q8` (mbp Vulkan, 1236 ms) first. Stack commit b072468 (`integrate/claude-code-z0-stack`,
merged here in 8f9883d) lets a host set `local_order`. This host now sets `local_order = ["groot", "local"]`
in `~/.z0int/config/worker_routing.local.json`, so local-only work goes to groot, whose first model is
`qwen3-8b-q4km`. That fix has not been evaluated. This addendum evaluates it.

## What is re-run (and what is not)

- Arms: `deterministic` (current `plan_route` → `candidates[0]` with the current host policy), plus the offline reference
  baselines `cheapest` and `strongest` (S1 only), which the policy change must not move.
- No LLM arm is re-run. No model is called. The v0 raw rows are not touched, and `results_v0.json` is not rewritten.
- Sets S1 (n=48) and S2 (n=11), same items, same per-candidate outcome files, same costs, same gold (cheapest passing, 1.05× ties).
  No retuning, no new items, no new candidates. S3 is unlabeled and is reported only as agreement with the v0 deterministic choice.
- **Frozen-input check (hard fail):** before scoring, the script asserts that every S1/S2 item's legal candidates, per-candidate
  pass/fail, cost, and gold set exactly equal the v0 `results_v0.json` per-item rows. The shas of the inputs must match too, except
  `host_override`, which is expected to change (c5005a80… → the file with `local_order`), and `worker_routing.py`.
  If any check fails, nothing is scored and that failure is the result.
- S2 deterministic stays `rule_always_act`. `local_order` does not touch it, so S2 must reproduce v0 exactly (8/11, pass 0.73, regret 0).

## Metrics

These are the same as v0: choice accuracy, pass rate, and mean cost-weighted regret, for S1, S2, and pooled labeled (n=59).
Each is reported next to the v0 row. There is no significance test. This is a descriptive before/after on one deterministic arm.

## Prediction and reading (fixed now)

- Prediction: S1 deterministic becomes `groot/qwen3-8b-q4km` on all 48 items, so it equals the v0 `strongest` row
  (4/48 choice accuracy, pass 0.90, regret 0.13). Pooled, that is 12/59, pass about 0.86, regret about 0.11.
- The fix counts as **confirmed** if S1 deterministic pass rate is ≥ 0.85 and S1 mean regret is below the v0 `cheapest` S1 regret (0.43).
  Otherwise it is **not confirmed**.
- Choice accuracy will stay low by construction, because gold is the *cheapest* passing candidate and 8B is rarely cheapest.
  The fix trades choice accuracy for delivered quality. That is the intended operating point, not a regression.
- The v0 orchestrator decision ("do not continue in shadow") is not re-adjudicated. Criterion (b) was scored against the old
  deterministic arm. Any comparison of `orch_think` against the new deterministic arm is exploratory only.

## Execution

`.venv/bin/python benchmarks/orchestrator_router/rerun_local_order.py` → `results_a1_local_order.json`.
