"""z0 Live provider-neutral contracts.

Two boundaries, deliberately separate:

``ConversationActor``
    the realtime surface — connect, audio/transcript in, delegation out, silent
    state or speakable commentary injected back, cancel, close. A provider
    (GPT Live / Grok Voice / Gemini Live / a native model) implements this and
    nothing above it needs to know which.

``AgentControl`` (v1)
    the harness-control surface — spawn, steer, redirect, status, wait, result,
    cancel, approve, list. OMP and Hermes implement this and nothing above it
    needs to know which.

The two are joined by the task ledger, not by direct coupling, so a slow worker
never blocks the audio loop.

INVARIANTS (enforced here or in ``task_ledger``)
    * every task carries stable ``task_id``, ``trace_id``, ``revision``,
      ``harness``, ``worker_id``, ``status``
    * ``trace_id`` keeps z0intelligence's existing idempotency semantics; this
      package does not open a second ledger
    * execution completion is NOT verified success
    * a superseded task's result can never be announced as current
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class TaskStatus(str, Enum):
    PENDING = "pending"
    STARTED = "started"
    PROGRESS = "progress"
    APPROVAL_REQUIRED = "approval_required"
    VERIFIED = "verified"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"

    @property
    def terminal(self) -> bool:
        return self in (TaskStatus.VERIFIED, TaskStatus.FAILED,
                        TaskStatus.CANCELLED, TaskStatus.SUPERSEDED)


class TaskEventType(str, Enum):
    TASK_STARTED = "TASK_STARTED"
    TASK_PROGRESS = "TASK_PROGRESS"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    TASK_VERIFIED = "TASK_VERIFIED"
    TASK_FAILED = "TASK_FAILED"
    TASK_CANCELLED = "TASK_CANCELLED"
    TASK_SUPERSEDED = "TASK_SUPERSEDED"


class ActorEventType(str, Enum):
    CONNECT = "connect"
    AUDIO_INPUT = "audio_input"
    TRANSCRIPT = "transcript"
    DELEGATION = "delegation"
    SILENT_STATE = "silent_state"
    SPEAKABLE = "speakable"
    CANCEL = "cancel"
    CLOSE = "close"


#: How a delegated task's result reaches the conversation.
SILENT = "silent"          # background state only; never spoken
SPEAKABLE = "speakable"    # may be spoken as commentary


@dataclass(frozen=True)
class TaskRef:
    """Stable identity for one unit of background work."""

    task_id: str
    trace_id: str
    revision: int
    harness: str
    worker_id: str | None = None
    status: TaskStatus = TaskStatus.PENDING

    def bump(self, *, status: TaskStatus | None = None,
             worker_id: str | None = None) -> "TaskRef":
        """Return a new revision. Revisions only ever increase."""
        return TaskRef(
            task_id=self.task_id,
            trace_id=self.trace_id,
            revision=self.revision + 1,
            harness=self.harness,
            worker_id=worker_id if worker_id is not None else self.worker_id,
            status=status or self.status,
        )


@dataclass(frozen=True)
class TaskEvent:
    """One typed event about a task. Carries the full TaskRef, never a bare id."""

    type: TaskEventType
    task: TaskRef
    ts: float = field(default_factory=time.time)
    payload: dict[str, Any] = field(default_factory=dict)
    #: execution finished (process exited 0) — explicitly NOT the same as verified
    execution_completed: bool = False
    #: independent evidence that the work is actually correct
    verified: bool = False
    #: monotonically increasing per task, so stale events are detectable
    sequence: int = 0

    def __post_init__(self) -> None:
        if self.type is TaskEventType.TASK_VERIFIED and not self.verified:
            raise ValueError("TASK_VERIFIED requires verified=True; "
                             "execution completion is not verified success")
        if self.execution_completed and self.type is TaskEventType.TASK_VERIFIED and not self.verified:
            raise ValueError("refusing to promote execution completion to verified success")


@dataclass(frozen=True)
class ActorEvent:
    """One inbound realtime event on the ConversationActor surface."""

    type: ActorEventType
    ts: float = field(default_factory=time.time)
    session_id: str | None = None
    text: str | None = None
    audio: bytes | None = None
    task: TaskRef | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ConversationActor(Protocol):
    """Realtime conversation surface. Implemented per provider; never per harness."""

    def connect(self) -> None:
        ...

    def on_audio(self, audio: bytes) -> None:
        ...

    def on_transcript(self, text: str, *, final: bool = True) -> None:
        ...

    def delegate(self, request: dict[str, Any]) -> TaskRef:
        ...

    def inject_silent(self, task: TaskRef, state: dict[str, Any]) -> None:
        ...

    def inject_speakable(self, task: TaskRef, text: str) -> None:
        ...

    def cancel(self, task: TaskRef) -> None:
        ...

    def close(self) -> None:
        ...


@runtime_checkable
class AgentControl(Protocol):
    """AgentControl v1 — harness-control surface."""

    name: str

    def spawn(self, request: dict[str, Any], *, task: TaskRef) -> TaskRef:
        ...

    def steer(self, task: TaskRef, instruction: str) -> TaskRef:
        ...

    def redirect(self, task: TaskRef, instruction: str) -> TaskRef:
        ...

    def status(self, task: TaskRef) -> dict[str, Any]:
        ...

    def wait(self, task: TaskRef, *, timeout_s: float | None = None) -> list[TaskEvent]:
        ...

    def result(self, task: TaskRef) -> dict[str, Any] | None:
        ...

    def cancel(self, task: TaskRef) -> TaskRef:
        ...

    def approve(self, task: TaskRef, decision: str, *, reason: str = "") -> TaskRef:
        ...

    def list(self) -> list[TaskRef]:
        ...
