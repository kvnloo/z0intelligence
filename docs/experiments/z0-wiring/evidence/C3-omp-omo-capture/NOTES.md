# C3-omp-omo-capture: build notes

Branch `feat/wire-omp-capture-20261003` in `/mnt/zer0models/z0-wt/wiring/wt/C3-omp-omo-capture`, based on the C1 head
`914dc99` (feat/wire-loop-core-20261003). Not pushed.

Commits:
- `7cc5353` test(omp): red tests (tests/test_omp_capture.py, bun index.test.ts / omo.test.ts, a local-cognition test, the
  changed contract of test_cognition_shadow::test_no_models_served_still_returns_the_compiled_legal_set)
- `1c35e8a` feat(omp): capture through the bridge, cognition-shadow hygiene, pinned install
- `06454d5` fix(omp): outcome model_id named like the opportunity's (provider/id), found by the e2e
- `6030df2` fix(omp): no capture thread in the resident worker (hook-adapter hand-off); load-robust latency test
- `7d64bd9` test(omp): p95 tail guard aligned with C1's +40 ms

## What was built (per red test)

1. turn_open -> `z0int.omp.opportunity_record.v0` via the detached core. The shim sends `cwd` (ctx.cwd), `harness`,
   `parent_id` / `agent_kind` (ctx.agent) and `model` (ctx.model, provider/id). `BridgeRuntime._capture` runs
   `harness_capture.begin_turn` and hands the 1-3 s build to `hc.spawn_detached` (the same child that the hook
   adapter uses: `python -m z0int.hook_adapter --harness omp opportunity`, with build slots). Latency: over 200
   alternating capture-off/on turns in a real task repo, the median per-turn cost is < 4 ms (measured 1.2-1.9 ms; with
   the build on the reply path it is 7.9-8.4 ms, which the test catches: latency-sensitivity-probe.txt 3/3 FAIL), and
   the p95 stays within capture-off p95 + 40 ms.
2. turn_close -> `turn_outcome.v0` on the same turn_key / work_item_id; `execution_completed` comes from the host
   (agent_end `aborted` gives false and ended=interrupted), and `verified_success` is always null in the capture row,
   even after an operator `/z0int-close --verified` (that claim stays in bridge_heart). missing_verifier and
   partial_measurement failure rows are written explicitly.
3. Subagent: a payload with `parent_id`, or with `agent_kind == "sub"`, gets cohort `agent` at capture. This is one
   condition in `harness_capture.begin_turn`. The shim keeps one open turn per session id, so an in-process
   subagent's turn no longer replaces or closes its parent's (`activeTurns` Map instead of a single `activeTurn`).
4. No cwd: C1's P-7 path applies. Git is a blocking unknown, the gate is OBSERVE/ASK, no ACT is legal and
   `repo` is null.
5. bridge.jsonl stores `capture: {is_harness_message, request_chars}` instead of `prompt[:400]`.
6. cognition_shadow writes a receipt row only when at least one backend answered. Not served, raised (e.g. URLError),
   timed out or transport_error each count as one `backend_unavailable` in
   `$Z0INT_HOME/shadow/cognition-shadow-counters.json` (flock), and the result also carries `backend_unavailable`.
   The receipt no longer stores `state` text; it stores `actual_tool: {name, risk_class}`, taken from the payload's
   `actual_tool` (name regex-checked, risk_class allow-listed) or from facts.tool_name. local-cognition no longer puts the
   tool input into `state` and sends `actual_tool`.
7. Parity: the new shim's frames (`turnOpenFrame` / `turnCloseFrame`, exported and built by bun) are served ok by the
   a9cbbed worker, and the a9cbbed shim's frames are served ok by the new worker, which also captures them. bridge-current.json
   written by either python tree or by the new TS shim (`publishCurrent`, now exported) reads the same in both. The
   a9cbbed `src/` (*.py, *.json, 148 files) is exported file by file with `git -C ~/tmp/z0int-canonical show
   a9cbbed:<path>` into the test's isolated TMPDIR. generation.py and protocol.py are byte-identical between the trees.
8. bun fake-pi: every capture handler returns undefined. Hostile ctx (throwing getters) or null events are caught,
   with no unhandledRejection or uncaughtException. With a missing interpreter the extension loads, and each lost turn
   is counted (`captureStatus().counters.worker_unavailable`) and persisted as a `z0int.omp.drop.v0` row
   (reason worker_unavailable) in `state/omp/drops.jsonl`. The spawn is guarded (try plus a stdin error handler), and
   `ensureWorker` de-duplicates concurrent starts. Salvaged from 815c680: `resolvePython` (Z0INT_PYTHON, then
   <root>/.venv, then $Z0INT_HOME/bin/python, then python3, resolved at spawn time) and `resolveSessionId`
   (ctx.sessionManager.getSessionId()).
9. scripts/omp_bridge_install.py salvages the behaviour of 815c680 (one symlink per extension, refuses anything that is
   not a symlink) and is narrowed to A2. Only z0int-bridge and local-cognition move; z0int-intelligence moves only with
   --include-routing. The target must be a clean git checkout with the extension index.ts files (dirty is refused,
   including in --dry-run). The JSON listing of every extensions/ entry goes to
   `<agent-dir>/extensions.links.bak-z0wiring-<YYYYMMDD>` before the first change, and an existing backup is kept.
   Links are swapped atomically (tmp link + os.replace), and a second run is all `unchanged`. The status/uninstall
   subcommands of the salvage were not carried over; rollback uses the backup listing.
10. OMO: `omp-extensions/z0int-bridge/omo.ts` is capture-only (`registerBridgeCapture(pi, {harness: "omo"})`), with
    no z0int-intelligence routing and no route_worker tool (G-#95). The bun test loads it through exactly the one-line
    re-export that activation writes. A diverged API, where `on` rejects an event or is missing, gives
    `{status: "UNSUPPORTED", missing}` plus a drop row (reason unsupported_api). The installed senpi 2026.9.27
    types.d.ts declares input/before_agent_start/turn_end/agent_end, cwd and sessionManager: SUPPORTED. Senpi says
    `willRetry` where OMP says `willContinue`; both keep the turn open.

## Deviations from the plan / honest notes

- red.txt is the red run of the first test version. After it, and before the final green, the tests changed in
  these ways: (a) the latency test was redesigned three times (separate fresh homes, then alternating off/on turns, a
  real git repo cwd, the real detached spawn of `cat`, and the paired-median bound); (b) the bun probe for
  `publishCurrent` returns `true`, because JSON.stringify(undefined) printed "undefined"; (c) `close_capture()` calls
  were removed with the design change below; (d) a new test, test_outcome_model_id_matches_the_opportunity_model_id,
  has its own red run in red-followup.txt. The final green.txt covers all of these at 7d64bd9.
- Design change during the build: the first implementation queued capture on C1's `Spool` thread inside the resident
  worker. Under a burst, that thread fought the reply for the GIL and starved itself; one full-suite run at 06454d5
  failed the latency test (kept as suite-head-06454d5.raw.txt). The final design has no capture thread. It is the hook
  adapter's pattern: begin_turn plus one detached spawn on open, and record_outcome on close, on the reply path at
  about 1.2-1.9 ms median per turn. C1's Spool is therefore not used by C3.
- Contract change in an existing test: test_cognition_shadow::test_no_models_served_still_returns_the_compiled_legal_set
  used to assert that a receipt file is written when nothing is served. The spec says "write only when the backend is
  served (else count backend_unavailable)", so it now asserts no receipt and backend_unavailable == 1. The result dict
  still lists every shadow row, including errors, so the other shadow tests are unchanged.
- The e2e's gate is ACT for a read-only question in a clean repo. That is C1's deterministic gate recorded in shadow;
  nothing acts on it.
- OMO: fixtures only. No real `omo` binary was run, because no isolated agent-dir env is verified for senpi; the
  spec's isolated e2e is OMP-only. OMO turns cannot be marked subagent, because senpi's ctx has no agent identity.
- The kerdoios defaults in bridge/runtime.py (`~/.hermes/profiles/chiefstaff/plugins/kerdoios`,
  `/workspace/evolution-lab/.venv/bin/python`) are unchanged live behaviour, out of scope. Every test and e2e here sets
  KERDOIOS_ROOT/KERDOIOS_PYTHON to nonexistent scratch paths, so nothing ran there.
- Owner item (found while reading, not changed): the bridge's default export imports `../z0int-intelligence/index.ts`
  relative to its own real path. After A2 repoints only the z0int-bridge link, the routing code registered *through
  the bridge* comes from the pinned tree, while the z0int-intelligence link stays on a9cbbed. A Symbol guard on
  pi.events makes only the first registration win, so which tree's routing runs depends on extension load order.
  This was already true before C3, since the bridge always imported its sibling. Use --include-routing, or keep
  both, as an owner decision.
- Owner item: historical rows written before activation are not rewritten. ~/.z0int/stream/bridge.jsonl still
  holds `prompt[:400]` and ~/.z0int/shadow/cognition-shadow.jsonl still holds state text with tool input. Purging
  or redacting them is an owner decision; nothing here touched live state.
- The live hermes-jev OMP extension (owner question 13) is not touched.

## Evidence files

red.txt, red-followup.txt, green.txt, suite.txt (+ raw logs), e2e.txt (+ e2e/omp-run.txt, e2e/parity.txt,
e2e/omp-run-06454d5.txt), latency-measure.txt, latency-sensitivity-probe.txt; scripts/: run.sh (isolated runner),
sitecustomize.py (socket guard), tdd_tests.sh, suite.sh, e2e_omp.sh, e2e_check.py, openai_chat_stub.py, measure.py,
test_latency_probe.py (scratch probe, not committed).

## Activation (NOT performed; owner approval + A0 pinned checkout /mnt/zer0models/z0-wt/pinned/z0intelligence)

1. A2 OMP: `python scripts/omp_bridge_install.py --target /mnt/zer0models/z0-wt/pinned/z0intelligence --dry-run`
   (review: only z0int-bridge and local-cognition retarget), then the same without --dry-run. It writes
   ~/.omp/agent/extensions.links.bak-z0wiring-<date> (JSON, every link target) before changing anything. If the owner
   already saved `ls -l` to that name (spec step 1), the script keeps that file.
2. Interpreter: set Z0INT_PYTHON=/mnt/zer0models/z0-wt/venv-wiring/bin/python in the environment OMP and OMO start
   from, or create $Z0INT_HOME/bin/python -> venv-wiring python (resolvePython's 3rd candidate). Without it the
   worker uses system python3, as it does today. Restart OMP (index.ts change).
3. OMO: write ~/.omo/agent/extensions/z0-capture.js containing exactly
   `export { default } from "file:///mnt/zer0models/z0-wt/pinned/z0intelligence/omp-extensions/z0int-bridge/omo.ts";`
4. Live proof: the owner runs one OMP turn and one OMO turn. Check ~/.z0int/state/{omp,omo}/{opportunities,outcomes}.jsonl
   for a pair joined on turn_key, with missing_verifier/partial_measurement rows as designed. A drops.jsonl row with
   reason worker_unavailable means the interpreter failed. Shadow lane: ~/.z0int/shadow/cognition-shadow-counters.json.
5. Kill switches: Z0INT_CAPTURE=0 or $Z0INT_HOME/config/capture.json {"enabled": false} (capture);
   OMP_Z0INT_COGNITION_SHADOW=0 (shadow lane).
6. Rollback: `ln -sfn <links.<name>.target from the backup> ~/.omp/agent/extensions/<name>` for z0int-bridge and
   local-cognition; rm ~/.omo/agent/extensions/z0-capture.js; restart OMP/OMO.

## REVISE round (verifier REVISE on 7d64bd9) -> commits b8a52af (tests) + fe077db (fixes)
Red: red-revise.txt = the final new/changed test files copied onto the unfixed implementation at 7d64bd9 in a temporary
detached worktree (removed). Every new test failed for its intended reason (route_worker tool registered; stale turn still
turn_in_flight; unclosed_turn not counted; shadow state without input; install had no kept/refusal). Green: green.txt at fe077db.
1. MAJOR (routing rode on the bridge): the z0int-bridge default export no longer imports ../z0int-intelligence. It registers
   the capture handlers and the three bridge commands only (label "z0int bridge v2 (capture)"). Routing comes only from the
   separate z0int-intelligence link, exactly once (bun test loads both on one fake bus: 1 tool, 2 before_agent_start handlers).
   Live behaviour before A2 is unchanged (canonical bridge + canonical link, Symbol guard). After A2 routing stays canonical.
   omp_bridge_install.py now reports z0int-intelligence as {"action": "kept", "target": ...} and refuses (dry run too) when the
   link is missing, since routing would vanish; --allow-no-routing is the explicit owner override (reported "absent").
   e2e: only z0int.bridge.worker is spawned; the bridged tool count equals baseline (11, was 12).
2. minor (OMO divergence): verified that the real senpi loader's on() (dist/core/extensions/loader.js) accepts any event name
   and exposes no runtime event list, so registration cannot detect divergence. Runtime detection: a turn opening in a session
   whose last turn never saw agent_end is a counted `unclosed_turn` drop (kind turn_outcome); bun test with an accept-anything
   host proves it. The types check resolves SENPI_TYPES, then module resolution, then $BUN_INSTALL (default ~/.bun) global
   install; when absent it is test.skipIf with the reason "OMO API status UNVERIFIED" in the test name, never a pass.
   No personal host path remains in the test. The runner passes SENPI_TYPES explicitly (isolated HOME).
3. minor (shadow request): restored the bounded tool input (2,000 chars) in the request to the served-only local shadow
   backend, so the lane's question is not trivial; persistence is unchanged (shadow.py receipt: actual_tool, no state/input).
   README updated. The e2e marker check still finds no tool-input text anywhere under z0home.
4. minor (stale turns): open turns carry openedAt; reload evicts turns older than Z0INT_BRIDGE_TURN_TTL_MS (default 2 h)
   with a counted `stale_turn` drop before the turn_in_flight guard; a fresh unclosed turn still defers reload (tested).
