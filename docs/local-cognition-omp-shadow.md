# Local cognition OMP shadow lane

Observe-only dogfood lane for [oh-my-pi#83](https://github.com/kvnloo/oh-my-pi/issues/83)
and z0intelligence#20. The bridge compiles the deterministic legal action set for
an observed OMP tool call and records what the local cognition models *would*
have chosen from that set. Nothing is executed.

## Components

| Piece | Path |
| --- | --- |
| Pure plan + op body | `src/z0int/cognition/shadow.py` |
| Bridge op `cognition_shadow` | `src/z0int/bridge/protocol.py`, `runtime.py`, `worker.py` |
| OMP extension | `omp-extensions/local-cognition/` |
| Receipts | `~/.z0int/shadow/cognition-shadow.jsonl` |

## Safety boundary

- Observe-only: every result and receipt has `selected_action: null` and
  `executed_action: null`. The lane never calls a tool.
- Compile first: backends only ever receive the compiled `LegalActionSet`. An
  answer outside that set is recorded as `invalid_call: true` with
  `selected_action: null`; it can never re-admit a filtered action.
- Fail open: a bad payload, a missing model or an unreachable server returns
  `{"ok": false, "reason": ...}` or a partial `shadow` list — never an exception
  into the host.
- Writes only under `Z0INT_HOME` (default `~/.z0int`).

## Op

Request (flat fields; a nested `payload` object is also accepted):

```json
{
  "op": "cognition_shadow",
  "trace_id": "...",
  "session_id": "...",
  "state": "free-form situation text",
  "actions": [{"action_id": "read", "kind": "tool", "description": "Read", "tool": "read",
               "family": "fs", "requires": [], "risk_class": "read", "cost_units": 1}],
  "granted_capabilities": ["read"],
  "authority": ["read"],
  "budget_units": 8,
  "satisfied": [],
  "facts": {},
  "objective": null,
  "risk_class": "read",
  "shadows": ["functiongemma_270m"]
}
```

Response: `z0int.cognition.shadow.v1` with `graph_digest`, `candidate_action_count`,
`legal_ids`, `eliminated`, `deterministic_solution`, `escalation`, `shadow`
(one row per backend) and `receipts_path`.

## Run it

```bash
cd <repo>
PYTHONPATH=src python -u -m z0int.bridge.worker --generation 1   # then send the op on stdin
```

Or drive the pure op directly (no worker):

```bash
PYTHONPATH=src python -c '
from z0int.cognition.shadow import run_shadow
import json
print(json.dumps(run_shadow({"state": "read a file",
  "actions": [{"action_id": "read", "kind": "tool", "tool": "read", "risk_class": "read"}],
  "authority": ["read"], "shadows": ["functiongemma_270m"]}), indent=2))
'
```

The extension is installed by symlinking `omp-extensions/local-cognition` into
`~/.omp/agent/extensions/` like the other z0int extensions. See its README for
env vars (`OMP_Z0INT_COGNITION_SHADOW`, `..._TIMEOUT_MS`, `..._MODELS`) and the
`/z0int-cognition-status` command.

## Tests

```bash
PYTHONPATH=src python -m pytest tests/test_cognition_shadow.py -q
(cd omp-extensions/local-cognition && bun test)
```

The unit tests use fake registries / fake transports, so they need no GPU and no
server. `shadow.real-omp.test.ts` is a separate, explicitly gated live qualifier;
ordinary offline runs skip it. The existing `Core unit` workflow runs the shadow
unit tests alongside the reuse Python and native OMP boundary tests against the
same frozen OMP SDK and native addon. Passing that workflow does not establish a
fresh live agent qualification.

## Integration provenance

The factory integration starts at reuse source
`199a849d1f064685b572bb614ddc9d1fb90dc781` and merges Claude's published shadow
history through `bd07c5d74b84f179c5d7615cb2319a426dfb7ea4`. This retains
`a90e3be0d2306eef0d62bacd9dbdf731bc491f7a`'s one-model default and
`d1a9f3a915efa16798913d7958b72e3f7f68d4da`'s session/tool-call joins without
copying the implementation or rewriting its authorship.

The previous live shadow receipt used a scripted parent provider and a real
served child model. It verifies the observation path, not a primary agent's
learned task behavior. Preserve the withdrawn cross-upstream latency comparisons
and remaining observations in
[the two-upstream correction](https://github.com/kvnloo/z0intelligence/issues/137#issuecomment-6092268035).
This integration neither repeats the completed D2 proof nor activates the
shadow extension in the user's installation.
