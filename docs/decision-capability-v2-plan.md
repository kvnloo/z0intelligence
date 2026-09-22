# decision-capability-v2 dataset plan (draft)

Status: **design only** — do not treat v1 smoke fixtures as a production frontier.

## Goal

Build ~50–100 **independently labeled, replayable** decision examples per capability from real OMP / Tokenomics traces, with verifier-backed gold labels that do not come from the backend being scored.

## Contract sketch

- **Schema:** `decision-capability-v2` (future; not implemented in bench runner yet)
- **Provenance:** each row carries `trace_id`, `task_snapshot_id`, `verifier_id`, `label_source`, `replay_bundle_uri`
- **Label rule:** gold derived from counterfactual outcome, frozen verifier, or human adjudication — never from the candidate backend under test
- **Evidence strength:** rows count toward VALIDATED only when independently labeled and replayable end-to-end

## Priority order (capability → target n)

| Priority | Capability | Target n | Rationale |
|----------|------------|----------|-----------|
| 1 | `rlm.worker_needed` | 50–100 | Shadow lane candidate (Decider); safety-critical |
| 2 | `context_compress_needed` | 50–100 | High tokenomics leverage |
| 3 | `verification_needed` | 50–100 | Laya CPU niche; ship-gate signal |
| 4 | `tool_family_select` | 50–100 | Routing quality vs latency |
| 5 | `retry_or_escalate` | 50–100 | Currently no safe model; needs real failure taxonomy |

## Sourcing pipeline (minimal v2)

1. **Collect** OMP shadow + authority outcomes already logged via Tokenomics (`harness=z0int`, `role=decision_backend`, capability tag).
2. **Filter** rows with frozen task snapshot + independent verifier result (test pass, grant miss, escalation policy hit, etc.).
3. **Label** using verifier/counterfactual script — e.g. worker needed iff grant repair failed and isolated worker succeeded on replay.
4. **Dedupe** by `task_snapshot_id`; keep earliest adjudicated label on conflict.
5. **Export** JSONL compatible with `BenchExample` loader + extended provenance fields.
6. **Hold out** 20% by trace_id hash for promotion gates.

## Row fields (additive over v1)

```json
{
  "id": "rlm.worker_needed/omp/<trace>/<snapshot>",
  "capability": "rlm.worker_needed",
  "provenance": "omp.tokenomics.v2",
  "trace_id": "...",
  "task_snapshot_id": "...",
  "label_source": "verifier.replay",
  "verifier_id": "rlm.grant_sufficiency.v1",
  "state": { "...": "..." },
  "question": { "...": "..." },
  "gold": "worker",
  "allow_abstain": true,
  "dangerous_if_gold_worker": ["native"]
}
```

## Non-goals (this phase)

- No OMP authority wiring
- No Kerdoios routing
- No openjev_4b benchmark
- No expansion of v1 smoke fixture count (11 rows remain regression/smoke)

## Promotion gate to VALIDATED Pareto

Reuse eligibility layer:

- `validated_min_examples = 50` (configurable)
- competence gate unchanged (trivial baseline + margin + optional production floor)
- dangerous_false hard exclusion unchanged

When v2 reaches ≥50 rows per capability, re-run four-backend Pareto on v2 contract only — keep v1 as CI smoke.

## Next engineering slice (when authorized)

1. Tokenomics export adapter: `z0int tokenomics export-decisions --capability ...`
2. Verifier replay harness for label confirmation
3. `benchmarks/fixtures/decision-capability-v2/` directory + loader flag in bench CLI
4. Shadow integration (observe-only) to feed the export pipeline

---

## Implemented 2026-09-22 (the plumbing, not the dataset)

This plan's "Next engineering slice (when authorized)" was partly executed. What
landed is the **evidence plumbing**, which the plan lists as prerequisites and
which the two historical invalid comparisons showed was missing. The dataset
itself (50–100 independently labeled rows per capability) is still not built.

| planned item | status | where |
|---|---|---|
| candidate-set identity | **done** | `src/z0int/backends/comparability.py` — `hash_candidates`, ordered, with descriptions hashed separately |
| question / state hashes | **done** | `hash_state`, `hash_question`, versioned by `*_schema_id` |
| semantic comparison contract | **done** | `contract_hash` over the semantic fields; `compare_receipts` / `require_comparable` |
| `INVALID_COMPARISON` instead of a score | **done** | mismatched candidates / question / unit / split returns `INVALID_COMPARISON`; `comparable_value` raises. There is deliberately no API that returns a delta for an invalid pair |
| runtime / revision / checkpoint identity on every receipt | **done** | `src/z0int/backends/lifecycle.py` — `InvocationReceipt` (runtime_id, backend_id, model_id, revision, checkpoint_digest + kind, device, resident_before/after, load/forward/serialization/total ms, network_model_calls, autoregressive_decode_steps) |
| declared metric unit | **done** | required field on `DecisionReceipt`; `metric_unit` / `aggregation_unit`. The `recovery_action` gate now declares `accuracy / per_episode` explicitly (see below) |
| per-capability trust | **done** | `src/z0int/backends/trust.py` + `manifests/capability_trust.json`; `assert_no_global_trust` rejects a bare per-model flag |
| verifier-backed replay rows | **not done** | designed in `docs/marginal-gain-labels.md`; the replay harness does not exist yet |
| decision-runtime lifecycle ownership | **done** | `DecisionRuntime` + `runtime_status()`, surfaced as `z0int cognition runtime-status` |
| source-of-truth reconciliation | **done** | `z0int models reconcile` — manifest vs disk vs generated projection, with plane-namespaced residency |

### Invariants enforced by tests

* `tests/test_comparison_contract.py` — reproduces both historical failures: the
  nanojev mismatched-candidate-set pair (43.6% vs 94.1%) and the
  `recovery_action` unit pair (per-episode 1.000 vs per-step 0.839). Both must be
  refused.
* `tests/test_decision_runtime_and_trust.py` — runtime receipt fields, both planes
  in one status, per-capability trust, global-trust rejection.
* `tests/test_nanojev_provenance.py` — the `compiler+jev` arm is `nanojev_06b`; the
  silent `"JEV"` → `nanojev_06b` substitution cannot return.
* `tests/test_models_reconcile.py` — a generated projection may not store a bare
  `resident` key.

### Still v1 smoke

`decision-capability-v1` and its fixtures are unchanged and remain CI/smoke. The
plan's rule holds: **do not treat v1 smoke fixtures as a production frontier.**
Nothing here raised the evidence level of any capability — it made the existing
numbers comparable, and in one case refused to promote on one.
