# feat(hermes): one Hermes capture plugin over the shared capture core (C4a)

Branch `feat/wire-hermes-capture-20261003` @ `ee7cae0` · stacked on `feat/wire-loop-core-20261003` (C1) · component C4a-hermes-capture · **verified**

Staged branch only. Do not open as a PR until the owner reviews it.

## What

- `harness-adapters/hermes-z0intelligence` becomes the single standalone Hermes plugin (owner layout (a)). It absorbs:
  - the bend-native observer (#385);
  - the State Packet / DecisionOpportunity projection;
  - the capture half of `hermes-z0int-decisions`. That directory is retired: a stale install now writes `retired_vehicle` rows and no opportunity.
- The existing automatic `pre_llm_call` is kept. It is not registered while `automatic.json` has hermes off (#95).
- Capture-path fixes, each written TDD:

  | Fix | Change |
  |---|---|
  | P-1 | No host/port setting and no socket from any hook. A port key becomes a `config_warning` row. |
  | P-2 | Bounded close. |
  | P-3 | Persisted drops. |
  | P-7 | Uses the task cwd. The gate never ACTs on unknown git facts. |
  | P-9 | No packet text unless `persist_packet_text`. No State Packet snapshot or transcript scan for a projection. |
  | P-10 | Mode `off` registers no capture hook. |
  | P-11 | `enabled()` is called inside try. No `__pycache__` in the plugin tree. |
  | A1 | Rows go to `$Z0INT_HOME/state/hermes`. |
  | A2 | turn_outcome rows. |
  | A3 | Canonical turn_key. |
  | A4 | Bounded child pool, with projections tied to the plugin lifetime. |

- A double-capture guard: when z0int-decisions is enabled, or bend has `stack_opportunities`, the plugin writes `double_capture_guard` and emits no opportunities.
- Rows carry model_id and `policy_revision` (`hermes-z0intelligence@0.2.0`).
- Cron, kanban and cluster-service sessions get cohort `automated` at capture.
- `src/z0int/hermes_capture.py` is the shared core side.

## Why

Hermes is the largest interactive source (1,789 sessions) and captures nothing today. The work covers hermes-agent#319 H1-H6 and #385, and z0int#62 R5. The scorer/evaluator move (C4b) is a separate, gated follow-up.

## Tests

- 19 planned red tests, plus P-9 snapshot round-2 tests, plus the REVISE red tests (975d759).
- Full suite under `flock -s`:
  - base: 11 failed / 1018 passed;
  - head: 11 failed / 1048 passed;
  - identical failure sets, 0 socket-guard violations.
- `tests/test_hermes_decisions.py` behaviours stay green.

## Evidence

- `/mnt/zer0models/z0-wt/wiring/evidence/C4a-hermes-capture/`:
  - e2e-check.json verdict PASS, 17/17;
  - real `hermes chat -q` from a `git archive` export of fork ad31bbf against a request-recording stub;
  - the off and shadow requests are identical;
  - bend-native exp/contract-20261002 H1/H2/H6 checks re-run.
- The step-by-step activation is in `ACTIVATE.md` in that same directory.
- Integration: real `hermes chat -q`, 3/3 joined.

## Activation (NOT performed; owner approval; gated by G0)

See `ACTIVATE.md` A3.

1. The owner names the profile and checks `hermes --version` against ad31bbf.
2. Back up `config.yaml` and the plugin dir, and record `readlink`.
3. Install with `hermes plugins install https://github.com/kvnloo/z0intelligence/tree/<sha>/harness-adapters/hermes-z0intelligence --ref <sha> --force`. This is UNVERIFIED: it needs the network. The fallback is a `git archive` copy into `plugins/`.
4. Set settings `{mode: shadow, opportunities: true, z0int_python: <venv-wiring>, z0int_home: ~/.z0int}`.
5. Keep `automatic.json` hermes off.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
