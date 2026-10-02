Prompt-only source-claim contract v2

The predecessor five-case cohort is complete with five semantic failures. All
five provider attempts and their consumed work stay unchanged. This separately
versioned study narrows the target to reporting declarations in supplied records,
not independent verification that an experiment occurred. It clarifies the exact
existing output fields and their unknown/conflict semantics. It is not a rescue,
regrading, holdout, or pooled extension of the predecessor.

Only the prompt changes. The five context/selection files, checker sources,
native driver, route, JSON-object format, model and caps remain identical.
The new prompt does not reveal the answer revision, expected per-case outcomes,
or hidden checker data. Both original checkers remain host-only and unchanged.

run_study.py is a thin adapter over verified_context_cohort/run_cohort.py. No old
code or completed result is edited. Preparation pins all predecessor files and
source bindings plus the new prompt/adapter/protocol. The new native-wire template
is seeded BEFORE inference from the predecessor template with only its prompt
changed. Missing or edited templates fail validation; there is no template fallback.

Execution must use the predecessor cwd and its now-absent _staging/profile path,
declared as a shared transient execution resource. This preserves model-visible
system paths. Each fresh profile is moved into the NEW study's runs/<case_id>.
The first and every later forwarding must pass the original full-wire guard.
A date or other boilerplate change rejects forwarding. Prepared wire sizes are
checked against 20,000 bytes; the actual transport guard remains authoritative.

Maximum five POSTs, one per case, 4,096 output tokens and 120 seconds per case;
no retries or fallback. Root alone executes after independent review. Transport
failures stop, semantic failures are retained, and old/new outcomes stay separate.
Provider=null still precludes backend causal attribution.

Offline prepare from the NEW worktree (the implementation lane stops here):

  PYTHONPATH=src /workspace/hermes-factory/venvs/hermes-state-v0/bin/python \
    benchmarks/thermocontext/hermes_phase_c/claim_contract_v2/run_study.py \
    --prior-cohort /workspace/hermes-factory/reviews/hermes-verified-cohort-01 \
    --out /workspace/hermes-factory/reviews/hermes-claim-contract-v2-01

Root executes the NEW adapter from the OLD cwd to preserve exact system text:

  cd /workspace/hermes-factory/experiments/hermes-verified-cohort/z0intelligence
  PYTHONPATH=src /workspace/hermes-factory/venvs/hermes-state-v0/bin/python \
    /workspace/hermes-factory/experiments/hermes-claim-contract-v2/z0intelligence/benchmarks/thermocontext/hermes_phase_c/claim_contract_v2/run_study.py \
    --out /workspace/hermes-factory/reviews/hermes-claim-contract-v2-01 \
    --nous-auth-home /home/agent/.z0int/config/hermes-nous-test --execute

Offline replay uses the same adapter command with --replay and no auth profile.
Use prompt-study-result.json as the v2 result; underlying cohort-result.json is
retained as the unchanged runner's mechanical receipt, with its own freeze.

Meaningful no-network checks (new worktree):

  PYTHONPATH=src HERMES_PRIOR_COHORT=/workspace/hermes-factory/reviews/hermes-verified-cohort-01 \
    /workspace/hermes-factory/venvs/hermes-state-v0/bin/python -m pytest -q \
    benchmarks/thermocontext/hermes_phase_c/claim_contract_v2/test_study.py
