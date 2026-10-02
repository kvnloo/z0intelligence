# Scope-preserving source declarations (offline development)

This experiment uses the existing ContextPacket and exact-reference materializer.
It tests a new, explicit source-reporting contract after the preserved v1 0/5 and
v2 1/5 outcomes. It never repairs or regrades those answers. Seven frozen controls
have full and minimal arms; only the frozen ancillary telemetry ref is removable.
The prompt, relevant evidence, provenance, contradictions, gaps, and checker stay
fixed within each pair. No generated answer or oracle reaches model context.

`freeze.json` binds the protocol, fixtures, independent oracle, prompt, and 262
predecessor source/artifact files before implementation. The implementation only
extracts strict typed declarations. Exact matching true and false are distinct
from missing, conflicting, or other-subject declarations. A missing source revision
does not erase an independently explicit scope claim. Duplicate JSON keys, malformed
known schemas, bool-like numbers, and unsupported candidate certainty are rejected;
answers are never rewritten. Unknown schemas and unstructured prose remain raw.
Syntactically broken JSON also remains untyped; malformed declarations in valid
JSON carrying the known schema are rejected.

Historical scope has no directly typed Boolean. Its exact evidence/provenance and
gap text are retained, and this new typed-only contract reports scope unknown.
That is not a reinterpretation of the old checker, nor a resolution of the old
prose interpretation failure. The historical fixture asks about a synthetic scope
and establishes preservation and abstention only.

From the repository root, with existing dependencies:

```sh
PYTHONPATH=src HERMES_SOURCE_TREE=/workspace/hermes-factory/experiments/hermes-state-v0/hermes /workspace/hermes-factory/venvs/hermes-state-v0/bin/python -m pytest -q benchmarks/thermocontext/hermes_phase_c/scope_contract_v1/test_contract.py benchmarks/thermocontext/hermes_phase_c/scope_contract_v1/test_saved_replay.py
PYTHONPATH=src /workspace/hermes-factory/venvs/hermes-state-v0/bin/python benchmarks/thermocontext/hermes_phase_c/scope_contract_v1/qualify.py qualify --hermes-repo /workspace/hermes-factory/experiments/hermes-state-v0/hermes --out /tmp/scope-contract-new-output
PYTHONPATH=src /workspace/hermes-factory/venvs/hermes-state-v0/bin/python benchmarks/thermocontext/hermes_phase_c/scope_contract_v1/qualify.py replay --out /tmp/scope-contract-new-output
```

Qualification exercises pinned native Hermes hook collection and composition with
a prior observed system message. It is offline serialization, not a transmitted
request or evidence of model consumption. Replay uses relative repository/artifact
paths, requires no Hermes checkout, and checks scientific source hashes rather than
the current Git HEAD. Publication may map the tested local tree to another commit;
the original source revisions remain pinned in the evidence.

The prospective protocol declares at most eight single-attempt calls: two matched
positive/negative pairs and four separate controls. It is **not executed**. Root
review, a final execution freeze, and a bounded execution decision remain; this
offline assignment adds no provider client or live command. The frozen protocol's
"NOT_IMPLEMENTED_OR_AUTHORIZED" wording describes this agent's current assignment,
not absence of the user's broader experiment authorization. Future execution must
reuse the existing native driver, invariant wire guard, constant fresh profile path,
unchanged caps, actual usage/cost, and saved failure/replay receipts.

The optional calls are not selected automatically: this fixture task is already
deterministic. The next planning step is to inspect at most three genuine downstream
tasks and freeze a semantic residual with an independent outcome and deterministic
comparator, or stop the model comparison when the deterministic path suffices.

These inspected synthetic fixtures qualify exact declaration reporting only.
The declaration schema does not authenticate a producer, bind a scope claim to a
tested revision, verify a real result, or reconcile arbitrary prose contradictions.
Known-schema extra keys are rejected; hex revision casing is preserved literally.
Ancillary membership is fixed by the fixture author, not selected semantically at
runtime. Byte reductions are not provider token, wall-time, or monetary savings.
No model necessity, holdout performance, population reliability, backend effect,
or real Hermes speedup is established.
