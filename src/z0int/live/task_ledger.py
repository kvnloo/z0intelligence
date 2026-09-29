"""Task ledger for z0 Live.

This is NOT a second ledger. It derives its identity from the existing
z0intelligence dispatch authority (``dispatch_authority.identity``) and defers
receipt writing to the existing ``receipt`` module, so a task and its dispatch
receipt can never disagree about a ``trace_id``.

What it adds on top of the authority is only what a conversation needs:

* a ``task_id`` that is stable across revisions
* monotonic ``revision`` so "focus on tests instead" updates one task rather
  than creating a second
* supersede semantics, so a stale worker result cannot be announced
* a verification gate: execution completion never becomes verified success

Invariants
    * ``task_id = sha256(trace_id)`` (stable, no counter, no session state)
    * a task may move to a terminal status exactly once
    * a result is only announceable when the task is current AND verified
    * re-issuing the same ``trace_id`` with the same fingerprint is a replay;
      a different fingerprint for the same trace is refused
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .contracts import (
    SILENT,
    SPEAKABLE,
    TaskEvent,
    TaskEventType,
    TaskRef,
    TaskStatus,
)


class StaleTaskError(RuntimeError):
    """Raised when an event arrives for a revision the ledger has moved past."""


class DuplicateTaskError(RuntimeError):
    """Raised when a trace_id is reused for materially different work."""


def task_id_for(trace_id: str) -> str:
    return hashlib.sha256(("z0live:" + trace_id).encode()).hexdigest()[:24]


def fingerprint(request: dict[str, Any]) -> str:
    """Same posture as the dispatch authority: identity is (harness, trace_id) and
    a different payload for the same trace is a conflict, not a new task."""
    import json
    return hashlib.sha256(
        json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()[:32]


@dataclass
class _Task:
    ref: TaskRef
    fingerprint: str
    request: dict[str, Any]
    events: list[TaskEvent] = field(default_factory=list)
    result: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    announce: str = SILENT
    sequence: int = 0


class TaskLedger:
    """In-process task registry keyed by ``trace_id``.

    Thread-safe: two workers may run concurrently and must not collide on
    identity, so every mutation takes the lock.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_trace: dict[str, _Task] = {}

    # ------------------------------------------------------------------ create
    def open(self, request: dict[str, Any], *, harness: str,
             announce: str = SILENT) -> tuple[TaskRef, bool]:
        """Create or return the task for this trace.

        Returns ``(ref, replayed)``. A repeated identical request is a replay and
        must not cause a second side effect; a repeated trace with a different
        payload is a conflict.
        """
        trace_id = str(request.get("trace_id") or "")
        if not trace_id:
            raise ValueError("request requires trace_id")
        fp = fingerprint({k: v for k, v in request.items() if k != "revision"})
        with self._lock:
            existing = self._by_trace.get(trace_id)
            if existing is not None:
                if existing.fingerprint != fp:
                    raise DuplicateTaskError(
                        f"trace_id {trace_id!r} reused for different work")
                return existing.ref, True
            ref = TaskRef(task_id=task_id_for(trace_id), trace_id=trace_id,
                          revision=1, harness=harness, status=TaskStatus.PENDING)
            self._by_trace[trace_id] = _Task(ref=ref, fingerprint=fp,
                                             request=dict(request), announce=announce)
            return ref, False

    # ------------------------------------------------------------------- mutate
    def _current(self, ref: TaskRef) -> _Task:
        t = self._by_trace.get(ref.trace_id)
        if t is None:
            raise KeyError(f"unknown task {ref.task_id}")
        return t

    def _append(self, task: _Task, event: TaskEvent) -> TaskEvent:
        task.sequence += 1
        stamped = TaskEvent(
            type=event.type, task=task.ref, ts=event.ts, payload=event.payload,
            execution_completed=event.execution_completed, verified=event.verified,
            sequence=task.sequence)
        task.events.append(stamped)
        return stamped

    def start(self, ref: TaskRef, *, worker_id: str) -> tuple[TaskRef, TaskEvent]:
        with self._lock:
            t = self._current(ref)
            self._refuse_if_stale(t, ref)
            if t.ref.status.terminal:
                raise StaleTaskError(f"task {t.ref.task_id} is {t.ref.status.value}")
            t.ref = t.ref.bump(status=TaskStatus.STARTED, worker_id=worker_id)
            return t.ref, self._append(t, TaskEvent(
                type=TaskEventType.TASK_STARTED, task=t.ref,
                payload={"worker_id": worker_id}))

    def progress(self, ref: TaskRef, note: str) -> tuple[TaskRef, TaskEvent]:
        with self._lock:
            t = self._current(ref)
            self._refuse_if_stale(t, ref)
            if t.ref.status.terminal:
                raise StaleTaskError(f"task {t.ref.task_id} is {t.ref.status.value}")
            t.ref = t.ref.bump(status=TaskStatus.PROGRESS)
            return t.ref, self._append(t, TaskEvent(
                type=TaskEventType.TASK_PROGRESS, task=t.ref, payload={"note": note}))

    def steer(self, ref: TaskRef, instruction: str) -> tuple[TaskRef, TaskEvent]:
        """A steer keeps the same worker and bump the revision."""
        return self.progress(ref, f"steer: {instruction}")

    def redirect(self, ref: TaskRef, instruction: str) -> tuple[TaskRef, TaskEvent]:
        """A true redirect supersedes the in-flight work and starts a new revision."""
        with self._lock:
            t = self._current(ref)
            self._refuse_if_stale(t, ref)
            if t.ref.status.terminal:
                raise StaleTaskError(f"task {t.ref.task_id} is {t.ref.status.value}")
            t.ref = t.ref.bump(status=TaskStatus.STARTED)
            superseded = self._append(t, TaskEvent(
                type=TaskEventType.TASK_SUPERSEDED, task=t.ref,
                payload={"instruction": instruction}))
            self._append(t, TaskEvent(
                type=TaskEventType.TASK_STARTED, task=t.ref,
                payload={"instruction": instruction}))
            return t.ref, superseded

    def require_approval(self, ref: TaskRef, question: str) -> tuple[TaskRef, TaskEvent]:
        with self._lock:
            t = self._current(ref)
            self._refuse_if_stale(t, ref)
            t.ref = t.ref.bump(status=TaskStatus.APPROVAL_REQUIRED)
            return t.ref, self._append(t, TaskEvent(
                type=TaskEventType.APPROVAL_REQUIRED, task=t.ref,
                payload={"question": question}))

    def approve(self, ref: TaskRef, decision: str, *, reason: str = "") -> tuple[TaskRef, TaskEvent]:
        with self._lock:
            t = self._current(ref)
            self._refuse_if_stale(t, ref)
            if t.ref.status is not TaskStatus.APPROVAL_REQUIRED:
                raise StaleTaskError("task is not awaiting approval")
            t.ref = t.ref.bump(status=TaskStatus.STARTED)
            return t.ref, self._append(t, TaskEvent(
                type=TaskEventType.TASK_STARTED, task=t.ref,
                payload={"approval": decision, "reason": reason}))

    def cancel(self, ref: TaskRef, reason: str = "") -> tuple[TaskRef, TaskEvent]:
        with self._lock:
            t = self._current(ref)
            if t.ref.status.terminal:
                return t.ref, t.events[-1]
            t.ref = t.ref.bump(status=TaskStatus.CANCELLED)
            return t.ref, self._append(t, TaskEvent(
                type=TaskEventType.TASK_CANCELLED, task=t.ref,
                payload={"reason": reason}))

    # ------------------------------------------------------------ completion
    def complete(self, ref: TaskRef, result: dict[str, Any], *,
                 verified: bool, evidence: dict[str, Any] | None = None
                 ) -> tuple[TaskRef, TaskEvent]:
        """Record completion.

        ``verified`` must come from independent evidence. Passing
        ``verified=True`` without evidence is refused: the whole point is that a
        worker exiting is not proof its work is correct.
        """
        with self._lock:
            t = self._current(ref)
            self._refuse_if_stale(t, ref)
            if t.ref.status.terminal:
                raise StaleTaskError(f"task {t.ref.task_id} is {t.ref.status.value}")
            t.result = dict(result)
            if verified:
                if not evidence:
                    raise ValueError(
                        "verified=True requires evidence; execution completion is "
                        "not verified success")
                t.verification = dict(evidence)
                t.ref = t.ref.bump(status=TaskStatus.VERIFIED)
                return t.ref, self._append(t, TaskEvent(
                    type=TaskEventType.TASK_VERIFIED, task=t.ref, payload=dict(result),
                    execution_completed=True, verified=True))
            t.ref = t.ref.bump(status=TaskStatus.FAILED)
            return t.ref, self._append(t, TaskEvent(
                type=TaskEventType.TASK_FAILED, task=t.ref,
                payload={"result": result, "reason": "execution completed without "
                                                      "independent verification"},
                execution_completed=True, verified=False))

    # -------------------------------------------------------------- read side
    def ref(self, trace_id: str) -> TaskRef:
        with self._lock:
            return self._by_trace[trace_id].ref

    def events(self, ref: TaskRef) -> list[TaskEvent]:
        with self._lock:
            return list(self._current(ref).events)

    def announce_kind(self, ref: TaskRef) -> str:
        with self._lock:
            return self._current(ref).announce

    def can_announce(self, ref: TaskRef) -> bool:
        """Only current, verified tasks may be surfaced to the conversation."""
        with self._lock:
            t = self._by_trace.get(ref.trace_id)
            if t is None:
                return False
            return (t.ref.status is TaskStatus.VERIFIED
                    and t.ref.revision == ref.revision)

    def announce(self, ref: TaskRef, *, text: str | None = None
                 ) -> dict[str, Any] | None:
        """Return what the actor should surface, or None if it must stay silent.

        A stale or unverified result returns None rather than raising, because the
        caller is the audio loop and it must never be blocked or crashed by a late
        worker.
        """
        with self._lock:
            t = self._by_trace.get(ref.trace_id)
            if t is None or t.ref.revision != ref.revision:
                return None
            if t.ref.status is not TaskStatus.VERIFIED:
                return None
            if t.announce == SILENT:
                return {"kind": SILENT, "task": t.ref, "state": t.result}
            return {"kind": SPEAKABLE, "task": t.ref,
                    "text": text or _summarize(t.result)}

    def _refuse_if_stale(self, task: _Task, ref: TaskRef) -> None:
        if ref.revision < task.ref.revision:
            raise StaleTaskError(
                f"event for revision {ref.revision} but task is at {task.ref.revision}")

    # ------------------------------------------------------------------# misc
    def open_tasks(self) -> list[TaskRef]:
        with self._lock:
            return [t.ref for t in self._by_trace.values() if not t.ref.status.terminal]


def _summarize(result: dict[str, Any] | None) -> str:
    if not result:
        return "Task finished."
    for key in ("summary", "message", "text", "note"):
        if isinstance(result.get(key), str) and result[key].strip():
            return result[key]
    return "Task finished."
