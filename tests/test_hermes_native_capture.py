"""Synthetic controller inputs only; never historical user/author statements."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import adapters.hermes_z0int as adapter
from z0int.memory.claims import project_claims
from z0int.memory.event_log import EventLog
from z0int.memory_contract import EventIdentity, MemoryScope


@pytest.fixture(autouse=True)
def references_only(monkeypatch, tmp_path):
    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "references")
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "z0-home"))


@pytest.fixture
def native(tmp_path):
    home = tmp_path / "hermes"
    home.mkdir()
    database = home / "state.db"
    with sqlite3.connect(database) as conn:
        conn.executescript("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY, source TEXT, parent_session_id TEXT, profile_name TEXT
            );
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
                timestamp REAL, message_uid TEXT, _compressed_summary INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1, compacted INTEGER DEFAULT 0,
                platform_message_id TEXT, display_kind TEXT, display_metadata TEXT,
                tool_name TEXT, tool_call_id TEXT, tool_calls TEXT
            );
            INSERT INTO sessions VALUES ('native-session', 'cli', NULL, 'capture-test');
        """)
    return home


def _scope(**changes):
    return MemoryScope(**{
        "user": "synthetic-controller", "project": "test-project",
        "repo": "example/repository", "task": "decision-test", **changes,
    })


def _content(**changes):
    return json.dumps({
        "schema": "z0int.hermes.native_decision.v1",
        "subject": "deployment", "predicate": "strategy",
        "value": {"decision": "blue-green", "synthetic": True,
                  "attribution": "test controller, not a historical human statement"},
        **changes,
    }, ensure_ascii=False, indent=2)


def _persist(home, content=None, *, row_id=1, **changes):
    row = {
        "id": row_id, "session_id": "native-session", "role": "user",
        "content": _content() if content is None else content,
        "timestamp": 1791504000.125, "message_uid": f"native-message-{row_id}",
        **changes,
    }
    with sqlite3.connect(home / "state.db") as conn:
        # Column names are test-owned, never external input.
        conn.execute(
            f"INSERT INTO messages ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
            tuple(row.values()),
        )
    return row


def _capture(home, row, ledger, *, binding=None, **changes):
    binding = binding or adapter.HermesSessionBinding(
        hermes_home=home, session_id="native-session", scope=_scope(),
        synthetic=True,
        attribution="test controller, not a historical human statement",
    )
    return adapter.capture_native_decision(binding, **{
        "session_id": row["session_id"], "message_id": row["id"],
        "message_uid": row["message_uid"], "scope": _scope(),
        "expected_content_hash": "sha256:" + hashlib.sha256(row["content"].encode()).hexdigest(),
        "ledger_root": ledger, **changes,
    })


def test_persisted_user_decision_uses_production_claim_admission(native, tmp_path):
    row = _persist(native)
    before = (native / "state.db").read_bytes()
    ledger = tmp_path / "ledger"
    event = _capture(native, row, ledger)
    events = list(EventLog(ledger).iter_events(require_complete=True))
    assert [item.event_type for item in events] == ["source.reference", "memory.claim"]
    assert events[-1] == event
    identity = events[0].event_identity()
    assert identity == EventIdentity.from_source(
        source_system="hermes", source_session=row["session_id"],
        source_event_id=row["message_uid"], source_seq=row["id"],
        payload_hash="sha256:" + hashlib.sha256(row["content"].encode()).hexdigest(),
        ledger_seq=events[0].event_id,
    )
    assert isinstance(events[0].payload, dict)
    assert isinstance(event.payload, dict)
    assert events[0].payload["locator"] == "agentsview:native-session#1"
    assert events[0].payload["native_source_validated"] is True
    assert event.payload["observed_at"] == "2026-10-09T00:00:00.125000Z"
    assert event.payload["admission_method"] == "native_user_claim_attestation.v1"
    assert event.payload["correction_of"] is None
    packet = project_claims(_scope(), ledger_root=ledger)
    selected = packet.measurements["selected_claims"]
    assert len(selected) == 1
    assert selected[0]["origin_trust"] == "explicit_user"
    assert selected[0]["value"] == json.loads(row["content"])["value"]
    assert row["content"] not in EventLog(ledger).events_path.read_text()
    assert (native / "state.db").read_bytes() == before


def test_explicit_correction_survives_restart_without_duplicate_claims(native, tmp_path):
    ledger = tmp_path / "ledger"
    first = _capture(native, _persist(native), ledger)
    assert isinstance(first.payload, dict)
    row = _persist(native, _content(
        value={"decision": "canary", "synthetic": True,
               "attribution": "test controller, not a historical human statement"},
        correction_of=first.payload["claim_id"],
    ), row_id=2, timestamp=1791504010.5)
    correction = _capture(native, row, ledger)
    assert isinstance(correction.payload, dict)
    assert correction.payload["correction_of"] == first.payload["claim_id"]
    before = EventLog(ledger).events_path.read_bytes()
    child = subprocess.run([
        sys.executable, "-c", """
import json, sys
from pathlib import Path
from adapters.hermes_z0int import HermesSessionBinding, capture_native_decision
from z0int.memory_contract import MemoryScope
home, ledger, row_json = sys.argv[1:]
row = json.loads(row_json)
scope = MemoryScope(user='synthetic-controller', project='test-project',
                    repo='example/repository', task='decision-test')
binding = HermesSessionBinding(Path(home), 'native-session', scope, synthetic=True,
    attribution='test controller, not a historical human statement')
import hashlib
event = capture_native_decision(binding, session_id=row['session_id'],
    message_id=row['id'], message_uid=row['message_uid'], scope=scope,
    expected_content_hash='sha256:' + hashlib.sha256(row['content'].encode()).hexdigest(),
    ledger_root=Path(ledger))
print(json.dumps({'claim_id': event.payload['claim_id'], 'event_id': event.event_id}))
""", str(native), str(ledger), json.dumps(row),
    ], cwd=Path(__file__).resolve().parents[1], text=True, capture_output=True, check=True)
    assert json.loads(child.stdout) == {
        "claim_id": correction.payload["claim_id"], "event_id": correction.event_id,
    }
    assert EventLog(ledger).events_path.read_bytes() == before
    packet = project_claims(_scope(), ledger_root=ledger)
    assert not packet.contradictions
    assert packet.measurements["selected_claims"][0]["claim_id"] == correction.payload["claim_id"]
    history = packet.measurements["claim_history"]
    assert len(history) == 2
    assert history[0]["superseded_by"] == correction.payload["claim_id"]


@pytest.mark.parametrize("changes,reason", [
    ({"message_uid": "invented-message"}, "identity"),
    ({"message_id": True}, "identity"),
    ({"message_id": "1"}, "identity"),
    ({"message_uid": " native-message-1"}, "identity"),
    ({"expected_content_hash": "sha256:" + "0" * 64}, "hash"),
    ({"expected_content_hash": "not-a-hash"}, "hash"),
    ({"scope": _scope(task="sibling-task")}, "scope"),
])
def test_host_request_must_match_native_identity_and_scope(native, tmp_path, changes, reason):
    row = _persist(native)
    ledger = tmp_path / "ledger"
    with pytest.raises(ValueError, match=reason):
        _capture(native, row, ledger, **changes)
    assert not ledger.exists()


def test_other_session_cannot_borrow_the_trusted_host_binding(native, tmp_path):
    with sqlite3.connect(native / "state.db") as conn:
        conn.execute("INSERT INTO sessions VALUES ('other-session', 'cli', NULL, NULL)")
    row = _persist(native, session_id="other-session")
    with pytest.raises(ValueError, match="session"):
        _capture(native, row, tmp_path / "ledger")
    assert not (tmp_path / "ledger").exists()


@pytest.mark.parametrize("scope", [MemoryScope(), MemoryScope(user="u"), _scope(task=7)])
def test_binding_requires_full_typed_scope(native, scope):
    with pytest.raises(ValueError, match="scope"):
        adapter.HermesSessionBinding(native, "native-session", scope)


@pytest.mark.parametrize("changes", [
    {"role": "assistant"}, {"role": "system"}, {"role": "tool"}, {"role": "USER"},
    {"_compressed_summary": 1}, {"active": 0}, {"compacted": 1},
    {"display_kind": "hidden"}, {"display_kind": "async_delegation_complete"},
    {"display_metadata": '{"internal":true}'}, {"tool_name": "run"},
    {"tool_call_id": "call-1"}, {"tool_calls": "[]"},
])
def test_only_unmarked_native_user_rows_are_attested(native, tmp_path, changes):
    row = _persist(native, **changes)
    with pytest.raises(ValueError, match="native"):
        _capture(native, row, tmp_path / "ledger")
    assert not (tmp_path / "ledger").exists()


@pytest.mark.parametrize("source,parent", [("subagent", None), ("cron", None), ("cli", "parent")])
def test_internal_or_unbound_session_origin_is_not_a_user_source(native, tmp_path, source, parent):
    row = _persist(native)
    with sqlite3.connect(native / "state.db") as conn:
        conn.execute("UPDATE sessions SET source = ?, parent_session_id = ?", (source, parent))
    with pytest.raises(ValueError, match="session"):
        _capture(native, row, tmp_path / "ledger")
    assert not (tmp_path / "ledger").exists()


@pytest.mark.parametrize("content", [
    "Remember that the rollout should use canary.",
    "```json\n" + _content() + "\n```", _content() + " trailing text",
    "[]", "{}", _content(schema="unrecognized.v1"),
    _content(role="user"), _content(scope=_scope().to_dict()),
    _content(subject=12), _content(predicate=["strategy"]),
    _content(subject=" deployment"), _content(predicate="strategy\n"),
    _content(subject="x" * 161), _content(value="x" * 1025),
    _content(correction_of=17), _content(correction_of="claim_no_such_format"),
    _content().replace('"subject": "deployment",', '"subject": "ignored", "subject": "deployment",'),
    _content().replace('"decision": "blue-green",', '"decision": "ignored", "decision": "blue-green",'),
    _content(value=float("nan")), _content(value=float("inf")),
    _content().replace('"blue-green"', '"\\ud800"'), " " * 16385 + _content(),
    _content(value=[[[[[[[[[[[[[[[[["too deep"]]]]]]]]]]]]]]]]]),
], ids=lambda value: hashlib.sha256(value.encode()).hexdigest()[:8])
def test_only_bounded_whole_declarative_json_is_supported(native, tmp_path, content):
    row = _persist(native, content)
    with pytest.raises(ValueError, match="decision|bounded"):
        _capture(native, row, tmp_path / "ledger")
    assert not (tmp_path / "ledger").exists()


@pytest.mark.parametrize("value", [
    "canary", {"decision": "canary"}, {"synthetic": True},
    {"synthetic": 1, "attribution": "test controller, not a historical human statement"},
    {"synthetic": False, "attribution": "test controller, not a historical human statement"},
    {"synthetic": True, "attribution": "a different speaker"},
])
def test_synthetic_capture_cannot_drop_or_rewrite_controller_attribution(native, tmp_path, value):
    row = _persist(native, _content(value=value))
    with pytest.raises(ValueError, match="attribution"):
        _capture(native, row, tmp_path / "ledger")
    assert not (tmp_path / "ledger").exists()


@pytest.mark.parametrize("value", ["enabled", 17, None, ["x", False], {"choice": "α", "ratio": 0.25}])
def test_real_input_is_declarative_not_fixture_specific(native, tmp_path, value):
    binding = adapter.HermesSessionBinding(native, "native-session", _scope())
    row = _persist(native, _content(subject="arbitrary-subject", predicate="arbitrary-key", value=value))
    event = _capture(native, row, tmp_path / "ledger", binding=binding)
    assert isinstance(event.payload, dict)
    assert event.payload["subject"] == "arbitrary-subject"
    assert event.payload["predicate"] == "arbitrary-key"
    packet = project_claims(_scope(), ledger_root=tmp_path / "ledger")
    assert packet.measurements["selected_claims"][0]["value"] == value


def test_synthetic_marker_must_match_host_mode(native, tmp_path):
    binding = adapter.HermesSessionBinding(native, "native-session", _scope())
    with pytest.raises(ValueError, match="attribution"):
        _capture(native, _persist(native), tmp_path / "ledger", binding=binding)


@pytest.mark.parametrize("changes", [
    {"synthetic": "true"}, {"synthetic": True},
    {"synthetic": True, "attribution": ""}, {"synthetic": True, "attribution": "x" * 161},
])
def test_synthetic_host_binding_requires_explicit_bounded_attribution(native, changes):
    with pytest.raises(ValueError, match="attribution"):
        adapter.HermesSessionBinding(native, "native-session", _scope(), **changes)


@pytest.mark.parametrize("failure", ["missing", "corrupt", "schema", "locked"])
def test_source_outages_are_explicit_without_initialization_or_claims(native, tmp_path, failure):
    row = _persist(native)
    database = native / "state.db"
    lock = None
    if failure == "missing":
        database.unlink()
    elif failure == "corrupt":
        database.write_bytes(b"not a SQLite database")
    elif failure == "schema":
        with sqlite3.connect(database) as conn:
            conn.execute("ALTER TABLE messages DROP COLUMN _compressed_summary")
    else:
        lock = sqlite3.connect(database)
        lock.execute("BEGIN EXCLUSIVE")
    before = database.read_bytes() if database.exists() else None
    try:
        with pytest.raises(ValueError, match="native source unavailable"):
            _capture(native, row, tmp_path / "ledger")
    finally:
        if lock:
            lock.close()
    assert (database.read_bytes() if database.exists() else None) == before
    assert not (tmp_path / "ledger").exists()


def test_unknown_native_row_remains_an_explicit_error(native, tmp_path):
    row = _persist(native)
    with pytest.raises(ValueError, match="not found"):
        _capture(native, row, tmp_path / "ledger", message_id=999)
    assert not (tmp_path / "ledger").exists()


def test_duplicate_native_uid_is_ambiguous_not_a_new_claim(native, tmp_path):
    row = _persist(native)
    _persist(native, row_id=2, message_uid=row["message_uid"])
    with pytest.raises(ValueError, match="identity"):
        _capture(native, row, tmp_path / "ledger")
    assert not (tmp_path / "ledger").exists()


def test_binding_refuses_redirected_database(native, tmp_path):
    _persist(native)
    database = native / "state.db"
    external = tmp_path / "external.db"
    database.rename(external)
    database.symlink_to(external)
    with pytest.raises(ValueError, match="database"):
        adapter.HermesSessionBinding(native, "native-session", _scope())


def test_existing_binding_refuses_database_replacement(native, tmp_path):
    binding = adapter.HermesSessionBinding(native, "native-session", _scope())
    row = _persist(native, _content(value="direct-declaration"))
    database = native / "state.db"
    replacement = native / "replacement.db"
    replacement.write_bytes(database.read_bytes())
    replacement.replace(database)
    with pytest.raises(ValueError, match="database"):
        _capture(native, row, tmp_path / "ledger", binding=binding)
    assert not (tmp_path / "ledger").exists()


def test_native_connections_really_are_read_only(native, tmp_path, monkeypatch):
    from adapters.hermes_z0int import native_capture

    row = _persist(native)
    real_connect = sqlite3.connect
    opened = []

    def probe(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("CREATE TABLE forbidden_write (value TEXT)")
        opened.append(conn)
        return conn

    monkeypatch.setattr(native_capture.sqlite3, "connect", probe)
    _capture(native, row, tmp_path / "ledger")
    assert len(opened) == 2, "canonical admission must independently rehydrate"
    for conn in opened:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")


@pytest.mark.parametrize("timestamp", [None, "not-a-timestamp", float("inf"), -1, 1e100])
def test_native_timestamp_is_not_invented_or_repaired(native, tmp_path, timestamp):
    row = _persist(native, timestamp=timestamp)
    with pytest.raises(ValueError, match="timestamp"):
        _capture(native, row, tmp_path / "ledger")
    assert not (tmp_path / "ledger").exists()


def test_message_id_must_fit_native_sqlite_identity(native, tmp_path):
    with pytest.raises(ValueError, match="identity"):
        _capture(native, _persist(native), tmp_path / "ledger", message_id=2**63)


def test_binding_requires_explicit_absolute_home(native, monkeypatch):
    monkeypatch.chdir(native.parent)
    with pytest.raises(ValueError, match="absolute"):
        adapter.HermesSessionBinding(Path("hermes"), "native-session", _scope())


@pytest.mark.parametrize("source", [None, "", "cli "])
def test_binding_requires_explicit_session_source(native, source):
    with pytest.raises(ValueError, match="session"):
        adapter.HermesSessionBinding(native, "native-session", _scope(), session_source=source)


@pytest.mark.parametrize("mismatch", ["scope", "subject", "predicate", "unknown"])
def test_correction_must_name_same_scoped_claim_key(native, tmp_path, mismatch):
    ledger = tmp_path / "ledger"
    first = _capture(native, _persist(native), ledger)
    assert isinstance(first.payload, dict)
    correction_id = first.payload["claim_id"]
    changes = {}
    binding = None
    capture_changes = {}
    if mismatch == "scope":
        scope = _scope(task="sibling")
        binding = adapter.HermesSessionBinding(native, "native-session", scope, synthetic=True,
            attribution="test controller, not a historical human statement")
        capture_changes["scope"] = scope
    elif mismatch == "unknown":
        correction_id = "claim_" + "0" * 32
    else:
        changes[mismatch] = "different-key"
    row = _persist(native, _content(correction_of=correction_id, **changes), row_id=2)
    before = EventLog(ledger).events_path.read_bytes()
    with pytest.raises(ValueError, match="correction_of"):
        _capture(native, row, ledger, binding=binding, **capture_changes)
    assert EventLog(ledger).events_path.read_bytes() == before


def test_canonical_admission_rehydrates_before_attesting(native, tmp_path, monkeypatch):
    from adapters.hermes_z0int import native_capture

    row = _persist(native)
    real_admit = native_capture.admit_user_claim

    def mutate_then_admit(*args, **kwargs):
        with sqlite3.connect(native / "state.db") as conn:
            conn.execute("UPDATE messages SET content = ? WHERE id = 1", (_content(subject="mutated"),))
        return real_admit(*args, **kwargs)

    monkeypatch.setattr(native_capture, "admit_user_claim", mutate_then_admit)
    with pytest.raises(ValueError, match="hydration failed"):
        _capture(native, row, tmp_path / "ledger")
    assert not (tmp_path / "ledger").exists()


def test_mutated_native_payload_cannot_replay_as_a_new_claim(native, tmp_path):
    from z0int.memory.event_log import EventIdentityConflict

    row = _persist(native)
    ledger = tmp_path / "ledger"
    _capture(native, row, ledger)
    before = EventLog(ledger).events_path.read_bytes()
    # Byte-only mutation: normalized claim remains identical, source hash does not.
    row["content"] += "\n"
    with sqlite3.connect(native / "state.db") as conn:
        conn.execute("UPDATE messages SET content = ? WHERE id = 1", (row["content"],))
    with pytest.raises(EventIdentityConflict):
        _capture(native, row, ledger)
    assert EventLog(ledger).events_path.read_bytes() == before


def test_canonical_source_ingest_opt_in_is_not_bypassed(native, tmp_path, monkeypatch):
    from z0int.memory.event_log import SourceIngestDisabled

    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "off")
    with pytest.raises(SourceIngestDisabled):
        _capture(native, _persist(native), tmp_path / "ledger")
    assert not list(EventLog(tmp_path / "ledger").iter_events())
