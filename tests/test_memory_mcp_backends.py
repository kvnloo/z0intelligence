"""Native-shaped AgentsView HTTP contract and full MCP facade tests.

Only deterministic dict fixtures; never connect to live archives or user data.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from z0int import memory_mcp_backends as backend
from z0int.memory_mcp import MemoryServer


SESSION = "codex:synthetic-111"
PROJECT = "test-project"


def grant(source="agentsview", scope=PROJECT):
    return {"source": source, "collection": "history",
            "scope": scope, "privacy": ["private"]}


@pytest.fixture()
def native(tmp_path, monkeypatch):
    root = tmp_path / ".agentsview"
    root.mkdir()
    (root / "config.toml").write_text(
        'port = 19481\nauth_token = "synthetic-token-not-real"\n'
    )
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    original = {
        "id": 701, "session_id": SESSION,
        "ordinal": 7, "role": "assistant", "is_system": False,
        "has_thinking": False, "has_tool_use": False,
        "thinking_text": "", "content": "Corrected synthetic answer.",
        "timestamp": "2026-10-22T17:00:00Z",
    }
    state = {
        "original": original, "project": PROJECT,
        "down": False, "calls": [], "omit_original": False,
        "match_location": "message",
    }

    def http(port, path, deadline, **kwargs):
        assert port == 19481
        assert deadline > 0
        assert kwargs["headers"]["Authorization"] == "Bearer synthetic-token-not-real"
        state["calls"].append(path)
        if state["down"]:
            raise backend.BackendUnavailable("synthetic server outage")
        if path.startswith("/api/v1/search/content?"):
            query = parse_qs(urlsplit(path).query)
            assert query["mode"] == ["fts"]
            assert query["in"] == ["messages"]
            assert query["reveal"] == ["false"]
            assert query["exclude_system"] == ["true"]
            assert query["project"] == [PROJECT] or query["project"] == ["other"]
            return {"matches": [
                {"project": query["project"][0], "session_id": SESSION,
                 "ordinal": 7, "location": state["match_location"],
                 "snippet": "UNTRUSTED_CACHED_SNIPPET_SHOULD_NOT_HYDRATE",
                 "timestamp": "2026-10-22T17:00:00Z"},
                {"project": "other", "session_id": "other-session",
                 "ordinal": 1, "location": "message", "snippet": "wrong project"},
            ]}
        if path.startswith("/api/v1/sessions/") and path.endswith("/messages?around=7&before=0&after=0"):
            assert unquote(path.split("/")[4]) == SESSION
            return {"messages": [] if state["omit_original"] else [dict(state["original"])],
                    "count": int(not state["omit_original"])}
        if path.startswith("/api/v1/sessions/"):
            assert unquote(path.split("/")[4]) == SESSION
            return {"id": SESSION, "project": state["project"], "machine": "fixture-host"}
        raise AssertionError("unauthorized route attempted")

    monkeypatch.setattr(backend, "_loopback_json", http)
    return state


def server(tmp_path, *, principal="operator", operator=None, chatgpt=None,
           result_bytes=4096):
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({
        "version": 2,
        "principals": {
            "operator": [grant()] if operator is None else operator,
            "chatgpt": [] if chatgpt is None else chatgpt,
        },
        "limits": {"result_bytes": result_bytes, "total_bytes": 32768,
                   "calls_per_minute": 30},
        "timeout_seconds": 2,
    }))
    return MemoryServer(policy, tmp_path / "state", principal=principal), policy


def test_real_adapter_search_reads_original_and_fetch_rereads_native_message(native, tmp_path):
    facade, _ = server(tmp_path)
    response = facade.search("user correction")
    assert response["status"] == "ok"
    hit = response["results"][0]
    assert hit["text"] == native["original"]["content"]
    assert "UNTRUSTED_CACHED_SNIPPET" not in json.dumps(response)
    assert hit["locator"] == f"agentsview:{SESSION}:message:701:ordinal:7"
    digest = "sha256:" + hashlib.sha256(native["original"]["content"].encode()).hexdigest()
    assert hit["source_version"] == digest
    assert digest in json.dumps(response["context_packet"])
    calls_after_search = len(native["calls"])
    assert calls_after_search == 4  # search, scope, exact message, recheck scope
    fetched = facade.fetch(hit["id"])
    assert fetched["status"] == "ok"
    assert fetched["record"]["mode"] == "original_read"
    assert fetched["record"]["text"] == native["original"]["content"]
    assert fetched["record"]["source_version"] == digest
    assert len(native["calls"]) == calls_after_search + 3  # Scope+original+scope
    assert all(path.startswith("/api/v1/") for path in native["calls"])


def test_same_date_same_size_replacement_is_stale_in_full_native_pipeline(native, tmp_path):
    facade, _ = server(tmp_path)
    issued = facade.search("revision")["results"][0]["id"]
    native["original"]["content"] = "Corrected synthetic anSwer."
    response = facade.fetch(issued)
    assert response["error"] == "stale"
    assert "record" not in response
    assert "Corrected synthetic" not in json.dumps(response)


@pytest.mark.parametrize("project", ["other", "", None])
def test_original_project_change_or_unknown_denies_without_content(native, tmp_path, project):
    facade, _ = server(tmp_path)
    token = facade.search("before scope change")["results"][0]["id"]
    native["project"] = project
    result = facade.fetch(token)
    assert result["error"] == "denied"
    assert "record" not in result


def test_wrong_project_during_search_is_empty_without_message_reads(native, tmp_path):
    native["project"] = "sibling-repo"
    facade, _ = server(tmp_path)
    reply = facade.search("strict scope")
    assert reply["status"] == "empty"
    assert reply["results"] == []
    assert not any("/messages?" in path for path in native["calls"])


@pytest.mark.parametrize("setting", ["is_system", "has_thinking", "has_tool_use", "thinking_text", "role"])
def test_forbidden_message_class_never_exposes_native_content(native, tmp_path, setting):
    if setting == "role":
        native["original"]["role"] = "tool"
    elif setting == "thinking_text":
        native["original"]["thinking_text"] = "hidden synthetic reasoning"
    else:
        native["original"][setting] = True
    facade, _ = server(tmp_path)
    result = facade.search("class")
    assert result["status"] == "empty"
    assert "Corrected synthetic" not in json.dumps(result)


def test_search_result_tool_location_not_misrepresented_as_message(native, tmp_path):
    native["match_location"] = "tool_result"
    facade, _ = server(tmp_path)
    reply = facade.search("tool")
    assert reply["status"] == "empty"
    assert not any("/messages?" in path for path in native["calls"])


def test_missing_original_is_unavailable_not_fabricated_hydration(native, tmp_path):
    native["omit_original"] = True
    facade, _ = server(tmp_path)
    reply = facade.search("missing")
    assert reply["status"] == "unavailable"
    assert reply["results"] == []
    assert reply["backends"] == {"agentsview": "unavailable"}


def test_outage_during_fetch_does_not_reuse_search_preview(native, tmp_path):
    facade, _ = server(tmp_path)
    issued = facade.search("outage")["results"][0]["id"]
    native["down"] = True
    assert facade.fetch(issued) == {"error": "unavailable"}


def test_native_reauthorizes_after_policy_revocation(native, tmp_path):
    facade, policy_path = server(tmp_path)
    token = facade.search("revocation")["results"][0]["id"]
    value = json.loads(policy_path.read_text())
    value["principals"]["operator"] = []
    policy_path.write_text(json.dumps(value))
    previous = len(native["calls"])
    result = facade.fetch(token)
    assert result["error"] == "denied"
    assert len(native["calls"]) == previous


def test_chatgpt_explicit_grant_separate_from_operator(native, tmp_path):
    facade, _ = server(tmp_path, principal="chatgpt")
    assert facade.search("not granted")["status"] == "denied"
    assert not native["calls"]
    # Same real source, but only after a separate ChatGPT grant exists.
    facade, _ = server(tmp_path, principal="chatgpt", chatgpt=[grant()])
    hit = facade.search("granted")["results"][0]
    assert facade.fetch(hit["id"])["status"] == "ok"


def test_secret_redaction_before_original_clip(native, tmp_path):
    key = "sk-" + "Aa" * 45
    native["original"]["content"] = "open\nOPENAI_API_KEY=" + key + "\nclosed"
    facade, _ = server(tmp_path)
    hit = facade.search("secret")["results"][0]
    fetched = facade.fetch(hit["id"])
    serialized = json.dumps([hit, fetched])
    assert key not in serialized
    assert "open" in serialized and "closed" in serialized


def test_unsupported_sources_cannot_use_search_or_fetch_or_commands(monkeypatch):
    invoked = []
    monkeypatch.setattr(backend, "_loopback_json",
                        lambda *args, **kwargs: invoked.append(args))
    for name in ("ctx", "qmd", "tencentdb"):
        with pytest.raises(backend.BackendUnavailable, match="exact_source_unavailable"):
            backend.search_backend(name, "no-op", [grant(name)], 1, 1)
        with pytest.raises(backend.BackendUnavailable, match="exact_source_unavailable"):
            backend.fetch_backend(name, {"native": {}}, grant(name), 1)
    assert invoked == []


@pytest.mark.parametrize("path", [
    "/etc/passwd",
    "/api/v1/sessions/%2Fetc%2Fpasswd/messages?around=7&before=0&after=0",
    "http://evil.test/api/v1/search/content?pattern=x",
    "/api/v1/sessions/abc/resume",
    "/v3/atomic/search",
])
def test_http_transport_rejects_nonfixed_endpoints_before_network(path):
    with pytest.raises(backend.BackendUnavailable, match="unsupported_endpoint"):
        backend._loopback_json(19481, path, 10_000_000_000.0)


def test_encoded_native_id_and_invalid_id_are_bounded(native):
    rows = backend.search_backend("agentsview", "verified", [grant()], 1, 2)
    assert rows[0]["native"] == {"session_id": SESSION, "ordinal": 7, "id": 701}
    assert any("%3A" in url for url in native["calls"])
    with pytest.raises(backend.BackendUnavailable, match="unsupported_native_identity"):
        backend._native_id("../outside", 7)
    with pytest.raises(backend.BackendUnavailable, match="unsupported_native_identity"):
        backend._native_id(SESSION, -1)


@pytest.mark.parametrize("bad_session", ["codex:sibling-session", None])
def test_adapter_search_denies_wrong_or_missing_message_session(native, tmp_path, bad_session):
    native["original"]["session_id"] = bad_session
    facade, _ = server(tmp_path)
    result = facade.search("same ordinal but wrong session")
    assert result == {"error": "denied"}
    assert "Corrected synthetic answer." not in json.dumps(result)
    assert "machine" not in json.dumps(result)
    assert not any("UNTRUSTED_CACHED_SNIPPET" in str(x) for x in result.values())


@pytest.mark.parametrize("bad_session", ["codex:sibling-session", None])
def test_adapter_fetch_denies_wrong_or_missing_message_session(native, tmp_path, bad_session):
    facade, _ = server(tmp_path)
    token = facade.search("authorized")["results"][0]["id"]
    native["original"]["session_id"] = bad_session
    result = facade.fetch(token)
    assert result == {"error": "denied"}
    assert "record" not in result and "context_packet" not in result
    assert "Corrected synthetic answer." not in json.dumps(result)


@pytest.mark.parametrize("bad_id", [None, 0, -1, True, 1 << 63, "701"])
def test_adapter_search_denies_missing_or_invalid_stable_message_id(native, tmp_path, bad_id):
    if bad_id is None:
        native["original"].pop("id")
    else:
        native["original"]["id"] = bad_id
    facade, _ = server(tmp_path)
    result = facade.search("native id required")
    assert result == {"error": "denied"}
    assert "Corrected synthetic answer." not in json.dumps(result)


def test_adapter_fetch_denies_missing_native_message_id(native, tmp_path):
    facade, _ = server(tmp_path)
    token = facade.search("before missing id")["results"][0]["id"]
    native["original"].pop("id")
    result = facade.fetch(token)
    assert result == {"error": "denied"}
    assert "record" not in result and "context_packet" not in result


def test_adapter_identical_text_replacement_changes_locator_and_denies_old_handle(native, tmp_path):
    facade, _ = server(tmp_path)
    hit = facade.search("original")["results"][0]
    old_hash = hit["source_version"]
    old_locator = hit["locator"]
    native["original"]["id"] = 702  # Same session/ordinal/date/content.
    result = facade.fetch(hit["id"])
    assert result == {"error": "denied"}
    assert "record" not in result and "context_packet" not in result
    current = facade.search("replacement")["results"][0]
    assert current["source_version"] == old_hash  # Content hash remains byte-pinned.
    assert current["locator"] == f"agentsview:{SESSION}:message:702:ordinal:7"
    assert current["locator"] != old_locator


def test_backend_direct_fetch_requires_full_native_locator(native):
    rows = backend.search_backend("agentsview", "verified", [grant()], 1, 2)
    valid = rows[0]
    assert valid["locator"] == backend.agentsview_locator(valid["native"])
    for broken in (
        {**valid, "native": {"session_id": SESSION, "ordinal": 7}},
        {**valid, "locator": "agentsview:bad:message:701:ordinal:7"},
    ):
        with pytest.raises(backend.BackendIdentityDenied):
            backend.fetch_backend("agentsview", broken, grant(), 2)


def _per_project(native, monkeypatch, *, slow=(), down=()):
    """Wrap the native fixture: some projects time out (consume the deadline) or are down."""
    inner = backend._loopback_json

    def http(port, path, deadline, **kwargs):
        if path.startswith("/api/v1/search/content?"):
            project = parse_qs(urlsplit(path).query)["project"][0]
            if project in down:
                raise backend.BackendUnavailable("synthetic outage")
            if project in slow:
                import time as _t
                _t.sleep(max(0.0, deadline - _t.monotonic()) + 0.05)
                raise backend.BackendUnavailable("deadline")
        return inner(port, path, deadline, **kwargs)

    monkeypatch.setattr(backend, "_loopback_json", http)


def test_one_slow_grant_does_not_hide_the_others(native, monkeypatch):
    """Live failure: a cold first project used the whole 10 s budget, so every search was unavailable."""
    _per_project(native, monkeypatch, slow={"cold-project"})
    import time as _t
    start = _t.monotonic()
    rows = backend.search_backend("agentsview", "verified", [grant(scope="cold-project"), grant()], 1, 1)
    assert [r["scope"] for r in rows] == [PROJECT]
    assert _t.monotonic() - start < 1.5


def test_all_grants_down_is_still_unavailable(native, monkeypatch):
    _per_project(native, monkeypatch, down={PROJECT, "other"})
    with pytest.raises(backend.BackendUnavailable):
        backend.search_backend("agentsview", "verified", [grant(), grant(scope="other")], 1, 1)


def test_identity_violation_in_any_grant_denies_the_whole_search(native, monkeypatch):
    _per_project(native, monkeypatch, down={"other"})
    native["original"]["session_id"] = "codex:sibling-session"
    with pytest.raises(backend.BackendIdentityDenied):
        backend.search_backend("agentsview", "verified", [grant(scope="other"), grant()], 1, 1)



def _sqlite_archive(tmp_path, rows, *, user_version=113, stamps=None):
    """Minimal AgentsView-shaped sessions.db: sessions, messages, external-content FTS5."""
    import sqlite3
    path = tmp_path / ".agentsview" / "sessions.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        create table sessions (id text primary key, project text, machine text);
        create table messages (id integer primary key, session_id text, ordinal integer, role text, content text,
          timestamp text, is_system integer default 0, has_thinking integer default 0,
          has_tool_use integer default 0, thinking_text text default '');
        create virtual table messages_fts using fts5(content, content='messages', content_rowid='id');
    """)
    for sid, project, ordinal, role, content, extra in rows:
        conn.execute("insert or ignore into sessions values (?,?,?)", (sid, project, "fixture-host"))
        cur = conn.execute("insert into messages (session_id, ordinal, role, content, timestamp, has_thinking) "
                           "values (?,?,?,?,?,?)", (sid, ordinal, role, content,
                                                    (stamps or {}).get((sid, ordinal), "2026-10-22T17:00:00Z"),
                                                    int(extra == "thinking")))
        conn.execute("insert into messages_fts (rowid, content) values (?, ?)", (cur.lastrowid, content))
    conn.execute(f"pragma user_version = {user_version}")
    conn.commit()
    conn.close()
    return path


def test_sqlite_path_is_scoped_in_sql_and_never_touches_the_http_server(native, tmp_path):
    _sqlite_archive(tmp_path, [
        (SESSION, PROJECT, 7, "assistant", "use uiautomator2 for android", None),
        ("codex:other-1", "other", 1, "user", "uiautomator2 in another project", None),
        (SESSION, PROJECT, 8, "assistant", "uiautomator2 hidden reasoning", "thinking"),
    ])
    rows = backend.search_backend("agentsview", "uiautomator2", [grant()], 5, 2)
    assert [r["text"] for r in rows] == ["use uiautomator2 for android"]
    assert rows[0]["scope"] == PROJECT and rows[0]["host"] == "fixture-host"
    assert native["calls"] == []
    identity = {k: rows[0][k] for k in ("source", "collection", "scope", "privacy", "locator", "native",
                                        "source_version")}
    again = backend.fetch_backend("agentsview", identity, grant(), 2)
    assert again["text"] == rows[0]["text"] and native["calls"] == []


def test_sqlite_fetch_denies_a_session_moved_out_of_scope(native, tmp_path):
    import sqlite3
    path = _sqlite_archive(tmp_path, [(SESSION, PROJECT, 7, "assistant", "scoped answer", None)])
    row = backend.search_backend("agentsview", "scoped", [grant()], 1, 2)[0]
    conn = sqlite3.connect(path)
    conn.execute("update sessions set project = 'elsewhere'")
    conn.commit()
    conn.close()
    identity = {k: row[k] for k in ("source", "collection", "scope", "privacy", "locator", "native",
                                    "source_version")}
    with pytest.raises(backend.BackendDenied):
        backend.fetch_backend("agentsview", identity, grant(), 2)


def test_unknown_archive_schema_falls_back_to_http(native, tmp_path):
    _sqlite_archive(tmp_path, [(SESSION, PROJECT, 7, "assistant", "verified", None)], user_version=1)
    rows = backend.search_backend("agentsview", "verified", [grant()], 1, 2)
    assert rows and native["calls"]  # served by the HTTP contract path


def test_sqlite_ranks_across_grants_instead_of_first_grant_filling_every_slot(native, tmp_path):
    filler = " ".join(["cron status line"] * 40)
    rows = [(f"codex:cron-{i}", "automation", 1, "assistant", f"{filler} hw_monitor {filler}", None) for i in range(6)]
    rows.append((SESSION, PROJECT, 7, "user", "call the folder hw_monitor on the big drive", None))
    _sqlite_archive(tmp_path, rows)
    found = backend.search_backend("agentsview", "hw_monitor", [grant(scope="automation"), grant()], 3, 2)
    assert found[0]["scope"] == PROJECT and "hw_monitor" in found[0]["text"]
    assert native["calls"] == []


def test_sqlite_caps_hits_per_session_and_keeps_scope_filter(native, tmp_path):
    rows = [(SESSION, PROJECT, i, "assistant", f"larkd note {i}", None) for i in range(1, 6)]
    rows.append(("codex:other-2", PROJECT, 1, "user", "larkd once", None))
    rows.append(("codex:hidden-3", "ungranted", 1, "user", "larkd larkd larkd", None))
    _sqlite_archive(tmp_path, rows)
    found = backend.search_backend("agentsview", "larkd", [grant()], 5, 2)
    sessions = [r["native"]["session_id"] for r in found]
    assert sessions.count(SESSION) == 2 and "codex:other-2" in sessions
    assert all(r["scope"] == PROJECT for r in found)


def test_sqlite_exact_phrase_beats_old_high_term_frequency_rows(native, tmp_path):
    # Old rows repeat the terms many times; only the owner's message has the exact phrase.
    filler = " ".join(["why is this automatable not"] * 30)
    rows = [(f"codex:old-{i}", "automation", 1, "assistant", f"{filler} report {i}", None) for i in range(12)]
    rows.append((SESSION, PROJECT, 7, "user", "ok but why is this not automatable? check memory first", None))
    _sqlite_archive(tmp_path, rows)
    found = backend.search_backend("agentsview", "why is this not automatable",
                                   [grant(scope="automation"), grant()], 5, 2)
    assert found[0]["native"]["session_id"] == SESSION
    assert "why is this not automatable" in found[0]["text"]
    assert len(found) == 5 and native["calls"] == []  # token pass still fills the remaining slots


def test_sqlite_phrase_and_token_passes_share_the_per_session_cap(native, tmp_path):
    rows = [(SESSION, PROJECT, i, "assistant", f"run the quiet lane now {i}", None) for i in range(1, 6)]
    rows += [(SESSION, PROJECT, 10 + i, "assistant", f"lane quiet run {i}", None) for i in range(1, 4)]
    rows.append(("codex:other-9", PROJECT, 1, "user", "quiet lane", None))
    _sqlite_archive(tmp_path, rows)
    found = backend.search_backend("agentsview", "quiet lane", [grant()], 5, 2)
    sessions = [r["native"]["session_id"] for r in found]
    assert sessions.count(SESSION) == 2 and "codex:other-9" in sessions
    assert len({(r["native"]["session_id"], r["native"]["ordinal"]) for r in found}) == len(found)


def test_sqlite_equal_rank_prefers_the_newer_message(native, tmp_path):
    rows = [("codex:older-1", PROJECT, 1, "user", "zanzibar plan", None),
            ("codex:newer-2", PROJECT, 1, "user", "zanzibar plan", None)]
    _sqlite_archive(tmp_path, rows, stamps={("codex:older-1", 1): "2026-09-01T00:00:00Z",
                                            ("codex:newer-2", 1): "2026-10-26T00:00:00Z"})
    found = backend.search_backend("agentsview", "zanzibar", [grant()], 2, 2)
    assert [r["native"]["session_id"] for r in found] == ["codex:newer-2", "codex:older-1"]


def test_sqlite_host_uses_agentsview_machine_label_when_present(native, tmp_path):
    import sqlite3
    path = _sqlite_archive(tmp_path, [(SESSION, PROJECT, 7, "assistant", "labelled host answer", None),
                                      ("codex:other-5", PROJECT, 1, "user", "labelled raw host", None)])
    conn = sqlite3.connect(path)
    conn.execute("update sessions set machine = 'b163c2db159f66e6' where id = ?", (SESSION,))
    conn.execute("update sessions set machine = 'mbp' where id = 'codex:other-5'")
    conn.execute("create table pg_sync_state (key text primary key, value text not null)")
    conn.execute("insert into pg_sync_state values ('machine_label:b163c2db159f66e6', 'groot')")
    conn.commit()
    conn.close()
    hosts = {r["native"]["session_id"]: r["host"]
             for r in backend.search_backend("agentsview", "labelled", [grant()], 5, 2)}
    assert hosts == {SESSION: "groot", "codex:other-5": "mbp"}


def test_sqlite_host_falls_back_to_raw_machine_without_label_table(native, tmp_path):
    _sqlite_archive(tmp_path, [(SESSION, PROJECT, 7, "assistant", "unlabelled host answer", None)])
    rows = backend.search_backend("agentsview", "unlabelled", [grant()], 1, 2)
    assert rows[0]["host"] == "fixture-host"
