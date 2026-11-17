# circadiand (CIRCADIAN-SPEC WP1)

A small user service that knows the owner's day phase and holds agent
notifications during the quiet part of the night. Stdlib Python, no UI.

| piece | where |
| --- | --- |
| pure twin | `src/z0int/human_posture.py` (`evaluate_human_state`, `policy`, `route`, `gate`) |
| adapters, daemon, CLI | `src/z0int/circadian/` |
| Bend laws | `bend/circadian/` (`bend PROOF.bend` -> ALL PROOFS CHECK) |
| unit | `circadiand.service` (MemoryMax 128M, CPUQuota 10%) |
| private config | `~/.config/circadian/config.json` (0600; host paths, ports, device model) |
| private state | `~/.local/state/circadian/` (0700): `posture.json`, `history.jsonl`, `deferred.jsonl`, `routes.jsonl`, `counters.json`, `phone-screen.json`, `replay/` |

## What it does

- Every tick: read ActivityWatch (desk), the phone screen on/off events
  (`dumpsys usagestats`, read-only, every 15 min) and the NOOP DB copy
  (wearable; stale is ignored), build the owner-day anchors, compute the
  phase and the notification policy, write `posture.json`.
- `circadian route ...` (called by `z0-phone-notify` and `dash-report`): in a
  quiet phase (WIND_DOWN after the freeze, PRE_SLEEP, SLEEP, DISENGAGED after
  `circadian done`) the notice is held. By day everything is delivered; what
  the full policy would batch is logged only (shadow, law C12).
- Held notices go out as one "Held overnight: N updates" notice when the owner
  is awake again: a day phase, wake + 30 min, not before 08:00, and before
  12:00 only with a measured or proxy wake (R2, C15).
- `GET /posture` on 127.0.0.1 (port from the private config) for the Dash bridge.

## Owner controls

```
circadian show          # phase now and why
circadian done          # done for today: hold everything until the wake target
circadian undone        # undo
circadian release --force   # send the held digest now
circadian-rollback      # restore the notify scripts and stop the service
```

`enforce: "none"` in the config turns enforcement off (shadow only). Owner
days in `shadowDates` (the 2026-11-01 DST night) never enforce.
