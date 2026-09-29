"""Typed task-event helpers.

Event *types* live in ``contracts`` so a single import describes the whole
vocabulary; this module holds the small amount of logic that decides what an
event means for the conversation.
"""
from __future__ import annotations

from .contracts import TaskEvent, TaskEventType, TaskRef

#: Events that may update background state but must never be spoken.
SILENT_EVENT_TYPES = frozenset({
    TaskEventType.TASK_STARTED, TaskEventType.TASK_PROGRESS,
    TaskEventType.TASK_SUPERSEDED,
})

#: Events that end the task.
TERMINAL_EVENT_TYPES = frozenset({
    TaskEventType.TASK_VERIFIED, TaskEventType.TASK_FAILED,
    TaskEventType.TASK_CANCELLED,
})

#: Events that need the operator before work continues.
BLOCKING_EVENT_TYPES = frozenset({TaskEventType.APPROVAL_REQUIRED})


def is_terminal(event: TaskEvent) -> bool:
    return event.type in TERMINAL_EVENT_TYPES


def is_announceable(event: TaskEvent, *, current_revision: int) -> bool:
    """A verified event for the CURRENT revision may be announced; nothing else.

    This is the stale-result guard: a late success from a superseded revision is
    never surfaced, however good the news is.
    """
    return (event.type is TaskEventType.TASK_VERIFIED
            and event.verified
            and event.task.revision == current_revision)


def summarize(events: list[TaskEvent], ref: TaskRef) -> dict:
    return {
        "task_id": ref.task_id,
        "trace_id": ref.trace_id,
        "revision": ref.revision,
        "status": ref.status.value,
        "events": [(e.type.value, e.sequence) for e in events],
    }
