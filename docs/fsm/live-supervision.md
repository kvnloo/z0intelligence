# Live supervision and opt-in dispatch gates

The loop-2 checker is evidence, not authority. The service only reads worker
logs, SQLite (mode=ro), process records, fleet logs and git metadata. It never
resumes, kills, approves, pushes or edits a worker. No hooks are installed.

## Live batch

`python -m z0int.fsm.supervise --live-dir /mnt/zer0models/workspace/zer0/data/operations/fsm/live`

The checked-in user units use the local worktree at
`/mnt/zer0models/z0-wt/fsm-worker-supervision`, `/usr/bin/python3`, and an
explicit PYTHONPATH. Install on groot:

    mkdir -p /mnt/zer0models/workspace/zer0/data/operations/fsm/live
    systemctl --user link "$PWD/systemd/z0-worker-supervision.service" "$PWD/systemd/z0-worker-supervision.timer"
    systemctl --user daemon-reload
    systemctl --user enable --now z0-worker-supervision.timer
    systemctl --user start z0-worker-supervision.service
    systemctl --user show z0-worker-supervision.service -p Result -p ExecMainStatus
    systemctl --user list-timers z0-worker-supervision.timer

Disable: `systemctl --user disable --now z0-worker-supervision.timer`.
Stop an in-flight read-only scan with `systemctl --user stop z0-worker-supervision.service`.
Re-enable with `systemctl --user enable --now z0-worker-supervision.timer`.
No bridge/Metro restart is necessary for the timer. Do not remove its worktree
while enabled. The timer runs every ten minutes; missed ticks coalesce. A
single oneshot cannot overlap itself. No notifications or sleep-time pings.

`state.json` contains current worker states, historical violations and source
counts. `needs-kevin.json` is the current batch projection. The authoritative
queue history is append-only `needs-kevin.jsonl`: stable worker-derived IDs,
open/update/resolved events, no truncation. Snapshots are atomically replaced,
not appended (appending JSON objects would not be a readable JSON document).
A lock serializes publication; consumers compare generated_at on both files.
SQLite spill files stay under live/.tmp, the unit's explicit TMPDIR. The unit
mounts worker trees read-only and allows writes only under live/. Unexpected
errors fail the service; ordinary findings do not fail a live tick. A failed
scan preserves the previous snapshot; Dash labels snapshots older than 20 min.

Append an owner disposition to
`/mnt/zer0models/workspace/zer0/data/operations/fsm/worker-decisions.jsonl`:

    {"worker":"deleg_x/task-0","action":"resume","by":"kevin","at":"2026-10-21T12:00:00Z"}

Accepted actions: abandon, resume/resumed, integrated/already integrated.
The last valid record per worker wins. Resume is a routing disposition, NOT
proof that execution happened; `meta.disposition` distinguishes it. Integration
records cannot clear unreaped processes. The pure Bend/Python statechart is
unchanged: external dispositions are a report projection. Historical law
violations remain evidence; an owner's answer is not retroactive absolution.
Non-delegated CLI/fleet sessions can be successors when their title declares
resume/continue/recovery/integration intent AND they actually read the prior
log after its last activity. Mere audits/mentions do not constitute adoption.

Needs Kevin is pull-only, batched for one or two deep-work blocks/day, never a
worker dispatch prerequisite. Every item has why, recommended_action, options
and evidence_path. Do not turn this batch into page-by-page interruptions.

## Opt-in write leases (W1, #185/#186)

Acquire before dispatching a writer, not after its first write:

    python -m z0int.fsm.gate lease acquire --worktree "$WT" --holder "$WORKER_ID" --pid "$WORKER_PID"
    python -m z0int.fsm.gate lease status --worktree "$WT" --holder "$WORKER_ID"
    python -m z0int.fsm.gate lease release --worktree "$WT" --holder "$WORKER_ID" --pid "$WORKER_PID"

Use the persistent local worker/orchestrator PID, not a short-lived gate CLI
PID. If all work is in one long-lived shell, pass `--pid $$` and release in a
shell EXIT trap. Hermes delegation: acquire in the parent with the parent's
persistent PID and a unique delegation/task holder ID, dispatch only on exit
0, then release after the child is joined and all writes stop. Fleet: acquire
on the authoritative checkout's host before the job, retain the local
orchestrator's lease through result import, then release. An ephemeral shipped
source tree is not the authoritative checkout. Independent remote clones use
the gate on their own host/common dir; this is not a distributed lock service.

The lock is a SHA256 of canonical worktree root under
`<git-common-dir>/z0-leases/`. Symlink aliases and subpaths share it. Each
creation uses O_EXCL, mode 0600. A flock guard serializes unlink/reclaim with
acquire, avoiding two simultaneous reclaimers. Metadata records holder, PID,
host, wall-clock start, kernel process-start tick and boot ID. A live lease
cannot be stolen or released by another holder. Only a provably dead/reused
local PID is reclaimed; malformed/unreadable/foreign-host leases HOLD. Status
never reclaims. Locks are local POSIX locks, not supported over NFS.
Read-only agents need no lease. These explicit locks prevent governed writers
from clashing, not ungoverned routes; replay's historical clash count does not
become zero by installing this gate.

## Heavy placement (W4)

    python -m z0int.fsm.gate heavy --cmd "cargo build --release"
    python -m z0int.fsm.gate heavy --cmd "fleet-build -- cargo build --release"

JSON status `hold` / exit 3 means route through `fleet-build -- <cmd>` on the
MBP; `ok` / exit 0 means no unexcepted local heavy command was recognized.
GPU and Bend/Lean have standing exceptions. For a genuinely host-only toolchain,
use an explicit `Z0_HEAVY_EXCEPTION=toolchain` prefix (or gpu/bend) on the sole
heavy command. Do not use exceptions to evade placement. Commands that only
print/search build text, or execute it under ssh/fleet, are not local builds.
A local heavy command after a remote segment still HOLDs. This is a bounded
shell classifier, not a shell interpreter or security sandbox: interpreter-
generated commands, substitutions and dynamically constructed argv need review.
Hermes/fleet launchers should run this check before dispatch; read-only human
batches are never a condition for dispatch. No global hooks are installed.

## Dash

The separate Dash draft adds bearer-authenticated GET /supervision (state +
needs_kevin), behind the existing bridge host/origin policy. POST is refused;
missing/corrupt/mixed-generation snapshots return 503, not an empty success.
DASH_SUPERVISION_DIR optionally overrides the fixed host directory. The app's
Approvals screen reads this batch and groups it by recommended decision. It
has no resume/kill/approval controls for supervision. Loading it does not alter
the existing approval outbox. Owner integrates and restarts dash-bridge-4747 /
dash-metro-8097 later; this worker must not restart them.
