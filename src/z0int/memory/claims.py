"""Small scoped claim projections over the canonical EventLog.

This module stores source references and normalized claims in the existing
append-only ledger. It never mirrors a provider transcript or creates a second
memory store.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..context_resolve import ContextPacket, EvidenceRef, InformationNeed
from ..memory_contract import BitemporalClaim, EventIdentity, MemoryScope, MemorySnapshot, MemoryUseReceipt
from .event_log import EventLog, MemoryEvent

CLAIM_POLICY_REVISION = "scoped-claims-v2"
MAX_CLAIM_VALUE_BYTES = 1024
MAX_CLAIM_LABEL_CHARS = 160
MAX_LOCATOR_CHARS = 1024
MAX_CLAIM_RECORD_BYTES = 4096


@dataclass(frozen=True)
class NativeUserClaimAttestation:
    """Typed normalized claim approved by the trusted native-source adapter."""

    identity: EventIdentity
    scope: MemoryScope
    role: str
    subject: str
    predicate: str
    value: Any
    observed_at: str


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _scope_from(raw: Any) -> MemoryScope | None:
    if isinstance(raw, MemoryScope):
        return raw
    if not isinstance(raw, Mapping):
        return None
    try:
        return MemoryScope(**{key: raw.get(key) for key in ("user", "project", "repo", "task")})
    except (TypeError, ValueError):
        return None


def _stable_identity(raw: Any) -> EventIdentity | None:
    if isinstance(raw, EventIdentity):
        return raw
    if not isinstance(raw, Mapping):
        return None
    if not all(key in raw for key in ("event_uid", "source_system", "source_session", "payload_hash")):
        return None
    try:
        identity = EventIdentity(
            event_uid=str(raw["event_uid"]),
            source_system=str(raw["source_system"]),
            source_session=str(raw["source_session"]),
            payload_hash=str(raw["payload_hash"]),
            source_event_id=(str(raw["source_event_id"]) if raw.get("source_event_id") is not None else None),
            source_seq=(int(raw["source_seq"]) if raw.get("source_seq") is not None else None),
        )
        derived = EventIdentity.from_source(
            source_system=identity.source_system,
            source_session=identity.source_session,
            source_event_id=identity.source_event_id,
            source_seq=identity.source_seq,
            payload_hash=identity.payload_hash,
        )
    except (TypeError, ValueError):
        return None
    return identity if identity.event_uid == derived.event_uid else None


def _content_hash(content: Any) -> str:
    raw = content.encode("utf-8") if isinstance(content, str) else _canonical(content)
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _validate_native_source(
    *,
    identity: EventIdentity,
    locator: str,
    scope: MemoryScope,
    subject: str,
    predicate: str,
    value: Any,
    observed_at: str,
    source_reader: Callable[[str], Mapping[str, Any]],
) -> None:
    if not isinstance(locator, str) or not locator.strip() or len(locator) > MAX_LOCATOR_CHARS:
        raise ValueError("locator must be nonempty bounded text")
    if not callable(source_reader):
        raise ValueError("source_reader must be a trusted callable")
    try:
        source = source_reader(locator)
    except Exception as exc:  # noqa: BLE001 - source outages are admission failures
        raise ValueError("native source hydration failed") from exc
    if not isinstance(source, Mapping):
        raise ValueError("native source reader must return a source mapping")
    if str(source.get("role") or "").strip().casefold() != "user":
        raise ValueError("native source role must be user")

    native_identity_raw = source.get("identity")
    if native_identity_raw is None and "event_uid" in source:
        native_identity_raw = source
    native_identity = _stable_identity(native_identity_raw)
    if native_identity is None or native_identity.event_uid != identity.event_uid:
        raise ValueError("native source identity does not match the admitted identity")
    if native_identity.payload_hash != identity.payload_hash:
        raise ValueError("native source payload hash does not match the admitted identity")
    if (
        native_identity.source_system != identity.source_system
        or native_identity.source_session != identity.source_session
        or native_identity.source_event_id != identity.source_event_id
        or native_identity.source_seq != identity.source_seq
    ):
        raise ValueError("native source stable identity fields do not match")

    source_scope = _scope_from(source.get("scope"))
    if source_scope is None or source_scope != scope:
        raise ValueError("native source scope does not match the requested scope")

    has_content = "content" in source
    has_hash = isinstance(source.get("payload_hash"), str)
    if not has_content and not has_hash:
        raise ValueError("native source must provide content or a payload_hash")
    if has_content and _content_hash(source["content"]) != identity.payload_hash:
        raise ValueError("hydrated native source content hash does not match")
    if has_hash and source["payload_hash"] != identity.payload_hash:
        raise ValueError("native source payload_hash does not match")

    attestations = source.get("admitted_claims")
    matches = [fact for fact in attestations if (
        isinstance(fact, NativeUserClaimAttestation)
        and isinstance(fact.identity, EventIdentity)
        and isinstance(fact.scope, MemoryScope)
        and isinstance(fact.role, str)
        and fact.role.strip().casefold() == "user"
        and fact.identity.event_uid == identity.event_uid
        and fact.identity.payload_hash == identity.payload_hash
        and fact.identity.source_system == identity.source_system
        and fact.identity.source_session == identity.source_session
        and fact.identity.source_event_id == identity.source_event_id
        and fact.identity.source_seq == identity.source_seq
        and fact.scope == scope
        and fact.subject == subject
        and fact.predicate == predicate
        and _canonical(fact.value) == _canonical(value)
        and fact.observed_at == observed_at
    )] if isinstance(attestations, (list, tuple)) else []
    if not matches:
        raise ValueError("trusted source reader did not attest this exact normalized user claim")


def _claim_event_rows(events: list[MemoryEvent]) -> list[tuple[MemoryEvent, dict[str, Any], MemoryScope]]:
    rows: list[tuple[MemoryEvent, dict[str, Any], MemoryScope]] = []
    for event in events:
        if event.event_type != "memory.claim" or not isinstance(event.payload, dict):
            continue
        scope = _scope_from(event.payload.get("scope"))
        if (
            scope is None
            or scope.user is None
            or not isinstance(event.payload.get("claim_id"), str)
            or not event.payload["claim_id"].strip()
            or not isinstance(event.payload.get("subject"), str)
            or not event.payload["subject"].strip()
            or not isinstance(event.payload.get("predicate"), str)
            or not event.payload["predicate"].strip()
        ):
            continue
        rows.append((event, dict(event.payload), scope))
    return rows


def _claim_id(
    *,
    event_uid: str,
    scope: MemoryScope,
    subject: str,
    predicate: str,
    value: Any,
    observed_at: str,
    correction_of: str | None,
) -> str:
    identity = {
        "event_uid": event_uid,
        "scope": scope.path(),
        "subject": subject,
        "predicate": predicate,
        "value": value,
        "observed_at": observed_at,
        "correction_of": correction_of,
    }
    return "claim_" + hashlib.sha256(_canonical(identity)).hexdigest()[:32]


def _check_claim_size(claim: BitemporalClaim, locator: str | None = None) -> None:
    if len(claim.subject) > MAX_CLAIM_LABEL_CHARS or len(claim.predicate) > MAX_CLAIM_LABEL_CHARS:
        raise ValueError("claim subject and predicate must be bounded normalized labels")
    if len(_canonical(claim.value)) > MAX_CLAIM_VALUE_BYTES:
        raise ValueError("claim value exceeds the normalized value limit")
    if len(_canonical(claim.scope.to_dict())) > 512:
        raise ValueError("claim scope exceeds the normalized scope limit")
    if len(claim.claim_id) > 128 or len(claim.evidence_event_uids) > 16:
        raise ValueError("claim identity or evidence list exceeds the normalized limit")
    if len(claim.observed_at) > 64 or len(claim.recorded_at) > 64:
        raise ValueError("claim timestamps must be bounded")
    if len(_canonical(claim.to_dict())) > MAX_CLAIM_RECORD_BYTES:
        raise ValueError("claim record exceeds the normalized record limit")
    if locator is not None and len(locator) > MAX_LOCATOR_CHARS:
        raise ValueError("locator exceeds the source-reference limit")


def _validated_user_reference(
    claim_event: MemoryEvent,
    claim_payload: Mapping[str, Any],
    claim_scope: MemoryScope,
    event_by_id: Mapping[int, MemoryEvent],
) -> bool:
    evidence_uids = set(str(uid) for uid in claim_payload.get("evidence_event_uids") or ())
    for parent_id in claim_event.parent_event_ids:
        parent = event_by_id.get(parent_id)
        if parent is None or parent.event_type != "source.reference" or not isinstance(parent.payload, dict):
            continue
        identity = parent.event_identity()
        ref_scope = _scope_from(parent.payload.get("scope"))
        if (
            identity is not None
            and identity.source_system in {"codex", "claude", "claude-code", "hermes", "omp", "omo", "dsh", "agentsview"}
            and parent.payload.get("locator") == f"agentsview:{identity.source_session}#{identity.source_seq if identity.source_seq is not None else identity.source_event_id}"
            and identity.event_uid in evidence_uids
            and parent.payload.get("native_source_validated") is True
            and parent.payload.get("role") == "user"
            and ref_scope == claim_scope
        ):
            return True
    return False


def _effective_trust(
    claim_event: MemoryEvent,
    claim_payload: Mapping[str, Any],
    claim_scope: MemoryScope,
    event_by_id: Mapping[int, MemoryEvent],
) -> str:
    declared = str(claim_payload.get("origin_trust") or "unknown")
    if (
        declared == "explicit_user"
        and claim_payload.get("admission_method") == "native_user_claim_attestation.v1"
        and _validated_user_reference(claim_event, claim_payload, claim_scope, event_by_id)
    ):
        return "explicit_user"
    # A claim label is not verifier provenance. This slice has no validated
    # independent-verifier admission contract, so such declarations stay in
    # history but have the untrusted/model authority tier.
    return "model_assertion"


def _visible_source_events(
    row: Mapping[str, Any],
    claim_scope: MemoryScope,
    event_by_id: Mapping[int, MemoryEvent],
) -> list[tuple[str, MemoryEvent]]:
    found: dict[str, MemoryEvent] = {}
    claim_event = row.get("event")
    if not isinstance(claim_event, MemoryEvent):
        return []
    pending = [claim_event]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if current.event_id in seen or not isinstance(current.payload, dict):
            continue
        seen.add(current.event_id)
        expected_uids = set(str(uid) for uid in current.payload.get("evidence_event_uids") or ())
        for parent_id in current.parent_event_ids:
            parent = event_by_id.get(parent_id)
            if parent is None or not isinstance(parent.payload, dict) or _scope_from(parent.payload.get("scope")) != claim_scope:
                continue
            if parent.event_type == "source.reference":
                identity = parent.event_identity()
                if identity is not None and identity.event_uid in expected_uids and parent.payload.get("locator"):
                    found.setdefault(identity.event_uid, parent)
            elif (
                parent.event_type == "memory.claim"
                and current.payload.get("correction_of") == parent.payload.get("claim_id")
                and current.payload.get("subject") == parent.payload.get("subject")
                and current.payload.get("predicate") == parent.payload.get("predicate")
                and _effective_trust(current, current.payload, claim_scope, event_by_id) == "explicit_user"
                and _effective_trust(parent, parent.payload, claim_scope, event_by_id) == "explicit_user"
            ):
                # A normalized correction can carry unchanged fields forward.
                # Its supporting evidence includes the validated base sources.
                pending.append(parent)
    return sorted(found.items())


def _trust_rank(trust: str) -> int:
    return {
        "model_assertion": 1,
        "explicit_user": 2,
        "independent_verification": 3,
    }.get(trust, 0)


def _reduce_scope_group(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str], list[str]]:
    # Event order is ledger order and cannot be changed by a caller-supplied
    # recorded_at timestamp. Trust outranks recency within one exact scope.
    winner = max(rows, key=lambda row: (_trust_rank(row["effective_trust"]), row["event_id"]))
    correction_sources = {
        str(row.get("correction_of"))
        for row in rows
        if row.get("effective_trust") == "explicit_user"
        and row.get("correction_of") in {
            candidate["claim_id"]
            for candidate in rows
            if candidate.get("effective_trust") == "explicit_user"
        }
    }
    contradictions: list[str] = []
    resolved_conflicts: list[str] = []
    for row in rows:
        if row["claim_id"] == winner["claim_id"]:
            continue
        row_rank = _trust_rank(row["effective_trust"])
        winner_rank = _trust_rank(winner["effective_trust"])
        if row.get("value") == winner.get("value"):
            continue
        if row["claim_id"] in correction_sources:
            continue
        conflict = (
            f"{winner['subject']} {winner['predicate']} has conflicting scoped claims "
            f"{winner['claim_id']}={json.dumps(winner.get('value'), ensure_ascii=False)} and "
            f"{row['claim_id']}={json.dumps(row.get('value'), ensure_ascii=False)}"
        )
        if winner["effective_trust"] == "explicit_user" and row_rank < winner_rank:
            resolved_conflicts.append(conflict)
        else:
            contradictions.append(conflict)
    return winner, contradictions, resolved_conflicts


def admit_user_claim(
    identity: EventIdentity,
    locator: str,
    scope: MemoryScope,
    subject: str,
    predicate: str,
    value: Any,
    observed_at: str,
    *,
    source_reader: Callable[[str], Mapping[str, Any]],
    correction_of: str | None = None,
    ledger_root: str | Path | None = None,
) -> MemoryEvent:
    """Admit one normalized user decision after hydrating its exact native source.

    The configured source reader is the integration trust boundary. Its
    response must contain the source-native role, stable identity, content or
    payload hash, exact scope, and a typed ``NativeUserClaimAttestation`` for
    this exact normalized claim. Those fields are checked here; the attestation
    object itself is data, not an authorization token. Transcript content is
    checked in memory and never copied into the ledger.
    """
    if not isinstance(identity, EventIdentity):
        raise TypeError("identity must be an EventIdentity")
    if not isinstance(scope, MemoryScope):
        raise TypeError("scope must be a MemoryScope")
    if scope.user is None:
        raise ValueError("user claims require at least a user-scoped MemoryScope")
    if not isinstance(observed_at, str) or not observed_at.strip() or len(observed_at) > 64:
        raise ValueError("observed_at must be bounded nonempty text")
    if correction_of is not None and (not isinstance(correction_of, str) or len(correction_of) > 128):
        raise ValueError("correction_of must be a bounded claim ID")
    subject = str(subject).strip()
    predicate = str(predicate).strip()
    if not subject or not predicate:
        raise ValueError("subject and predicate are required")
    _validate_native_source(
        identity=identity,
        locator=locator,
        scope=scope,
        subject=subject,
        predicate=predicate,
        value=value,
        observed_at=observed_at,
        source_reader=source_reader,
    )
    claim_id = _claim_id(
        event_uid=identity.event_uid,
        scope=scope,
        subject=subject,
        predicate=predicate,
        value=value,
        observed_at=observed_at,
        correction_of=correction_of,
    )
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    claim = BitemporalClaim(
        claim_id=claim_id,
        scope=scope,
        subject=subject,
        predicate=predicate,
        value=value,
        status="observed",
        observed_at=observed_at,
        recorded_at=now,
        evidence_event_uids=(identity.event_uid,),
        origin_trust="explicit_user",
        privacy_class="private",
    )
    _check_claim_size(claim, locator)

    log = EventLog(ledger_root)
    events = list(log.iter_events()) if log.events_path.is_file() else []
    by_id = {event.event_id: event for event in events}
    claim_rows = _claim_event_rows(events)
    previous: tuple[MemoryEvent, dict[str, Any], MemoryScope] | None = None
    if correction_of is not None:
        previous = next(((event, payload, row_scope) for event, payload, row_scope in claim_rows
                         if payload.get("claim_id") == correction_of), None)
        if previous is None:
            raise ValueError("correction_of must name an existing claim")
        prior_event, prior_payload, prior_scope = previous
        if (
            prior_scope != scope
            or prior_payload.get("subject") != subject
            or prior_payload.get("predicate") != predicate
            or _effective_trust(prior_event, prior_payload, prior_scope, by_id) != "explicit_user"
        ):
            raise ValueError("correction_of must name an explicit user claim for the same scoped subject/predicate")

    # Reject a second, differently normalized decision for the same source event
    # and claim key. A separate source message is required for a new decision.
    for event, prior_payload, prior_scope in claim_rows:
        if (
            prior_scope == scope
            and prior_payload.get("subject") == subject
            and prior_payload.get("predicate") == predicate
            and identity.event_uid in (prior_payload.get("evidence_event_uids") or ())
            and prior_payload.get("claim_id") != claim_id
        ):
            raise ValueError("the same native source event already supports another value for this claim")

    reference_payload = {
        "locator": locator,
        "scope": scope.to_dict(),
        "role": "user",
        "native_source_validated": True,
    }
    source_name = locator.split(":", 1)[0] if ":" in locator else identity.source_system
    reference = log._append_admitted_user_event(
        "source.reference",
        reference_payload,
        source=source_name,
        project=scope.project,
        session_id=identity.source_session,
        identity=identity,
    )

    existing = next((event for event, payload, _ in claim_rows if payload.get("claim_id") == claim_id), None)
    if existing is not None:
        return existing

    claim_payload = {
        **claim.to_dict(),
        "correction_of": correction_of,
        "admission_method": "native_user_claim_attestation.v1",
    }
    parents = [reference.event_id]
    if previous is not None:
        parents.append(previous[0].event_id)
    return log._append_admitted_user_event(
        "memory.claim",
        claim_payload,
        source="z0-memory",
        project=scope.project,
        parent_event_ids=parents,
    )


def record_claim(claim: BitemporalClaim, *, ledger_root: str | Path | None = None) -> MemoryEvent:
    """Append a bounded model/untrusted assertion with no caller-set authority."""
    if not isinstance(claim, BitemporalClaim):
        raise TypeError("claim must be a BitemporalClaim")
    _check_claim_size(claim)
    payload = claim.to_dict()
    payload["origin_trust"] = "model_assertion"
    return EventLog(ledger_root).append("memory.claim", payload, source="z0-memory")


def project_claims(
    request_scope: MemoryScope,
    *,
    subject: str | None = None,
    predicate: str | None = None,
    ledger_root: str | Path | None = None,
) -> ContextPacket:
    """Build a ContextPacket-compatible view from scope-visible ledger claims.

    Visibility filtering happens before claim reduction. The packet carries
    current claim IDs, source references, history/supersession links, conflicts,
    a content-addressed snapshot and the existing memory-use receipt contract.
    """
    if not isinstance(request_scope, MemoryScope):
        raise TypeError("request_scope must be a MemoryScope")
    started = time.perf_counter()
    log = EventLog(ledger_root, read_only=True)
    try:
        # Claim selection is a snapshot operation: a recoverable event-log
        # prefix is not sufficient evidence that the visible claims are current.
        events = list(log.iter_events(require_complete=True))
        ledger_status = "complete"
        gap: list[str] = []
    except Exception as exc:  # noqa: BLE001 - malformed canonical history is an explicit packet gap
        events = []
        ledger_status = "unavailable"
        gap = [f"memory EventLog unavailable ({type(exc).__name__})"]

    event_by_id = {event.event_id: event for event in events}
    visible: list[dict[str, Any]] = []
    for event, payload, claim_scope in _claim_event_rows(events):
        # Scope is deliberately tested before grouping, trust reduction or
        # contradiction generation. Out-of-scope rows leave no packet trace.
        if not claim_scope.is_visible_to(request_scope):
            continue
        if subject is not None and payload.get("subject") != subject:
            continue
        if predicate is not None and payload.get("predicate") != predicate:
            continue
        visible.append(
            {
                **payload,
                "claim_id": str(payload.get("claim_id") or f"event-{event.event_id}"),
                "event_id": event.event_id,
                "event_checksum": event.checksum,
                "scope_object": claim_scope,
                "effective_trust": _effective_trust(event, payload, claim_scope, event_by_id),
                "event": event,
            }
        )
    for row in visible:
        row["visible_source_events"] = _visible_source_events(row, row["scope_object"], event_by_id)

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in visible:
        scope_obj = row["scope_object"]
        key = (scope_obj.path(), row.get("subject"), row.get("predicate"))
        groups[key].append(row)

    winners: list[dict[str, Any]] = []
    contradiction_text: list[str] = []
    resolved_conflict_text: list[str] = []
    winner_by_group: dict[tuple[Any, ...], dict[str, Any]] = {}
    for key, rows in groups.items():
        winner, conflicts, resolved_conflicts = _reduce_scope_group(rows)
        winners.append(winner)
        winner_by_group[key] = winner
        contradiction_text.extend(conflicts)
        resolved_conflict_text.extend(resolved_conflicts)

    winner_event_ids = {row["event_id"] for row in winners}
    visible_by_claim_id = {row["claim_id"]: row for row in visible}
    correction_parent: dict[int, str] = {}
    for row in visible:
        prior = visible_by_claim_id.get(str(row.get("correction_of")))
        if (
            row.get("effective_trust") == "explicit_user"
            and prior is not None
            and prior["event_id"] != row["event_id"]
            and prior["effective_trust"] == "explicit_user"
            and prior["scope_object"] == row["scope_object"]
            and prior.get("subject") == row.get("subject")
            and prior.get("predicate") == row.get("predicate")
        ):
            correction_parent[prior["event_id"]] = row["claim_id"]
    history: list[dict[str, Any]] = []
    for row in sorted(visible, key=lambda item: item["event_id"]):
        superseded_by = correction_parent.get(row["event_id"])
        if superseded_by is None and row["event_id"] not in winner_event_ids:
            scope_obj = row["scope_object"]
            key = (scope_obj.path(), row.get("subject"), row.get("predicate"))
            superseded_by = winner_by_group[key]["claim_id"]
        history.append(
            {
                "claim_id": row["claim_id"],
                "subject": row.get("subject"),
                "predicate": row.get("predicate"),
                "value": row.get("value"),
                "status": row.get("status"),
                "observed_at": row.get("observed_at"),
                "recorded_at": row.get("recorded_at"),
                "scope": row.get("scope"),
                "origin_trust": row["effective_trust"],
                "declared_origin_trust": row.get("origin_trust"),
                "superseded_by": superseded_by,
                "current": row["event_id"] in winner_event_ids,
                "event_id": row["event_id"],
                "evidence_event_uids": [uid for uid, _ in row["visible_source_events"]],
            }
        )

    selected = sorted(winners, key=lambda row: (row["scope_object"].path(), row["subject"], row["predicate"]))
    included_claim_ids = tuple(row["claim_id"] for row in history)
    selected_claim_ids = [row["claim_id"] for row in selected]
    selected_claim_event_ids = [row["event_id"] for row in selected]

    evidence: list[EvidenceRef] = []
    evidence_uids: list[str] = []
    source_reference_event_ids: list[int] = []
    evidence_by_uid: dict[str, EvidenceRef] = {}
    included_source_event_ids: set[int] = set()
    for row in visible:
        for uid, parent in row["visible_source_events"]:
            identity = parent.event_identity()
            if identity is None:
                continue
            evidence_by_uid.setdefault(
                uid,
                EvidenceRef(
                    source_id=str(parent.payload["locator"]),
                    source_version=identity.payload_hash,
                    locator=str(parent.payload["locator"]),
                    trust_class="conversation",
                    observed_at=str(row.get("observed_at") or ""),
                    note=f"source event {identity.event_uid}; ledger event {parent.event_id}",
                ),
            )
            included_source_event_ids.add(parent.event_id)
    for uid, ref in sorted(evidence_by_uid.items()):
        evidence.append(ref)
        evidence_uids.append(uid)
    source_reference_event_ids = sorted(included_source_event_ids)

    all_claim_ids_for_snapshot = tuple(included_claim_ids)
    scoped_revision_input = {
        "scope": request_scope.path(),
        "claims": sorted(
            (row["event_id"], row["event_checksum"])
            for row in visible
        ),
        "source_references": sorted(
            (event_id, event_by_id[event_id].checksum)
            for event_id in included_source_event_ids
            if event_id in event_by_id
        ),
    }
    scoped_source_revision = "sha256:" + hashlib.sha256(_canonical(scoped_revision_input)).hexdigest()
    snapshot = MemorySnapshot.build(
        scope=request_scope,
        state_revision=CLAIM_POLICY_REVISION,
        source_revisions={"eventlog.scoped_claims": scoped_source_revision},
        claim_ids=all_claim_ids_for_snapshot,
        evidence_event_uids=tuple(evidence_uids),
        policy_revision=CLAIM_POLICY_REVISION,
    )
    receipt = MemoryUseReceipt(
        snapshot_id=snapshot.snapshot_id,
        capability_ids=("eventlog.scoped_claims",),
        query_ids=(f"scope:{request_scope.fingerprint()}",),
        included_claim_ids=included_claim_ids,
        evidence_event_uids=tuple(evidence_uids),
        retrieval_latency_ms=round((time.perf_counter() - started) * 1000, 2),
        raw_source_reads=0,
        tainted_evidence=any(row["effective_trust"] == "model_assertion" for row in visible),
    )

    if not selected and not gap:
        gap.append("no claims are visible at the requested scope")
    packet = ContextPacket(
        task_id=request_scope.task,
        needs=[InformationNeed(id="memory", description="scope-visible memory claims", kind="memory")],
        evidence=evidence,
        contradictions=sorted(set(contradiction_text)),
        unresolved_gaps=gap,
        measurements={
            "capability": "eventlog.scoped_claims",
            "coverage": ledger_status,
            "scope": request_scope.to_dict(),
            "source_revision": scoped_source_revision,
            "selected_claim_ids": selected_claim_ids,
            "selected_claim_event_ids": selected_claim_event_ids,
            "selected_claims": [
                {
                    "claim_id": row["claim_id"],
                    "event_id": row["event_id"],
                    "subject": row.get("subject"),
                    "predicate": row.get("predicate"),
                    "value": row.get("value"),
                    "scope": row.get("scope"),
                    "origin_trust": row["effective_trust"],
                    "declared_origin_trust": row.get("origin_trust"),
                    "evidence_event_uids": [uid for uid, _ in row["visible_source_events"]],
                }
                for row in selected
            ],
            "claim_history": history,
            "resolved_lower_trust_conflicts": sorted(set(resolved_conflict_text)),
            "included_claim_event_ids": [row["event_id"] for row in visible],
            "source_reference_event_ids": source_reference_event_ids,
            "memory_snapshot": snapshot.to_dict(),
            "memory_snapshot_id": snapshot.snapshot_id,
            "memory_use_receipt": receipt.to_dict(),
        },
    )
    return packet
