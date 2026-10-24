# State laws A–E

`z0-guard state --config operations/state-laws/live-groot.json` observes live state and emits `z0.state_guard.v1`. `--input tests/fixtures/state-laws.json` is finite incident replay, explicitly not live evidence. Exit codes:0 all pass,3 any violation,4 unknown without a violation. `blocked:true` means the checked positive claim cannot be promoted; the command remains report-only. It does not execute actions, delete files, restart services, write ledgers, install timers, or activate enforcement. Unknown escalates to Kevin. Blocking integration requires Kevin to review the live list and approve it separately.

| Law | Finite contract | Live sensor |
|---|---|---|
| A observed beats claimed | available,current,scope_matches,agrees | stable file hash or twice-read systemd InvocationID/MainPID plus kernel PID/starttime/boot identity; exact host |
| B one live manifest | available,disk_matches,process_matches,canonical | release pointer/DEPLOYED_SHA, resolved installed entrypoints, systemd wiring and live runtime paths; absent loaded-code digest stays unknown |
| C revocation removes everywhere | available,epoch_current,no_active_hits,coverage_complete | stable owner-ledger epoch and explicit admitted-store file inventory; exact revoked phone rule IDs; inaccessible/unscanned stores cannot pass |
| D success needs typed evidence | available,typed_receipt,output_verified,work_positive,transport_ok,target_current | stable `z0.state_outcome.v1` receipt, nonempty output artifact with matching SHA256, positive attempted/valid/transport counts and nonretired target |
| E constants cite measurements | available,measured,scope_matches,agrees | actual Python constant assignment, dated `z0.measurement.v1` command receipt, same scope/units/source hash and measured value |

All required fields are strict booleans in the finite model. Missing/nonboolean evidence has precedence over known false predicates: unknown is never pass. Sensor bindings are event-based, not arbitrary time-to-live. File replacement/mutation, unit invocation change, PID reuse, reboot and owner epoch changes invalidate positive claims. Read-only observation is not target-side compare-and-set and does not authorize later effects.

C is a revocation audit, not another phone admission controller: O10 retains admission; O11–O13 retain policy/option-capacity gates. AODL I0–I4 remain the chat-action authority and done gate. Law B verifies the OneLiveCopy release; it neither performs the cutover nor kills old interactive residents. A compatible multi-component release can use deliberately distinct components; uncontrolled feature-worktree and unmanaged entrypoints remain violations. An installed path alone never proves already-imported code was reloaded.

## Historical evidence and scope

Revoked phone policy mentions remain findings unless explicitly enclosed by standalone `historical:`…`end-historical` or `platform-observation:`…`end-platform-observation` lines. These blocks preserve historical evidence and actual Android platform constraints without reviving them as agent policy. In mixed fleet YAML, only `kind: phone` nodes are scanned for phone limits. The below2percent/not-charging owner condition is not the revoked50percent rule. Actual ENOSPC is not an agent-chosen disk floor. Review findings rather than mechanically rewriting source.

The included live inventory deliberately declares `inventory_complete:false`: the shared store inventory includes archives and unmerged branches not exhaustively scanned here. Consequently no global 'revocation complete everywhere' claim can pass. Add admitted stores explicitly or retain the human escalation; never silently call a partial scan complete. Directory symlink omissions are reported unknown. Scan reports contain rule IDs, paths, line numbers and hashes, not source text or process environments.

## Evidence schemas

A D receipt contains `schema:z0.state_outcome.v1`, `outcome:success`, `output_path`, `output_sha256`, positive integer `attempted`, `valid_decisions`, `transport_ok`, `exit_code:0`, and `target_retired:false`. It establishes that narrow output/count contract, not arbitrary output semantics. No-work is a legitimate non-success state; do not restart idle workers.

An E receipt contains `schema:z0.measurement.v1`, timezone-bearing `measured_at`, observing `command`, `kind:measurement` (not guess), numeric `value`, `source_sha256`, `scope`, and `units`. Inventory declares source `path`, constant `name`, claimed `value`, scope/units and measurement path. Values from different boundaries cannot be compared without an explicit measured conversion. Missing measurement is unknown, not a fabricated tolerance or invented throttle.

## Verification and install

Run pytest and Bend only on the MBP queued lane using `fleet-build --node mbp --wait -1`; never on groot. Run `bend/state_fsm/PROOF.bend --verdict`, its mutation runner and parity runner. Fixture table is in `tests/fixtures/state-laws.json` (all nine seeds plus legitimate exceptions).

After green verification, ServiceWatch owns installation through the content-stamped `.files/scripts/install-z0-guard.sh` with `Z0INT_BACKEND_REPO` set to this committed checkout. Do not use `--activate`, change state blocking modes, or independently switch the live guard link. Install archives the exact committed backend into `~/.local/lib/z0-guard/<content-stamp>/src` and records backend-head. Keep this command report-only until Kevin sees the live list and approves blocking. The machine-specific sensor inventory is passed explicitly; it is not automatically enabled as a timer.

## Implementation and observed operational result —2026-10-24

Draft PR: https://github.com/kvnloo/z0intelligence/pull/220, stacked on #212. Source commit `fb496fc` implements the sensors and regressions. The Draft is not deployment approval. PR registration in T3 failed because this MCP client is not in a T3 thread; the GitHub Draft exists.

### Seed-incident fixture table

These are executable finite incident replays, not retrospective proof that an unmediated historical action was blocked. `blocked:yes` means the unsupported claim is refused by the gate; no service is stopped or file deleted.

| Incident | Failure | Law | Blocked |
|---|---|---|---|
|1|Revoked phone caps survive/reappear|A,C|yes|
|2|Exit0 or transport without verified output called success|D|yes|
|3|Timer/service/extension split across incompatible trees|B|yes|
|4|Live hook resolves a feature worktree|B|yes|
|5|Guessed interval or rate claim contradicts measurements|A,E|yes|
|6|S22 evidence widened to S25|A|yes|
|7|Missing harness/store inventory called complete|C|yes|
|8|Stopped handoff contradicts running service|A|yes|
|9|Unmerged/harness-local codification called complete|B,D|yes|
|Exception|Explicit historical/platform observation; compatible component manifest; current matching observation; verified output|A,B,C,D|no|
|Exception|No-work promoted to success|D|yes (not a worker failure)|
|Exception|Measurement unavailable|E|yes, unknown escalates to Kevin|

Fixture: `tests/fixtures/state-laws.json`,20 rows covering all9 seeds and positive exceptions. Sensor tests additionally execute real file parsing, mocked invocation/PID bindings, source/hash changes, pointer swaps, mixed host/phone YAML, historical blocks and native tick outcomes.

### Live read-only groot audit

Command: `PYTHONPATH=src python3 -m z0int.fsm.guard state --config operations/state-laws/live-groot.json`, executed from this checkout. This is a real live-system read-only run of the candidate command, not the `--input` replay. Captured at **2026-10-24T23:30:15.710657+00:00**. Exit3; **1 pass,39 violations,9 unknowns**. Evidence: `/mnt/zer0models/workspace/zer0/data/operations/state-laws/live-fb496fc-20261024.json`. Every row carries typed status and binding evidence where observable. It made no changes. Counts can change after this observation; they do not assert future state.

| Law | Violation(s) observed |
|---|---|
|B|Release generation: pointer/content-stamp/cutover tuple did not establish completed cutover.|
|B|`z0int-loop-tick.service` and `z0intelligence.service` still reference worktree source.|
|B|Installed `local-cognition` and `z0int-bridge` extension paths resolve under `z0-wt/shadow-learning-loop`.|
|B|`/workspace/boat-fleet/scripts/fleet-avrea` and `fleet-avrea-budget` lack the declared release binding; this stale checkout is not the newly approved Avrea release.|
|B|Live worktree-linked PIDs:6198,6200,3310698,3741239,3899008,3899520,3899541. PID/starttime/boot identities are in the receipt. Interactive resident3310698 is intentionally left running; no kill/restart is requested or performed by this guard.|
|C|`/mnt/zer0models/worktrees/files-live/config/fleet/nodes.yaml`:116,142 retain phone `min_disk_free_gb:5`. This is the exact condition PhoneTestLane reported rejecting a job with1.9GiB free.|
|C|`/workspace/boat-fleet/config/fleet/nodes.yaml`: memory restrictions at99,100,109,114,115,116,137,138,144,149,150,151; CPU restrictions at99,100,117,137,138,152; disk floors at110,145; thermal caps at111,146.|
|D|Current native `z0int.loop.tick_summary.v0` is `degraded`, exit3, not verified success. The observed receipt reports68 shadow decisions and17 verified rows, but4501 errors in4608 transport attempts; no exit-code normalization can promote it.|

| Law | Unknown(s), requiring Kevin escalation rather than pass |
|---|---|
|B|Loaded source digest unavailable for PIDs5509,5810,5894,5961,3310946,3899015. A resolved disk path cannot establish loaded code.|
|B|PID2967187 environment unreadable (permission denied); no root escalation or inferred pass.|
|C|Global revocation coverage incomplete:4107 declared text files scanned, one missing legacy `.files` path and unscanned linked skill roots are listed in the JSON receipt. Archives and every unmerged branch are explicitly not claimed exhaustively covered; `inventory_complete:false`.|
|E|`DISPATCH_INTERVAL` currently declares5 seconds at the per-device boundary, but no same-scope dated measurement receipt exists at the declared path. The old21-sends/85s global observation cannot be substituted as a per-device measurement.|

Law A's live S25 queue claim passed: its twice-observed scoped systemd state is active/running with current invocation/process identity. This does not authorize phone actions.

The installed shared hooks have separately moved to ServiceWatch's content-stamped `files-hooks` release, according to its owner; this audit does not turn that peer report into an independently observed B pass. OneLiveCopy owns actual source cutover. PhoneTestLane owns removal of the live disk floor. This task only observes and reports.
