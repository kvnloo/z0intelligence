# z0intelligence for DeepSeek Harness (DSH)

A cordis plugin loaded from a DSH profile (`cordis.patch.yml` entry `automatic-z0intelligence`, plugin
`dsh-z0intelligence`). It needs no DSH core change and replaces the `hermes-jev-dsh` bundle
(hermes-jev-skills `hermes-jev/omp-adapter` @9ea5777): `capture.mjs`, `lineage.mjs` and `shadow.mjs` carry its
lineage, sampling and in-flight cap over to the z0 contracts.

| DSH seam | z0int surface | Default |
| --- | --- | --- |
| `agent/request` (waterfall, returns `next()` unchanged) | root user turn, step 1: `python -m z0int.hook_adapter --harness dsh prompt` (detached) → `z0int.dsh.opportunity_record.v0`; every request: `z0int.dsh.lineage.v0` | on |
| `agent/turn-stopping` (returns nothing) | `... --harness dsh stop` → `z0int.dsh.turn_outcome.v0`, joined on `turn_key` | on |
| `agent/error` | closes an open root turn: `z0int.dsh.drop.v0` `turn_errored` (counted), no outcome | on |
| shadow plane | `POST <shadow.url origin>/v1/plan` (pure shadow route, never executed; the route is fixed, not configurable) → `z0int.dsh.shadow_decision.v0` | off (no URL) |
| `llm/stream` router (`z0int.automatic`) | governed routing | off: needs `router: true` and `automatic.json` `{"dsh": {"enabled": true}}` (z0intelligence#95) |

The turn id is the DSH lineage turn_key `<agent id>:<turn>` (alias `dsh.lineage_turn_key`), so
`turn_key = harness_id.turn_key('dsh', <session id>, <agent id>:<turn>)`. Subagent requests get lineage rows
only. No row holds prompt or response text; the prompt reaches the hook child on stdin and, when configured,
the z0 shadow service.

Profile config (all optional):

```yaml
config:
  capture: true          # false registers nothing
  router: false          # see the table
  shadow:
    url: null            # e.g. a private z0 service; port 11501 is refused unless allow_live_service: true
    sample_rate: 0.1     # deterministic per turn key
    max_inflight: 1      # extra turns are recorded queue_saturated, never queued
    timeout_ms: 60000
```

Behaviour change from `hermes-jev-dsh`: no JEV route call and no reasoning-effort or model change on any turn;
the online Jev shadow lanes stop until `shadow.url` names a z0 service, so DSH learning is offline shadow-slot
only by default. The OMP key-file read, the Hermes routing config, the private shadow venv defaults and the
direct Hermes memory reader are gone (memory comes from the z0 memory seam).

Failures never reach the turn: a missing `Z0INT_PYTHON` or a failed spawn writes a `z0int.dsh.drop.v0` row
(`spawn_failed`); a shadow service that is down writes `backend_unavailable`. `Z0INT_CAPTURE=0` or
`$Z0INT_HOME/config/capture.json` `{"enabled": false}` turns capture off. Rows live under
`$Z0INT_HOME/state/dsh/`. A shadow response that says it executed is recorded `executed_unexpectedly` and
counted (`shadow_executed`), never `ok`.

Known limits of the outcome row (labels come from the AgentsView `deepseek-harness` rows, C2):
- The outcome is sent on the first `agent/turn-stopping` of a turn. A listener can steer from that hook and DSH
  then runs more steps, so the outcome can be sent before the turn really ends. The stop event carries ids only
  (every DSH outcome already has `partial_measurement` and `missing_verifier` rows), so only its timing is early.
- A turn that errors gets a `turn_errored` drop row instead of an outcome. A turn aborted without `agent/error`
  never reaches the stop boundary and stays open until it is evicted (the 256 most recent turns are kept); it
  gets no row from this plugin.
