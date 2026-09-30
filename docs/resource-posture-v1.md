# Resource posture v1

**Status:** shadow only (`posture_enforce: false`). Branch `feat/resource-posture-v1b`.
Modules `z0int.posture` (evaluation), `z0int.posture_history` (recent rate), `z0int.posture_sim` (replay +
evaluation runner), `z0int.worker_routing` (enforce path), `z0int.claude_code` (hint).

v1 changes the posture *method* (how the burn rate is estimated and when a verdict is asserted). The
v0 pre-registration in [resource-posture.md](resource-posture.md) is **unchanged**, and v1 has its own
pre-registration below.

## 1. Recent burn rate (EWMA over the snapshot history)

v0 used one rate per pool, the window average `used / (window_hours - hours_until_reset)`. That is the
only rate a single codexbar snapshot supports, and it misses bursts. On 2026-09-30, Claude weekly went
from 81% to 70% remaining in 4.4h (about 2.5 pp/h), while the window average read 0.12 to 0.19 pp/h.

v1 reads `~/.z0int/state/posture/history.jsonl` (v0 and v1 rows) plus the live observation:

| step | rule (defaults, `rate` in `posture.local.json`) |
|---|---|
| window | only the current reset window. A `resets_at` that moves by more than 15 min, or `remaining` rising by more than 1pp, starts a new window |
| thinning | keep the newest point, then every point at least `min_interval_hours: 0.75` before the last kept point |
| EWMA | per interval: `w = exp(-age_of_midpoint / horizon_hours)` with `horizon_hours: 6`. `rate = Σ w·used / Σ w·dt` over `lookback_hours: 48` |
| band | `half = z·se + quantum / Σ w·dt`, with `z: 1.96` and `quantum: 1` pp. `se` comes from the time-weighted dispersion of per-interval rates, **net of** the `(q²/6)/dt²` variance that whole-percent rounding alone produces |
| fallback | fewer than `min_intervals: 2` intervals, or less than `min_span_hours: 1` of history, falls back to the v0 window average. That rate gets its own band, `±quantum/observed_hours`. `rate_source` says which rate was used and why |

`"rate": {"method": "window_average"}` switches the EWMA off and uses the v0 rate with a band.
`"band": false` switches off the band as well, which reproduces v0 exactly.

## 2. Confidence gating (band) and hysteresis

`evaluate_pool` still classifies at the central rate. It then re-checks the verdict at the edges of the band:

* **BURN** is asserted only if it still holds at `rate_hi`, meaning the surplus survives the fastest plausible burn. If it does not, the pool is **BALANCED** with `unconfirmed_posture: BURN`.
* **OFFLOAD** is asserted only if it still holds at `rate_lo`, meaning the pool runs out even at the slowest plausible burn. If it does not, the pool is **RESERVE**, since it is possibly tight.
* **Hysteresis:** entering BURN or OFFLOAD needs the band to confirm it. Staying needs only the central
  estimate, and only if the previous snapshot of the *same window* already asserted that posture
  (`confidence: band_held`). Without this, a rate jittering at a threshold flipped a synthetic pool every
  hour, which failed v0 criterion 4.
* The v0 low-confidence and stale gates still apply first.

Every row carries `rate_lo`, `rate_hi`, `rate_window_avg`, `projected_surplus_band` and the band in its
`arithmetic`. Log rows (`z0int.resource_posture.v1.log`) add `group`, the band, `rate_source` and
`unconfirmed_posture`.

**2026-09-30, replayed:** v1 sees about 2.5 pp/h on Claude weekly, compared with 0.19 pp/h from the window average. It still says **BURN**: at 23:00Z the projected surplus is about 48%, with a band of 30 to 65%. At 22:00Z the band alone would not have confirmed BURN, and hysteresis held it. Codex weekly moves from BALANCED to RESERVE at 22:00Z after a 3pp hour. That verdict is not OFFLOAD, because the band's low rate does not run the pool dry.

## 3. Replay simulator (`z0int posture-replay`)

```
z0int posture-replay [--history P] [--policy v0-logged|v0|v1 ...] [--start ISO]
                     [--transcripts ~/.claude/projects] [--receipts P] [--decisions] [--json]
```

* `replay(rows, policy)` produces one decision per snapshot: factory, group postures, binding pools, and per-pool rate, band and projected surplus. It uses **only rows at or before that snapshot**. This is tested: `replay(rows[:k]) == replay(rows)[:k]`. Hysteresis state is the policy's own previous decision, never another policy's logged posture.
* The policies are:
  * `v0-logged`: the live log verbatim. This is the v0 evaluation.
  * `v0`: a window-average replay without a band.
  * `v1`: the EWMA, band and hysteresis.
  * a dict with custom `rate` and `thresholds`.
* `outcomes(rows)` gives, per (pool, reset window): the final remaining at the last snapshot at most 1h before the reset, whether the pool was exhausted, and `ran_dry`, which means exhausted or ending at ≤5% remaining.
* `evaluate_preregistered(...)` runs the v0 pre-registration end to end:
  * calibration on days 1–3: a no-intercept least-squares fit of `Δused_pp ~ a·noncache + b·cache_read`, per pool, with a single-coefficient fallback;
  * scoring on days 4–7;
  * the Blind vs Aware counterfactual. BURN charges `a·estimated_frontier_tokens_avoided` for route_worker receipts whose parent group was burning. OFFLOAD credits bounded frontier turns: no tool calls and ≤2048 output tokens.
  * `L`, including new limit time that Aware *causes*;
  * criteria 1 to 4 and the falsification rules.

  The result is `pass`, `fail` or `insufficient_data`, never a silent pass. Criteria that need streams that were not supplied are `n/a`.
* Interpretations fixed in code, before any data:
  * a window is the run of snapshots with one `resets_at` (±15 min);
  * projection error clamps the projected surplus at 0, and the raw error is reported beside it;
  * a group's posture is attributed through its binding pool;
  * only groups with a calibrated token stream are charged or credited. Claude Code transcripts cover `claude`. Other groups are listed as `uncalibrated`.

The synthetic tests cover:
* the 2026-09-30 day as logged;
* an 8-day week where Claude perishes about 33% and Codex runs dry, with a ground-truth token stream;
* a burst that turns v0 BURN into v1 OFFLOAD;
* limit avoidance and limit causation;
* a week where nothing perishes, which is falsified.

On the live history today, the runner reports `insufficient_data` (0.18 d of 7 d).

## 4. Enforce path: designed, OFF

Nothing changes while `posture_enforce` is false. In shadow mode, every `route_worker` plan still records
what enforcement *would* do, in `resource_posture.would = {action, reason, changes_plan, candidates}`.
That record flows into every attempt receipt and gives the evaluation the counterfactual stream.

With `posture_enforce: true` (`worker_routing.posture_route_decision`):

| factory posture | action | exactly what changes |
|---|---|---|
| **BURN** | `return_to_parent` | The candidate list empties, and each removed candidate is recorded in `skipped`, reason `posture_burn_parent_should_absorb`. The tool returns `ok: false`, `requires_parent: true`, refusal `Resource posture BURN (enforced)…`. This happens **only if** the parent's pool is one of the burning groups (`harness → group`: `claude-code→claude`, `codex→codex`). A Codex parent does not absorb work because *Claude* quota perishes. |
| **OFFLOAD** | `prefer_zero_cost` | The candidates become every usable validated $0 route. Host-local routes come first, in `local_order`: **groot `qwen3-8b-q4km`**, then the Radeon `local` route. Hosted free tiers come next, then any original $0 candidate. One candidate per provider: the provider's default model, because `available()` and `execute_plan`'s `require_free_route` re-check exactly that route. Capped or unavailable providers are dropped, and the list is capped at `max_attempts`. Paid routes are never added. |
| BALANCED / RESERVE | `none` | nothing |
| any posture, task category `local` | `none` | Local-only work never leaves the host, whatever the posture. |
| posture unavailable or errored | `none` | fail-open |

Tests use an injected policy and providers, never host config, and cover: a burning parent, a non-burning parent, local-only tasks, OFFLOAD ordering with groot first, dropping capped or unvalidated or unavailable routes, shadow counterfactuals for all four postures, and an unavailable posture.

Turning it on means setting `posture_enforce: true` in `~/.z0int/config/posture.local.json`. That is not done, and it should not be done until an evaluation passes (v0 or v1, below).

## 5. Claude Code hint (one line, only on change)

`z0int.claude_code.posture_hint` runs in the existing SessionStart and UserPromptSubmit hooks. No new hook is needed.

* **Key** = the factory posture plus the frontier group postures, for example `BURN|claude=BURN,codex=RESERVE,…`. Numbers never change the key.
* **Emitted only when the session's key changes.** A session's first observation counts only if it is actionable, meaning not BALANCED. Harness hand-backs (`<task-notification…`) never trigger a hint. State is kept per session in `state/claude-code/posture-hint.json`, bounded to 256 sessions.
* **Line.** One line of at most 320 chars, for example:
  `[z0 resource posture] BURN: claude:weekly ~45% projected unused at reset in 8.5h. Frontier quota perishes at reset: do bounded work directly rather than offloading it.`
  OFFLOAD names the $0 routes (`groot, local, …`). A return to BALANCED says what the posture was before.
* **Mode.** Set with `Z0INT_CLAUDE_CODE_POSTURE_HINT` or `posture_hint` in `~/.z0int/config/claude-code.json`.
  * `shadow` is the default. It decides and appends to `state/claude-code/posture-hints.jsonl` with `delivered: false`, and injects nothing.
  * `on` injects the line.
  * `off` computes nothing.
* **Fail-open.** A posture error never alters the turn. The hint is appended to any existing `additionalContext`, such as the automatic-context result or the State Packet.

## Pre-registered evaluation (v1): does the v1 method make better posture calls than v0?

Registered 2026-09-30, before any v1 code is live, before any enforced run, and before the v0
evaluation window's days 4–7 exist. This registration is separate from v0's and does not amend it. The v0
question, data, metrics and pass criteria stand as written.

**Question.** Over the same 7-day window and the same data, is posture computed by v1 (EWMA recent rate, band gating,
hysteresis) at least as good as v0 (window average), and good enough to enforce by v0's own bar?

**Window and data.** The window is the v0 window: day 1 starts at the first logged snapshot, `2026-09-30T18:38:40Z`. It uses the same three streams. v1 is **replayed** from the logged snapshots (the live timer runs v0 code, but the fields v1 needs, `remaining`, `resets_at` and `window_hours`, are the same). The v0 policy is replayed the same way (`v0`), next to `v0-logged`.

**Runner (mechanical).**
`z0int posture-replay --policy v0-logged --policy v0 --policy v1 --transcripts ~/.claude/projects --json`
prints each policy's v0-criteria report and `v1_vs_v0`, which is `posture_sim.v1_preregistered`.

**Fixed parameters.** These are not tuned on this window:
- `horizon_hours 6`, `lookback_hours 48`, `min_intervals 2`, `min_span_hours 1`, `min_interval_hours 0.75`, `quantum 1`, `z 1.96`;
- hysteresis on;
- v0 default thresholds.

**Criteria.** v1 is adopted only if **all** of the following hold.
- **A.** The v1 replay passes v0's criteria 1–4 exactly as registered in `resource-posture.md` (status `pass`).
- **B1.** The median projection error of v1 is ≤ v0's median + **1 pp**. This is non-inferiority: on steady usage, the window average is the better estimator, and the synthetic week shows a 0.3 pp gap.
- **B2.** BURN precision and OFFLOAD precision of v1 are each ≥ v0's. Each is scored only when both policies have n ≥ 5 verdicts of that kind; otherwise it is reported and not scored.
- **B3.** Maximum flips per group per day of v1 is ≤ v0's + 0.5.
- **B4.** The share of central BURN or OFFLOAD verdicts that the band withholds is ≤ 50%. Above that, the band is too wide to act on.

**Outcomes.**
- `v1_adopted`: all of the criteria hold. v1 becomes the method for any enforcement decision.
- `v0_retained`: A holds but some B criterion fails, or A fails. If neither v0 nor v1 passes v0's criteria, nothing is enforced.
- `insufficient_data`: fewer than 7 days of history, or streams missing. Enforcement stays off.

**Falsified.** If v1 fails A because criterion 3 fails (projection or precision), the recent-rate method is
not trustworthy enough here. Re-tune on a **new** window and register again. Do not re-tune on this window.

**Not claimed.** The same exclusions as v0 apply: quality parity of offloaded work and dollar costs. The hint's effect on Claude Code behaviour is also not claimed. The hint ships in `shadow`, and its log only establishes *when* it would have fired.
