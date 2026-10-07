from __future__ import annotations

import hashlib
import json
from unittest.mock import Mock

import pytest

from z0int.memory.claims import project_claims
from z0int.memory.event_log import EventLog, SourceIngestDisabled
from z0int.memory.native_preferences import (
    NativePreferenceAdmissionError,
    NativeWorkstreamBinding,
    UnsupportedNativePreference,
    admit_delegated_agent_config,
    agentsview_native_reader,
)
from z0int.memory_contract import MemoryScope


SESSION_ID = "codex:synthetic-native-preferences-session"
OTHER_SESSION_ID = "codex:synthetic-other-preferences-session"
SCOPE = MemoryScope(
    user="test-user",
    project="example-project",
    repo="example/repository",
    task="test-workstream",
)
REQUEST_WITH_SUBAGENT_CONFIG = "Please review this synthetic task (use 10 luna subagents)"
MAX_EFFORT_CORRECTION = "luna max**"


def binding(
    scope: MemoryScope = SCOPE,
    session_id: str = SESSION_ID,
) -> NativeWorkstreamBinding:
    return NativeWorkstreamBinding(session_id=session_id, scope=scope)


def native_message(
    ordinal: int,
    content: str,
    *,
    session_id: str = SESSION_ID,
    role: str = "user",
    timestamp: str = "2025-04-03T10:20:30.000Z",
    scope: MemoryScope | None = None,
    payload_hash: str | None = None,
) -> dict[str, object]:
    message: dict[str, object] = {
        "id": f"synthetic-native-message-{ordinal}",
        "session_id": session_id,
        "ordinal": ordinal,
        "role": role,
        "content": content,
        "timestamp": timestamp,
    }
    if scope is not None:
        message["scope"] = scope.to_dict()
    if payload_hash is not None:
        message["payload_hash"] = payload_hash
    return message


def reader_for(message: dict[str, object]):
    def read(session_id: str, ordinal: int):
        assert session_id == message["session_id"]
        assert ordinal == message["ordinal"]
        return message

    return read


def enable_reference_ingest(monkeypatch):
    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "references")


def test_affirmative_request_admits_only_normalized_config_and_source_reference(tmp_path, monkeypatch):
    enable_reference_ingest(monkeypatch)
    message = native_message(7, REQUEST_WITH_SUBAGENT_CONFIG)

    claim = admit_delegated_agent_config(
        SESSION_ID,
        7,
        binding(),
        native_reader=reader_for(message),
        ledger_root=tmp_path / "memory",
    )

    log = EventLog(tmp_path / "memory")
    reference = log.get(claim.parent_event_ids[0])
    identity = reference.event_identity()
    assert claim.event_type == "memory.claim"
    assert claim.payload["subject"] == "delegated_agent"
    assert claim.payload["predicate"] == "configuration"
    assert claim.payload["value"] == {"model": "luna", "subagents": 10}
    assert claim.payload["origin_trust"] == "explicit_user"
    assert reference.event_type == "source.reference"
    assert reference.payload == {
        "locator": f"agentsview:{SESSION_ID}#7",
        "scope": SCOPE.to_dict(),
        "role": "user",
        "native_source_validated": True,
    }
    assert identity.source_system == "codex"
    assert identity.source_session == SESSION_ID
    assert identity.source_seq == 7
    assert identity.payload_hash == "sha256:" + hashlib.sha256(REQUEST_WITH_SUBAGENT_CONFIG.encode()).hexdigest()
    assert claim.payload["evidence_event_uids"] == [identity.event_uid]
    serialized = log.events_path.read_text(encoding="utf-8")
    assert REQUEST_WITH_SUBAGENT_CONFIG not in serialized


def test_correction_merges_explicit_fields_and_preserves_source_chain(tmp_path, monkeypatch):
    enable_reference_ingest(monkeypatch)
    first = admit_delegated_agent_config(
        SESSION_ID,
        7,
        binding(),
        native_reader=reader_for(native_message(7, REQUEST_WITH_SUBAGENT_CONFIG)),
        ledger_root=tmp_path / "memory",
    )
    corrected = admit_delegated_agent_config(
        SESSION_ID,
        9,
        binding(),
        native_reader=reader_for(native_message(9, MAX_EFFORT_CORRECTION)),
        correction_of=first.payload["claim_id"],
        ledger_root=tmp_path / "memory",
    )

    assert corrected.payload["value"] == {"model": "luna", "subagents": 10, "effort": "max"}
    assert first.event_id in corrected.parent_event_ids
    projection = project_claims(
        SCOPE,
        subject="delegated_agent",
        predicate="configuration",
        ledger_root=tmp_path / "memory",
    )
    assert projection.measurements["selected_claims"][0]["value"] == corrected.payload["value"]
    history = projection.measurements["claim_history"]
    assert next(row for row in history if row["claim_id"] == first.payload["claim_id"])["superseded_by"] == corrected.payload["claim_id"]
    serialized = EventLog(tmp_path / "memory").events_path.read_text(encoding="utf-8")
    assert REQUEST_WITH_SUBAGENT_CONFIG not in serialized
    assert MAX_EFFORT_CORRECTION not in serialized


def test_modal_affirmative_request_form_is_bounded_and_explicit(tmp_path, monkeypatch):
    enable_reference_ingest(monkeypatch)
    content = "Could you inspect this synthetic change (use 10 luna subagents)"
    claim = admit_delegated_agent_config(
        SESSION_ID,
        8,
        binding(),
        native_reader=reader_for(native_message(8, content)),
        ledger_root=tmp_path / "memory",
    )

    assert claim.payload["value"] == {"model": "luna", "subagents": 10}


@pytest.mark.parametrize(
    "ordinal,content,expected",
    [
        (7, "model=luna, effort=max", {"model": "luna", "effort": "max"}),
        (8, "model=luna", {"model": "luna"}),
        (9, "luna max**", {"model": "luna", "effort": "max"}),
        (10, "use 10 luna subagents", {"model": "luna", "subagents": 10}),
    ],
)
def test_supported_explicit_config_forms_are_small_and_normalized(
    tmp_path,
    monkeypatch,
    ordinal,
    content,
    expected,
):
    enable_reference_ingest(monkeypatch)
    claim = admit_delegated_agent_config(
        SESSION_ID,
        ordinal,
        binding(),
        native_reader=reader_for(native_message(ordinal, content)),
        ledger_root=tmp_path / "memory",
    )

    assert claim.payload["value"] == expected
    assert len(json.dumps(claim.payload["value"])) < 100


@pytest.mark.parametrize(
    "message",
    [
        native_message(7, REQUEST_WITH_SUBAGENT_CONFIG, role="assistant"),
        native_message(7, REQUEST_WITH_SUBAGENT_CONFIG, payload_hash="sha256:spoofed"),
        native_message(8, REQUEST_WITH_SUBAGENT_CONFIG),
        native_message(7, REQUEST_WITH_SUBAGENT_CONFIG, session_id=OTHER_SESSION_ID),
        native_message(
            7,
            REQUEST_WITH_SUBAGENT_CONFIG,
            scope=MemoryScope(
                user="test-user",
                project="other-project",
                repo="example/repository",
                task="test-workstream",
            ),
        ),
    ],
)
def test_spoofed_native_role_hash_scope_or_locator_is_rejected_without_writes(tmp_path, monkeypatch, message):
    enable_reference_ingest(monkeypatch)
    memory = tmp_path / "memory"
    with pytest.raises(NativePreferenceAdmissionError):
        admit_delegated_agent_config(
            SESSION_ID,
            7,
            binding(),
            native_reader=reader_for(message),
            ledger_root=memory,
        )
    assert not (memory / "events.jsonl").exists()


@pytest.mark.parametrize(
    "content",
    [
        "maybe use 10 luna subagents",
        "Do not review this task (use 10 luna subagents)",
        "Please review this synthetic task without opting in (use 10 luna subagents)",
        "'Please review this task (use 10 luna subagents)'",
        "unrelated words (use 10 luna subagents)",
    ],
)
def test_nonaffirmative_quoted_or_suffix_only_requests_are_not_admitted(tmp_path, monkeypatch, content):
    enable_reference_ingest(monkeypatch)
    memory = tmp_path / "memory"
    with pytest.raises(UnsupportedNativePreference):
        admit_delegated_agent_config(
            SESSION_ID,
            7,
            binding(),
            native_reader=reader_for(native_message(7, content)),
            ledger_root=memory,
        )
    assert not (memory / "events.jsonl").exists()


def test_unrelated_preferences_are_not_autolearned(tmp_path, monkeypatch):
    enable_reference_ingest(monkeypatch)
    memory = tmp_path / "memory"
    with pytest.raises(UnsupportedNativePreference):
        admit_delegated_agent_config(
            SESSION_ID,
            7,
            binding(),
            native_reader=reader_for(native_message(7, "maybe try Luna with more thought")),
            ledger_root=memory,
        )
    assert not (memory / "events.jsonl").exists()


def test_adapter_rejects_caller_supplied_identity_and_normalized_values(tmp_path, monkeypatch):
    enable_reference_ingest(monkeypatch)
    message = native_message(7, "model=luna")
    message["identity"] = {"event_uid": "evt_caller_supplied"}
    with pytest.raises(NativePreferenceAdmissionError, match="derived internally"):
        admit_delegated_agent_config(
            SESSION_ID,
            7,
            binding(),
            native_reader=reader_for(message),
            ledger_root=tmp_path / "memory",
        )
    with pytest.raises(TypeError):
        admit_delegated_agent_config(
            SESSION_ID,
            7,
            binding(),
            native_reader=reader_for(native_message(7, "model=luna")),
            value={"model": "invented"},
            ledger_root=tmp_path / "memory",
        )
    assert not (tmp_path / "memory" / "events.jsonl").exists()


def test_scope_binding_requires_user_project_task_and_matching_session(tmp_path, monkeypatch):
    enable_reference_ingest(monkeypatch)
    with pytest.raises((TypeError, ValueError, NativePreferenceAdmissionError)):
        NativeWorkstreamBinding(session_id=SESSION_ID, scope=MemoryScope(user="test-user"))
    with pytest.raises(NativePreferenceAdmissionError):
        admit_delegated_agent_config(
            SESSION_ID,
            7,
            binding(session_id="codex:synthetic-different-session"),
            native_reader=reader_for(native_message(7, "model=luna")),
            ledger_root=tmp_path / "memory",
        )


def test_replay_is_idempotent_and_changed_source_hash_conflicts(tmp_path, monkeypatch):
    enable_reference_ingest(monkeypatch)
    memory = tmp_path / "memory"
    original = native_message(7, "model=luna")
    first = admit_delegated_agent_config(
        SESSION_ID, 7, binding(), native_reader=reader_for(original), ledger_root=memory
    )
    replay = admit_delegated_agent_config(
        SESSION_ID, 7, binding(), native_reader=reader_for(original), ledger_root=memory
    )
    assert replay.event_id == first.event_id
    assert len(list(EventLog(memory).iter_events())) == 2

    changed = native_message(7, "model=luna, effort=max")
    with pytest.raises(ValueError, match="same native source event"):
        admit_delegated_agent_config(
            SESSION_ID, 7, binding(), native_reader=reader_for(changed), ledger_root=memory
        )


def test_correction_requires_existing_explicit_claim_in_same_scope(tmp_path, monkeypatch):
    enable_reference_ingest(monkeypatch)
    with pytest.raises(ValueError, match="correction_of"):
        admit_delegated_agent_config(
            SESSION_ID,
            9,
            binding(),
            native_reader=reader_for(native_message(9, MAX_EFFORT_CORRECTION)),
            correction_of="claim_does_not_exist",
            ledger_root=tmp_path / "memory",
        )


def test_correction_must_follow_prior_source_ordinal_in_same_session(tmp_path, monkeypatch):
    enable_reference_ingest(monkeypatch)
    memory = tmp_path / "memory"
    first = admit_delegated_agent_config(
        SESSION_ID,
        7,
        binding(),
        native_reader=reader_for(native_message(7, "model=luna")),
        ledger_root=memory,
    )

    with pytest.raises(NativePreferenceAdmissionError, match="later source ordinal"):
        admit_delegated_agent_config(
            SESSION_ID,
            6,
            binding(),
            native_reader=reader_for(native_message(6, "luna max**")),
            correction_of=first.payload["claim_id"],
            ledger_root=memory,
        )
    assert len(list(EventLog(memory).iter_events())) == 2


def test_correction_cannot_cross_native_sessions(tmp_path, monkeypatch):
    enable_reference_ingest(monkeypatch)
    memory = tmp_path / "memory"
    first = admit_delegated_agent_config(
        OTHER_SESSION_ID,
        7,
        binding(session_id=OTHER_SESSION_ID),
        native_reader=reader_for(native_message(7, "model=luna", session_id=OTHER_SESSION_ID)),
        ledger_root=memory,
    )

    with pytest.raises(NativePreferenceAdmissionError, match="same native session"):
        admit_delegated_agent_config(
            SESSION_ID,
            9,
            binding(),
            native_reader=reader_for(native_message(9, "luna max**")),
            correction_of=first.payload["claim_id"],
            ledger_root=memory,
        )
    assert len(list(EventLog(memory).iter_events())) == 2


def test_correction_cannot_branch_from_superseded_source_claim(tmp_path, monkeypatch):
    enable_reference_ingest(monkeypatch)
    memory = tmp_path / "memory"
    first = admit_delegated_agent_config(
        SESSION_ID,
        7,
        binding(),
        native_reader=reader_for(native_message(7, "model=luna")),
        ledger_root=memory,
    )
    admit_delegated_agent_config(
        SESSION_ID,
        9,
        binding(),
        native_reader=reader_for(native_message(9, "luna max**")),
        correction_of=first.payload["claim_id"],
        ledger_root=memory,
    )

    with pytest.raises(NativePreferenceAdmissionError, match="already been superseded"):
        admit_delegated_agent_config(
            SESSION_ID,
            10,
            binding(),
            native_reader=reader_for(native_message(10, "model=luna")),
            correction_of=first.payload["claim_id"],
            ledger_root=memory,
        )
    assert len(list(EventLog(memory).iter_events())) == 4


def test_adapter_does_not_enable_source_ingest_implicitly(tmp_path, monkeypatch):
    monkeypatch.delenv("Z0INT_MEMORY_SOURCE_INGEST", raising=False)
    monkeypatch.setattr("z0int.memory.event_log.paths.home", lambda: tmp_path / "empty-home")
    with pytest.raises(SourceIngestDisabled):
        admit_delegated_agent_config(
            SESSION_ID,
            7,
            binding(),
            native_reader=reader_for(native_message(7, "model=luna")),
            ledger_root=tmp_path / "memory",
        )
    assert "Z0INT_MEMORY_SOURCE_INGEST" not in __import__("os").environ


def test_agentsview_reader_uses_configured_read_only_session_messages_command(monkeypatch):
    from z0int.memory import native_preferences

    message = native_message(7, "model=luna")
    run = Mock(return_value=Mock(returncode=0, stdout=json.dumps({"messages": [message]}), stderr=""))
    monkeypatch.setattr(native_preferences.subprocess, "run", run)
    monkeypatch.delenv("AGENTSVIEW_DATA_DIR", raising=False)

    loaded = agentsview_native_reader(SESSION_ID, 7)

    assert loaded == message
    args, kwargs = run.call_args
    assert args[0] == [
        str(native_preferences.DEFAULT_AGENTSVIEW_CLI),
        "session",
        "messages",
        SESSION_ID,
        "--from",
        "7",
        "--limit",
        "1",
        "--role",
        "user",
        "--json",
    ]
    assert kwargs["env"]["AGENTSVIEW_DATA_DIR"] == str(native_preferences.DEFAULT_AGENTSVIEW_DATA_DIR)
    assert kwargs["timeout"] > 0
