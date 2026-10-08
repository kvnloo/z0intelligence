Frozen verified-context development cohort

Five planned live cases, one attempt each, no retry or fallback:
  full-complete
  minimal-complete
  full-missing_source_identity
  minimal-missing_source_identity
  minimal-contradictory_source_identity

The full contradiction control is retained in offline preparation but excluded
from inference before observing any new answers. Its unchanged 16,096-byte
context, serialized with the existing prompt and previously observed native
system boilerplate, produces an approximately 21,422-byte request. That exceeds
the unchanged 20,000-byte cap. This estimate is not a cohort transport witness.
No cap increase is authorized. There are TWO matched token-efficiency pairs;
the minimal contradiction is a separate negative control.

The original discriminator creates all three evidence controls. ContextPacket
and selection_boundary.materialize render each selected exact-ref subset.
The host pins limits and retains every available identity declaration in the
minimal arm. Contradictions and unresolved gaps are never removed. No excerpts,
provenance, prompt text, task labels, answer labels or checker output are edited
into model-visible content. This is inspected host policy, not learned selection.

The independent original frozen_check.py is unchanged. cohort_check.py adds a
fixed visibility rule: the minimal arm cannot cite omitted refs. Both checker
sources, the driver, renderer, native sources, protocol, pools and preparation
artifacts are frozen before inference. The wrapper runs only in the observer.

The earlier full-context Solar JSON success used a different output contract.
Its 7,645 input tokens / 183 output tokens / $0.00041885 remain prior evidence,
not a comparator arm in this abstention-capable task. Earlier failures and the
old causal pair are untouched.

Execution reuses live_baseline.run and OneRequestPolicy. Every case runs fresh
state at the SAME absolute _staging/profile path and cwd. Completed staging
artifacts are moved, never overwritten, into runs/<case_id>. An exclusive
execution marker and per-case markers prohibit reexecution after a crash.
The first admitted native request creates wire-template.json BEFORE forwarding.
A scoped subclass adds only an admission assertion: replace that case's exact
context with a sentinel, then compare the COMPLETE native request to the template.
System text, user prompt, metadata, output cap, model and response format must
match; no path/date/profile masking is performed. Original one-POST policy and
byte limits still apply. A midnight or cwd change therefore rejects the call.

At most five physical POSTs, 4,096 output tokens each, 20,000 serialized bytes,
120 seconds per case, zero tools, fixed cheap Nous Solar and JSON-object mode.
The raw-catalog estimate at all input/output caps is $0.009096 total. This is NOT
a billing guarantee; the original route's $0.02/run resource-eligibility threshold
is unchanged. Actual response usage and cost, including failed work, are retained.
Unknown usage or interrupted physical accounting remains unknown, never zero.

Transport/resource/source/identity/wire failures stop remaining attempts.
Semantic failures remain observations and do not stop the planned cohort.
Replay independently grades saved answers, compares them with provider output,
validates raw wire/physical records, rechecks the legal renderer and sources,
and reports all attempted work. No provider calls occur during replay.

Nous response provider may be null. Endpoint and model attribution do not
identify the backend provider, so no backend causal attribution is claimed.
Two observed reductions are development observations, not speedup, holdout
certification, population noninferiority, thermal benefit or model necessity.

Root commands (all run from this repository root, using the existing venv):

  export PYTHONPATH=src
  export HERMES_SOURCE_TREE=/workspace/hermes-factory/experiments/hermes-state-v0/hermes
  /workspace/hermes-factory/venvs/hermes-state-v0/bin/python -m pytest -q \
    benchmarks/thermocontext/hermes_phase_c/verified_context_cohort/test_cohort.py

Prepare a NEW create-only path, then independently inspect its freeze/protocol:

  /workspace/hermes-factory/venvs/hermes-state-v0/bin/python \
    benchmarks/thermocontext/hermes_phase_c/verified_context_cohort/run_cohort.py \
    --out /workspace/hermes-factory/reviews/hermes-verified-cohort-01 \
    --hermes-repo "$HERMES_SOURCE_TREE" \
    --route-file /workspace/hermes-factory/reviews/nous-provider-next/routes/nous-cheap-solar.json

After root's review, root alone may execute that existing freeze:

  /workspace/hermes-factory/venvs/hermes-state-v0/bin/python \
    benchmarks/thermocontext/hermes_phase_c/verified_context_cohort/run_cohort.py \
    --out /workspace/hermes-factory/reviews/hermes-verified-cohort-01 \
    --nous-auth-home /home/agent/.z0int/config/hermes-nous-test --execute

Offline replay (no auth profile needed):

  /workspace/hermes-factory/venvs/hermes-state-v0/bin/python \
    benchmarks/thermocontext/hermes_phase_c/verified_context_cohort/run_cohort.py \
    --out /workspace/hermes-factory/reviews/hermes-verified-cohort-01 --replay

The implementation lane performs preparation and synthetic tests only. It does
not read auth credentials or execute provider calls. Synthetic test transports
are confined to temporary test directories and are not experiment results.
