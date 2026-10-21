# Worker supervision statechart (loop 2)

The second loop of the "statechart, then verify" layer. Loop 1
([README](README.md)) found 13 workers that were interrupted or vanished and
then sat with no resume, PR or abandon decision past the 2 h limit, and the
owner saw builds pile up on memory-starved groot instead of running on the
MBP and phones. This loop supervises the workers themselves.

![worker supervision statechart](worker-supervision.svg)

Source: [`worker-supervision.mmd`](worker-supervision.mmd), generated from
`src/z0int/fsm/worker_supervision.py` (`python -m z0int.fsm.supervise --mermaid
docs/fsm/worker-supervision.mmd`); a test fails if it drifts.

```
Dispatched -> Running (heartbeats) -> Completed | Interrupted | Stalled
           -> Resumed | Abandoned | Integrated        (+ Overdue: interrupted past the limit, needs Kevin)
resources: Lease (one owner per worktree/branch; also the phone install lock), the S25 Device
```

## Laws (all proven in Bend: `bend/worker_supervision_fsm`, receipt `c1de0af0…` PASS)

| | law | Bend claims |
| --- | --- | --- |
| W1 | one owner per worktree or branch at a time (lease): it changes hands only through Free or a Reclaim from a dead holder; only the holder writes | `lease_no_steal`, `lease_release_by_holder`, `lease_free_acquires`, `write_needs_lease` |
| W2 | an Interrupted worker is resumed from its log or explicitly abandoned within the limit; past it, it is Overdue (needs-Kevin), never silently dropped; a silent worker stalls, then is declared interrupted | `interrupted_can_resume`, `interrupted_can_abandon`, `interrupted_overdue`, `overdue_needs_kevin`, `interrupted_exits`, `overdue_exits`, `running_stalls`, `stalled_interrupts` |
| W3 | background processes of a dead worker are reaped or adopted (no Abandoned/Integrated with one still running; Resume hands them to the successor) | `end_needs_reaped`, `interrupted_can_resume` |
| W4 | heavy commands run on a fleet node, not groot, unless a GPU or toolchain (Bend/Lean) exception is declared | `running_is_placed`, `stalled_is_placed` (reachable invariant) |
| W5 | installs to the owner's phone are serialized by a lock | `install_needs_lock`, `install_lock_no_steal` |
| W6 | no input to the S25 while another app is in the foreground; every input needs a fresh look | `input_needs_foreground`, `input_needs_fresh_look` |

17/17 law-breaking mutants rejected (kernel mode); Python twin equal to Bend
on 400 worker + 100 lease + 100 device tapes. See
[`bend/worker_supervision_fsm/README.md`](../../bend/worker_supervision_fsm/README.md).

## `supervise`: the read-only checker

```bash
PYTHONPATH=src python -m z0int.fsm.supervise                       # state + violations; exit 1 if any
PYTHONPATH=src python -m z0int.fsm.supervise --json report.json --now 2026-10-21T05:31:00Z
PYTHONPATH=src python -m z0int.fsm.supervise --worktree /mnt/zer0models/hermes-wt/fix-f1 --heavy
#   dispatch: HOLD (exit 3) if the worktree's lease is held by a live/interrupted worker,
#   or the new work is heavy and no --exception gpu|toolchain is declared (use fleet-build)
```

Hermes runs it before dispatching new workers. It opens `state.db` with
`mode=ro`, runs only `git worktree list/status/rev-parse/for-each-ref/merge-base`,
and never resumes, kills, prunes or deletes. `--now` makes the output
deterministic for a fixed snapshot. Kevin answers the needs-Kevin batch by
appending lines like `{"worker": "deleg_x/task-0", "action": "abandon", "by": "kevin"}`
to `workspace/zer0/data/operations/fsm/worker-decisions.jsonl`; the next run
replays them as Abandon events (a refused Abandon, e.g. with live processes,
is reported).

| source | mapped to |
| --- | --- |
| delegation log `started:`, last write, `end status=` | Start(groot), Beat(last write or last session activity), Finish / Interrupt; no final line: Tick(hb+lim) -> Stalled, Tick(hb+2 lim) -> Interrupted |
| later task naming the delegation id, branch, worktree or a RESUME task with >=35% of the goal words, started after the old one's last write | Resume(by, from_log = it names or reads the old log) |
| state.db `async_delegations` | the delegate_task lists (dispatch, per-task status) |
| state.db subagent sessions (title prefix + start time) | the worker's tool calls: writes, background processes, commands |
| terminal `background=true` / process_manage exits, kills, handoffs / "Child left N background process(es) ... TERMINATED" | Spawn / Reap (adopt on handoff) |
| `processes.json` entry alive after its owner session ended | orphan (W3 violation) unless `persist_on_release` |
| patch/write_file paths and git write commands in a known worktree | lease use; a write by another session inside a holder's lease window (subagent: first write to session end; interactive: 30 min bursts) breaks W1 |
| terminal command under `heavy.lock` or a build/test command, not via `fleet-build`/`fleet run`/ssh | a heavy job started on groot: W4 refuses it unless GPU work or Bend/Lean (standing exceptions) or `Z0_HEAVY_EXCEPTION=gpu|toolchain` |
| `adb ... install` / `input tap|text|...` to the S25 (its adb address from the fleet registry or `Z0_FSM_S25_MATCH`) | Install (needs `flock ...install*.lock`) / Input (needs a `dumpsys window|activity` foreground check in the same call or the previous 60 s) |
| `~/.local/state/fleet/runs/*.log` | fleet jobs (node, start from the name, end = mtime, exit) |

## What it found (2026-10-21 05:31 UTC snapshot)

468 delegation tasks, ~120,300 tool calls, 1,807 worktrees in 287 repos, 36 fleet runs.

**W2: 26 workers Overdue**, all 13 of loop 1's dropped workers among them.
The other 13 are mostly workers whose "successor" never read their log
(`resume_without_log`, 10) and the E2E tester army `deleg_9c151b34`.

The 13 from loop 1, with the recommended action (needs Kevin; nothing was done):

| worker | goal | recommended | evidence / leftover worktree, branch |
| --- | --- | --- | --- |
| deleg_14619e78/task-0 | z0live QR-code pairing + CI APK | **already integrated** | its commit `ca90e307` "scan pairing QR" is on `origin/feat/z0live-realtime` (lawnchair); `/mnt/zer0models/lawnchair-z0live` clean of its edits |
| deleg_190e1e71/task-0 | ff-tracking/ff-unmute on Vulkan + CPU fallback | **resume** | `/mnt/zer0models/workspace/upstream/ff-tracking` [`linux-vulkan-cpu-fallback`]: its uncommitted edit; ff-unmute on the same branch |
| deleg_64373d51/task-0 | privacy/sensitivity tier classifier | **already integrated** | `/mnt/zer0models/sft-svlm` [main] on origin/main, no own edits left |
| deleg_651f05ed/task-0 | review portal P1 | **resume** | `quackles-work/wt-review-portal` [`feat/review-portal`, local only]: 2 own files uncommitted |
| deleg_65371b53/task-0 | ACE architecture v2 | **resume** (push first) | `/workspace/wt/ace-campus-arch2` [`ace/lane-architecture-v2`]: commits on no remote |
| deleg_656fb990/task-0 | Tern FIX F1 dock/approval | **resume** | `/mnt/zer0models/hermes-wt/fix-f1` [`fix/tern-army-f1-dock-prompts`, local only]: 7 own files uncommitted |
| deleg_656fb990/task-1 | Tern FIX F2 slash commands | **resume** | `hermes-wt/fix-f2` [`fix/tern-army-f2-slash`, local only]: 9 own files uncommitted |
| deleg_656fb990/task-2 | Tern FIX F3 keyboard/plan | **resume** | `hermes-wt/fix-f3` [`fix/tern-army-f3-keyboard-plan`, local only]: 7 own files uncommitted |
| deleg_656fb990/task-3 | RESUME TESTER G02 | **resume** (or abandon if its findings are stale) | `hermes-wt/army-r02` detached at a local-only commit; tester output lives in project-artifacts |
| deleg_656fb990/task-4 | RESUME TESTER G04 | **resume** (or abandon if stale) | `hermes-wt/army-r04` detached at a local-only commit |
| deleg_d208392a/task-0 | OMARemote + Gliff + scrcpy hotkey windows | **resume** | `/workspace/.files-remote-workspaces` [`feat/remote-workspaces`]: 1 own file uncommitted |
| deleg_d3c91202/task-0 | event-driven phone supervisor | **resume** | `/workspace/.files-k3s` [`feat/k3s-cluster`]: 5 own files uncommitted (shared checkout: other sessions' edits are excluded); `scratch/omalauncher-fork` [`feat/keep-linux-running`]: 2 |
| deleg_e07e87da/task-1 | night lane stone-25 | **abandon** | artifact-only work, no worktree/branch/commit; re-dispatch only if still wanted |

**W1: 419 writes inside another session's lease** in 19 worktrees;
`products/garden_room` alone has 242 (parallel subagents editing one
checkout), then `hermes-wt/ui-tsp-capture` 64 and `products/fframestudio` 38.
Two branches are checked out in two clones of one remote (factory verify
clones; candidate).

**W3:** 1,867 background processes spawned, 1,367 seen exiting/killed/handed
off; 21 "Child left N background process(es)" notices (26 processes, all
TERMINATED by Hermes: reaped, but their result never reached the parent);
**1 orphan now**: `ripple-review` (pid 7316) outlived its owner session
`20261020_105057_66a5a2` without `persist_on_release`.

**W4, groot vs fleet** (the owner's complaint, measured):

| | groot (heavy.lock) | fleet |
| --- | --- | --- |
| jobs | 333 under heavy.lock (2026-09-21 .. now); 3,005 heavy commands in all | 36 (mbp 28, omarchy-phone 5, galaxy-s22plus 3) since 2026-10-20 22:08 UTC |
| waited | **118 jobs waited, 21.5 h total, median 3.9 min, max 74 min** (lower-bound estimate) | 0 by construction (admitted jobs start a capped scope); 6 `fleet-build` busy retries seen |
| ran | 8.2 h of locked run time | 34 min |
| since the fleet came up | 63 heavy.lock jobs, 20 waited (1.6 h); 97 heavy commands still ran on groot outside fleet-build | |
| law 4 | **1,695 violations** (heavy on groot, not GPU, not Bend/Lean, not via fleet-build); 1,291 excepted as GPU work, 19 as Bend/Lean | |

Agents' own `lslocks` snapshots saw up to 10 processes queued on heavy.lock at once.

**W5:** 17 adb installs to the S25, 7 outside the install lock. **W6:** 23
inputs, 11 with no foreground check in the same call or the previous 60 s.
`phone_exec`/`phone_goal` calls (83) are harness-mediated and not judged.

Limits: the event mapping is heuristic and only unit-tested; heartbeats are
the log's last write or the session's last activity, so a long silent tool
call looks like a stall; wait times assume each command held heavy.lock until
it ended (an overestimate when work follows the flock section); writes are
attributed by path, so a write through a symlinked path or a script is
missed; "own files" are paths the worker itself patched or wrote.

## Next

- Hermes calls `supervise --worktree P [--heavy]` before every dispatch and
  refuses on HOLD; `fleet-build` stays the default route for builds.
- Stamp leases at dispatch (`git worktree add` + a lease file the next
  dispatcher can read) so W1 is enforced, not just replayed.
- Loop 3 (proposed in loop 1 as the GUI action loop) consumes W6's Device model.
