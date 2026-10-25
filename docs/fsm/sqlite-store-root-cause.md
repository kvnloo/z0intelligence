# Recurring OMP agent.db corruption: root cause and lifecycle guard

## Result and evidence boundary

The recurrence has a concrete unsafe lifecycle mechanism: **the main SQLite
file was replaced while another OMP process still held the old DB/WAL/SHM
inodes**. A database filename is not its lock identity. The old process and
new process can own different main-file inodes but address the same sidecar
names. Repeating recovery creates more split generations instead of fixing
the lifecycle bug. The shared store also contains credentials, so recreating
it as empty is an authentication outage, not just a memory-cache reset.

This report distinguishes direct observations, parent-provided historical
evidence, and causal inference. No OMP process was killed, restarted, launched
or signalled for this task. No checkpoint, repair, live SQL connection,
credential restore or sidecar deletion was performed. The new guard is an
explicit read-only command, not an installed OMP recovery hook.

**Important change since the parent snapshot:** the first direct observation
at 2026-10-25 07:40:37 UTC found PID 1826151 already absent. No current ghost
was found among readable FDs. Therefore this task cannot honestly claim a
live guard hit on that PID. It does reproduce ghost detection with real
unlinked FDs on disposable test stores, without touching OMP.

## What happened

| Time (CDT unless UTC stated) | Evidence | Interpretation |
| --- | --- | --- |
| Oct 20, 22:16:10 | Earlier `REPORT.md:19` cites `~/.omp/logs/omp.2026-10-20.1737028.log:4` | Earliest failure in the earlier retained-log census, not the event that caused corruption. That exact file is absent from this task's current log directory. |
| Oct 24, 17:57:43–48 | `REPAIR-LOG.json:11-16`: holders 19454/3106326; integrity `ok`; auth 9→9; swap; relaunch | A recovered DB passed the immediate integrity test. This does not establish future safe ownership. |
| Oct 24, 19:22:05.715 | `~/.omp/logs/omp.2026-10-24.649783.log:18`: `Memory startup failed`, `SQLiteError: database disk image is malformed` | Corruption recurred after the repair. |
| Oct 24, 20:58:59.867 | `~/.omp/logs/omp.2026-10-24.1826151.log:15`: same malformed error | The later ghost process had already seen a corrupt store before the 23:36 recreation. |
| Oct 24, 23:36:13.570 | `~/.omp/logs/omp.2026-10-24.512200.log:1`: `SQLite database corrupt; preserved damaged store before recreating it` | PID 512200 quarantined and recreated the shared store; the log explicitly warns credentials may require re-login. |
| Parent snapshot, before this task | Parent directly observed PID 1826151 with `agent.db (deleted)` inode 302843056, plus deleted WAL/SHM FDs, while the path pointed to inode 304170503 | Historical split-generation/ghost-holder evidence supplied to this task; no longer re-observable here. Parent identified Tern pane 50470160695297 and a 20:58 CDT start. |
| Parent auth repair | Parent reported 9 auth rows restored from the corrupt backup after the recreated store had lost all 9, preserving `agent.db.pre-auth-restore-20261025T070931Z` | Historical auth-loss/restore evidence; this task did not repeat the restore. |
| Oct 25, 07:40:37 UTC | Direct `/proc` census: PID 1826151 absent; current main inode 304170503, WAL inode 302843056, SHM inode 305004431, device 66305; readable holder PID 2266022 | Current generations differ from the earlier ghost snapshot. Inode numbers can be reused: today's WAL inode matching yesterday's old DB inode is not evidence they are the same object. |

The 23:36 quarantine backup is
`~/.omp/agent/agent.db.corrupt-1792902973567-51760d99-30c3-4b86-9b19-0dd88aef1131`.
`findmnt -T ~/.omp/agent/agent.db` directly reports local XFS on
`/dev/nvme0n1p1`; a network filesystem is not the observed explanation.

A fresh scan of 444 retained log-directory files found 38 relevant events
(including the recreation event); its earliest currently readable memory
failure is Oct 21 03:03:39.645 CDT in
`omp.2026-10-21.2857531.log:5`. Paths, line numbers and count-safe extracted
messages are preserved in `sqlite-log-evidence.json`. This is a different
retention snapshot from the earlier report's 260 logs / 29 startup failures.
**The original Oct 20 trigger remains undetermined.** Neither its writer nor
an original unlink/recovery syscall trace is available; no hardware, SQLite
version bug, or particular Oct 20 agent action is established.

## Why this can corrupt a store repeatedly

1. Unix permits unlink/rename while an existing descriptor remains open.
   Existing processes keep the old inode; new openers get the new inode.
2. SQLite locking is on the file object/inode, but WAL/journal names derive
   from the database pathname. Locks on the old inode cannot exclude holders
   of the new one. The companion SHM/WAL generation can therefore mismatch.
3. SQLite's `sqlite3WalClose()` tries an EXCLUSIVE lock on its `pDbFd`. If it
   is the last holder of the *old* inode, that lock can succeed despite active
   holders on the new inode. It is not guaranteed to succeed if other old
   holders remain. After a successful checkpoint and absent PERSIST_WAL, the
   close path calls `sqlite3OsDelete(pWal->pVfs, pWal->zWalName, 0)` by name.
   That can delete the new store's live WAL, losing committed frames. The
   SHM close/unmap path is also given its deletion flag.
4. A later OMP startup sees malformed state and invokes quarantine/recreate,
   again replacing the path underneath remaining holders. A repair that
   checks only `fuser agent.db` can miss deleted or renamed old generations;
   a check immediately before `mv` also has a reopen race without a common
   lifecycle lock. The loop can repeat even when each recovered candidate
   initially passes `integrity_check`.

SQLite explicitly documents this in
[How To Corrupt §2.5](https://www.sqlite.org/howtocorrupt.html#_unlinking_or_renaming_a_database_file_while_in_use):
“the two different database files will share the same rollback journal or WAL
file” and recovery can use the other database's content. The captured
`wal.c.source.txt` contains the exact `sqlite3WalClose` sequence above.
Source URL: <https://raw.githubusercontent.com/sqlite/sqlite/master/src/wal.c>.
This fetched master source demonstrates the mechanism; it is **not a proven
version mapping** to Bun's embedded SQLite. No destructive experiment or live
syscall trace was run to attribute a particular WAL unlink to PID 1826151.
The documented mechanism plus historical ownership violation is strong
recurrence evidence, not proof of every individual corruption event.

## Installed OMP recovery: confirmed code, not a guessed version

The installed binary symlink resolves to
`/home/kvn/.local/share/tern/omp/versions/18.12.6` (256702432 bytes).
SHA-256: `85d9daf3a05fc76e8cd425ae1904eb576af8d21cc85fcf4690742626e07ccfa4`.
Its embedded JS was extracted without executing OMP into
`omp-recovery-installed-excerpt.js.txt`, byte range 147863923..147868998.

- `Zr = ["-wal", "-shm", "-journal", ""]` defines the quarantine set.
- `ni(e,t)` copies those files to a unique corrupt backup, closes its local
  handle `t?.close()`, then `unlinkSync`s each original pathname.
- `ln(...)` already uses `hAe(`${e}.recovery`, ...)`, a cross-process
  recovery lock, and rechecks `dev:ino:birthtimeMs` before quarantine.
- That lock serializes recovery contenders; it is **not a shared lifetime
  lock held by all ordinary DB connections**. The shown normal `rn`/`nn`
  open paths do not acquire that lifecycle lock or scan all process FDs.
- The embedded `je` handle registry is a process-local `Map`, not a census
  of other processes. The quarantine function does not carry auth rows into
  the new store; it logs “Stored credentials ... may require re-login.”
- `Mv`/`Eoe` reopen/init after quarantine. This explains why an empty,
  structurally valid store can break model restore/auth despite no current
  malformed-image error.

The earlier repair script's `repair-agentdb.sh:30-40,66-69` uses `fuser` on
only the current pathname, checks it, then directly removes WAL/SHM and
renames the candidate over the DB. It did stop its observed holders first;
we do **not** claim its particular swap was caught racing an opener. Its
protocol nevertheless lacks shared exclusion and a deleted-inode census.
Do not rerun it unchanged as a general safe repair procedure.

## Dash multiplies lifecycle opportunities, not a proven first cause

Inspected source: `/mnt/zer0models/dash-acp/bridge/src/acp/`.
`supervisor.ts:11` selects `[omp, "acp"]`; `:30-36` creates a fresh
`AcpClient` for a turn and closes it in `finally`. `client.ts:32-37` spawns
the process; `:145-147` makes the connection single-use.
`client.ts:200-210` ends stdin, sends SIGTERM, waits for exit or 500 ms, then
SIGKILLs the process group. The SIGKILL is issued even if the leader exited,
to clean surviving children. The supervisor limits its own concurrent turn
to one; it does not exclude Tern's other OMP instances.

This churn creates more opens/startup recovery attempts and graceful-close
opportunities against a shared store. SIGTERM can run the dangerous
close-time cleanup of a *ghost* connection. SIGKILL itself bypasses
user-space SQLite close cleanup; an ordinary abrupt death is something
SQLite is designed to recover from and is **not by itself proof of corruption**.
No Dash change was made, and source inspection alone does not establish that
this exact checkout is the deployed bridge or caused the Oct 20 incident.

## Safe remediation plan — not executed; requires fresh owner approval

The user explicitly forbade stopping/restarting OMP in this task. The earlier
ghost is already gone, so do not act on that old PID. If a fresh census finds
ghosts again, an owner-approved coordinated cleanup should be:

1. Prevent new OMP openers/recovery attempts with a lifecycle maintenance
   barrier that all launchers honor. Preserve count-only evidence and
   private, consistent backups; identify the sessions to restore.
2. On the **current live DB generation**, run `PRAGMA wal_checkpoint(TRUNCATE)`
   and verify a successful result with no busy readers/writers and no
   outstanding frames. Stop on BUSY, corruption or uncertain inode identity;
   do not manually unlink sidecars or install a new DB under holders. A
   checkpoint changes live state and is intentionally outside this audit.
3. Reobserve each ghost PID's start identity and FDs immediately before
   acting (prefer a pidfd to avoid PID reuse), then **SIGKILL confirmed ghost
   holders**, not SIGTERM/graceful exit. This avoids their user-space
   `sqlite3_close`/`sqlite3WalClose` exit path unlinking current sidecars.
   Never signal a stale PID from a report or indiscriminately kill current
   legitimate holders. If safe checkpoint/quiescence cannot be established,
   stop and plan fully offline recovery instead.
4. Confirm the ghost set is empty and the live inode is unchanged. Check
   integrity and auth preservation against the prior count receipt. Only
   then relaunch the affected session with `omp --restore <session>`.
5. If actual file replacement is still necessary, **every** DB/WAL/SHM holder
   must be gone, all openers excluded, auth carried over and verified before
   atomic installation. No manual sidecar removal by stale-inode holders.
   A “good” pre-swap integrity check alone is insufficient.

## Upstream fix recommendation

Keep the existing recovery-contender lock, but strengthen it to a genuine
cross-process **store lifecycle protocol**: ordinary connections hold shared
leases for their lifetime; recovery needs an exclusive lease. Refuse
replacement while another process has any current/ghost/sidecar FD or the
census is unavailable, and hold exclusion through identity recheck, backup,
close, quarantine, recreation, fsync and verified reopen. Path-identity
checks and a per-process handle map alone are insufficient.

Recovery must preserve the credential set (including per-provider identity,
not just its cardinality) from the damaged-store backup into a private
candidate. Abort recovery rather than silently publishing a store with fewer
`auth_credentials` rows or an unknown baseline. Count non-decrease is the
minimum implemented law, not full credential equivalence. Prefer separating
auth from disposable memory state. Never make auth re-login the automatic
fallback for a memory-table failure while other users of the store remain.
Ensure any sidecar-removal path holds the current main DB inode and common
exclusion; ordinarily leave sidecar lifecycle to SQLite itself.

## Delivered laws and read-only guard

O16 reserves the next free owner-law number after existing O14/O15 branches.
The existing `bend/ops_fsm` now has 68 claims (11 new O16 claims), Python twins
in `src/z0int/fsm/ops.py`, finite-domain/bounded parity, type-preserving mutants,
and a seven-item incident/edge tape explicitly distinguishing historical
parent evidence from synthetic cases. Core claims:

- `o16_sqlite_replace_no_holders` and `o16_sqlite_replace_scan_complete`:
  replacing/unlinking/recreating requires a complete empty-holder census.
- `o16_sqlite_no_ghost_holders` and `o16_sqlite_unknown_holders`:
  deleted/mismatched holders violate health; incomplete visibility is not clean.
- `o16_sqlite_auth_non_decreasing` and `o16_sqlite_auth_known`:
  no count loss; missing/unreadable baseline never earns preservation credit.
- `o16_sqlite_sidecar_current_inode`: only a current-DB holder meets the
  necessary sidecar-removal condition.
- Four positive rows: `o16_sqlite_quiescent_replace`,
  `o16_sqlite_healthy_holders`, `o16_sqlite_auth_retained`,
  `o16_sqlite_current_sidecar` prevent vacuous always-refuse tables.

Use from `/mnt/zer0models/z0-wt/sqlite-store-laws`:

```sh
PYTHONPATH=src python3 -m z0int.fsm.guard sqlite-ghosts \
  --db ~/.omp/agent/agent.db \
  --last-receipt /mnt/zer0models/workspace/zer0/data/operations/omp-memory/sqlite-auth-baseline.json
```

`--operation replace|unlink|recreate|remove-sidecars` checks a proposed action
but never performs it. `--actor-pid` checks the necessary current-DB FD condition
for sidecar removal. No signals, service operations, repairs, checkpoints or
credential-row output exist in this subcommand. It emits stdout only; a
previous receipt is read, never overwritten. No timer, upstream OMP patch,
launcher interception or stamped `z0-guard` deployment was performed.
Older installed copies may lack this command; use the explicit checkout.

The count query never connects SQLite to the live path: it reads DB+WAL bytes
into a mode-0600 private temporary copy, verifies source metadata stability,
and queries only that copy. It does not copy live SHM. This avoids a live
`mode=ro` connection's possible SHM writes/close cleanup. The census and
copy are not an atomic transaction/lock; inaccessible processes, concurrent
changes, missing/invalid baseline, or count-read failure are unavailable,
not zero. Namespace restrictions, mmap-only holders, arbitrary renamed
unknown old inodes and schema/credential validity remain outside this guard.

### Executed live proposed-replacement audit

Snapshot: 2026-10-25T07:50:04.540727+00:00. Full receipt:
`/mnt/zer0models/workspace/zer0/data/operations/omp-memory/sqlite-live-replace.json`.
It returned exit **3** without executing a replacement:

```json
{
  "ts": "2026-10-25T07:50:04.540727+00:00",
  "status": "violated",
  "pids": [
    2266022,
    2334175,
    2363215
  ],
  "ghost_pids": [],
  "auth_credentials_count": 9,
  "previous_auth_credentials_count": 9,
  "violations": [
    {
      "law": "o16_sqlite_replace_no_holders",
      "pids": [
        2266022,
        2334175,
        2363215
      ]
    }
  ],
  "requirements_met": {
    "auth_preserved": true,
    "holder_health": false,
    "replacement": false,
    "sidecar_actor_current": false
  },
  "unavailable_reasons": [
    "holder-census-incomplete"
  ],
  "authorization": false,
  "read_only": true
}
```

The complete receipt enumerates all inaccessible PIDs and FD identities;
467 inaccessible PIDs caused fail-closed `holder-census-incomplete`.
Current readable holders block replacement even though no current ghost was
observed. The 9→9 auth comparison is count-only observational evidence.

## Verification receipts and remaining limits

All repository-relative paths below are in
`/mnt/zer0models/z0-wt/sqlite-store-laws`:

- Direct `bend PROOF.bend --verdict`: **ALL PROOFS CHECK**.
- `bend/ops_fsm/sqlite-receipt.json` (also canonical `receipt.json`):
  `hermes bend verify` **PASS**, receipt `7977c6b1424546efa82556f41b7df698`.
  Input manifest SHA-256
  `c3874e910f3eb4b0953f0af85cd073c2b76aae577f976e21760e1071bd58caf2`;
  three input files. Replay matched exactly with `replay_differences: []`.
- The requested `bend_verify` tool was run twice but its wrapper failed to
  find `lean` on PATH. The CLI alternative succeeded with
  `PATH=/home/kvn/.elan/bin:$PATH`; no global toolchain change was made.
- `bend/ops_fsm/sqlite-mutations.json` (also canonical `mutations.json`):
  **76/76 rejected**, reference passes/restored; MBP fleet job
  `fleet-mbp-6a3e91f364a74fac`. That combined job's tests initially selected a
  nonexistent filename and ran none; it is **not** the passing test receipt.
- Corrected MBP job `fleet-mbp-91fe0da802d04393`: **203 tests passed**, no
  skipped parity, in ops/SQLite/policy guard test files. Artifact:
  `bend/ops_fsm/sqlite-pytest.log`. Local focused suite: **28 passed**.
- `bend/ops_fsm/sqlite-parity.json`: **1007/1007 equal**; O16 Boolean domains
  exhaustive and auth counts bounded to {0,1,9,10}. This is parity evidence,
  not proof of Python/compiler equivalence on all inputs.
- O16 tape replay: all seven expected results matched, including historical
  ghost detection and count-loss refusal; no incident action was executed.

A Bend PASS covers the emitted abstract book only:
`source_semantics_attested: false` (Bend 2.0.34 translation limitation noted
in the receipt). It does not prove `/proc` completeness, Python semantics,
SQLite's implementation, the original Oct 20 trigger, which exact process
unlinked any WAL, or the whole historical causal chain. This work supplies
necessary-condition laws and an executable diagnostic/preflight, **not an
upstream fix or a claim that live corruption can no longer recur**.


### Source custody incident during this task

An external reset to HEAD plus removal of untracked material was observed in
this isolated worktree at 2026-10-25 07:54:21 UTC (reflog). The actor was not
identified. Passing fleet/live receipts survived outside the worktree; the
exact authored edit scripts and original actual verifier JSON were recovered
from this subagent's own read-only Hermes session records. The source was
reconstructed and committed immediately, then focused checks and proof were
re-exercised. No other worktree was reset or cleaned by this task. Preserve
commits/checkpoints before running concurrent cleanup.
