"""Capture-only view over the existing EventLog and scoped claim projection.

This deliberately does not port the legacy automatic brief, lexical/semantic
ranking, disk cache, or host wiring. Those are independent read-path owners.
Unbound, global, sibling and unknown scopes fail before source identity or
payload examination. Generic source references have no user-attestation mark
and cannot turn an injected brief into a user decision.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..memory_contract import EventIdentity, MemoryScope
from .claims import project_claims
from .event_log import EventLog, MemoryEvent, SourceIngestDisabled, source_ingest_enabled


def _workstream(scope: MemoryScope) -> MemoryScope:
    if not isinstance(scope, MemoryScope) or any(
        not isinstance(value, str) or not value.strip()
        for value in (scope.user, scope.project, scope.repo, scope.task)
    ):
        raise ValueError("a fully bound user/project/repo/task scope is required")
    return scope


def worker_ledger(root: str | Path | None = None) -> EventLog:
    """Return the canonical ledger read-only, without initializing a directory."""
    return EventLog(root, read_only=True)


def ingest_reference(
    identity: EventIdentity,
    locator: str,
    *,
    scope: MemoryScope,
    authorized_scope: MemoryScope,
    ledger_root: str | Path | None = None,
) -> MemoryEvent:
    """Write a scoped, non-attesting original-source pointer, not transcript text.

    The trusted caller supplies its independently bound workstream. This
    generic reference is *not* the explicit-user decision admission path;
    use the native adapter and claims.admit_user_claim for that.
    """
    bound = _workstream(authorized_scope)
    if scope != bound:
        raise ValueError("source scope does not match authorized workstream")
    if not source_ingest_enabled():
        raise SourceIngestDisabled("source reference capture is disabled")
    if not isinstance(identity, EventIdentity):
        raise TypeError("native identity must be an EventIdentity")
    if not isinstance(locator, str) or not locator.strip() or len(locator) > 1024:
        raise ValueError("native locator must be nonempty and bounded")
    payload: dict[str, Any] = {"locator": locator, "scope": bound.to_dict()}
    source = locator.split(":", 1)[0] if ":" in locator else identity.source_system
    return EventLog(ledger_root).append(
        "source.reference", payload, source=source,
        project=bound.project, session_id=identity.source_session, identity=identity,
    )


def claim_history(
    subject: str,
    predicate: str | None = None,
    *,
    scope: MemoryScope,
    ledger_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Read admitted versions in this workstream; never hydrate native bodies."""
    bound = _workstream(scope)
    if not isinstance(subject, str) or not subject.strip():
        raise ValueError("a claim subject is required")
    packet = project_claims(bound, subject=subject, predicate=predicate, ledger_root=ledger_root)
    return list(packet.measurements.get("claim_history", []))
