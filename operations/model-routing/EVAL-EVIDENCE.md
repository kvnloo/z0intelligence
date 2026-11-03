# Eval evidence for O14

How measured task fit and external benchmarks reach the O14 catalog. Nothing here adds an objective, an optimizer or a router; it fills inputs the existing ones already read.

| Source | Owner | Enters as | Becomes | Never becomes |
|---|---|---|---|---|
| z0evals `studies/free-taskfit-v0` receipt (`z0eval.result.v1`) | z0evals (frozen task set + grader) | `z0int policy ingest`, source kind `z0eval_result` | `models[].task_classes[taskfit.<family>].p_accept` (basis `measured_rubric_acceptance_not_product_success`); unknown `performance.ttft_p50_ms` / `tokens_per_second` (basis `z0eval_certified_battery_median`) | product-success acceptance; promotion; a value that overwrites retained OMP TTFT samples |
| Hermes Index snapshot (z0evals `studies/hermes-index-prior-v0`) | Nous Research (source-reported) | `generate_catalog.py --benchmark <rows.json>` | `models[].benchmarks[]` with capture-stamped source URL, `[provisional]` kept in the name | `p_accept`, a floor, or any O14 input |

Run (in a scratch `Z0INT_HOME` first; the live ledger only takes receipts you mean to keep for 14 days):

```bash
Z0INT_HOME=/tmp/z0int-eval PYTHONPATH=src python3 -c 'from pathlib import Path; from z0int.policy.ingest import ingest_sources; print(ingest_sources([{"kind":"z0eval_result","path":"<abs>/result.json"}]))'
PYTHONPATH=src python3 operations/model-routing/generate_catalog.py --output /tmp/catalog.json \
  --findings /tmp/z0int-eval/findings/findings.jsonl --benchmark <z0evals>/studies/hermes-index-prior-v0/data/benchmark-rows-<ts>.json
```

Rules the generator enforces (tested in `tests/test_model_routing_eval_evidence.py`):
- Acceptance and performance findings must be `measured`, non-synthetic, unexpired at generation time, on the validated canonical hash chain, and for a provider in the catalog's scope. NVIDIA NIM (`nvidia`) is not in scope yet, so its certified findings are dropped and reported as `performance_finding_provider_not_in_catalog_scope`, never relabelled.
- Task classes are exact. A `taskfit.reason` prior says nothing about `coding` or `research`.
- Battery latency fills only metrics with no retained measurement.

What O14 still lacks for free lanes after this: `burn`, `remaining` and `time_to_reset` per model. Those are Kerdoios capacity's (kerdoios#66, turbo-router R4 #293). Until they are present, `plan_routing` reports every lane `unknown_objective_inputs`, as designed.
