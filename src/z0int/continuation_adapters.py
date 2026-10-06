"""Opt-in observation adapters, with no client, scheduler or dispatch dependency.

OMP fields are pinned in docs/continuations.md. Tern's save/init adapter carries
an opaque canonical payload string; it never interprets Luau stack addresses.
"""
from __future__ import annotations

from typing import Any, Mapping

from .continuation import (
    ContinuationCheckpoint, ContinuationContext, InvalidCheckpoint,
    PendingOperation, RestorePlan, plan_restore,
)


def omp_boundary(
    *, context: ContinuationContext, session_id: str, boundary_ref: str,
    snapshot: dict[str, Any], event: dict[str, Any],
    source_revisions: Mapping[str, str],
    pending: tuple[PendingOperation, ...] = (),
) -> ContinuationCheckpoint:
    """Capture ONE correlated raw get_state/event boundary, without defaulting gaps.

    `boundary_ref` must be assigned by the host to the same ordered observation
    on every replay. Callers must not mix snapshots from different turns. A
    process-local proc:// ID is never promoted to a durable job reference here.
    Raw transcript, credentials, system prompts and tool arguments are omitted.
    """
    if context.runtime != "omp" or snapshot.get("sessionId") != session_id:
        raise InvalidCheckpoint("OMP runtime/session identity mismatch")
    if type(boundary_ref) is not str or not boundary_ref.strip():
        raise InvalidCheckpoint("a stable boundary reference is required")
    kind = event.get("type")
    if kind not in {"agent_end", "prompt_result", "session_settled"}:
        raise InvalidCheckpoint("not a supported OMP boundary event")
    names = ("isStreaming", "isCompacting", "hasPendingAsyncWork", "isSettled")
    view = {name: snapshot.get(name) for name in names}
    for name, value in view.items():
        if value is not None and type(value) is not bool:
            raise InvalidCheckpoint(f"invalid OMP boolean: {name}")
    queued = snapshot.get("queuedMessageCount")
    if queued is not None and (type(queued) is not int or queued < 0):
        raise InvalidCheckpoint("invalid OMP queue count")
    view["queuedMessageCount"] = queued
    event_view = {"type": kind}
    for name in ("isTerminal", "yielded", "awaitingAsyncWork", "sessionSettled"):
        value = event.get(name)
        if value is not None and type(value) is not bool:
            raise InvalidCheckpoint(f"invalid OMP event boolean: {name}")
        event_view[name] = value
    if kind == "prompt_result":
        if event.get("status") not in {"completed", "aborted", "error"}:
            raise InvalidCheckpoint("invalid prompt outcome")
        event_view["status"] = event["status"]
    quiet = (view["isSettled"] is True and view["isStreaming"] is False
             and view["isCompacting"] is False and view["hasPendingAsyncWork"] is False
             and queued == 0 and event_view["awaitingAsyncWork"] is not True
             and all(op.status == "completed" for op in pending))
    signalled = kind == "session_settled" or event_view["sessionSettled"] is True
    phase = "settled" if quiet and signalled else "yielded" if event_view["yielded"] is True else "unsettled"
    if view["hasPendingAsyncWork"] is True or event_view["awaitingAsyncWork"] is True:
        phase = "awaiting_async"
    if not quiet and not any(op.status in {"started", "unknown"} for op in pending):
        pending = (*pending, PendingOperation("unresolved:" + boundary_ref, "omp_runtime_work", "unknown"))
    # Keep only goal lifecycle/accounting metadata, not the private objective.
    goal = snapshot.get("goal")
    goal_view = None
    if isinstance(goal, dict):
        inner = goal.get("goal")
        if isinstance(inner, dict):
            goal_view = {key: inner.get(key) for key in ("id", "status", "tokenBudget", "tokensUsed")}
            goal_view["enabled"] = goal.get("enabled")
    refs = {"session_id": session_id, "boundary": boundary_ref}
    if snapshot.get("sessionFile") is not None:
        refs["session_file"] = snapshot["sessionFile"]
    return ContinuationCheckpoint.build(
        continuation_id=session_id, context=context, phase=phase,
        resume_entrypoint="omp.session_boundary", source_revisions=source_revisions,
        state={"observation": view, "event": event_view, "goal": goal_view},
        durable_refs=refs, pending=pending,
        # A quiet session or completed prompt is not a completed verified TASK.
        terminal=False, execution_completed=None, verified_success=None,
    )


def tern_save(checkpoint: ContinuationCheckpoint) -> dict[str, str]:
    """Value for a Tern block's save(state); all envelope values are strings."""
    if checkpoint.body["context"]["runtime"] != "tern":
        raise InvalidCheckpoint("not a Tern checkpoint")
    return checkpoint.to_dict()


def tern_init(
    saved: dict[str, Any], *, context: ContinuationContext,
    source_revisions: Mapping[str, str], allowed_entrypoints: set[str],
) -> RestorePlan:
    """Host-side equivalent of init(cx, args, saved), not a Luau VM adapter.

    Current host identity/revisions must come from configuration, NOT saved.
    Only the runtime may bind a fresh callback for an allowed named entrypoint;
    this function does not call it or reactivate old permissions/goal state.
    """
    if context.runtime != "tern":
        raise InvalidCheckpoint("not a Tern host")
    return plan_restore(ContinuationCheckpoint.from_dict(saved), context=context,
                        source_revisions=source_revisions, allowed_entrypoints=allowed_entrypoints)
