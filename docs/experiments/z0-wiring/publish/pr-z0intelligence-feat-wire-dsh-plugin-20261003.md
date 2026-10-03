# feat(dsh): observe-only capture, lineage and z0 shadow plane in dsh-z0intelligence; router behind the #95 gate (C5)

Branch `feat/wire-dsh-plugin-20261003` @ `73fe3d7` · stacked on `feat/wire-loop-core-20261003` (C1) · component C5-dsh-plugin · **verified**

Staged branch only. Do not open as a PR until the owner reviews it.

## What

- The `hermes-jev-dsh` bundle (hermes-jev-skills `hermes-jev/omp-adapter` @9ea5777) folds into `harness-adapters/dsh-z0intelligence` as three files:
  - `capture.mjs`: an observe-only `agent/request` middleware that returns `next()` unchanged, plus `agent/turn-stopping` and `agent/error` handlers. It spawns `Z0INT_PYTHON -m z0int.hook_adapter --harness dsh`, detached.
  - `lineage.mjs`: lineage turn_key aliasing.
  - `shadow.mjs`: the z0 shadow plane. It calls only the fixed `POST <origin>/v1/plan`, and is off by default (no URL).
- Port 11501 is refused unless `allow_live_service` is set.
- A shadow response that says it executed is counted as `executed_unexpectedly` and never treated as ok.
- The `llm/stream` router is registered only when `router: true` and `automatic.json` has `dsh.enabled`. That combination is gated by z0intelligence#95.
- Removed:
  - the `~/.omp/.env` key read;
  - the `/workspace/hermes-home` routing config;
  - the openjev venv defaults;
  - `memory.js`.
- Errored turns close as `turn_errored` drop rows.
- The node contract tests run in CI (`core-unit.yml`).

## Why

Owner layout (b): DSH capture lives in the z0 plugin with no DSH core PR (z0int#62).

Behaviour change: the online Jev shadow lanes stop until a z0 shadow URL is configured (owner question 10).

## Tests

- 8 planned red tests, plus the REVISE red tests (0a887bd): fixed /v1/plan, executed responses and errored turns.
- pytest:
  - base: 12 failed / 1017 passed;
  - head: 11 failed / 1019 passed;
  - the head failure set is the known 11, with 0 socket violations.
- The `node --test` DSH contract passes, rc 0.

## Evidence

- `/mnt/zer0models/z0-wt/wiring/evidence/C5-dsh-plugin/`:
  - mock cordis host with real `hook_adapter` spawns;
  - 3 opportunities and 3 outcomes joined;
  - replay transcript identical with capture on and off;
  - capture off sends no shadow requests;
  - `forbidden-mutation-check.txt`.
- The DSH binary or wrapper was never run.
- The llm-replay source-tree e2e still needs owner approval, so DSH G-CAP is "unit / mock-host proven".

## Activation (NOT performed; owner approval)

See `ACTIVATE.md` A4.

1. For each of the 4 profiles, back up `cordis.patch.yml` and `package.json`, and record the node_modules link target.
2. Repoint `automatic-z0intelligence` to `<pinned>/harness-adapters/dsh-z0intelligence` with `{capture: true, router: false}` and no `shadow.url`.
3. Remove the `hermes-jev-dsh` bundle from the web profile.
4. The owner runs one DSH turn.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
