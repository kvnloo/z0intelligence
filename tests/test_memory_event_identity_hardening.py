from __future__ import annotations

import hashlib
import json

import pytest

from z0int.memory.event_log import (
    EventIdentityConflict,
    EventLog,
    EventLogCorruption,
    SourceIngestDisabled,
)
from z0int.memory_contract import EventIdentity


def _identity(
    *, source_event_id: str = "m-1", payload: str = "hello", source_system: str = "codex"
) -> EventIdentity:
    return EventIdentity.from_source(
        source_system=source_system,
        source_session="session-1",
        source_event_id=source_event_id,
        payload_hash="sha256:" + hashlib.sha256(payload.encode()).hexdigest(),
    )


def test_exact_replay_after_reopen_returns_original_row(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "references")
    first_log = EventLog(tmp_path / "memory")
    original = first_log.append(
        "source.reference",
        {"locator": "agentsview:session-1#m-1", "scope": {"user": "u"}},
        source="agentsview",
        identity=_identity(),
        ts=10,
    )
    original_bytes = first_log.events_path.read_bytes()

    replay = EventLog(tmp_path / "memory").append(
        "source.reference",
        {"locator": "agentsview:session-1#m-1", "scope": {"user": "u"}},
        source="agentsview",
        identity=_identity(),
        ts=99,
    )

    assert replay == original
    assert replay.event_identity().ledger_seq == 0
    assert first_log.events_path.read_bytes() == original_bytes
    assert len(list(first_log.iter_events())) == 1


def test_same_uid_with_changed_hash_is_a_conflict_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "references")
    log = EventLog(tmp_path / "memory")
    log.append("source.reference", {"locator": "agentsview:session-1#m-1"}, source="agentsview", identity=_identity())
    before = log.events_path.read_bytes()

    with pytest.raises(EventIdentityConflict):
        log.append(
            "source.reference",
            {"locator": "agentsview:session-1#m-1"},
            source="agentsview",
            identity=_identity(payload="edited"),
        )

    assert log.events_path.read_bytes() == before


def test_same_uid_and_hash_with_changed_reference_payload_is_a_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "references")
    log = EventLog(tmp_path / "memory")
    identity = _identity()
    log.append(
        "source.reference",
        {"locator": "agentsview:session-1#m-1"},
        source="agentsview",
        identity=identity,
    )
    before = log.events_path.read_bytes()

    with pytest.raises(EventIdentityConflict, match="payload"):
        log.append(
            "source.reference",
            {"locator": "agentsview:session-1#changed"},
            source="agentsview",
            identity=identity,
        )

    assert log.events_path.read_bytes() == before


def test_source_ingestion_is_off_by_default_but_native_append_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("Z0INT_MEMORY_SOURCE_INGEST", raising=False)
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "z0"))
    log = EventLog(tmp_path / "memory")

    with pytest.raises(SourceIngestDisabled):
        log.append("source.reference", {"locator": "x"}, source="agentsview", identity=_identity())

    event = log.append("memory.note", {"text": "native event"}, source="z0")
    assert event.event_id == 0
    assert event.event_identity() is None


@pytest.mark.parametrize("field", ["content", "body", "text", "prompt", "response"])
def test_harness_identity_events_reject_transcript_body_fields(tmp_path, monkeypatch, field):
    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "references")
    log = EventLog(tmp_path / "memory")

    with pytest.raises(ValueError, match="references only"):
        log.append(
            "source.reference",
            {"locator": "agentsview:session-1#m-1", field: "private conversation"},
            source="agentsview",
            identity=_identity(),
        )

    assert not log.events_path.exists() or log.events_path.read_bytes() == b""


def test_identity_source_reference_body_guard_covers_custom_source_system(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "references")
    log = EventLog(tmp_path / "memory")
    identity = _identity(source_system="custom_adapter")

    with pytest.raises(ValueError, match="references only"):
        log.append(
            "source.reference",
            {"locator": "custom_adapter:session-1#m-1", "content": "private conversation body"},
            source="custom_adapter",
            identity=identity,
        )

    assert not log.events_path.exists() or log.events_path.read_bytes() == b""


def test_identity_claim_values_remain_allowed_for_custom_source_system(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "references")
    log = EventLog(tmp_path / "memory")
    identity = _identity(source_system="custom_adapter")

    event = log.append(
        "memory.claim",
        {"claim_id": "normalized", "subject": "deployment", "predicate": "strategy", "value": "blue-green"},
        source="custom_adapter",
        identity=identity,
    )

    assert event.identity["source_system"] == "custom_adapter"
    assert event.payload["value"] == "blue-green"


def test_schema_v1_rows_without_identity_remain_compatible(tmp_path):
    log = EventLog(tmp_path / "memory")
    legacy = log.append("memory.note", {"value": "kept"}, source="z0")
    raw = json.loads(log.events_path.read_text(encoding="utf-8"))

    assert "identity" not in raw
    assert EventLog(tmp_path / "memory").get(legacy.event_id).event_identity() is None
    assert log.verify()["ok"]


def test_read_only_view_does_not_rebuild_a_missing_index(tmp_path):
    log = EventLog(tmp_path / "memory")
    log.append("memory.note", {"value": "kept"}, source="z0")
    event_bytes = log.events_path.read_bytes()
    log.index_path.unlink()
    reader = EventLog(tmp_path / "memory", read_only=True)

    assert reader.get(0).payload == {"value": "kept"}
    assert not log.index_path.exists()
    assert log.events_path.read_bytes() == event_bytes
    with pytest.raises(PermissionError):
        reader.rebuild_index()


def test_complete_snapshot_rejects_incomplete_tail_but_recovery_iterator_tolerates_it(tmp_path):
    log = EventLog(tmp_path / "memory")
    committed = log.append("memory.note", {"value": "kept"}, source="z0")
    with log.events_path.open("ab") as events:
        events.write(b'{"event_id":1,"partial":')

    reader = EventLog(tmp_path / "memory", read_only=True)
    # Preserve the existing recovery behavior for ordinary readers.
    assert list(reader.iter_events()) == [committed]

    # A caller that needs a complete ledger snapshot must not mistake the
    # recoverable tail for a complete history.
    with pytest.raises(EventLogCorruption, match="incomplete trailing event"):
        list(reader.iter_events(require_complete=True))


def test_complete_snapshot_rejects_truncated_committed_suffix_but_recovery_remains_tolerant(tmp_path):
    log = EventLog(tmp_path / "memory")
    first = log.append("memory.note", {"value": "first"}, source="z0")
    log.append("memory.note", {"value": "second"}, source="z0")
    committed_rows = log.events_path.read_bytes().splitlines(keepends=True)
    log.events_path.write_bytes(committed_rows[0])

    reader = EventLog(tmp_path / "memory", read_only=True)
    assert list(reader.iter_events()) == [first]
    with pytest.raises(EventLogCorruption, match="state|committed|endpoint|count"):
        list(reader.iter_events(require_complete=True))


@pytest.mark.parametrize("state_edit", ["missing", "malformed", "unreadable", "count", "endpoint", "last_row"])
def test_complete_snapshot_requires_consistent_committed_state(tmp_path, monkeypatch, state_edit):
    log = EventLog(tmp_path / "memory")
    log.append("memory.note", {"value": "first"}, source="z0")
    if state_edit == "missing":
        log.state_path.unlink()
    elif state_edit == "unreadable":
        from pathlib import Path

        original_read_text = Path.read_text

        def deny_state_read(path, *args, **kwargs):
            if path == log.state_path:
                raise PermissionError("test unreadable event state")
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", deny_state_read)
    else:
        if state_edit == "malformed":
            log.state_path.write_text("{broken", encoding="utf-8")
        else:
            state = json.loads(log.state_path.read_text(encoding="utf-8"))
            if state_edit == "count":
                state["next_event_id"] += 1
            elif state_edit == "endpoint":
                state["end_offset"] += 1
            elif state_edit == "last_row":
                state["last_checksum"] = "0" * 64
            log.state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(EventLogCorruption):
        list(EventLog(tmp_path / "memory", read_only=True).iter_events(require_complete=True))
