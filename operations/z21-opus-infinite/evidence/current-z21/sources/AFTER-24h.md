# Z21 24 h A/B — AFTER measurement

as of 2026-11-07 20:00 CST. Owner: hypothesis (kanban t_649b99c1). Read-only: state.db and kanban.db opened `mode=ro`; no config, job or plugin file was touched.

## 0. Status: the 24 h after-window has NOT elapsed — this is a partial note

The ship landed 18:20–18:35 CST on 2026-11-07. At the time of measurement it is 20:00 CST, i.e. **~100 min of after-window, not 24 h**. The requested before/after-24 h comparison therefore cannot be produced from real data and is not in this note. The 24 h window closes **2026-11-08 18:20 CST**; the re-run line is at the bottom.

What this note does contain: the arms verified live, the post-ship traffic actually observed, and one measured defect that changes which arms are worth keeping.

## 1. Arms verified live (evidence)

| Arm | Verification | Result |
|---|---|---|
| clean cron default + auxiliary | `yaml.safe_load(/workspace/hermes-home/profiles/clean/config.yaml)` | `cron: {model: claude-sonnet-5-5, model_provider: anthropic}`; `auxiliary:` 18 tasks (9 haiku, 8 sonnet, 1 other); `model.default` still `claude-opus-5-5` (interactive untouched) |
| 013 cron pin | `cron/jobs.json` | `6b604f772f6d` dash-wave-integrator → `claude-sonnet-5-5`/anthropic/`reasoning_effort: low`; `18ae43c4e812` factory-brain-1102 keeps `claude-opus-5-5` (deliberate); other enabled jobs (mobile-lane-refresh, ux1107-returned, ux1107b-returned) carry no model → inherit the Sonnet cron default |
| trim plugin (subprocesses) | `ls -l profiles/*/plugins/tool-context-trim` | symlinks created 18:32 in clean, reliability, ayo, ossfactory, product, z0i |
| trim plugin (gateway) | ayo: `reload-plugins`, PID 2996 unchanged | live for interactive/gui from ~18:35–18:48 CST; no `hermes-gateway` restart, so Kevin's live sessions kept running |
| 012 critic panel | `STREETIQ_PANEL_MODEL` default sonnet/medium in ideate.py; PR kvnloo/streetiq#41 | code live; PR still OPEN, its `test` check is red on a **pre-existing** Playwright openApp failure also present on main |

**Post-ship cron sessions on clean (proof the routing actually moved):**

```
cron / claude-sonnet-5-5  started 2026-11-08 00:30:14, 00:40:xx, 00:50:15 UTC   (18:30, 18:40, 18:50 CST)  x3
cron / claude-opus-5-5    started 2026-11-08 00:20:15 UTC                      (18:20:15 CST, boundary)    x1
```

## 2. Before / after table

Both windows computed with `z21-after.py` weighting (input 1, output 1, cache-read 1, cache-write 1.25, reasoning 2) over the same profiles.

| | pre-ship 24 h (11-06 18:20 → 11-07 18:20 CST) | post-ship 36 min (11-07 18:20 → 18:56 CST) | post-ship 87 min (11-07 18:20 → 19:47 CST) | post-ship 100 min (11-07 18:20 → 20:00 CST) |
|---|---|---|---|---|
| total weighted (all families) | 2,253,517,900 | 58,051,424 | 193,883,070 | 205,136,796 |
| **opus share of weighted** | **76.2%** (1,717,816,804) | **2.3%** (1,336,991) | **45.7%** (88,696,921) | **45.3%** (92,948,105) |
| ayo/local opus | 482,865,884 | 0 | 0 | 0 |
| clean/gui opus | 446,192,161 | 87,037,062 | 87,359,930 | 90,790,780 |
| z0i/local opus | 239,014,209 | 0 | 0 | 0 |
| reliability/kanban opus | 114,422,025 | 0 | 0 | 0 |
| clean/cron opus | 111,469,078 | 0 rows | 0 rows | 3,170,334 |
| clean/oneshot opus | 105,322,958 | 0 rows | 0 rows | 0 rows |
| clean/any | 453,057,101 | **0 rows** | **0 rows** | 93,961,114 |
| other families | 535,701,096 | 56,714,433 (vllm-sr/free + nemotron, all `kanban` workers) | 105,186,149 (kanban non-Claude) | 112,188,691 (kanban non-Claude) |
| opus prompt tokens per API call | 135,775 (input 42 + cache_read 135,733 over 11,883 calls) | n/a | n/a | n/a |
| finished tasks in window | 40 | 3 (0 with opus rows) | 5 (0 with opus rows) | 6 (0 with opus rows) |

Cross-check against the shipped artifact `z21-before-24h.json` (trailing 24 h ending 18:45): opus share 76.6%, clean/cron 109.6 M, clean/oneshot 105.3 M — same numbers within window noise. The tool reproduces the baseline.

**Read the "after" column with care — it is not an effect:**
- The post-ship weight is dominated by `kanban`-source traffic on non-Claude free models (`vllm-sr/free`, `nvidia/nemotron-3-ultra-550b-a55b:free`) — the very worker rows this measurement card generates, plus ayo/reliability/z0i/ossfactory/hypothesis workers. None of the four arms touches kanban worker routing (per-card `model_override`).
- **clean has new usage rows since ~20:12 CST** (one `gui` session continuing from 11-05, one `cron` session at 20:14). The `cron` session is the **factory-brain-1102** deliberate Opus pin (`18ae43c4e812`); the Sonnet-pinned `dash-wave-integrator` (`6b604f772f6d`) has not produced an agent-run execution in this window (monitor suppression). So `clean/cron` moving off Opus is proven by the *session model* (3 Sonnet cron sessions at 18:30/18:40/18:50), but its measurable lever (an agent-run execution) has not fired in 100 min.
- The per-finished-task number is 0/6 and under-reports by design: `tasks.session_id` is only written back for gateway-originated tasks (1 of 40 finished tasks joined at 18:45), so by-source is the reliable signal, per-finished-task a floor.
- **Correction to earlier attribution:** the 1,336,991 opus weighted tokens in the early post-ship slice are **not** free-model traffic — they are one ayo kanban worker (session `local-31ae…`, started 18:47:37, `claude-opus-5-5`, 22 calls). Kanban worker routing inherits the profile default and is untouched by all four arms; ayo's default model is still Opus.

## 3. Trim plugin sanity check — the arm is inert as shipped

Method: load the plugin's real `_on_transform_tool_result` in this process and replay it over the actual pre-ship 24 h tool results (`messages.role='tool'`, `length(content)>4000`, tool in `_READ_TOOLS`) from clean/reliability/z0i/ayo/ossfactory/product, read `mode=ro`. Scripts (durable copies in this directory, read-only, re-runnable on any window): `trim_classify.py`, `trim_arms.py`, `trim_counterfactual.py`, `trim_persist.py`, `trim_probe.py`, `trim_arms_postship.py`, plus the raw post-ship slice `post-ship-36min.json`.

Reproduction note on the clock: `z21-after.py --hours N` takes `since = now - N*3600`. Derive the ship boundary from the system clock inside the query (`SHIP = int(datetime(2026,11,7,18,20, tzinfo=ZoneInfo('America/Chicago')).timestamp())` = 1794097200), never by hand — a hand-computed cutoff that lands in the future silently returns "no post-change rows" and reads as "the change had no effect".

| measurement | value (pre-ship 24 h) | value (post-ship 100 min) |
|---|---|---|
| over-4k read-tool results in window | 1,115 | 55 |
| **cap actually fired** | **23 of 1,115 (2.06%)** — 250,799 → 65,877 chars, 73.7% of those, **0.43% of all tool-result chars** | **1 of 55 (1.82%)** — 4,320 → 1,888 chars, 56.3% of that one, **~0.11% of tool-result chars** |
| skipped as JSON payload | **1,092 (97.9%)** | **54 (98.2%)** |
| skipped as owner-path | 0 | 0 |
| in-session identical read-only repeats (dedupe arm) | 57 messages, 160,474 chars in 24 h | 0 |
| cap chars removed by tool | read_file: 184,922 | search_files: 2,432 |

Why the cap does not fire: Hermes wraps `read_file`/`search_files`/`skill_view` results in a JSON envelope (`{"content": "1|# Factory brain …"}`), and `_is_json_text` treats any parseable JSON as "a payload the model parses whole" and skips it. 1,092 of the 1,115 eligible results pre-ship, and 54 of 55 post-ship, are exactly that envelope, so the exemption swallows its own target population.

Counterfactual (unwrap the envelope, then cap the inner text at 3,200 chars):

| | value (pre-ship 24 h) |
|---|---|
| over-4k read results with a >4k inner payload | 928 of 1,115 |
| inner chars | 12,777,088 → 3,036,070 (**76.2% removed = 17.5% of ALL tool-result chars**) |
| inner payload size | median 9,496, p90 22,400 chars |
| prompt tokens not re-sent per 24 h (chars/4 × remaining calls in session) | **78,997,216 ≈ 4.60% of the same-window Opus weighted total (1.72 B)** |

Filed as **hypothesis-queue/014.md** for analyst (impact 4.6% of the Opus meter, ease S). The 4.6% is an upper bound: it assumes the affected sessions were Opus and that the trimmed text would have been re-sent on every later call in the session.

## 4. Which arms I would keep

1. **cron default → Sonnet + the 013 pin: KEEP.** clean/cron was the #5 Opus source at 111.5 M weighted/24 h (6.5% of the Opus meter) and 3 post-ship cron sessions already ran Sonnet; the only remaining Opus cron session is the deliberate factory-brain judgment pin. Watch for one week that dash-wave-integrator's output quality holds.
2. **auxiliary routing (18 tasks, fixed effort): KEEP.** Nothing in it is judgment work and it can only move weight down; it is also the arm with the least measurement coverage so far.
3. **012 critic panel → Sonnet: KEEP, but merge-gated.** PR #41 is red on a pre-existing failure that also fails on main; that is an owner merge call, not a reason to revert the code path.
4. **trim: FIX, do not roll back.** Rolling back discards a measured 4.6%-of-meter lever that has not been realised yet, because the JSON exemption disables it. Fix `_is_json_text` first (unwrap `{"content": …}` envelopes, then cap the inner text), then re-measure. The gateway reload means interactive sessions carry the same inert behaviour today, so nothing is lost by fixing forward.

## 5. Re-run line (24 h after the ship)

```
cd /mnt/zer0models/workspace/zer0/data/operations/z21-opus-infinite
python3 z21-after.py --hours 24 --json z21-after-24h.json
```

Not valid before **2026-11-08 18:20 CST**; before that the clean window is a few minutes long and the numbers below are noise. The after-vs-before judgment that matters then: does `clean/cron` and `clean/oneshot` token weight move to `sonnet`/`haiku` families, and does the opus share drop below 76.2% at equal finished-task count. This card is parked in `scheduled` with `SCHEDULED_UNTIL=2026-11-08T18:20:00-06:00` so the same measurement runs then.

## 6. Pre-gate partial slices — accumulated evidence

| | pre-ship 24 h (baseline) | post-ship 36 min | post-ship 69 min | post-ship 87 min | post-ship 100 min |
|---|---|---|---|---|---|
| total weighted | 2,253,517,900 | 58,051,424 | 190,599,494 | 193,883,070 | 205,136,796 |
| **opus share of weighted** | **76.2%** | **2.3%** | **47.8%** | **45.7%** | **45.3%** |
| clean/gui opus | 446,192,161 | 87,037,062 | 87,037,062 | 87,359,930 | 90,790,780 |
| clean/cron opus | 111,469,078 | 0 rows | 0 rows | 0 rows | 3,170,334 |
| clean/oneshot opus | 105,322,958 | 0 rows | 0 rows | 0 rows | 0 rows |
| ayo/kanban opus | — | 1,336,991 | 1,336,991 | 1,336,991 | 1,336,991 |
| non-Claude (kanban) | 535,701,096 | 62,745,000 | 105,186,149 | 105,186,149 | 112,188,691 |
| finished tasks in window | 40 | 3 | 4 | 5 | 6 |
| finished tasks with opus rows | — | 0 | 0 | 0 | 0 |

Read this table the same way as §2 — it is a **slice, not an effect**:

1. **The only clean rows are the untouched interactive arm and the deliberate factory-brain pin.** One `session_model_usage` row on clean `gui` since 18:12 CST (session `20261105…`, started 11-05 15:51, `claude-opus-5-5`, 295 calls, 87.0 M → 90.8 M weighted across the slice). One `cron` row at 20:14 CST (30 calls, `claude-opus-5-5`, 3.17 M weighted) — this is the **factory-brain-1102** (`18ae43c4e812`) deliberate Opus pin. `model.default` for interactive is deliberately still `claude-opus-5-5` (graph hubs `profile:clean` thr 1.841, `model:claude-opus-5-5` thr 1.098 deg 5209). Kevin using Opus interactively post-ship is the design, not a regression.
2. **clean/cron Sonnet pin is live but its measurable lever has not fired.** `cron/executions.db` (read-only) shows jobs firing and completing pre-ship only (latest 2026-11-01). The 3 post-ship Sonnet cron sessions (18:30/18:40/18:50) were observed via session starts in the parent task but their token accounting has not landed (monitor suppression — "monitor output unchanged — suppressing agent run" / `no_change (agent run suppressed)`). In the pre-ship 24 h, 35 of 42 `6b604f772f6d` outputs were suppressed, 7 were agent runs. So the Sonnet cron pin is live but its measurable lever (an agent-run execution) has not fired in 100 min.
3. **factory-brain-1102 (18ae43c4e812, the deliberate `claude-opus-5-5` pin) fired at 20:14 CST.** Last execution 17:21 → 17:50 **failed `interrupted`** (`cron_incidents` row `18ae43_17abc2b53c92`, state `detected`, output `2026-11-07_17-50-13.md`), which is **pre-ship**; next fire re-anchored to ~19:50 and completed at 20:14 with Opus tokens. If it stays up through the 24 h window, the after-window will include its Opus weight — the 24 h re-run must report its execution state, not just tokens.
4. **Trim arm on the new window: still inert, as expected.** Re-ran the shipped plugin over post-ship tool results (`trim_arms_postship.py`): 55 over-4k read results, cap fired on **1** (1.82% reach, 4,320 → 1,888 chars), **54 skipped as JSON payload**, 0 owner-path — i.e. 2,432 chars = **~0.11%** of tool-result chars in the window, versus 2.06% / 0.43% pre-ship. Same defect shape as `hypothesis:014`; a null result here is the expected outcome of the JSON-envelope exemption, not a pass.
5. **burn-pace gate is a both-windows confound — quote it at the 24 h re-run.** `burn-pace --json` at 20:01 CST: lane `claude` 59.0% used pace 2.22 `hard-throttle`, `claude2` 85.0% pace 2.54 `hard-throttle`, `claude3` 100.0% `hard-stop-exhausted`; `z0_factory` posture `RESERVE`, `avoid: [claude]`. `clean/logs/errors.log` carries 2,052 `every subscription row is over pace; refusing to drain one` entries spread across 11:00–19:00 today — **including pre-ship hours** (16:xx 122, 17:xx 335, 18:xx 16, 19:xx 169). The gate therefore rides inside both windows and does not void the A/B, but the pre-ship 76.2% already contains gate effects, so the 24 h after-number must be published with the gate state measured in the same window.
6. **Per-finished-task floor unchanged:** 6 finished tasks in the 100-min window, 0 joined to opus rows. `tasks.session_id` is only written back for gateway-originated tasks, so this number is a floor, not a total.

The 24 h re-run must add, beyond the table: (a) the burn-pace gate state at measurement time, (b) agent-run vs suppressed execution counts for `6b604f772f6d` and the up/down state of `18ae43c4e812`, (c) a fresh `trim_arms_postship.py` run. Graph context: network-model latest = `graph-20261107-192248.json` (generated 19:22:48 CST, fresh < 2 h); nodes cited: `model:claude-opus-5-5`, `model:claude-sonnet-5-5`, `profile:clean`, `act:read`, `hypothesis:014`, `issue:kanban/t_649b99c1`.

## 7. Pre-gate dispatch #2 — post-ship 116-minute slice, measured 2026-11-07 20:19 CST

Card `t_649b99c1` was spawned at 20:16 CST with the gate still closed (24 h window opens **2026-11-08 18:20 CST**), so per the card's own policy this dispatch measured whatever slice existed and re-parks. Slice: ship boundary 18:20:01 → 20:19:32 CST = **1.992 h**. Method identical: `z21-after.py` weights, state.db + kanban.db `mode=ro`, ship boundary derived from the system clock inside the shell (`1794097200`), never by hand. Raw output: `z21-after-post195.json`.

| | pre-ship 24 h baseline | post-ship 116 min (20:19 CST) |
|---|---|---|
| total weighted (all families) | 2,253,517,900 | 709,127,893 |
| **opus share of weighted** | **76.2%** | **83.1%** (589,444,360) |
| ayo/local opus | 482,865,884 | 479,652,910 — **cumulative artifact** (see below) |
| clean/gui opus | 446,192,161 | 106,339,539 (89.98 M + 0.81 M + 15.55 M) |
| clean/cron opus | 111,469,078 | 3,170,334 (factory-brain-1102 deliberate Opus pin; cron session started 19:51, 30 calls) |
| clean/oneshot opus | 105,322,958 | **no rows** |
| sonnet / haiku rows, all profiles | — | **ZERO — no Sonnet/Haiku token row has landed anywhere post-ship** |
| ayo/kanban opus (worker, untouched by arms) | — | 1,336,991 (22 calls, session started 18:47) |
| finished tasks in window | 40 | 6, 0 joined to opus rows (floor caveat) |

**The 83.1% opus share is NOT a post-ship regression — it is a cumulative-row artifact of the join.** `session_model_usage` rows hold each (session, model[, task]) *cumulative* total, so a trailing `last_seen` window counts the whole lifetime total of any long-lived session that was merely *touched* in the window. Verified row-level (read-only query on the two state.dbs):

- `ayo/local` 479.7 M = one interactive session `local-dda30205c2ee64`, **started 11-04 21:37**, 2,432 cumulative calls — three days of Kevin-side interactive weight, not a 2 h burst (the pre-ship 24 h baseline shows the same session at 482.9 M).
- `clean/gui` 89.98 M = session started **11-05 15:51**; the 15.55 M = session started **11-07 16:59**. Both interactive; `model.default` stays `claude-opus-5-5` by design.
- The only genuinely *new* post-ship rows: `clean/cron` = **factory-brain-1102** (`18ae43c4e812`, deliberate Opus pin, started 19:51), and the ayo kanban Opus worker (kanban routing inherits the ayo profile default; no arm touches it).

**Where the arms stand at token level, 116 min in:**

1. **clean/cron → Sonnet: proven at session level, still unproven at token level.** Three cron sessions ran `claude-sonnet-5-5` (18:30/18:40/18:50), but their usage rows remain monitor-suppressed (`no_change (agent run suppressed)`) — zero Sonnet token rows exist anywhere. The only cron row landed is the *deliberate* factory-brain Opus pin. The 24 h re-run is still the first chance to measure the pin's lever.
2. **clean/oneshot → auxiliary routing: still no rows.** Unmeasurable at 116 min, same as at 100 min.
3. **auxiliary routing: no token evidence yet** (it fires on job executions, suppressed).
4. **Trim arm — fresh run over the 116-min window (`trim_arms_postship.py`, durable copy here):** 64 over-4k read-tool results, cap fired on **3** (web_search 15,370 + search_files 2,432 = 17,802 chars removed), **61 skipped as JSON payload**, 0 dedupe-replayed. Same null shape as the pre-ship window (2.06% reach / 0.43% of chars) — the arm stays inert until the JSON-envelope exemption is fixed (`hypothesis-queue/014.md`). Per reliability's verification (card t_895fcae1): a null result here is the **expected** outcome of the exemption, not a pass, and not evidence the cap design fails.
5. **burn-pace confound (quoted at measurement time, `burn-pace --json` 20:19 CST):** lane `claude` 59.0% used, pace 2.21, **hard-throttle**; `claude2` 85.0%, pace 2.52, hard-throttle; `claude3` 100.0%, **hard-stop-exhausted**; `z0_factory` posture `RESERVE`, `avoid: [claude]`. Unchanged since the 20:01 reading — the gate rides inside both windows, so the 24 h after-number must be published with the gate state measured in the same window (§6 note 5 stands).
6. **Dispatch starvation, this card:** 4 spawn attempts, **37 m10 s of zero-progress wall**, all 3 finished attempts dead at the provider seam (exit 75, `HTTP 429: model service returned an invalid error response`, `respawn_guarded reason=rate_limit_cooldown` ×4 per attempt). Fleet-wide the same seam ate **594 of 695 spawned workers in 24 h** (`task_events`, `mode=ro`) — filed as **hypothesis-queue/015.md**. This is why the slice is dated 20:19 and not earlier: the card's first three attempts never reached a tool call.

**Verdict at 116 min: unchanged from §4 — KEEP cron/013/012/auxiliary, FIX (not roll back) trim.** No measurable token-level move has happened on any arm's lever yet because (a) the Sonnet/cron and auxiliary executions are monitor-suppressed and (b) no oneshot/cron agent run produced token rows. The 24 h re-run at/after 2026-11-08 18:20 CST remains the first honest A/B read. Card re-parked `scheduled` with `SCHEDULED_UNTIL=2026-11-08T18:20:00-06:00`.