"""Versioned contracts for z0's lifelong memory control plane.

This module deliberately does not implement a memory database, temporal tree,
retriever, or state reducer. It defines the stable identities and provenance
objects those components can share.

Core invariant::

    evidence != belief/state != authority != action != outcome

Persisted memory is data. It never acquires instruction authority merely by
being retained, summarized, or retrieved.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Literal

SCHEMA = "z0int.memory_contract.v1"

ClaimStatus = Literal[
    "unknown",
    "provisional",
    "observed",
    "verified",
    "contradicted",
    "stale",
]
PrivacyClass = Literal["public", "personal", "private", "secret"]

_ALLOWED_CLAIM_STATUS = {
    "unknown",
    "provisional",
    "observed",
    "verified",
    "contradicted",
    "stale",
}
_ALLOWED_PRIVACY = {"public", "personal", "private", "secret"}


def _canonical_json(value: Any) -> str:
    """Canonical JSON used only for identity/fingerprint construction."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(prefix: str, value: Any, *, n: int = 32) -> str:
    payload = f"{prefix}\0{_canonical_json(value)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:n]


def derive_event_uid(
    *,
    source_system: str,
    source_session: str,
    source_event_id: str | None = None,
    source_seq: int | None = None,
) -> str:
    """Return replay-stable event identity independent of local ledger position.

    Prefer a source-native event ID. When a source has no durable message ID,
    its stable source sequence is the fallback. Payload bytes are intentionally
    *not* part of identity: `payload_hash` detects source mutation/conflict
    without silently minting a second identity.
    """
    source_system = str(source_system).strip()
    source_session = str(source_session).strip()
    event_id = None if source_event_id is None else str(source_event_id).strip()
    if not source_system:
        raise ValueError("source_system is required")
    if not source_session:
        raise ValueError("source_session is required")
    if not event_id and source_seq is None:
        raise ValueError("source_event_id or source_seq is required")
    if source_seq is not None and source_seq < 0:
        raise ValueError("source_seq must be >= 0")

    native_key: dict[str, Any]
    if event_id:
        native_key = {"source_event_id": event_id}
    else:
        native_key = {"source_seq": int(source_seq)}  # type: ignore[arg-type]
    digest = _digest(
        "event_uid.v1",
        {
            "source_system": source_system,
            "source_session": source_session,
            **native_key,
        },
    )
    return f"evt_{digest}"


@dataclass(frozen=True)
class EventIdentity:
    """Canonical identity for an event imported/appended into the z0 ledger.

    `ledger_seq` is local ordering only. It must never participate in
    `event_uid` because the same source event may land at a different local
    position after replay, restore, or migration.
    """

    event_uid: str
    source_system: str
    source_session: str
    payload_hash: str
    source_event_id: str | None = None
    source_seq: int | None = None
    ledger_seq: int | None = None
    schema: str = SCHEMA

    def __post_init__(self) -> None:
        if not self.event_uid:
            raise ValueError("event_uid is required")
        if not self.source_system.strip():
            raise ValueError("source_system is required")
        if not self.source_session.strip():
            raise ValueError("source_session is required")
        if not self.payload_hash.strip():
            raise ValueError("payload_hash is required")
        if not (self.source_event_id and self.source_event_id.strip()) and self.source_seq is None:
            raise ValueError("source_event_id or source_seq is required")
        if self.source_seq is not None and self.source_seq < 0:
            raise ValueError("source_seq must be >= 0")
        if self.ledger_seq is not None and self.ledger_seq < 0:
            raise ValueError("ledger_seq must be >= 0")

    @classmethod
    def from_source(
        cls,
        *,
        source_system: str,
        source_session: str,
        payload_hash: str,
        source_event_id: str | None = None,
        source_seq: int | None = None,
        ledger_seq: int | None = None,
    ) -> "EventIdentity":
        return cls(
            event_uid=derive_event_uid(
                source_system=source_system,
                source_session=source_session,
                source_event_id=source_event_id,
                source_seq=source_seq,
            ),
            source_system=source_system,
            source_session=source_session,
            source_event_id=source_event_id,
            source_seq=source_seq,
            payload_hash=payload_hash,
            ledger_seq=ledger_seq,
        )

    def same_source_event(self, other: "EventIdentity") -> bool:
        return self.event_uid == other.event_uid

    def is_exact_duplicate(self, other: "EventIdentity") -> bool:
        return self.event_uid == other.event_uid and self.payload_hash == other.payload_hash

    def has_payload_conflict(self, other: "EventIdentity") -> bool:
        return self.event_uid == other.event_uid and self.payload_hash != other.payload_hash

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass(frozen=True)
class MemoryScope:
    """Hierarchical visibility boundary: global -> user -> project -> repo -> task."""

    user: str | None = None
    project: str | None = None
    repo: str | None = None
    task: str | None = None

    def __post_init__(self) -> None:
        values = (self.user, self.project, self.repo, self.task)
        seen_gap = False
        for value in values:
            if value is None:
                seen_gap = True
                continue
            if not str(value).strip():
                raise ValueError("scope segments must be non-empty when present")
            if seen_gap:
                raise ValueError("memory scope must be a contiguous hierarchy")

    @property
    def level(self) -> Literal["global", "user", "project", "repo", "task"]:
        if self.task is not None:
            return "task"
        if self.repo is not None:
            return "repo"
        if self.project is not None:
            return "project"
        if self.user is not None:
            return "user"
        return "global"

    def path(self) -> tuple[str, ...]:
        return tuple(str(x) for x in (self.user, self.project, self.repo, self.task) if x is not None)

    def fingerprint(self) -> str:
        return f"scope_{_digest('memory_scope.v1', self.path(), n=24)}"

    def is_visible_to(self, request_scope: "MemoryScope") -> bool:
        """Whether memory at this scope is eligible for a request scope.

        Filtering should happen before semantic ranking. Ancestor memory is
        visible to a descendant; sibling or child memory is not.
        """
        mine = self.path()
        theirs = request_scope.path()
        return len(mine) <= len(theirs) and theirs[: len(mine)] == mine

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            **{k: v for k, v in asdict(self).items() if v is not None},
        }


@dataclass(frozen=True)
class BitemporalClaim:
    """Evidence-derived belief with separate world-validity and recording time."""

    claim_id: str
    scope: MemoryScope
    subject: str
    predicate: str
    value: Any
    status: ClaimStatus
    observed_at: str
    recorded_at: str
    valid_from: str | None = None
    valid_to: str | None = None
    superseded_by: str | None = None
    evidence_event_uids: tuple[str, ...] = ()
    confidence: float | None = None
    origin_trust: str = "unknown"
    privacy_class: PrivacyClass = "private"
    derived_from_untrusted: bool = False
    instruction_capability: bool = False
    schema: str = SCHEMA

    def __post_init__(self) -> None:
        if not self.claim_id.strip():
            raise ValueError("claim_id is required")
        if not self.subject.strip():
            raise ValueError("subject is required")
        if not self.predicate.strip():
            raise ValueError("predicate is required")
        if self.status not in _ALLOWED_CLAIM_STATUS:
            raise ValueError(f"unsupported claim status {self.status!r}")
        if self.privacy_class not in _ALLOWED_PRIVACY:
            raise ValueError(f"unsupported privacy class {self.privacy_class!r}")
        if not self.observed_at.strip():
            raise ValueError("observed_at is required")
        if not self.recorded_at.strip():
            raise ValueError("recorded_at is required")
        if self.status == "verified" and not self.evidence_event_uids:
            raise ValueError("verified claims require evidence_event_uids")
        if self.confidence is not None and not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        if self.superseded_by == self.claim_id:
            raise ValueError("claim cannot supersede itself")
        if self.instruction_capability:
            raise ValueError("persisted memory is data and cannot carry instruction authority")
        _canonical_json(self.value)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["scope"] = self.scope.to_dict()
        out["evidence_event_uids"] = list(self.evidence_event_uids)
        return {k: v for k, v in out.items() if v is not None}


@dataclass(frozen=True)
class MemorySnapshot:
    """Content-addressed decision-time view of memory/state inputs."""

    snapshot_id: str
    scope: MemoryScope
    state_revision: str
    source_revisions: tuple[tuple[str, str], ...]
    claim_ids: tuple[str, ...] = ()
    evidence_event_uids: tuple[str, ...] = ()
    policy_revision: str | None = None
    ontology_revision: str | None = None
    created_at: str | None = None
    schema: str = SCHEMA

    @classmethod
    def build(
        cls,
        *,
        scope: MemoryScope,
        state_revision: str,
        source_revisions: dict[str, str] | tuple[tuple[str, str], ...],
        claim_ids: tuple[str, ...] | list[str] = (),
        evidence_event_uids: tuple[str, ...] | list[str] = (),
        policy_revision: str | None = None,
        ontology_revision: str | None = None,
        created_at: str | None = None,
    ) -> "MemorySnapshot":
        if not str(state_revision).strip():
            raise ValueError("state_revision is required")
        if isinstance(source_revisions, dict):
            revisions = tuple(sorted((str(k), str(v)) for k, v in source_revisions.items()))
        else:
            revisions = tuple(sorted((str(k), str(v)) for k, v in source_revisions))
        claims = tuple(sorted(set(str(x) for x in claim_ids)))
        evidence = tuple(sorted(set(str(x) for x in evidence_event_uids)))
        identity = {
            "scope": scope.path(),
            "state_revision": str(state_revision),
            "source_revisions": revisions,
            "claim_ids": claims,
            "evidence_event_uids": evidence,
            "policy_revision": policy_revision,
            "ontology_revision": ontology_revision,
        }
        snapshot_id = f"mem_{_digest('memory_snapshot.v1', identity)}"
        return cls(
            snapshot_id=snapshot_id,
            scope=scope,
            state_revision=str(state_revision),
            source_revisions=revisions,
            claim_ids=claims,
            evidence_event_uids=evidence,
            policy_revision=policy_revision,
            ontology_revision=ontology_revision,
            created_at=created_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "snapshot_id": self.snapshot_id,
            "scope": self.scope.to_dict(),
            "state_revision": self.state_revision,
            "source_revisions": dict(self.source_revisions),
            "claim_ids": list(self.claim_ids),
            "evidence_event_uids": list(self.evidence_event_uids),
            **({"policy_revision": self.policy_revision} if self.policy_revision is not None else {}),
            **({"ontology_revision": self.ontology_revision} if self.ontology_revision is not None else {}),
            **({"created_at": self.created_at} if self.created_at is not None else {}),
        }


@dataclass(frozen=True)
class MemoryUseReceipt:
    """Memory-specific measurements attached to existing DecisionReceipt.extra."""

    snapshot_id: str
    capability_ids: tuple[str, ...] = ()
    query_ids: tuple[str, ...] = ()
    included_claim_ids: tuple[str, ...] = ()
    excluded_claim_ids: tuple[str, ...] = ()
    evidence_event_uids: tuple[str, ...] = ()
    retrieval_latency_ms: float | None = None
    input_tokens: int | None = None
    raw_source_reads: int | None = None
    tainted_evidence: bool = False
    instruction_capability: bool = False
    schema: str = SCHEMA

    def __post_init__(self) -> None:
        if not self.snapshot_id.strip():
            raise ValueError("snapshot_id is required")
        if self.instruction_capability:
            raise ValueError("memory use is evidence and cannot carry instruction authority")
        if self.retrieval_latency_ms is not None and self.retrieval_latency_ms < 0:
            raise ValueError("retrieval_latency_ms must be >= 0")
        if self.input_tokens is not None and self.input_tokens < 0:
            raise ValueError("input_tokens must be >= 0")
        if self.raw_source_reads is not None and self.raw_source_reads < 0:
            raise ValueError("raw_source_reads must be >= 0")

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "schema": self.schema,
            "snapshot_id": self.snapshot_id,
            "capability_ids": list(self.capability_ids),
            "query_ids": list(self.query_ids),
            "included_claim_ids": list(self.included_claim_ids),
            "excluded_claim_ids": list(self.excluded_claim_ids),
            "evidence_event_uids": list(self.evidence_event_uids),
            "tainted_evidence": self.tainted_evidence,
        }
        if self.retrieval_latency_ms is not None:
            out["retrieval_latency_ms"] = self.retrieval_latency_ms
        if self.input_tokens is not None:
            out["input_tokens"] = self.input_tokens
        if self.raw_source_reads is not None:
            out["raw_source_reads"] = self.raw_source_reads
        return out

    def to_decision_extra(self) -> dict[str, Any]:
        """Projection for DecisionReceipt.extra; no second receipt schema."""
        return {"memory": self.to_dict()}

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "MemoryUseReceipt":
        """Inverse of ``to_dict`` (DecisionReceipt.extra.memory, opportunity_record.memory); validates on the way in."""
        tuples = ("capability_ids", "query_ids", "included_claim_ids", "excluded_claim_ids", "evidence_event_uids")
        known = {f for f in cls.__dataclass_fields__ if f != "schema"}
        return cls(**{k: tuple(v) if k in tuples else v for k, v in row.items() if k in known})
