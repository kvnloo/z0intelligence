Saved-artifact audit: two separate Hermes development studies

AUDIT_DECISION.json contains the compact decision and precise scope-field
diagnosis. independent-audit.json retains all original outcomes and accounting.
scope-evidence-inventory.json inventories structured declarations and scope text;
it does not repair any answer or define a replacement judge.

v1 remains 0/5: 20,937 input tokens, 305 output tokens, reported $0.00107041.
v2 remains 1/5: 21,322 input tokens, 404 output tokens, reported $0.00114690.
All five v2 source/status/reason/citation results satisfy the unchanged checker.
The four failures return null rather than false for hermes_speedup_established.
The full-complete case returns false and passes. No more calls are authorized.

All 198 predecessor files match their before-v2 hashes. Across each of five
case pairs, replacing the JSON-encoded old prompt with the new prompt produces
the exact new native and forwarded raw bytes. Contexts, selections, full system
text, serialization, route and limits are unchanged. Backend provider is null,
so these controls do not identify a backend or remove temporal/service variation.

Source identity is a typed declaration in evidence excerpt JSON. Hermes scope
is not a typed boolean: it requires interpretation of the identical limits prose
and the supplied textual gap saying no real Hermes provider/end-to-end result
exists for this pool. A prospectively declared deterministic reporting policy
could expose such bounds, but no posthoc repair, new scoring rule or adapter is
implemented here. These failures are narrower than generic model incapability.

Portable replay uses Python standard library only and the exact checker snapshots
in source-snapshots/. It never imports the driver, reads credentials, follows
historical absolute source paths, contacts a provider, or changes saved answers.

  python audit_saved_studies.py --v1 /path/to/v1/archive --v2 /path/to/v2/archive \
    --source-snapshots ./source-snapshots --out /new/path/audit.json

For published archives that intentionally omit local profiles and logs:

  python audit_saved_studies.py --v1 /path/to/v1/archive --v2 /path/to/v2/archive \
    --source-snapshots ./source-snapshots --public-artifacts \
    --out /new/path/public-audit.json

Public mode still requires all scientific evidence, raw wires, provider response
records and independent verdicts. It explicitly reports 86 omitted runtime/log
files and verifies the other 112 predecessor artifacts; it does not pretend to
recheck omitted bytes. The local receipt independently verifies all 198.

Four tests verify relocation with absolute-path access blocked, reject missing
scientific evidence, detect changed native bytes, and detect changed cost:

  HERMES_AUDIT_V1=/path/to/v1/archive HERMES_AUDIT_V2=/path/to/v2/archive \
    python -m unittest -v test_saved_audit

The same-task/same-seed development contracts remain separate. Observed token
reductions are not verified savings because the paired strict outcomes fail.
