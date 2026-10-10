# Serializable continuations (#130)

This implementation persists **data at named suspension points**, not Python,
JavaScript or Luau stacks. A checkpoint, hash, UI acknowledgement or old approval
is not authority to dispatch a tool. OMP/Herdsman keeps ownership of sessions,
queues, leases and legal continuation. TSP remains presentation, not execution.

## Implemented surface

- `z0int.continuation`: immutable, versioned checkpoints; strict bounded JSON;
  content identity; independently supplied restore context/revisions; pending
  operation reconciliation; atomic publication; existing EventLog/receipt join.
- `z0int.task_loop`: existing `resume_task()` uses sealed, atomic checkpoints.
  Local POSIX task ownership prevents two resume processes applying a patch.
  Durable before/after byte hashes reconcile a crash around the bounded file
  edit. This does **not** generalize exactly-once effects to arbitrary tools.
- `z0int.continuation_adapters`: opt-in OMP boundary observations and host-side
  Tern save/init contract functions. These are **not installed live adapters**.
  No provider calls, scheduler, GUI changes, sockets or model calls are added.

## Wire and validation

```python
from z0int.continuation import (
    ContinuationCheckpoint, ContinuationContext, PendingOperation, plan_restore,
)

current = ContinuationContext(
    runtime="tern", runtime_revision="installed-build",
    code_revision="plugin-sha", policy_revision="current-policy-sha",
    authority_ref="intent:demo", scope="project:demo",
    trace_id="trace:1", work_item_id="work:1", attempt_lineage_id="attempt:1",
)
checkpoint = ContinuationCheckpoint.build(
    continuation_id="block:1", context=current, phase="waiting",
    resume_entrypoint="demo.wait_for_result", state={"request": "read:1"},
    source_revisions={"input": "sha256:observed-input"},
    pending=(PendingOperation(
        "read:1", "read", "started", "readback", durable_ref="result:1"
    ),),
)
wire = checkpoint.dumps()
restored = ContinuationCheckpoint.loads(wire)
plan = plan_restore(
    restored, context=current,  # independently resolve CURRENT identity/policy
    source_revisions={"input": "sha256:observed-input"},
    allowed_entrypoints={"demo.wait_for_result"},
)
assert plan.disposition == "reconcile"
```

The envelope contains `schema`, `checkpoint_id`, and a canonical **payload
string**. Keep that string unchanged across JS/Luau save/load; this avoids
cross-language float formatting, null, and empty-array/object changes. This is
not a claim of RFC 8785/JCS conformance. The owning Python host validates the
payload/hash. The 256 KiB limit applies to each encoded document; envelope
escaping can make the outer document exceed the limit before the inner one.
Use durable refs instead of raw histories. Integers outside the JS/Luau safe
range must be strings; non-finite numbers, duplicate keys, closures, tuples,
bytes and excessively nested state are rejected.

The SHA-256 identity covers the schema and entire canonical body. It detects
accidental corruption, **not malicious replacement**: checkpoints are not
signed. Use a private, runtime-selected local path and retain host authorization
checks. No credential material belongs in checkpoint state. Runtime, code,
policy, scope, source revisions and allowlisted entrypoint must match on restore.
Do not populate the current context by copying it from the untrusted checkpoint.

`started` becomes `unknown` after process loss, even with an `idempotent_retry`
label. The latter requires a key but does not prove target support. A restore
plan returns `terminal`, `reconcile` or `runtime_owned`; none authorizes an
external action. Terminal cancellation cannot be resurrected by pending work.
Duplicate pure restores produce the same data without dispatching anything.
External runtimes still need their own lease/epoch and deadline enforcement.

## Existing local task loop

Top-level v1 fields remain readable. `_continuation` adds metadata and a hash
without duplicating task state. Bad schemas/IDs/statuses and corrupted sealed
state are rejected. A legacy success flag without verifier evidence is not
exported as newly verified success.

The seal is mandatory for every checkpoint in the sealed format. Every writer
since the seal was introduced emits both `_continuation` and the `pending_patch`
field, so a file that has `pending_patch` but no `_continuation` has had its
seal removed and is refused. Only the pre-seal shape (neither key) loads
unsealed; it is sealed on the next save and has **no retroactive integrity
guarantee**. This needs no second store, and it is a corruption/hand-edit check
only: the hash is not keyed, so a writer who rewrites the whole file (removes
both keys, or recomputes the hash) is not detected.

`list_checkpoints()` / `z0int task status` keep a refused checkpoint (corrupt,
stale, unsealed, or unreadable: a directory, dangling symlink or permission
error) visible as `status: "refused"` with a `reason` and no outcome
flags, instead of omitting the task.

The local adapter fingerprints `task_loop.py`, the Python major/minor version,
and the persisted AODL/context artifacts. A changed required artifact is not
silently accepted. This is not a whole-repository dependency fingerprint or a
substitute for fresh authorization under a changed policy. No automatic schema
or code migration is attempted.

`run_until` and `resume_task` own a per-task POSIX lock. A stale in-memory caller
must reload rather than overwrite a newer run. Lower-level `step_*` functions
remain host primitives: their caller must own the task. `save_checkpoint` alone
is atomic publication, not compare-and-swap or a cross-runtime lease.

Before editing, persist the exact before/after byte hashes. On process restart:

| Readback | Behavior |
| --- | --- |
| Exact after-image | Do not write again; run the independent content predicate. |
| Exact before-image | The owning local runtime may perform this bounded edit. |
| Anything else, or missing target | Leave data untouched and require reconciliation. |
| Terminal/abandoned task | No further dispatch. |

A recovered after-image does **not** prove a Git commit completed, so resume
checks it against the recorded after-image, never against whatever bytes are in
the worktree. The patch is committed when the **task branch ref**
(`z0int/<task_id>`) has, for the target, the blob that Git itself derives from
the recorded after-image under the repository's attributes and config. On every
path (apply, reconcile, verify) the commit step:

- commits only while the worktree target's sha256 equals the recorded
  after-image; any other content is left untouched and uncommitted;
- commits only the target path (`git commit --only -- <target>`), so anything
  else staged in the worktree stays staged and out of the "bounded patch";
- commits only when the worktree HEAD is the task branch. A detached worktree,
  or one on another branch, is never checked out, moved or committed to.

If any of these fails, or the commit itself fails, `verified_success` stays
null, `last_error` says why (for example that the worktree content does not
match the recorded patch) and the task stays resumable. A pre-seal checkpoint
that reaches verification with no recorded after-image is not committed at
all; it is verified by the content predicate alone, as before, and
`verify_detail` says the commit was not checked. The stored blob is not
required to equal the worktree bytes: a clean filter, line-ending conversion
(`core.autocrlf`, `text`/`eol` attributes) or `ident` legitimately changes the
file on the way in. The expected blob id is taken from a real `git add` of the
target into a scratch index read from the task branch, with a scratch object
directory, so the worktree's index and the object store are not touched; the
staged entry and the task branch entry must both be that id, as a regular
file. (`git hash-object` is not used: it does not load the index, so it
converts a path whose stored blob already holds CRLF where `git add` does
not.) A filter that appears after the write is therefore applied, exactly as
if it had always been configured. A target that is a symlink in the worktree
is refused outright. When Git derives the blob the branch already has (a patch
that only turns CRLF into LF under `core.autocrlf`, or only edits `$Id$` text
under `ident`), there is nothing to commit and the content predicate decides.
Another process that rewrites the target while the commit step runs is not
excluded; the worktree hash is re-checked after the blob id is derived, and
the task-branch check after the commit then withholds verification.
Verification otherwise remains the existing content predicate, not a claim that
arbitrary tests ran or all project requirements were satisfied.
Atomic checkpoint writes flush the temporary file and POSIX parent directory;
Windows lacks the directory fsync guarantee. The local task owner currently
requires POSIX; the pure contract does not.

Audit admission is explicit, avoiding a new always-on raw history collector:

```python
from z0int.memory.event_log import EventLog
from z0int.task_loop import record_task_checkpoint
record_task_checkpoint(task_id, EventLog())
```

This uses `continuation.checkpoint` in the existing ledger, including its blob
storage. `receipt_extra()` fits `DecisionReceipt.extra` and keeps the #62 trace,
work-item and attempt-lineage IDs. Duplicate admissions share a `replay_key`;
the existing ledger may contain both events. Consumers deduplicate by that key.
Neither event IDs nor receipts constitute an exactly-once execution lease.

## OMP and Tern boundaries

OMP source inspection is pinned to
[`2be0ab4a59`](https://github.com/can1357/oh-my-pi/tree/2be0ab4a59fdd1ac9ab297344b0bb2c4d609c6d8).
The adapter uses raw `get_state` fields and boundary events from
[`wire/state.ts`](https://github.com/can1357/oh-my-pi/blob/2be0ab4a59fdd1ac9ab297344b0bb2c4d609c6d8/packages/coding-agent/src/modes/rpc/wire/state.ts)
and [`wire/frames.ts`](https://github.com/can1357/oh-my-pi/blob/2be0ab4a59fdd1ac9ab297344b0bb2c4d609c6d8/packages/coding-agent/src/modes/rpc/wire/frames.ts).
A terminal `agent_end` or subagent `yield` alone is not `session_settled`.
Missing telemetry stays null. The observer requires an explicit settled signal
plus a compatible quiet snapshot; this is **observed quiescence**, not a durable
execution checkpoint or permission to resume. Correlating event/snapshot order
and independently supplying source revisions is the host's responsibility.
Process-local job IDs are not made durable. Goals are observed, not reactivated;
private objectives, system prompts and tool arguments are omitted.

Tern's public [block contract](https://docs.stencil.so/tern/guides/blocks.html)
persists the JSON returned by `save(state)` and supplies it to
`init(cx, args, saved)` after reload. Old callbacks, timers and VM state do not
survive. `tern_save`/`tern_init` model that boundary on the owning Python side.
The fixture destroys an old callback, round-trips JSON, then explicitly binds a
new **readback-only** callback from a validated named step and durable result ref.
It does not run Tern, Luau, or a native GUI. TSP surface adoption and
[`omp --resume` session-file reporting](https://github.com/can1357/oh-my-pi/commit/6e9d34a726c68c8982bc3712cd2425d0ce12527b)
are distinct from restoring in-flight tool execution.

## Reproduce and remaining integration work

```sh
PYTHONPATH=src:tests python -m unittest discover -s tests -p '*continuation*.py' -v
PYTHONPATH=src python -m unittest discover -s tests -p test_task_crash_regression.py -v
```

`Continuation restart proof` runs Python 3.11/3.13 on a full checkout with the
original task-loop tests and real EventLog. The same crash regression is also
run against pre-change `43b796aa`; it must fail specifically because the old
executor cannot resume an already-written patch, not due to missing imports.

Remaining before claiming live OMP/Tern integration: wire host-owned capture at
an ordered safe boundary; validate fresh ownership, policy and source identity;
perform actual OMP process-loss/reattach and Tern plugin/daemon-reload dogfooding.
Do not equate these fixture tests with those host integration proofs. The
adapter is opt-in and cannot affect an unmodified OMP/Tern installation.

## Credit

[Can Bölük / can1357](https://github.com/can1357) and Stencil Labs supplied the
coroutine requirement and OMP/Tern surfaces. [Andre Brait](https://github.com/andrebrait)
owns the hosted-session work in [OMP #14166](https://github.com/can1357/oh-my-pi/pull/14166).
[jroth1111](https://github.com/jroth1111)'s [recovery proposal #14386](https://github.com/can1357/oh-my-pi/issues/14386)
frames ambiguous effects carefully. [Yağız Katerli](https://github.com/yagizkaterli),
the Pi Herdsman authors and Herdr informed the runtime/policy ownership boundary
in [#129](https://github.com/kvnloo/z0intelligence/issues/129). These are design
sources, not claims that they authored or reviewed this implementation.
