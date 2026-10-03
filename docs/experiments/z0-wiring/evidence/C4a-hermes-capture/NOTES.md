# C4a-hermes-capture: build notes

Worktree /mnt/zer0models/z0-wt/wiring/wt/C4a-hermes-capture, branch feat/wire-hermes-capture-20261003, base
feat/wire-loop-core-20261003 @ 914dc99 (C1 head). Not pushed.

Commits:
- ebb0fde test(hermes): red tests for the one Hermes capture plugin (C4a)
- f29ec04 feat(hermes): one Hermes capture plugin over the shared capture core (C4a)
- 07a2272 fix(hermes): no State Packet snapshot or transcript scan for a Hermes projection (P-9)

## What was built

`harness-adapters/hermes-z0intelligence` is now the single standalone Hermes plugin (plugin.yaml + register(ctx),
stdlib only). `harness-adapters/hermes-z0int-decisions` is removed. No Hermes code was changed; the plugin reads
three Hermes facts through Hermes's own modules, each inside try (fail open): the task cwd
(`tools.file_tools_paths._authoritative_workspace_root`), the session source (`gateway.session_context`), and the
profile config for the double-capture guard (`hermes_cli.config.load_config_readonly`, read-only, at load).

Shape (one sentence each):
- Hooks build small content-free jobs and put them on a bounded queue (MAX_QUEUE 4,096), then return None.
- One writer thread hands batches (<=512) in order to `<z0int_python> -m z0int.hermes_capture batch`, a detached
  child (one at a time, so a turn's prompt is processed before its outcome), which acks each job on stdout.
- The child writes through C1's `harness_capture` (begin_turn, outcome_context, record_outcome, record_drop,
  record_failure) to `$Z0INT_HOME/state/hermes/`; events go to `events.jsonl` (`z0int.hermes.event.v0`).
- A projection takes one of `harness_capture.MAX_CHILDREN` build slots without waiting (new `try_slot`) and runs
  detached (`z0int.hermes_capture project`, inheriting the locked slot file), or is refused as `fanout_cap`.
- Unacked jobs, a full queue, an unavailable z0int (no interpreter, or a child exiting non-zero with no ack;
  retried after 30 s) and `close()` leftovers are counted into `drops.jsonl`; `z0int_unavailable`,
  `config_warning` and `double_capture_guard` are failure rows written by the plugin itself.
- `close()` (plugin unload, and at interpreter exit via atexit because `hermes chat -q` exits right after its
  turn) drains while time remains, kills a running batch child at ~1.6 s, counts what is left and returns <= 2 s.
- The automatic `pre_llm_call` (z0int.automatic) is registered only when automatic.json has hermes.enabled true at
  load; otherwise nothing is called or spawned for it (today hermes is off, #95).

Capture-core extensions (C1 callers unchanged): `begin_turn(..., cohort=None)` (shim cohort; `harness` forces the
injected path), `try_slot()` (build_slot now uses it), `opportunity_record/record_opportunity(packet_text=None,
packet_args=None)` (None keeps C1 rows byte-identical; `redacted` digests every State Packet claim/superseded/
contradiction value, `opt_in` keeps them; packet_args are passed to build_state_packet).
`hermes_decisions.decisions_report` gains `rows_dropped` (from drops.jsonl) and tolerates a null request.

Red tests -> fixes (all in tests/test_hermes_capture.py unless noted):
P-1 schema + socket guard (in-process and a sitecustomize guard in the children) + counted config_warning;
P-2 bounded close with a stuck child; P-3 persisted drops = fresh `z0int hermes decisions` rows_dropped; P-7 task
cwd from Hermes's resolver, never os.getcwd(), no ACT with git unknown; P-9 packet text + snapshot files; P-10 mode
off registers nothing, never pre_tool_call (also host test); P-11 settings error fails open, no __pycache__ from
the child in the installed tree; A1 sink; A2 outcome fields + session-end gap fill; A3 canonical turn_key and both
aliases; A4 H3 flood (3,400 events, 0 event drops, 800 fanout_cap) + slot bound; double-capture guard (both
vehicles); retirement; inertness (normal + hostile payloads; host test through PluginManager); automatic inert;
post_tool_call check class + exit only; model_id + policy_revision; cohort automated (cron, kanban platform,
HERMES_KANBAN_TASK, cluster session source); z0int missing/broken fail open + counted;
toolsets-shape profile loads the plugin (host test, tests/test_hermes_capture_host.py).
Regression guards: tests/test_hermes_decisions.py (13 functions, 23 cases) retargeted to the moved plugin, all
green; tests/test_turn_key.py repointed to the new plugin dir (`_key` kept).

## Evidence files
- red-attempt1-seam-errors.txt, red-attempt2.txt: first two red runs, kept. In attempt 1 most tests stopped at a
  monkeypatch of a seam that did not exist yet (AttributeError); attempt 2 still had a fixture ImportError.
  The helpers were changed to `raising=False` plus an explicit "register() in mode shadow must return the capture
  instance" assertion, so the tests fail on behaviour.
- red.txt: the red run on 914dc99 + the uncommitted red tests: 23 failed + 2 errors. The 2 errors are setup
  failures of the `hcap` fixture with the message "z0int.hermes_capture (the child that writes the Hermes capture
  rows) does not exist", which is the missing behaviour. Host tests ran against the isolated Hermes venv.
- red-round2-p9-snapshot.txt: red found by the e2e trial (State Packet latest.json kept README text), on f29ec04.
- green.txt: 85 passed at 07a2272 (red tests, host tests, regression guards, C1 core files); 0 socket-guard
  violations.
- suite.txt (+ suite-head.raw.txt, suite-base.raw.txt): full suite head vs base.
- e2e.txt, e2e-run.log, e2e-check.json: isolated real-Hermes e2e, verdict PASS.
- scripts/: run.sh (isolated runner), sitecustomize.py (socket guard), chat_stub.py, e2e_hermes.sh,
  e2e_config.py, e2e_check.py.

## Deviations from the plan (also in the structured result)
1. Opportunities are built for interactive user turns only. Cron, kanban, cluster and subagent turns get outcome
   rows with cohort `automated`/`agent` and no opportunity; injected system turns get cohort `harness`. This keeps
   the z0int-decisions regression guard (cron/subagent turns never project) while adding the capture-time cohort.
2. Test-side corrections after the recorded red run, intent unchanged: the P-1 schema check matched the substring
   `port` inside `opportunities` (now matches `_`-separated words); the P-7 check read `source_status` but the
   opportunity's unknown carries `status`. Added during implementation (green only): the interpreter-exit drain
   test. P-9 extended after the e2e trial (red round 2), then relaxed from "no state_packet dir at all" to "no
   latest.json/history.jsonl and no packet text anywhere", because state_packet's revision probe always writes a
   transcript-cursor file; for Hermes it now scans a path with no transcripts, so that file holds no text.
3. Red test 13's "CONTRACT H1 harness" is reproduced as real Hermes (`hermes chat -q`) against a recording stub,
   off vs shadow, 10 pairs + A/A, instead of the contract lane's CUA/Ollama harness (no model, no GUI).
4. e2e install: `hermes plugins install <path> --enable --no-deps` is parsed by this Hermes as GitHub shorthand; the
   local form is `file://<repo>#<subdir> --ref <sha>`, which clones and scans, then fails offline at Hermes PM's
   runtime sync (python-build-standalone + hermes-pm-runtime downloads). Network was not opened (the install path
   also records shared metrics). The e2e lays out the same tree (`git archive <sha>` of the plugin dir) in
   $HERMES_HOME/plugins and enables it in config.yaml. On the live host (A3) PM has its runtime, so the planned
   `hermes plugins install https://github.com/kvnloo/z0intelligence/tree/<sha>/harness-adapters/hermes-z0intelligence
   --ref <sha> --force` path applies (INFERRED, not run here).
5. Hermes venv: `uv sync --locked --offline` failed (kittentts direct-URL wheel not cached). The venv is a fresh
   python3.14 venv whose .pth puts the git-archive export first and a private reflink copy of the lane setup venv's
   third-party site-packages (/mnt/zer0models/hermes-wt/bend-integration-venv, same uv.lock) second. My first probe
   pointed the .pth at that setup venv directly and Python wrote 532 .pyc files into it; I deleted exactly those
   files (listed in homes/C4a-hermes-capture/setup-venv-pyc-written.txt; zero remain newer than the marker) and
   switched to the private copy. No source file there was changed.
6. P-7 in the e2e: for the Hermes CLI with the local backend, Hermes itself sets the task cwd (TERMINAL_CWD) to the
   launch directory, so process cwd and task cwd coincide; the e2e asserts the projection is on Hermes's task cwd.
   The separation (resolver value vs os.getcwd()) is proven by the unit test.
7. Session-end gap rows record asked_user / tool_calls / assistant_messages as null (unknown) instead of the old
   plugin's `asked_user: false`; the capture core then writes partial_measurement for them.
8. A hook call after close() is ignored (not counted); calls between the start of close() and its return are counted
   as `closed` drops. [Round 1 also allowed a projection that started before close() to write its row afterwards;
   the verifier rejected that (P-2), and the fix round removed it: see "Fix round" below.]
9. Cohort detection for cluster services is INFERRED: the word `cluster` in the platform or in
   HERMES_SESSION_SOURCE; kanban workers also by HERMES_KANBAN_TASK. The live cluster plugin's markers were not read.
10. Duplication kept small but present: the plugin cannot import z0int (it runs in Hermes's interpreter), so it has
   its own bounded queue/close (same contract as C1's Spool) and writes drop/failure rows in the C1 format itself.
11. Two timing tests are flaky on this host (load ~4-5.5) on base and head alike: the C1 hook-latency test
   (base 3/10, head 1/10) and test_agentweb_bridge_overload (base 1/10, head 2/10). Each full head run had one of
   them as its single extra failure (a different one each run); the deterministic failure set equals the base's.
   Details in suite.txt.

## Activation (NOT performed; owner approval required; G0 gate)
See the structured result `activation_notes`.

## Isolation record (reported honestly)
- All tests, suites and e2e ran under `flock -s quiet-lane.lock` with isolated HOME/Z0INT_HOME/HERMES_HOME under
  homes/C4a-hermes-capture/; the socket guard logged 0 violations; the real-Hermes e2e ran in hostless + bwrap with
  the live Hermes home and ~/.z0int masked by tmpfs (both empty inside) and a private net namespace.
- Metadata reads outside the homes: the e2e compares `stat` metadata of /workspace/hermes-home/{config.yaml,plugins}
  and ~/.z0int/{state/hermes,config} before/after (unchanged). While diagnosing a changed directory mtime I ran
  `stat` twice on /workspace/hermes-home (its mtime changes every ~20 s from the live services) and one
  `ls -lat /workspace/hermes-home | head -5` (directory metadata listing; nothing was opened or written).
  No file content under the live Hermes home or ~/.z0int was read; nothing there was written.
- Not live, but outside my homes: 532 .pyc files written into /mnt/zer0models/hermes-wt/bend-integration-venv by
  my first venv probe, deleted again (list in homes/C4a-hermes-capture/setup-venv-pyc-written.txt); the deps were
  then copied into homes/C4a-hermes-capture/hermes-deps.
- No push, no stash, no live config/service/systemd change, no DSH wrapper, no paid or model calls (stub only).
  My two unpushed commits were re-split once with `git reset --soft` (the directory removal had landed in the test
  commit); nothing else in history was touched. No stub, child or namespace process is left running.

## Fix round (verifier REVISE: 2 major, 4 minor), 2026-10-03

Commits: 975d759 test (red), ee7cae0 fix. Evidence: red-fix-round.txt, green.txt (round 1 kept as
green-round1-07a2272.txt), green-fix-stress.txt, p2-probe.txt, suite.txt (+ suite-{head,base}-fix.raw.txt; round 1 in
suite-round1-07a2272.txt), e2e.txt / e2e-run.log / e2e-check.json (round 1 kept with the -round1-07a2272 suffix),
ACTIVATE.md.

- MAJOR P-2, a projection wrote after close(): fixed. Every Capture owns a gate file
  `$Z0INT_HOME/runtime/hermes-gates/<uuid>.gate`, whose path rides on the opportunity job. The project child appends its
  row (and session-state update) only while it holds the gate shared and the gate is still linked, then appends one
  settle line. The batch child acks `p` for each projection it starts, so the plugin knows how many it must
  account for. close() waits for them within its 2 s budget, takes the gate exclusively (non-blocking, bounded),
  counts the unsettled ones as `opportunity_record`/`closed` drops and unlinks it. A projection still running then
  finds no gate and writes nothing. Red: test_p2_a_projection_in_flight_at_close_writes_nothing_after_close (real
  projection children, close budget 0.05 s, state/hermes snapshot identical after every child exited,
  built + closed == turns). The verifier's probe now gives 3 rows at close and 3 after 8 s. In the real-Hermes e2e
  every `chat -q` still records its opportunity (close waits for it; 0 closed drops; +89.5 ms median exit).
  Capture-core change: `record_built_opportunity` (write half of record_opportunity; callers unchanged).
- MAJOR retired capture path in z0int: `hermes_decisions.on_opportunity` removed. `z0int hermes opportunity` /
  `python -m z0int.hermes_decisions opportunity` reads and discards stdin and writes one content-free
  `retired_vehicle` failure row (respecting the capture kill switch), never an opportunity. Red:
  test_the_retired_opportunity_command_writes_no_opportunity (both entry points, no opportunities.jsonl, no canary
  anywhere, attribute gone). The regression guard test_opportunity_record_for_repo_and_non_repo was retargeted to
  the one capture path (plugin -> batch -> project), assertions kept.
- minor P-1 child guard: tests/fixtures/socket_guard_sitecustomize.py now refuses and logs every connect/connect_ex
  (any family, loopback on any port, AF_UNIX). Red: test_p1_the_child_socket_guard_logs_every_connect_including_
  loopback_and_unix. The P-1 test's children still log nothing.
- minor automatic path: invoke() runs the resolved capture interpreter (z0int_python / Z0INT_PYTHON /
  hermes.json) with Z0INT_HOME = the home whose automatic.json gated it; the parents[2]/src PYTHONPATH injection is
  gone (no interpreter: fail open, nothing spawned). Red: test_automatic_invoke_runs_the_configured_z0int_with_the_
  same_home_as_its_gate.
- minor install path: the README and ACTIVATE.md state the GitHub-URL install is unverified and give the
  git-archive fallback; the README notes a local path is parsed as GitHub shorthand.
- minor live-path reads in the e2e: the SNAP `stat` of /workspace/hermes-home and ~/.z0int and the `readlink -f`
  of live paths are removed; masks are fixed paths and their emptiness is checked inside the sandbox. This round
  read no live path at all.
- Test-only adjustments that came with the fix, intent unchanged: P-7 and P-9 now build their collected jobs
  before close() (after close a build correctly writes nothing). The automatic-on test's invoke stub takes the new
  (python, home) arguments.
