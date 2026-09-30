# Resource posture v0

**Status:** shadow only (`posture_enforce: false`). Module `z0int.posture`, CLI `z0int posture`.

The factory had no view of budgets over time. It could not tell *"Claude weekly
quota resets at 03:00 tonight and 80% is unused"* (spend frontier now) from
*"a week left, but at this burn rate it's gone in 2 days"* (move bounded work to
cerebras/groq/local SLMs). Resource posture adds that view.

## Model

A **pool** is one budget window:
`{id, kind: frontier|fast|local|free|paid, group, unit, capacity, remaining, resets_at, window_hours, burn_rate_per_hour, rate_observed_hours, observed_at}`.

For each pool, `evaluate_pool` is a pure, deterministic function of `(pool, now, thresholds)`:

```
hours_until_reset         = resets_at - now
projected_use_until_reset = burn_rate_per_hour * hours_until_reset
ratio                     = projected / remaining
surplus_at_reset          = remaining - projected
```

| posture | rule (defaults) |
|---|---|
| **OFFLOAD** | `remaining <= 0`, or `ratio >= 1.0`: the pool runs out before it resets |
| **RESERVE** | `0.85 <= ratio < 1.0` (tight), or a non-perishable pool with runway `< 72h` |
| **BURN** | reset within `24h` and `surplus_at_reset >= 20%` of capacity: unused quota is about to expire |
| **BALANCED** | on pace; also unmetered pools, pools with no rate yet, and observations that predate a reset |

Safety rules: a BURN or OFFLOAD verdict is **not asserted** (the pool is downgraded
to BALANCED and the verdict is kept as `unconfirmed_posture`) in two cases. One is
a rate observed over less than 0.5h. The other is an observation older than 6h.
Every row carries its `arithmetic` string, for example
`projected 0.123/h x 13.4h = 1.7% vs remaining 81.0% (ratio 0.02); 79.3% perishes at reset in 13.4h`.

**Group** (one provider, several windows, for example Claude's 5h and weekly windows): the binding
window wins, with precedence `OFFLOAD > RESERVE > BURN > BALANCED`. Ties go to the longest window,
which is the plan budget.

**Factory recommendation** (`rule` names the branch taken):
1. any frontier group is BURN → **BURN**. `prefer` lists the burning groups and `avoid` lists the exhausted ones.
2. no frontier group is BALANCED → **OFFLOAD** to `offload_targets` (fast, then local, then free; usable z0int routes first), or **RESERVE** if no target exists
3. otherwise → **BALANCED**. `prefer` lists the groups that are on pace.

Thresholds can be overridden per host (`thresholds` in the config).

## Sources (read-only, fail-open; none is a second tracker)

| source | provides |
|---|---|
| `~/.cache/codexbar-waybar/last.json` (usage-island Waybar module, CodexBar CLI) | Claude / Codex / Cursor / Grok rate windows (`usedPercent`, `resetsAt`, `windowMinutes`) and OpenRouter credits. Identity fields are never copied. Override the path with `Z0INT_POSTURE_CODEXBAR`. |
| `z0int.worker_routing.configuration()` validated $0 routes | offload targets the router can actually use (`route:local` = host Radeon Qwen3, `route:groq`, …) |
| Kerdoios inventory cache (`kerdoios.cache.load_inventory_cache`, if importable; `~/.hermes/plugins/kerdoios`) | unmetered free-model offload targets per provider. No network calls. Kerdoios quota figures are provider claims, so they are recorded but not metered. |
| `~/.z0int/config/posture.local.json` | manual pools and overrides, `thresholds`, `posture_enforce`, `sources.{kerdoios,worker_routing}: false` |

The burn rate is the window average since the window started: `usedPercent / (window_hours - hours_until_reset)`. This is the only rate a single snapshot supports. A recency-weighted rate needs the snapshot log (`--log`, below).

Override keys resolve in this order: exact pool id, then group, then alias (`claude-max`→`claude`, `chatgpt-pro`→`codex`). A group key is applied to the group's longest window:

```json
{"claude-max": {"resets_at": "2026-10-01T03:00:00-05:00"}, "posture_enforce": false}
```

An unknown key creates a manual pool (`kind` defaults to `frontier`).

## Wiring (shadow)

* **State Packet:** a new `resource` adapter emits `resource.posture` (the factory verdict, prefer/avoid, targets) and `resource.posture[<group>]` (binding window plus arithmetic). Both are non-material: they never block ACT. `resource.posture` renders in the NOW block. The packet's `source_revisions.resource` tracks *verdicts*, not numbers, so the cache is invalidated only when a posture flips. A missing usage source is a non-blocking unknown. Adapter errors become `resource.adapter` unknowns.
* **worker_routing:** `route_worker` calls `posture_annotate(plan)`, which adds `plan.resource_posture` (factory posture, `agrees`, `enforce`). The annotation flows into the MCP result and every attempt receipt (`extra.resource_posture`). The plan is not changed. With `posture_enforce: true`, an enforced BURN empties the candidate list and the tool returns `requires_parent` with a posture refusal reason. That path is off by default and is not enabled.
* **CLI:** `z0int posture [--json] [--now ISO] [--codexbar P] [--config P] [--no-kerdoios] [--log]`.

## Pre-registered evaluation: does posture-aware routing beat posture-blind?

Registered 2026-09-30, before any enforced run. Enforcement stays off until this evaluation passes.

**Question.** Over real Claude Code usage, would posture-aware routing:
- waste less frontier quota at reset, and
- lose less work to rate limits

than the current posture-blind routing, which always offloads bounded work and never considers resets?

**Data.** The evaluation needs three streams:

1. **Transcripts.** Claude Code transcripts in `~/.claude/projects/**/*.jsonl` for a 7-day window. From each assistant turn: timestamp, model, and `message.usage` (`input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`). Rate-limit events: API-error turns (`isApiErrorMessage`, `"Claude AI usage limit reached|<epoch>"`). On 2026-09-30, 189 transcripts were modified in the last 7 days, and 2 limit hits were recorded.
2. **Posture snapshots.** Run `z0int posture --log` hourly, for example from a timer, starting now. The snapshots go to `~/.z0int/state/posture/history.jsonl` and give the observed `remaining` / `resets_at` at each point.
3. **Route receipts.** `route_worker` receipts with `extra.resource_posture`: which tasks were offloaded while the posture said BURN (`agrees=false`).

**Calibration.** Tokens are mapped to percent per pool by regressing the change in `usedPercent` between consecutive snapshots on the transcript tokens in the same interval. Cache reads get their own coefficient. This is fitted on days 1–3 and applied to days 4–7.

**Policies replayed** on days 4–7:
- *Blind* is what actually happened.
- *Aware* applies two counterfactual moves:
  - While the logged posture was **BURN**, every task offloaded by `route_worker` instead runs on frontier. It costs the receipt's `estimated_frontier_tokens_avoided`, converted with the calibration.
  - While the posture was **OFFLOAD**, frontier turns that `worker_routing.plan_route` classifies as bounded (non-`local`, ≤ 2048 output tokens, no tool calls) move to the first offload target. Their calibrated cost is removed from the frontier pool.

**Metrics:**
- `W`: perished frontier capacity, which is the sum over resets of `remaining%` at the last snapshot before each reset (≤ 1h before).
- `L`: rate-limited hours, which is the sum over limit events of the time from the hit to the reported reset. Under *Aware*, a limit event counts as avoided if the replayed pool stays above 0%.
- `F`: posture flips per group per day, a stability measure.
- Projection error: for each snapshot with a verdict, `|projected_surplus_at_reset − observed remaining at reset|`.

**Pass: all of the following must hold on days 4–7.**
1. `W_aware ≤ W_blind − 10` percentage points per weekly window, averaged. The 10-point margin is fixed before seeing the data.
2. `L_aware ≤ L_blind`. BURN must never cause more rate-limit time.
3. Median projection error ≤ 15 pp. BURN precision ≥ 0.8, meaning that among BURN snapshots, the blind policy actually ended the window with ≥ 20% unused. OFFLOAD precision ≥ 0.7, meaning the blind policy hit a limit or used ≥ 95% before reset.
4. `F ≤ 2` flips per group per day.

**Fail / falsified.**
- If `W_blind < 10` pp in every window (nothing perishes), posture has nothing to gain from BURN. Keep shadow mode and do not enforce.
- If criterion 2 or 3 fails, the projection is not trustworthy enough to act on. Tune the thresholds on a *new* window, not on these days, then re-register.

**Not claimed.** Quality parity between frontier and offloaded work. That is `worker_routing`'s execution receipts plus JEV verification, not this evaluation. Dollar costs are also not claimed: subscription windows are sunk. Only perishability and rate-limit loss are measured.

**Baseline observation at registration (2026-09-30T18:33Z, live CLI):**
- Claude Max weekly was 18% used, 13.4h before its 03:00 CDT reset. The projection had 79.3% perishing: BURN.
- Codex weekly had ratio 0.80: BALANCED.
- Cursor was 100% used: OFFLOAD.
- Factory posture: **BURN**.
