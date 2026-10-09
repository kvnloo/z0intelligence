# Hermes native decision capture

`capture_native_decision` is a **trusted host/controller API**, not a registered
model tool or a memory backend. It reads one exact, already persisted native
Hermes user message and delegates admission to
`z0int.memory.claims.admit_user_claim`, including a typed
`NativeUserClaimAttestation` and explicit `correction_of`.

## Input contract

The entire persisted `messages.content` must be one JSON object:

```json
{
  "schema": "z0int.hermes.native_decision.v1",
  "subject": "deployment",
  "predicate": "strategy",
  "value": {
    "decision": "canary",
    "synthetic": true,
    "attribution": "synthetic test controller; not a historical human statement"
  }
}
```

For a correction, add `"correction_of": "claim_<32 lowercase hex digits>"`,
using the actual prior admitted claim ID. The canonical admission path enforces
an existing explicit user claim with the **same exact scope, subject and
predicate**. Omitting the field (or using JSON `null`) declares no correction;
there is no automatic correction-target guessing.

Subject and predicate are arbitrary nonempty, printable labels, at most 160
characters, without leading/trailing whitespace. Value is arbitrary finite JSON,
bounded by the canonical 1,024-byte normalized-value limit. The entire native
input is limited to 16,384 UTF-8 bytes and nesting to 16 levels from the root.
Prose, code fences, content blocks, duplicate keys, unknown fields/schema,
nonfinite numbers, malformed Unicode and oversized values fail explicitly.
Labels/identifiers are validated, not silently repaired.

Synthetic host bindings require `value.synthetic` to be exactly `true` and
`value.attribution` to equal the host-pinned, bounded attribution. They cannot
be omitted or converted into historical human statements. Non-synthetic
bindings support scalar/list/object/null values, but cannot accept a synthetic
marker inconsistent with the host mode. Attribution is preserved in the
normalized value, not a new source-metadata contract.

## Host call after native persistence

Run from the repository import root with its `src` on `PYTHONPATH`:

```python
from hashlib import sha256
from pathlib import Path

from adapters.hermes_z0int import HermesSessionBinding, capture_native_decision
from z0int.memory_contract import MemoryScope

scope = MemoryScope(
    user="synthetic-controller", project="dedicated-project",
    repo="example/repository", task="decision-capture",
)
binding = HermesSessionBinding(
    hermes_home=Path(dedicated_hermes_home),  # explicit absolute path; DB must exist
    session_id=native_session_id,
    scope=scope,
    session_source="cli",  # exact sessions.source; default is cli
    synthetic=True,
    attribution="synthetic test controller; not a historical human statement",
)
claim_event = capture_native_decision(
    binding,
    session_id=native_session_id,
    message_id=persisted_message_id,       # messages.id, integer
    message_uid=persisted_message_uid,     # exact messages.message_uid
    expected_content_hash="sha256:" + sha256(exact_submitted_text.encode("utf-8")).hexdigest(),
    scope=scope,
    ledger_root=existing_eventlog_root,
)
claim_id = claim_event.payload["claim_id"]
```

These variables come from the trusted native submission/persistence boundary,
not model arguments or a search for the most recent similar message. Pin the
complete `user/project/repo/task` scope in the host. Do not derive it from input
text or ambient environment. The existing references-only source-ingest gate
must already be enabled by its owner; this API does not enable or bypass it.
Calls sharing a ledger must be serialized by the host, as canonical claim
admission is a multi-event operation.

The return value is the production `MemoryEvent` for `memory.claim`. Existing
claim projection/ContextPacket consumers work unchanged. Exact replay, including
after a process restart, returns the same claim event through EventLog identity
and claim deduplication. Source mutation is an error, not a second identity.

## Boundaries and failures

- Bind a dedicated home and top-level native session. The binding pins the
  canonical `state.db` path and file identity; a redirected/replaced database
  requires explicit host intervention. There is no default-home fallback.
- Read with SQLite `mode=ro`, never `SessionDB` initialization or migration.
  A read transaction binds each hydration's row and UID uniqueness check.
  Canonical admission independently rehydrates before admitting the claim.
- Accept exact role `user`, active/uncompressed/uncompacted rows, with no
  display/internal or tool metadata. Parent/continuation sessions and marked
  internal rows are deliberately unsupported by this bounded fresh-input lane.
- Validate session, integer row ID, unique message UID, exact submitted-content
  SHA-256, source timestamp, scope and declaration before writes. No assistant,
  tool, compressed summary or inferred prose attestation is possible here.
- Identity uses `source_system="hermes"`, native session, native message UID
  and row ID. Locator `agentsview:<session>#<row ID>` is the existing canonical
  claims contract; hydration still reads **Hermes SQLite directly**, not an
  AgentsView service.
- Raw input is hydrated transiently for validation, never mirrored into the
  EventLog. Only the existing source reference and bounded normalized claim are
  admitted. No provider/model call, launcher change or plugin registration.
- Invalid input/binding, missing rows, ambiguity and native source outages raise
  `ValueError` explicitly. Canonical ledger/ingest errors propagate; they are
  never reported as success. SQLite lock wait is bounded to one second.

This module intentionally does **not** install a `pre_llm_call` capture hook:
that hook's arbitrary text envelope is not sufficient evidence of the exact
persisted native user row. The host/controller calls after confirmed persistence;
a future automatic seam must supply the same trustworthy identifiers and hash.

## Verification and attribution

Offline tests exercise actual SQLite reads and production EventLog admission,
correction/projection, child-process replay, source mutation, failure paths and
the existing ingest gate. No model calls are required:

```sh
PYTHONPATH="$PWD/src" TMPDIR="$(realpath "$TMPDIR")" \
  python -m pytest -q tests/test_hermes_native_capture.py \
  tests/test_hermes_adapter.py tests/test_hermes_context_adapter.py tests/test_hermes_decisions.py
```

Kevin Rajan authored the requirements and invariants. Existing EventLog and
scoped-claims authors own the reused canonical persistence/admission/projection
implementation; Hermes upstream authors own native session/message persistence.
Hermes Agent (Nous Research) supplied this downstream adapter, tests and docs.
