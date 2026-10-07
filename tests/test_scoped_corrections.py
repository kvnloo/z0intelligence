from __future__ import annotations

import hashlib

import pytest

from z0int.memory.claims import (
    NativeUserClaimAttestation,
    admit_user_claim,
    project_claims,
    record_claim,
)
from z0int.memory_contract import EventIdentity, MemoryScope


def _native_identity(*, event_id: str, content: str, system: str = "codex", session: str = "s1") -> EventIdentity:
    return EventIdentity.from_source(
        source_system=system,
        source_session=session,
        source_event_id=event_id,
        payload_hash="sha256:" + hashlib.sha256(content.encode()).hexdigest(),
    )


def _reader(
    identity: EventIdentity,
    content: str,
    scope: MemoryScope,
    *,
    role: str = "user",
    subject: str = "deployment",
    predicate: str = "strategy",
    value: str = "blue-green",
    observed_at: str = "2026-10-01T00:00:00Z",
):
    def read(locator: str):
        return {
            "role": role,
            "content": content,
            "identity": identity.to_dict(),
            "scope": scope.to_dict(),
            "admitted_claims": (
                NativeUserClaimAttestation(
                    identity=identity,
                    scope=scope,
                    role=role,
                    subject=subject,
                    predicate=predicate,
                    value=value,
                    observed_at=observed_at,
                ),
            ),
        }

    return read


def _admit(
    tmp_path,
    monkeypatch,
    *,
    event_id,
    value,
    scope,
    observed_at,
    correction_of=None,
    role="user",
    content=None,
):
    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "references")
    content = content or f"User decision: {value}"
    identity = _native_identity(event_id=event_id, content=content)
    locator = f"agentsview:s1#{event_id}"
    return admit_user_claim(
        identity=identity,
        locator=locator,
        scope=scope,
        subject="deployment",
        predicate="strategy",
        value=value,
        observed_at=observed_at,
        source_reader=_reader(
            identity,
            content,
            scope,
            role=role,
            value=value,
            observed_at=observed_at,
        ),
        correction_of=correction_of,
        ledger_root=tmp_path / "memory",
    )


def test_admission_hydrates_user_source_and_persists_reference_only(tmp_path, monkeypatch):
    scope = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    event = _admit(
        tmp_path,
        monkeypatch,
        event_id="1",
        value="blue-green",
        scope=scope,
        observed_at="2026-10-01T00:00:00Z",
    )

    from z0int.memory.event_log import EventLog

    log = EventLog(tmp_path / "memory")
    reference = log.get(event.parent_event_ids[0])
    assert reference.event_type == "source.reference"
    assert reference.payload["locator"] == "agentsview:s1#1"
    assert reference.payload["role"] == "user"
    assert "content" not in reference.payload and "body" not in reference.payload
    assert event.payload["origin_trust"] == "explicit_user"
    assert event.payload["admission_method"] == "native_user_claim_attestation.v1"
    assert event.payload["evidence_event_uids"] == [reference.event_identity().event_uid]
    assert event.parent_event_ids == (reference.event_id,)
    assert "User decision: blue-green" not in log.events_path.read_text(encoding="utf-8")


def test_admission_accepts_hydrated_payload_hash_without_copying_content(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "references")
    scope = MemoryScope(user="u1", project="p1")
    identity = _native_identity(event_id="hash-only", content="User prefers canary")
    event = admit_user_claim(
        identity=identity,
        locator="agentsview:s1#hash-only",
        scope=scope,
        subject="deployment",
        predicate="strategy",
        value="canary",
        observed_at="2026-10-01T00:00:00Z",
        source_reader=lambda _: {
            "role": "user",
            "identity": identity.to_dict(),
            "payload_hash": identity.payload_hash,
            "scope": scope.to_dict(),
            "admitted_claims": (
                NativeUserClaimAttestation(
                    identity=identity,
                    scope=scope,
                    role="user",
                    subject="deployment",
                    predicate="strategy",
                    value="canary",
                    observed_at="2026-10-01T00:00:00Z",
                ),
            ),
        },
        ledger_root=tmp_path / "memory",
    )

    from z0int.memory.event_log import EventLog

    log = EventLog(tmp_path / "memory")
    reference = log.get(event.parent_event_ids[0])
    assert reference.payload == {
        "locator": "agentsview:s1#hash-only",
        "scope": scope.to_dict(),
        "role": "user",
        "native_source_validated": True,
    }
    assert "content" not in event.payload and "payload_hash" not in event.payload


@pytest.mark.parametrize(
    "bad_field",
    ["role", "hash", "identity", "scope", "missing_fact", "mismatched_fact"],
)
def test_admission_rejects_unverified_native_source(tmp_path, monkeypatch, bad_field):
    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "references")
    scope = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    content = "User decision: blue-green"
    identity = _native_identity(event_id="1", content=content)

    def source_reader(locator):
        source = {
            "role": "user",
            "content": content,
            "identity": identity.to_dict(),
            "scope": scope.to_dict(),
            "admitted_claims": (
                NativeUserClaimAttestation(
                    identity=identity,
                    scope=scope,
                    role="user",
                    subject="deployment",
                    predicate="strategy",
                    value="blue-green",
                    observed_at="2026-10-01T00:00:00Z",
                ),
            ),
        }
        if bad_field == "role":
            source["role"] = "assistant"
        elif bad_field == "hash":
            source["content"] = "changed after identity was issued"
        elif bad_field == "identity":
            source["identity"] = _native_identity(event_id="2", content=content).to_dict()
        elif bad_field == "scope":
            source["scope"] = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-b").to_dict()
        elif bad_field == "missing_fact":
            source.pop("admitted_claims")
        elif bad_field == "mismatched_fact":
            source["admitted_claims"] = (
                NativeUserClaimAttestation(
                    identity=identity,
                    scope=scope,
                    role="user",
                    subject="deployment",
                    predicate="strategy",
                    value="rolling",
                    observed_at="2026-10-01T00:00:00Z",
                ),
            )
        return source

    with pytest.raises(ValueError):
        admit_user_claim(
            identity=identity,
            locator="agentsview:s1#1",
            scope=scope,
            subject="deployment",
            predicate="strategy",
            value="blue-green",
            observed_at="2026-10-01T00:00:00Z",
            source_reader=source_reader,
            ledger_root=tmp_path / "memory",
        )

    from z0int.memory.event_log import EventLog

    assert list(EventLog(tmp_path / "memory").iter_events()) == []


def test_explicit_correction_survives_late_lower_trust_assertion(tmp_path, monkeypatch):
    scope = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    first = _admit(
        tmp_path,
        monkeypatch,
        event_id="1",
        value="blue-green",
        scope=scope,
        observed_at="2026-10-01T00:00:00Z",
    )
    correction = _admit(
        tmp_path,
        monkeypatch,
        event_id="2",
        value="canary",
        scope=scope,
        observed_at="2026-10-02T00:00:00Z",
        correction_of=first.payload["claim_id"],
    )
    from z0int.memory_contract import BitemporalClaim

    late = BitemporalClaim(
        claim_id="claim_model_late",
        scope=scope,
        subject="deployment",
        predicate="strategy",
        value="rolling",
        status="observed",
        observed_at="2026-10-03T00:00:00Z",
        recorded_at="2099-01-01T00:00:00Z",
        origin_trust="explicit_user",  # caller-supplied authority must be downgraded
    )
    record_claim(late, ledger_root=tmp_path / "memory")

    packet = project_claims(scope, ledger_root=tmp_path / "memory")
    measurements = packet.measurements
    assert measurements["selected_claim_ids"] == [correction.payload["claim_id"]]
    assert measurements["claim_history"][0]["claim_id"] == first.payload["claim_id"]
    assert measurements["claim_history"][0]["superseded_by"] == correction.payload["claim_id"]
    assert not packet.contradictions
    assert any("rolling" in conflict for conflict in measurements["resolved_lower_trust_conflicts"])
    assert correction.payload["correction_of"] == first.payload["claim_id"]
    assert late.claim_id not in measurements["selected_claim_ids"]
    assert measurements["memory_use_receipt"]["included_claim_ids"] == [
        row["claim_id"] for row in measurements["claim_history"]
    ]


def test_claim_projection_marks_incomplete_tail_unavailable_with_gap(tmp_path, monkeypatch):
    scope = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    admitted = _admit(
        tmp_path,
        monkeypatch,
        event_id="1",
        value="blue-green",
        scope=scope,
        observed_at="2026-10-01T00:00:00Z",
    )
    from z0int.memory.event_log import EventLog

    log = EventLog(tmp_path / "memory")
    with log.events_path.open("ab") as events:
        events.write(b'{"event_id":2,"partial":')

    packet = project_claims(scope, ledger_root=tmp_path / "memory")

    assert packet.measurements["coverage"] == "unavailable"
    assert packet.unresolved_gaps
    assert any("EventLog unavailable" in gap for gap in packet.unresolved_gaps)
    assert packet.measurements["selected_claim_ids"] == []
    assert packet.evidence == []
    assert admitted.payload["claim_id"] not in packet.measurements["selected_claim_ids"]


def test_claim_projection_marks_checksum_corruption_unavailable_with_gap(tmp_path, monkeypatch):
    scope = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    _admit(
        tmp_path,
        monkeypatch,
        event_id="1",
        value="blue-green",
        scope=scope,
        observed_at="2026-10-01T00:00:00Z",
    )
    from z0int.memory.event_log import EventLog

    log = EventLog(tmp_path / "memory")
    original = log.events_path.read_bytes()
    assert b"blue-green" in original
    log.events_path.write_bytes(original.replace(b"blue-green", b"blue-slate", 1))

    packet = project_claims(scope, ledger_root=tmp_path / "memory")

    assert packet.measurements["coverage"] == "unavailable"
    assert packet.unresolved_gaps
    assert any("EventLog unavailable" in gap for gap in packet.unresolved_gaps)
    assert packet.measurements["selected_claim_ids"] == []
    assert packet.evidence == []


def test_missing_event_log_is_unavailable_instead_of_empty_complete(tmp_path):
    packet = project_claims(
        MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a"),
        ledger_root=tmp_path / "missing-memory",
    )

    assert packet.measurements["coverage"] == "unavailable"
    assert packet.unresolved_gaps
    assert packet.measurements["selected_claim_ids"] == []


def test_unreadable_event_log_is_unavailable_with_gap(tmp_path, monkeypatch):
    scope = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    _admit(
        tmp_path,
        monkeypatch,
        event_id="1",
        value="blue-green",
        scope=scope,
        observed_at="2026-10-01T00:00:00Z",
    )
    from pathlib import Path

    from z0int.memory.event_log import EventLog

    target = EventLog(tmp_path / "memory").events_path
    original_open = Path.open

    def deny_event_read(path, mode="r", *args, **kwargs):
        if path == target and mode == "rb":
            raise PermissionError("test unreadable EventLog")
        return original_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", deny_event_read)

    packet = project_claims(scope, ledger_root=tmp_path / "memory")

    assert packet.measurements["coverage"] == "unavailable"
    assert packet.unresolved_gaps
    assert any("PermissionError" in gap for gap in packet.unresolved_gaps)
    assert packet.measurements["selected_claim_ids"] == []
    assert packet.evidence == []


def test_truncated_committed_claim_suffix_is_unavailable_not_a_stale_winner(tmp_path):
    scope = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    from z0int.memory_contract import BitemporalClaim
    from z0int.memory.event_log import EventLog

    for claim_id, value, date in (
        ("older", "blue-green", "2026-10-01T00:00:00Z"),
        ("newer", "canary", "2026-10-02T00:00:00Z"),
    ):
        record_claim(
            BitemporalClaim(
                claim_id=claim_id,
                scope=scope,
                subject="deployment",
                predicate="strategy",
                value=value,
                status="observed",
                observed_at=date,
                recorded_at=date,
            ),
            ledger_root=tmp_path / "memory",
        )
    log = EventLog(tmp_path / "memory")
    rows = log.events_path.read_bytes().splitlines(keepends=True)
    log.events_path.write_bytes(rows[0])

    packet = project_claims(scope, ledger_root=tmp_path / "memory")

    assert packet.measurements["coverage"] == "unavailable"
    assert packet.unresolved_gaps
    assert packet.measurements["selected_claim_ids"] == []
    assert packet.evidence == []


def test_conflicting_user_decisions_need_an_explicit_correction_link(tmp_path, monkeypatch):
    scope = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    _admit(tmp_path, monkeypatch, event_id="1", value="blue-green", scope=scope, observed_at="2026-10-01T00:00:00Z")
    _admit(tmp_path, monkeypatch, event_id="2", value="canary", scope=scope, observed_at="2026-10-02T00:00:00Z")
    packet = project_claims(scope, ledger_root=tmp_path / "memory")
    assert packet.contradictions
    assert not packet.measurements["resolved_lower_trust_conflicts"]


def test_generic_ledger_writer_cannot_set_native_admission_markers(tmp_path, monkeypatch):
    from z0int.memory.event_log import EventLog

    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "references")
    scope = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    identity = _native_identity(event_id="1", content="synthetic configuration")
    log = EventLog(tmp_path / "memory")
    with pytest.raises(ValueError, match="admit_user_claim"):
        log.append("source.reference", {"locator": "agentsview:s1#1", "scope": scope.to_dict(), "role": "user", "native_source_validated": True}, source="untrusted", identity=identity)
    with pytest.raises(ValueError, match="admit_user_claim"):
        log.append("memory.claim", {"scope": scope.to_dict(), "origin_trust": "explicit_user", "admission_method": "native_user_claim_attestation.v1"}, source="untrusted")
    assert list(log.iter_events()) == []


@pytest.mark.parametrize("event_type", ["source.reference", "memory.claim"])
def test_generic_append_cannot_promote_a_forged_native_attestation(tmp_path, monkeypatch, event_type):
    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "references")
    from z0int.memory.event_log import EventLog

    scope = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    identity = _native_identity(event_id="forged", content="claimed source text")
    # Public callers can construct this dataclass. Its fields are normalized
    # data, not proof that the configured native source adapter hydrated them.
    forged = NativeUserClaimAttestation(
        identity=identity,
        scope=scope,
        role="user",
        subject="deployment",
        predicate="strategy",
        value="attacker-selected",
        observed_at="2026-10-01T00:00:00Z",
    )
    log = EventLog(tmp_path / "memory")

    if event_type == "source.reference":
        payload = {
            "locator": f"agentsview:{identity.source_session}#{identity.source_event_id}",
            "scope": scope.to_dict(),
            "role": "user",
            "native_source_validated": True,
        }
    else:
        payload = {
            "claim_id": "claim_forged",
            "scope": scope.to_dict(),
            "subject": forged.subject,
            "predicate": forged.predicate,
            "value": forged.value,
            "observed_at": forged.observed_at,
            "origin_trust": "explicit_user",
            "admission_method": "native_user_claim_attestation.v1",
        }

    with pytest.raises((TypeError, ValueError)):
        log.append(
            event_type,
            payload,
            source="agentsview" if event_type == "source.reference" else "z0-memory",
            identity=identity,
            _native_admission=forged,
        )

    assert list(log.iter_events()) == []


def test_current_correction_keeps_its_validated_base_source_reference(tmp_path, monkeypatch):
    scope = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    first = _admit(tmp_path, monkeypatch, event_id="1", value="blue-green", scope=scope, observed_at="2026-10-01T00:00:00Z")
    _admit(tmp_path, monkeypatch, event_id="2", value="canary", scope=scope, observed_at="2026-10-02T00:00:00Z", correction_of=first.payload["claim_id"])
    projection = project_claims(scope, ledger_root=tmp_path / "memory")
    selected = projection.measurements["selected_claims"][0]
    assert len(selected["evidence_event_uids"]) == 2
    assert {ref.locator for ref in projection.evidence} == {"agentsview:s1#1", "agentsview:s1#2"}


def test_unvalidated_verification_label_is_retained_but_not_authoritative(tmp_path, monkeypatch):
    from z0int.memory.event_log import EventLog
    from z0int.memory_contract import BitemporalClaim

    scope = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    user_claim = _admit(
        tmp_path,
        monkeypatch,
        event_id="1",
        value="canary",
        scope=scope,
        observed_at="2026-10-01T00:00:00Z",
    )
    unvalidated = BitemporalClaim(
        claim_id="claim_unvalidated_verifier",
        scope=scope,
        subject="deployment",
        predicate="strategy",
        value="rolling",
        status="verified",
        observed_at="2026-10-02T00:00:00Z",
        recorded_at="2099-01-01T00:00:00Z",
        evidence_event_uids=("fabricated-evidence",),
        origin_trust="independent_verification",
    )
    EventLog(tmp_path / "memory").append(
        "memory.claim",
        unvalidated.to_dict(),
        source="unvalidated-caller",
    )

    packet = project_claims(scope, ledger_root=tmp_path / "memory")
    verifier_row = next(
        row
        for row in packet.measurements["claim_history"]
        if row["claim_id"] == unvalidated.claim_id
    )
    assert packet.measurements["selected_claim_ids"] == [user_claim.payload["claim_id"]]
    assert verifier_row["origin_trust"] == "model_assertion"
    assert verifier_row["declared_origin_trust"] == "independent_verification"
    assert not packet.contradictions
    assert any("rolling" in conflict for conflict in packet.measurements["resolved_lower_trust_conflicts"])


def test_wrong_task_scope_is_filtered_before_reduction_and_packet_creation(tmp_path, monkeypatch):
    task_a = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    task_b = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-b")
    _admit(tmp_path, monkeypatch, event_id="1", value="secret-a", scope=task_a, observed_at="2026-10-01T00:00:00Z")

    packet = project_claims(task_b, ledger_root=tmp_path / "memory")

    assert packet.measurements["selected_claim_ids"] == []
    assert packet.measurements["selected_claim_event_ids"] == []
    assert packet.evidence == []
    assert "secret-a" not in str(packet.to_dict())


def test_unknown_global_scope_claim_is_not_visible_to_user_scope(tmp_path):
    from z0int.memory_contract import BitemporalClaim

    unknown = BitemporalClaim(
        claim_id="claim_unknown_scope",
        scope=MemoryScope(),
        subject="deployment",
        predicate="strategy",
        value="global-secret",
        status="observed",
        observed_at="2026-10-01T00:00:00Z",
        recorded_at="2026-10-01T00:00:00Z",
    )
    record_claim(unknown, ledger_root=tmp_path / "memory")
    packet = project_claims(
        MemoryScope(user="u1", project="p1"),
        ledger_root=tmp_path / "memory",
    )

    assert packet.measurements["selected_claim_ids"] == []
    assert "global-secret" not in str(packet.to_dict())


def test_sibling_task_append_does_not_change_visible_packet_identity(tmp_path, monkeypatch):
    task_a = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    task_b = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-b")
    _admit(tmp_path, monkeypatch, event_id="1", value="blue-green", scope=task_a, observed_at="2026-10-01T00:00:00Z")
    before = project_claims(task_a, ledger_root=tmp_path / "memory")
    _admit(tmp_path, monkeypatch, event_id="2", value="secret-b", scope=task_b, observed_at="2026-10-02T00:00:00Z")
    after = project_claims(task_a, ledger_root=tmp_path / "memory")

    assert before.measurements["memory_snapshot_id"] == after.measurements["memory_snapshot_id"]
    assert "secret-b" not in str(after.to_dict())


def test_snapshot_binds_selected_claims_and_source_revision(tmp_path, monkeypatch):
    scope = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    first = _admit(
        tmp_path,
        monkeypatch,
        event_id="1",
        value="blue-green",
        scope=scope,
        observed_at="2026-10-01T00:00:00Z",
    )
    before = project_claims(scope, ledger_root=tmp_path / "memory")
    _admit(
        tmp_path,
        monkeypatch,
        event_id="2",
        value="canary",
        scope=scope,
        observed_at="2026-10-02T00:00:00Z",
        correction_of=first.payload["claim_id"],
    )
    after = project_claims(scope, ledger_root=tmp_path / "memory")

    assert (
        before.measurements["memory_snapshot"]["snapshot_id"]
        != after.measurements["memory_snapshot"]["snapshot_id"]
    )
    assert (
        before.measurements["memory_use_receipt"]["snapshot_id"]
        == before.measurements["memory_snapshot"]["snapshot_id"]
    )
    assert (
        after.measurements["memory_use_receipt"]["snapshot_id"]
        == after.measurements["memory_snapshot"]["snapshot_id"]
    )
