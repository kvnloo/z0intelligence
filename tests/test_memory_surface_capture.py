"""Synthetic scope and reference-only controls for the capture surface."""
from __future__ import annotations

import hashlib

import pytest

from z0int.memory import surface
from z0int.memory.event_log import EventLog, SourceIngestDisabled
from z0int.memory_contract import EventIdentity, MemoryScope


def _scope(task="alpha"):
    return MemoryScope(user="synthetic-user", project="project", repo="org/repo", task=task)


def _identity():
    return EventIdentity.from_source(
        source_system="hermes", source_session="synthetic-session",
        source_event_id="msg-1",
        payload_hash="sha256:" + hashlib.sha256(b"synthetic persisted source").hexdigest(),
    )


def test_disabled_ingest_has_no_files_or_directories(tmp_path, monkeypatch):
    monkeypatch.delenv("Z0INT_MEMORY_SOURCE_INGEST", raising=False)
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "isolated-home"))
    root = tmp_path / "missing-ledger"
    with pytest.raises(SourceIngestDisabled):
        surface.ingest_reference(
            _identity(), "agentsview:synthetic-session#1",
            scope=_scope(), authorized_scope=_scope(), ledger_root=root,
        )
    assert not root.exists()


def test_sibling_scope_denied_before_native_identity_touched(tmp_path):
    class CannotInspect:
        def __getattribute__(self, name):
            raise AssertionError("identity touched before scope gate")

    root = tmp_path / "no-ledger"
    with pytest.raises(ValueError, match="scope"):
        surface.ingest_reference(
            CannotInspect(), "agentsview:synthetic-session#1",
            scope=_scope(task="sibling"), authorized_scope=_scope(), ledger_root=root,
        )
    assert not root.exists()


@pytest.mark.parametrize("scope", [None, MemoryScope(), MemoryScope(user="synthetic-user")])
def test_unknown_or_partial_workstream_denied_before_identity_access(tmp_path, scope):
    root = tmp_path / "no-ledger"
    with pytest.raises(ValueError, match="scope"):
        surface.ingest_reference(
            object(), "agentsview:synthetic-session#1",
            scope=scope, authorized_scope=scope, ledger_root=root,
        )
    assert not root.exists()


def test_references_only_replay_stays_single_native_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "references")
    root = tmp_path / "ledger"
    args = {"scope": _scope(), "authorized_scope": _scope(), "ledger_root": root}
    a = surface.ingest_reference(_identity(), "agentsview:synthetic-session#1", **args)
    b = surface.ingest_reference(_identity(), "agentsview:synthetic-session#1", **args)
    assert a.event_id == b.event_id
    assert len(list(EventLog(root, read_only=True).iter_events(require_complete=True))) == 1
    assert "content" not in a.payload and "native_source_validated" not in a.payload


def test_worker_view_never_initializes_or_writes(tmp_path):
    root = tmp_path / "absent-ledger"
    view = surface.worker_ledger(root)
    assert not root.exists()
    with pytest.raises(PermissionError):
        view.append("note", {"synthetic": True}, source="test")
    assert not root.exists()


def test_claim_history_rejects_unknown_workstream_without_read(tmp_path):
    root = tmp_path / "absent-ledger"
    with pytest.raises(ValueError, match="scope"):
        surface.claim_history("deployment", scope=MemoryScope(), ledger_root=root)
    assert not root.exists()
