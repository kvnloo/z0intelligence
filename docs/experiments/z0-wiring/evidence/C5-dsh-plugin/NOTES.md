# C5-dsh-plugin — build notes

Worktree `/mnt/zer0models/z0-wt/wiring/wt/C5-dsh-plugin`, branch `feat/wire-dsh-plugin-20261003`, base C1 head
`feat/wire-loop-core-20261003 @ 914dc99`. Commits: `7ea2445` (red tests), `157e1a5` (implementation). Not pushed.

## Files
- `harness-adapters/dsh-z0intelligence/capture.mjs` (new): observe-only `agent/request` middleware that returns the
  exact `next()` object, plus `agent/turn-stopping`, which returns nothing. Per root user turn it spawns one detached
  `Z0INT_PYTHON -m z0int.hook_adapter --harness dsh prompt` at step 1 and one `... stop` at the stop boundary,
  writing the normalized payload `{session_id, turn_id=<agent id>:<turn>, prompt|-, model, cwd}` on stdin. Every
  request also writes a content-free `z0int.dsh.lineage.v0` row. A spawn failure writes a `z0int.dsh.drop.v0`
  `spawn_failed` row and bumps a counter. The node side obeys the same kill switches (`Z0INT_CAPTURE=0`, capture.json).
- `lineage.mjs` (new): port of hermes-jev-skills `dsh/plugin/lineage.js` @9ea5777, plus `isRootAgent` and
  `latestUserText` from its index.js, the lineage turn key, and `canonicalTurnKey` (a JS port of `harness_id.turn_key`
  for dsh; parity is checked against Python in test_dsh_capture.py and the e2e).
- `shadow.mjs` (new): the Jev shadow plane via a configured z0 service only. It sends POST `<url>/v1/plan` (the
  existing pure shadow route, which never executes). It keeps the deterministic FNV sampling per lineage turn key
  and the max-inflight cap from 9ea5777, and writes `z0int.dsh.shadow_decision.v0`. With the default (no URL) it
  makes no call and writes one `shadow_plane=off` row per process (per reason). Port 11501 is refused unless
  `allow_live_service: true`.
- `index.mjs`: `apply(ctx, config, deps)` registers capture unless `capture: false`. It registers the existing
  llm/stream router only when `config.router === true` AND `automatic.json` `dsh.enabled === true` (the same field
  `z0int.automatic.settings` reads; `Z0INT_AUTO_DSH=0` also blocks it). The router body is unchanged.
- `README.md` (new); `tests/test-dsh-plugin.mjs` (16 node tests); `tests/test_dsh_capture.py` (1 pytest).

## Red-test map (spec red_tests 1-8)
1 observe-only + byte-identical stream: two node tests "1 ...". 2 router gate: "2 ...". 3 one normalized event per
hook + join on the aliased lineage turn_key: node "3 ..." x2 and `test_dsh_capture.py`. 4 forbidden paths (source +
child env): "4 ...". 5 shadow plane: five "5 ..." tests (default off once per process in a child process; sampling at
the configured rate against a fake z0 service on 11540-11559; inflight cap; service down gives backend_unavailable
counted; 11501 refused). 6 fail-open: three "6 ..." tests incl. `--unhandled-rejections=strict`. 7 privacy: "7 ...".
8 no memory.js: "8 ...".

Red (red.txt): all 16 node tests and the pytest failed on missing behaviour: no agent/request middleware, the
router registered by default, missing exports and modules. No test failed on an import or syntax error of the test.

Red-test correction after red, before green: in test 4 the dotenv regex `/\.env\b/` also matched the JS spread
`...env` in the new capture.mjs. It was narrowed to a dotenv path, `/(^|[\/'"`])\.env\b/m`. A mutation check
(forbidden-mutation-check.txt) shows the narrowed set still flags the original hermes-jev-dsh index.js on 7 patterns.
The README was also reworded so it does not name the forbidden paths or memory.js, because test 4 and test 8 scan
every plugin file.

## Deviations / interpretations
- "one normalized hook event" means one event per hook kind per user turn: one `prompt` and one `stop`. Tool
  continuations (step > 1), repeated stop boundaries and subagent requests send no hook event. Subagents get
  lineage rows only, with no `agent` cohort capture. DSH has no SubagentStart/Stop seam proven here; this is left for
  a follow-up.
- The stop event carries ids only (no response text), so each DSH outcome has `partial_measurement` (asked_user,
  tool_calls, assistant_messages missing) and `missing_verifier` rows, as the C1 core designs for unverified
  harnesses. Labels come from AgentsView/C2.
- `agent/turn-stopping` can fire more than once if another listener steers. Only the first closes the turn.
- "automatic.json dsh===true" is implemented as `{"dsh": {"enabled": true}}`, matching z0int.automatic.settings.
- Capture defaults to on when the plugin is loaded (activation sets `capture: true` explicitly anyway). The shadow
  plane defaults to off.
- The shadow request body sends the prompt (cut to 4000 chars) to the configured z0 service, as the
  hermes-jev-dsh lanes did. No row stores it. The default is no URL.
- Dropped from hermes-jev-dsh (spec "Removed" list plus routing): the `jev route` call and effort/model rewriting
  (the observe-only rule), the `~/.omp/.env` key read, the `/workspace/hermes-home` routing config, the openjev
  venv/Z0INT_SRC defaults, the `~/.dsh/jev/receipts.jsonl` private schema, the agent_probe/provider_inventory
  diagnostics and memory.js.
- The DSH profile intelligence-z0intelligence MCP rows were inventoried read-only: all four profiles (web, headless,
  sdk-minimal, deepseek-sdk) carry an `automatic-z0intelligence` -> `dsh-z0intelligence` entry. Nothing was changed
  (A9).
- Integrate note: C8 adds `dsh-z0intelligence/memory.mjs` registered from index.mjs. Test 8 forbids only `memory.js`,
  `state.db`, `TencentDB` and `.hermes`, and exact export keys `apply, canonicalTurnKey, counters, name, sampleHash`.
  If C8 adds an export, update that list in the merge commit.

## E2E
See e2e.txt. Unit-level isolated e2e with an unmocked spawn: the real `python -m z0int.hook_adapter --harness dsh`
plus detached opportunity builds, Z0INT_HOME=homes/C5-dsh-plugin/e2e-on/z0home, and a fake z0 service on 127.0.0.1:11551.
Result: 3 opportunities and 3 outcomes joined on the canonical turn_key (= the alias of the lineage key), 0 drops,
3 shadow ok rows, 7 lineage rows, no synthetic text in any row, and an identical replay transcript with capture on vs off.
Socket guard (Python + node): zero violations.
The DSH-source headless/llm-replay e2e was NOT run because it needs owner approval: DSH G-CAP = "unit-proven only".

## REVISE round (verifier REVISE on 157e1a5) -> commits 0a887bd (red tests) + 73fe3d7 (fix)
- major, shadow route configurable: `shadow.path` removed; the endpoint is always `new URL('/v1/plan', url)` (a base
  URL with its own path or query is reduced to its origin + /v1/plan). New node test: shadow.path / endpoint / route =
  /v1/worker, /v1/intelligence, a relative path or an absolute URL, and base URLs ending in /v1/worker, all hit
  /v1/plan only (red run showed /v1/worker and /v1/intelligence actually being called). A response with
  executed:true (top-level or route) is written `executed_unexpectedly` and counted `shadow_executed` (new test).
- minor, CI: core-unit offline-suite runs `node --unhandled-rejections=strict --test tests/test-dsh-plugin.mjs`
  (setup-node 22, as aodl-admission.yml); push/pull_request paths include harness-adapters/**.
- minor, lineage surface: resolveRootSession / traceIdFor / mintTraceId removed (unreachable); a root's trace id is
  hash32(`task:<session>`) as before; operationId / lineageOf / roleFor are module-private.
- minor, tests mirroring implementation: test 8 now asserts only no memory.js file, no memory.js / state.db /
  .hermes reference, no Hermes export (exact export list and TencentDB ban dropped, so C8's memory.mjs merges
  cleanly). The JS turn_key test checks the known vector 6935feb94c3840e4f0e08d176cabe416 =
  harness_id.turn_key('dsh','abc','session-abc:1') = turn_key_from_alias('dsh.lineage_turn_key', ...).
- minor, turn-stopping / errored turns: agent/error closes an open root turn with a counted
  z0int.dsh.drop.v0 {kind: turn_outcome, reason: turn_errored, turn_key} row (counter turn_errored); errors on
  children/unknown turns do nothing (new test). The error-before-stop path does not send a stop event to the C1 core
  because the core's only failed-ending signal is a Claude/Grok hook_event_name, which would trip misattribution.
  README documents the steering limit and the aborted-without-error limit (open until LRU eviction, no row).
- Evidence: red.txt (3 new tests fail for the intended reasons, 16 pass), green.txt (19/19 node under
  --unhandled-rejections=strict, 1 pytest), suite.txt, e2e.txt (round-1 files kept as *-round1.txt).
