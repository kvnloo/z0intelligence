# feat(omp): OMP and OMO capture through the z0-owned bridge, cognition-shadow hygiene, pinned install (C3)

Branch `feat/wire-omp-capture-20261003` @ `3f6d139` · stacked on `feat/wire-loop-core-20261003` (C1) · component C3-omp-omo-capture · **verified**

The head includes the Integrate-phase fix pair `a6d38de` (red) / `3f6d139` (fix). The component verification ran at `fe077db`. The fix was written TDD on this owning branch and re-verified in the integration suite and e2e.

Staged branch only. Do not open as a PR until the owner reviews it.

## What

- `omp-extensions/z0int-bridge`:
  - passes the ctx cwd;
  - `turn_open` / `turn_close` emit `z0int.omp.{opportunity_record,turn_outcome}.v0` through the C1 core, detached;
  - subagent turns get cohort `agent` at capture;
  - `bridge.jsonl` no longer stores `prompt[:400]`.
- The bridge is capture-only. Routing stays on the separate `z0int-intelligence` link, so live routing behaviour is unchanged.
- Turns not closed within `Z0INT_BRIDGE_TURN_TTL_MS` (default 2 h) are evicted as `stale_turn`.
- `local-cognition` / `cognition_shadow`:
  - write a shadow answer only when the backend is served; otherwise they count `backend_unavailable`;
  - record a structured `actual_tool` with no tool input;
  - bound the shadow input.
- OMO: `omp-extensions/z0int-bridge/omo.ts`, a capture-only senpi re-export shim with no route_worker. If the senpi API diverges, it records UNSUPPORTED.
- `scripts/omp_bridge_install.py`:
  - retargets only the `z0int-bridge` and `local-cognition` links;
  - writes `extensions.links.bak-z0wiring-<date>` first, and keeps an existing backup;
  - is idempotent;
  - refuses a dirty target;
  - refuses to proceed without a routing link unless `--allow-no-routing` is passed;
  - never moves `z0int-intelligence` without `--include-routing`.
- Protocol parity: z0int.bridge.v2 frames and `bridge-current.json` are tested against a `git show` export of the canonical a9cbbed tree.
- Integration fix 3f6d139: the resident bridge worker child and its stdio are unref'd at spawn. Before this, `omo -p` hung until timeout because the worker kept the host alive.

## Why

OMP is the highest-volume live harness (oh-my-pi#109, z0int#62). The owner's capture order is OMP, then Hermes, then DSH.

## Tests

- 10 planned red tests, plus the REVISE red tests: routing split, stale turns, senpi divergence and the shadow request.
- The Integrate fix pair (`evidence/integration/c3-fix-{red,green}.txt`).
- pytest at fe077db:
  - base: 11 failed / 1018 passed;
  - head: 11 failed / 1035 passed;
  - identical failure sets, 0 socket-guard violations.
- bun at fe077db: 28 pass / 0 fail.
- Integration, final e02bcf5: bun omp-extensions 28 pass / 1 skip.

## Evidence

- `/mnt/zer0models/z0-wt/wiring/evidence/C3-omp-omo-capture/`:
  - e2e_check 16/16 true;
  - a real `omp -p` against a private stub;
  - parity with the canonical a9cbbed export.
- `evidence/integration/E2E.md`:
  - real `omp -p` and `omo -p`, 2/2 joined each;
  - model requests identical with shadows on, off and with no extension.

## Activation (NOT performed; owner approval)

ACTIVATE.md A2.

1. Run `python scripts/omp_bridge_install.py --target /mnt/zer0models/z0-wt/pinned/z0intelligence --dry-run`. Only bridge and local-cognition should change, and `z0int-intelligence` must show as kept.
2. Run the same command without `--dry-run`.
3. Set `Z0INT_PYTHON` to venv-wiring and restart OMP.
4. Write `~/.omo/agent/extensions/z0-capture.js` as a one-line re-export.

Kill switches: `Z0INT_CAPTURE=0`, `OMP_Z0INT_COGNITION_SHADOW=0`.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
