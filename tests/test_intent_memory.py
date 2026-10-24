"""Synthetic-only retrieval contracts. Run on the build host, not Groot."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

import pytest

from z0int.intent_memory import MemoryRetriever, SourceUnavailable, _read_local, validate_citations


def record(source="test", identity="native-1", text="synthetic phone policy", path=None):
    row = {"source": source, "id": identity, "text": text, "evidence_kind": "unknown"}
    if path is not None:
        row["path"] = str(path)
    return row


@pytest.fixture
def roots(tmp_path):
    return {name: tmp_path / name for name in (
        "memory_mcp", "events", "hermes", "chiefstaff", "agentsview", "recall",
        "statepack", "state_packet", "unified_index", "qmd",
    )}


def local_request(roots, source, query="phone", limit=5):
    return {"source": source, "query": query, "roots": {k: str(v) for k, v in roots.items()}, "limit": limit, "record_chars": 2400}


def event_line(identity, payload, **extras):
    row = {"schema": "z0int.memory.event.v1", "event_id": identity,
           "event_type": "intent.observed", "ts": 1.0, "source": "synthetic",
           "payload": payload, "parent_event_ids": [], **extras}
    raw = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    row["checksum"] = hashlib.sha256(raw.encode()).hexdigest()
    return json.dumps(row, ensure_ascii=False) + "\n"


def test_concurrent_sources_share_one_deadline_and_cancel():
    async def scenario():
        entered = set()
        cancelled = set()
        release = asyncio.Event()

        def blocked(name):
            async def run(query):
                entered.add(name)
                try:
                    await release.wait()
                    return []
                finally:
                    cancelled.add(name)
            return run

        async def fast(query):
            # Every slow source must have started, rather than serial waits.
            await asyncio.sleep(0.01)
            return [record()]

        retriever = MemoryRetriever(timeout_s=0.08, source_timeout_s=1,
            sources={"fast": fast, **{name: blocked(name) for name in ("a", "b", "c")}})
        start = time.monotonic()
        result = await retriever.retrieve("phone")
        assert time.monotonic() - start < 0.5
        assert entered == cancelled == {"a", "b", "c"}
        assert result["records"] == [record()]
        assert [s["status"] for s in result["sources"]] == ["answered", "timeout", "timeout", "timeout"]
        assert all(s["error"] == "total_deadline" for s in result["sources"][1:])
        assert all(s["latency_ms"] >= 0 for s in result["sources"])
    asyncio.run(scenario())


def test_per_source_timeout_is_independent():
    async def scenario():
        cancelled = asyncio.Event()
        async def slow(query):
            try:
                await asyncio.sleep(20)
            finally:
                cancelled.set()
        async def useful(query):
            await asyncio.sleep(0.06)
            return [record()]
        retriever = MemoryRetriever(timeout_s=1, source_timeout_s={"slow": 0.02, "useful": 0.2}, sources={"slow": slow, "useful": useful})
        result = await retriever.retrieve("phone")
        assert cancelled.is_set()
        assert result["sources"][0]["status"] == "timeout"
        assert result["sources"][0]["error"] == "source_deadline"
        assert result["sources"][1]["status"] == "answered"
        assert len(result["records"]) == 1
    asyncio.run(scenario())


def test_outer_cancellation_drains_all_sources():
    async def scenario():
        entered = asyncio.Event()
        exited = asyncio.Event()
        async def source(query):
            entered.set()
            try:
                await asyncio.sleep(20)
            finally:
                exited.set()
        retriever = MemoryRetriever(sources={"slow": source})
        task = asyncio.create_task(retriever.retrieve("phone"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert exited.is_set()
    asyncio.run(scenario())


def test_unavailable_is_not_an_answer_and_errors_do_not_leak_context():
    async def failure(query):
        raise RuntimeError("synthetic-private-context-do-not-return")
    async def empty(query):
        return []
    result = asyncio.run(MemoryRetriever(sources={"failed": failure, "empty": empty}).retrieve("phone"))
    assert result["records"] == []
    assert result["sources"][0]["status"] == "unavailable"
    assert result["sources"][0]["error"] == "RuntimeError"
    assert result["sources"][1]["status"] == "answered"
    assert "synthetic-private" not in json.dumps(result)


def test_stable_dedup_and_output_bounds(tmp_path):
    path = tmp_path / "memory.md"
    path.write_text("phone")
    async def source(query):
        return [record(identity="same", text="phone " * 10, path=path),
                record(identity="same", text="duplicate"), record(identity="second", text="phone " * 10),
                record(identity="missing", path=tmp_path / "not-there")]
    retriever = MemoryRetriever(sources={"one": source, "two": source}, record_chars=10, total_chars=17, max_records=3)
    first = asyncio.run(retriever.retrieve("phone"))
    second = asyncio.run(retriever.retrieve("phone"))
    assert first["records"] == second["records"]
    assert [r["id"] for r in first["records"]] == ["same", "second"]
    assert sum(len(r["text"]) for r in first["records"]) == 17


def test_query_and_recent_context_are_bounded():
    seen = []
    async def source(query):
        seen.append(query)
        return []
    retriever = MemoryRetriever(sources={"source": source})
    asyncio.run(retriever.retrieve("phone", ["old ignored", "fleet", "policy", "recent"]))
    assert seen == ["phone fleet policy recent"]
    asyncio.run(retriever.retrieve("界" * 1000))
    assert len(seen[-1].encode()) <= 1000


@pytest.mark.parametrize("citation", [
    "fabricated", {"id": "fabricated"}, {"source": "wrong", "id": "native-1"},
    {"id": "native-1", "path": "/etc/passwd"}, {"source": "test"},
    {"id": "native-1", "text": "spoof"}, {"id": ""}, 12, None,
])
def test_citation_spoofs_fail(citation, tmp_path):
    path = tmp_path / "memory.md"
    path.write_text("synthetic")
    assert not validate_citations([citation], [record(path=path)])


def test_catalog_citation_requires_exact_pair_and_live_path(tmp_path):
    path = tmp_path / "memory.md"
    other = tmp_path / "other.md"
    path.write_text("synthetic")
    other.write_text("synthetic")
    rows = [record(path=path), record(identity="native-2", path=other)]
    assert validate_citations(["native-1", str(path), {"source": "test", "id": "native-1", "path": str(path)}], rows)
    assert not validate_citations([{"id": "native-1", "path": str(other)}], rows)
    assert not validate_citations([str(path) + "#L1"], rows)
    path.unlink()
    assert not validate_citations(["native-1"], rows)
    assert not validate_citations([{"id": "native-1", "path": str(path)}], rows)


def test_remote_ids_only_match_returned_records():
    rows = [record(source="tencentdb", identity="42")]
    assert validate_citations([{"source": "tencentdb", "id": "42"}], rows)
    assert not validate_citations([{"source": "tencentdb", "id": "43"}], rows)
    assert not validate_citations([{"id": "42", "path": "https://example.invalid/42"}], rows)
    assert not validate_citations(["42"], rows + [record(source="expert", identity="42")])
    assert validate_citations([], rows)
    assert not validate_citations("42", rows)


def test_missing_and_invalid_paths_fail_closed():
    rows = [record(path="/does-not-exist/intent-test"), record(identity="null-path", path="/tmp/\0spoof")]
    assert not validate_citations(["native-1"], rows)
    assert not validate_citations(["null-path"], rows)


def test_source_roots_override_defaults_and_home(monkeypatch, tmp_path):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "home"))
    retriever = MemoryRetriever(source_roots={"hermes": tmp_path / "notes"}, sources={})
    assert retriever.roots["events"] == tmp_path / "home/memory/events.jsonl"
    assert retriever.roots["hermes"] == tmp_path / "notes"
    assert asyncio.run(retriever.retrieve("phone"))["records"] == []


def test_hermes_and_chiefstaff_are_retrieved_locally(roots):
    for key in ("hermes", "chiefstaff"):
        roots[key].mkdir()
    (roots["hermes"] / "MEMORY.md").write_text("Phone fleet policy.\n\nUnrelated oranges.")
    (roots["chiefstaff"] / "USER.md").write_text("Phone worker uses the fleet.")
    rows = _read_local(local_request(roots, "hermes"))
    assert len(rows) == 2
    assert all("phone" in row["text"].casefold() for row in rows)
    assert all(Path(row["path"]).is_file() for row in rows)
    assert rows == _read_local(local_request(roots, "hermes"))


def test_state_packet_headings_are_evidence_not_a_registry(roots):
    roots["state_packet"].write_text("# Orientation\n\n## phone fleet\n\nThe phone worker is local.")
    roots["unified_index"].write_text("phone → /synthetic/skill → read before operating")
    rows = _read_local(local_request(roots, "state_packet"))
    assert len(rows) == 3
    assert all(row["source"] == "state_packet" for row in rows)
    assert all("project" not in row for row in rows)


def test_events_preserve_native_id_checksum_and_partial_tail(roots):
    roots["events"].write_text(event_line(9, {"fact": "phone policy"}) + event_line(10, "oranges") + '{"unfinished":')
    rows = _read_local(local_request(roots, "events"))
    assert [row["id"] for row in rows] == ["9"]
    assert rows[0]["path"] == str(roots["events"])
    roots["events"].write_text(event_line(9, "phone").replace("phone", "spoof"))
    with pytest.raises(SourceUnavailable, match="corrupt_event_log"):
        _read_local(local_request(roots, "events"))


def test_event_blob_is_hydrated_and_integrity_checked(roots):
    blob = json.dumps({"fact": "phone policy"}, separators=(",", ":"))
    sha = hashlib.sha256(blob.encode()).hexdigest()
    ref = {"schema": "z0int.memory.blob_ref.v1", "sha256": sha, "bytes": len(blob.encode())}
    directory = roots["events"].parent / "blobs"
    directory.mkdir()
    path = directory / f"{sha}.json"
    path.write_text(blob)
    roots["events"].write_text(event_line(11, None, blob=ref))
    assert _read_local(local_request(roots, "events"))[0]["id"] == "11"
    path.write_text("spoof")
    with pytest.raises(SourceUnavailable, match="corrupt_blob"):
        _read_local(local_request(roots, "events"))


def test_agentsview_reads_fts_readonly_and_preserves_exact_identity(roots):
    roots["agentsview"].mkdir()
    path = roots["agentsview"] / "sessions.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, ordinal INTEGER, content TEXT, role TEXT, is_system INTEGER, has_thinking INTEGER, has_tool_use INTEGER)")
        db.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(content,content='messages',content_rowid='id')")
        db.executemany("INSERT INTO messages VALUES (?,?,?,?,?,?,?,?)", [
            (7, "session", 2, "phone fleet", "user", 0, 0, 0),
            (8, "session", 3, "phone hidden", "assistant", 0, 1, 0),
            (9, "session", 4, "phone tools", "assistant", 0, 0, 1),
        ])
        db.execute("INSERT INTO messages_fts(messages_fts) VALUES ('rebuild')")
    before = path.read_bytes()
    rows = _read_local(local_request(roots, "agentsview"))
    assert [row["id"] for row in rows] == ["agentsview:session:message:7:ordinal:2"]
    assert rows[0]["text"] == "phone fleet"
    assert path.read_bytes() == before


def test_local_worker_runs_under_overridden_roots_only(roots):
    roots["hermes"].mkdir()
    (roots["hermes"] / "MEMORY.md").write_text("synthetic phone worker")
    retriever = MemoryRetriever(source_roots=roots, sources={})
    retriever.sources = {"hermes": lambda query: retriever._local("hermes", query)}
    result = asyncio.run(retriever.retrieve("phone"))
    assert result["sources"][0]["status"] == "answered"
    assert result["records"][0]["text"] == "synthetic phone worker"


def test_command_collects_all_output_chunks_and_reaps_on_timeout(tmp_path):
    async def scenario():
        retriever = MemoryRetriever(sources={})
        output = await retriever._command([sys.executable, "-c", "import sys,time; print('first',flush=True); time.sleep(.02); print('second',flush=True)"])
        assert output == b"first\nsecond\n"
        pidfile = tmp_path / "owned.pid"
        code = "import os,pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(30)"
        task = asyncio.create_task(retriever._command([sys.executable, "-c", code, str(pidfile)]))
        for _ in range(200):
            if pidfile.exists():
                break
            await asyncio.sleep(.005)
        assert pidfile.exists()
        pid = int(pidfile.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    asyncio.run(scenario())


def test_command_output_limit_kills_reader():
    async def scenario():
        retriever = MemoryRetriever(sources={})
        with pytest.raises(SourceUnavailable, match="output_limit"):
            await retriever._command([sys.executable, "-c", "import sys; sys.stdout.write('x'*1100000)"])
    asyncio.run(scenario())


def test_tencentdb_native_protocol_and_no_invented_ids(monkeypatch):
    retriever = MemoryRetriever(sources={})
    calls = []
    async def http(port, path, **kwargs):
        calls.append((port, path, kwargs))
        return {"code": 0, "data": {"items": [{"id": "native-42", "content": "phone"}, {"content": "no native id"}]}}
    monkeypatch.setattr(retriever, "_http", http)
    rows = asyncio.run(retriever._tencentdb("phone"))
    assert rows == [record(source="tencentdb", identity="native-42", text="phone")]
    assert calls[0][0:2] == (8420, "/v3/atomic/search")
    assert calls[0][2]["body"]["query"] == "phone"
    assert calls[0][2]["body"]["limit"] == 5


def test_statepack_uses_untyped_existing_api(roots):
    roots["statepack"].write_text("def orient(query, *, typed=True):\n    assert typed is False\n    return {'evidence': [{'source_id':'synthetic','locator':'native-1','excerpt':query}]}\n")
    retriever = MemoryRetriever(source_roots=roots, sources={})
    rows = asyncio.run(retriever._statepack("phone"))
    assert rows == [record(source="statepack", identity="synthetic:native-1", text="phone")]


def test_qmd_reads_document_not_search_preview(monkeypatch):
    retriever = MemoryRetriever(sources={})
    calls = []
    async def command(argv, payload=None):
        calls.append(argv)
        if "search" in argv:
            return b'[{"docid":"#abcdef","file":"qmd://notes/phone.md","snippet":"preview only"}]'
        return b"qmd://notes/phone.md #abcdef\n---\noriginal phone evidence"
    monkeypatch.setattr(retriever, "_command", command)
    rows = asyncio.run(retriever._qmd("phone"))
    assert rows[0]["id"] == "#abcdef"
    assert "original phone evidence" in rows[0]["text"]
    assert [call[1] for call in calls] == ["search", "get"]
    assert not any("query" in call or "vsearch" in call for call in calls)


def test_unii_uses_readonly_export_no_model_act(monkeypatch):
    monkeypatch.setenv("Z0INT_INTENT_UNII_TOKEN", "synthetic-token")
    retriever = MemoryRetriever(sources={})
    calls = []
    async def http(port, path, **kwargs):
        calls.append((port, path, kwargs))
        return {"messages": [{"id": 7, "kind": "unii", "text": "phone fleet rule"}, {"id": 8, "kind": "mind", "text": "phone private reasoning"}, {"kind": "unii", "text": "phone missing identity"}]}
    monkeypatch.setattr(retriever, "_http", http)
    assert asyncio.run(retriever._expert("phone")) == [record(source="unified_expert", identity="7", text="phone fleet rule")]
    assert calls[0][0:2] == (8798, "/export")
    assert "body" not in calls[0][2]


def test_unii_missing_auth_reports_unavailable_without_call(monkeypatch):
    monkeypatch.delenv("Z0INT_INTENT_UNII_TOKEN", raising=False)
    retriever = MemoryRetriever(sources={})
    retriever.sources = {"unified_expert": retriever._expert}
    result = asyncio.run(retriever.retrieve("phone"))
    assert result["records"] == []
    assert result["sources"][0]["status"] == "unavailable"
    assert result["sources"][0]["error"] == "expert_read_auth_unavailable"


def test_mcp_search_fetch_same_connection_preserves_native_identity(tmp_path):
    async def scenario():
        path = tmp_path / "operator.sock"
        seen = []
        original = {"source": "agentsview", "id": "opaque-handle", "locator": "agentsview:session:message:9:ordinal:2", "source_version": "sha256:synthetic", "text": "phone original"}
        async def serve(reader, writer):
            try:
                for _ in range(2):
                    message = json.loads(await reader.readline())
                    seen.append(message)
                    name = message["params"]["name"]
                    payload = {"status": "ok", "results": [original]} if name == "search" else {"status": "ok", "record": original}
                    frame = {"jsonrpc": "2.0", "id": message["id"], "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}}
                    writer.write(json.dumps(frame).encode() + b"\n")
                    await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_unix_server(serve, path=str(path))
        path.chmod(0o600)
        try:
            retriever = MemoryRetriever(source_roots={"memory_mcp": path}, sources={})
            rows = await retriever._mcp("phone")
            assert rows == [record(source="agentsview", identity=original["locator"], text="phone original")]
            assert [message["params"]["name"] for message in seen] == ["search", "fetch"]
            assert seen[1]["params"]["arguments"] == {"id": "opaque-handle"}
            path.chmod(0o666)
            with pytest.raises(SourceUnavailable, match="private_socket_required"):
                await retriever._mcp("phone")
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(scenario())


def test_loopback_http_decodes_chunked_json_and_never_redirects():
    async def scenario():
        seen = []
        async def serve(reader, writer):
            seen.append(await reader.readuntil(b"\r\n\r\n"))
            writer.write(b'HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\na\r\n{"ok":true\r\n1\r\n}\r\n0\r\n\r\n')
            await writer.drain()
            writer.close()
            await writer.wait_closed()
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        try:
            port = server.sockets[0].getsockname()[1]
            retriever = MemoryRetriever(sources={})
            assert await retriever._http(port, "/export") == {"ok": True}
            assert seen[0].startswith(b"GET /export HTTP/1.1")
            assert b"Host: 127.0.0.1:" in seen[0]
        finally:
            server.close()
            await server.wait_closed()
    asyncio.run(scenario())


@pytest.mark.parametrize("kwargs", [{"timeout_s": 0}, {"timeout_s": float("inf")}, {"source_timeout_s": -1}, {"max_records": 0}, {"expert_port": 70000}])
def test_invalid_configuration_is_rejected(kwargs):
    with pytest.raises(ValueError):
        MemoryRetriever(**kwargs)
