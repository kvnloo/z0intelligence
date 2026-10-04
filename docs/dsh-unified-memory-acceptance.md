# DSH × z0 unified-memory acceptance

Tracks #116 and #121.

## Start

From the z0intelligence checkout:

```sh
export Z0INT_PYTHON="${Z0INT_PYTHON:-$HOME/.z0int/bin/python}"
dsh web --patch "$PWD/harness-adapters/dsh-z0intelligence/z0-dsh.cordis.yml"
```

The overlay enables the existing DSH capture/shadow seam and the read-only `z0-memory` MCP. Automatic
`agent/pre-step` memory remains on the default temporal + AgentsView + TencentDB layers. ctx is pull-only
until its latency/precision bakeoff clears promotion.

## Machine checks

After the current ctx import finishes:

```sh
ctx status --format json | jq '{
  history_epoch,
  lexical,
  indexed_sessions: (.history.indexed_sessions // .indexed_sessions),
  indexed_sources: (.history.indexed_sources // .indexed_sources)
}'
```

Then prove the z0 pull surface independently of the model:

```sh
PYTHONPATH=src "$Z0INT_PYTHON" - <<'PY'
from z0int.memory.surface import PULL_LAYERS, ScopePolicy, search
from z0int.memory_contract import MemoryScope

out = search(
    "what was the last work on unified memory",
    ScopePolicy(scope=MemoryScope(user="local", project="z0intelligence"), requester="dsh"),
    layers=PULL_LAYERS,
    limit=8,
)
print("snapshot", out["memory_snapshot_id"])
print("layers", {k: (v["status"], v.get("reason"), v.get("revision")) for k, v in out["layers"].items()})
print("locators", [e["locator"] for e in out["evidence"]])
PY
```

Expected: ctx is either `ok` with an exact Core generation or explicitly unavailable; the other memory
layers continue independently. A missing DSH parser in ctx is therefore not a blocker for DSH memory.

## DSH model-visible acceptance

Use a fresh DSH session for each case. Ask the model to **use z0-memory, cite the locator, and inspect the
exact hit when needed**.

| case | prompt shape | pass condition |
|---|---|---|
| exact identifier | ask for a known PR/commit/path/session id | exact evidence locator reaches the model; no invented id |
| supersession | ask for an old decision whose newer value is known | current value wins; older evidence remains inspectable |
| cross-harness | ask for a decision made in Claude/Hermes from DSH | DSH retrieves source-backed foreign-harness evidence |
| contradiction | ask about two conflicting historical claims | answer exposes the conflict instead of silently picking one |
| missing evidence | ask for a deliberately absent fact | explicit abstention/gap; no fabricated evidence |
| minimal context | ask a question answerable by one exact event | selective `inspect`; no transcript dump |

For ctx evidence the trace must show:

```text
DSH tool call
  -> z0-memory memory_search/orient
  -> ctx search --refresh off --include-current-session
  -> ctx:event:<id>
  -> z0-memory inspect (when hydration is needed)
  -> model-visible evidence
  -> answer citing the locator
```

Retrieval JSON alone does **not** count as injection/use. The evidence must be visible in the request/tool
result consumed by the answering model.

## Receipts / invariants

Record the DSH session/turn/step plus the memory snapshot and source generation. Keep:

```text
evidence != claim/state != authority != action != outcome
```

ctx never runs `setup`, `import`, `index`, refresh, or maintenance from z0. Unknown ctx project scope
fails closed. Hybrid/semantic ctx retrieval remains explicit opt-in.

## Current known coverage gaps

- ctx may not parse DSH's zstd-compressed `session.v3` files yet.
- OpenCode mixed-format records may still be rejected.
- These are provider-parity gaps, not DSH runtime blockers: AgentsView/EventLog/TencentDB remain the control
  and fallback until ctx coverage proves better.
