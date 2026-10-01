# AODL structural admission v1

This is the runtime continuation of z0intelligence#13.

```
validated AODL HOTL 0.2
  + observed controller state
  + proposed spawn
        |
        v
plain-Python structural admission
        |
        +-- ALLOW
        `-- DENY + reason codes + aodl-canon-1 fingerprint
```

AODL remains authoritative for validation and semantic identity. z0int owns live
observed state and the admission decision. Bend is an independent CI/shadow
verifier for the same pure transition rules; it is not on the hot path.

The caller cannot supply its own authority. The gate reads these fields from the
validated document:

- `revision`
- `policies.dynamic.allowed/maxChildren/maxDepth`
- `constraints.budgets`
- the declared parent node's `authorityCeiling`

The caller supplies only facts about the proposed transition:

- requested contract revision
- parent node id
- current live child count and depth
- observed spend
- proposed spend
- requested capabilities

Codes 101–106 intentionally match the Bend experiment:

| code | meaning |
| --- | --- |
| 101 | revision mismatch |
| 102 | dynamic spawn disallowed |
| 103 | max children |
| 104 | max depth |
| 105 | budget |
| 106 | authority |

Every decision can emit a `z0int.aodl_admission.v1` receipt containing both
the versioned AODL semantic fingerprint and the contract's
`provenance.sourceHash`, plus latency. They are deliberately distinct:

- `aodl_semantic_fingerprint` names the full canonical semantic snapshot;
- `aodl_intent_source_hash` preserves authored intent/source lineage.

Runtime/event semantics may change the former without pretending authored intent
was rewritten. The receipt is structural gate evidence only. It never claims
task success or `verified_success`.

If `aodl_contract` is unavailable, invalid, uses an unknown canonicalization
version, or cannot validate/fingerprint the document, admission fails closed.
