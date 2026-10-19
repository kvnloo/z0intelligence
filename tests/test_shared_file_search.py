"""Real shared-service retrieval; never replace the native FFF content index."""
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import threading
import urllib.error
import urllib.request

import pytest

from z0int import file_search
from z0int.intelligence_service import Handler, Service
from z0int.memory.event_log import EventLog


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    (root / "engine.py").write_text(
        "# Native content, deliberately absent from the file name.\n"
        "def quasar_shared_needle():\n    return 'bounded evidence'\n"
    )
    return root


@pytest.fixture
def service(tmp_path, monkeypatch, repository):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("Z0INT_FILE_SEARCH_ROOTS", json.dumps([str(repository)]))
    monkeypatch.delenv("Z0INT_FFF_DISABLE", raising=False)
    file_search.close_all()
    server = Service(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        file_search.close_all()


def post(service, payload):
    request = urllib.request.Request(
        service + "/v1/file-search", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def request_for(repository, **overrides):
    return {
        "repository_root": str(repository), "query": "quasar_shared_needle",
        "kind": "exact_symbol", "harness": "codex",
        "trace_id": "search-trace", "session_id": "search-session", **overrides,
    }


def assert_content_packet(body, repository):
    assert body["ok"] is True, body
    assert body["schema"] == "z0int.harness_context.v1"
    packet = body["packet"]
    assert packet["schema"] == "z0int.context_resolve.v1"
    assert packet["measurements"]["fff_status"] == "ready"
    assert packet["measurements"]["gpu_loaded"] is False
    assert packet["measurements"]["network_model_calls"] == 0
    assert packet["measurements"]["allow_qmd"] is False
    assert packet["unresolved_gaps"] == []
    ref = next(ref for ref in packet["evidence"] if ref["locator"].endswith("engine.py:2"))
    assert ref["source_id"] == f"file:{repository}/engine.py"
    assert "quasar_shared_needle" in ref["excerpt"]
    assert ref["trust_class"] == "index_hit"
    assert "fff=0.11.0;epoch=" in ref["source_version"]
    return packet


def test_http_native_content_and_cross_harness_index_reuse(service, repository):
    for index, harness in enumerate(("codex", "claude-code", "dsh", "hermes")):
        trace = f"trace-{harness}"
        status, body = post(service, request_for(repository, harness=harness, trace_id=trace))
        assert status == 200, body
        packet = assert_content_packet(body, repository)
        assert packet["task_id"] == trace
        assert body["harness_id"] == harness
        operation = next(op for op in packet["recipe"]["operations"] if op["op"] == "fff_symbol")
        assert operation["reused"] is (index > 0)
        event = EventLog().get(body["event_id"])
        assert event.source == f"harness:{harness}"
        assert event.project == str(repository)
        assert event.session_id == "search-session"
        assert isinstance(event.payload, dict)
        assert event.payload["task_id"] == trace
        assert event.payload["execution_completed"] is False
        assert event.payload["verified_success"] is None
        assert event.payload["evidence"] == packet["evidence"]
    assert len(file_search._POOL) == 1


def test_http_rejects_invalid_requests_before_indexing(service, repository):
    invalid_fields = [
        {"harness": "invented"}, {"harness": []}, {"harness": ""},
        {"repository_root": "relative"}, {"repository_root": "/"},
        {"repository_root": str(repository / "missing")},
        {"repository_root": str(repository / "engine.py")},
        {"repository_root": 17}, {"repository_root": "\x00"},
        {"repository_root": "/" + "a" * 4096},
        {"query": ""}, {"query": "   "}, {"query": ["needle"]},
        {"query": "a" * 2049}, {"query": "bad\x00query"},
        {"query": "bad\ud800query"}, {"kind": "memory"}, {"kind": []},
        {"trace_id": 7}, {"trace_id": "a" * 257},
        {"session_id": False}, {"session_id": "bad\nidentity"},
        {"allow_qmd": True}, {"record": False},
        {"limit": 0}, {"limit": 21}, {"limit": True}, {"limit": 2.5},
        {"time_budget_ms": 9}, {"time_budget_ms": 1001},
        {"time_budget_ms": "120"}, {"time_budget_ms": False},
    ]
    payloads = [request_for(repository, **fields) for fields in invalid_fields]
    payloads += [[], None, {}, {"query": "needle"}]
    for payload in payloads:
        status, body = post(service, payload)
        assert status == 400, (payload, status, body)
    assert not file_search._POOL
    assert not EventLog().events_path.exists()


def test_http_exact_paths_cannot_escape_repository(service, repository):
    outside = repository.parent / "outside.py"
    outside.write_text("OUTSIDE_PRIVATE_MARKER\n")
    (repository / "escape.py").symlink_to(outside)
    (repository / "escape-dir").symlink_to(repository.parent, target_is_directory=True)
    for query in (str(outside), "../outside.py", "escape.py", "escape-dir/outside.py"):
        status, body = post(service, request_for(repository, kind="exact_path", query=query))
        assert status == 400, (query, body)
        assert "OUTSIDE_PRIVATE_MARKER" not in json.dumps(body)
    assert not EventLog().events_path.exists()
    for query in ("engine.py", str(repository / "engine.py")):
        status, body = post(service, request_for(repository, kind="exact_path", query=query))
        assert status == 200
        assert body["packet"]["evidence"][0]["locator"] == str(repository / "engine.py")
        assert len(body["packet"]["evidence"][0]["excerpt"]) <= 400
    assert not file_search._POOL  # Known paths use the direct resolver, not FFF.
    status, body = post(service, request_for(repository, query="OUTSIDE_PRIVATE_MARKER"))
    assert status == 200
    assert body["packet"]["evidence"] == []
    assert body["packet"]["unresolved_gaps"]


def test_http_exact_path_read_and_excerpt_are_bounded(service, repository, monkeypatch):
    import io
    target = repository / "large.md"
    target.write_text("\n" * 10000)
    reads = []
    real_open = io.open

    class ObservedReader:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.stream.close()

        def read(self, size=-1):
            reads.append(size)
            return self.stream.read(size)

    def observed_open(path, *args, **kwargs):
        stream = real_open(path, *args, **kwargs)
        if path == target:
            return ObservedReader(stream)
        return stream

    monkeypatch.setattr(io, "open", observed_open)
    status, body = post(service, request_for(repository, kind="exact_path", query="large.md"))
    assert status == 200
    assert reads and all(0 < size <= 400 for size in reads), reads
    assert len(body["packet"]["evidence"][0]["excerpt"]) <= 400


def test_http_propagates_result_and_time_budgets_to_native_fff(service, repository, monkeypatch):
    from z0int import context_resolve
    calls = []
    native_search = context_resolve._fff_search_repository

    def observed_search(*args, **kwargs):
        calls.append(kwargs)
        return native_search(*args, **kwargs)

    monkeypatch.setattr(context_resolve, "_fff_search_repository", observed_search)
    for index in range(8):
        (repository / f"part{index}.py").write_text("def quasar_shared_needle(): pass\n")
    for kind in ("exact_symbol", "natural_language"):
        status, body = post(service, request_for(repository, kind=kind, limit=2, time_budget_ms=25))
        assert status == 200
        assert len(body["packet"]["evidence"]) == 2
        assert body["packet"]["measurements"]["fff_hits"] == 2
        assert calls[-1]["limit"] == 2
        assert calls[-1]["time_budget_ms"] == 25
        assert all(len(ref.get("excerpt", "")) <= 400 for ref in body["packet"]["evidence"])


@pytest.mark.parametrize("outage", ["absent", "disabled"])
def test_http_fff_outage_is_explicit_with_gaps(service, repository, monkeypatch, outage):
    if outage == "disabled":
        monkeypatch.setenv("Z0INT_FFF_DISABLE", "1")
    else:
        monkeypatch.setattr(file_search, "_load_fff", lambda: None)
    status, body = post(service, request_for(repository))
    assert status == 200
    assert body["ok"] is False
    assert body["error"] == "file_search_unavailable"
    assert body["packet"]["measurements"]["fff_status"] == outage
    assert body["packet"]["evidence"] == []
    assert body["packet"]["unresolved_gaps"]
    assert body["event_id"] is not None
    assert "fallback" in body["instruction"].lower()


def test_http_eventlog_outage_does_not_hide_retrieved_evidence(service, repository):
    EventLog().events_path.mkdir()
    status, body = post(service, request_for(repository))
    assert status == 200
    assert_content_packet(body, repository)
    assert body["event_id"] is None
    assert body["event_log_error"] == "IsADirectoryError"


def mcp_exchange(service, harness, shared_only, arguments):
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "file_search", "arguments": arguments}},
    ]
    run = subprocess.run(
        [sys.executable, "-m", "z0int.intelligence_mcp"],
        input="".join(json.dumps(message) + "\n" for message in messages),
        text=True, capture_output=True, timeout=20,
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "Z0INT_SERVICE_URL": service, "Z0INT_HARNESS": harness,
             "Z0INT_SHARED_ONLY": "1" if shared_only else "0"},
    )
    assert run.returncode == 0, run.stderr
    replies = [json.loads(line) for line in run.stdout.splitlines()]
    assert [reply["id"] for reply in replies] == [1, 2, 3]
    return replies[1]["result"]["tools"], replies[2]["result"]


def test_mcp_native_calls_and_visibility_for_each_harness(service, repository):
    index = 0
    for harness in ("codex", "claude-code", "dsh"):
        for shared_only in (False, True):
            tools, result = mcp_exchange(
                service, harness, shared_only,
                request_for(repository, harness="spoofed", trace_id=f"mcp-{harness}"),
            )
            names = {tool["name"] for tool in tools}
            assert {"route_worker", "file_search"} <= names
            assert names == ({"route_worker", "file_search"} if shared_only else
                             {"route_worker", "file_search", "delegate_worker", "list_models"})
            tool = next(tool for tool in tools if tool["name"] == "file_search")
            assert "harness" not in tool["inputSchema"]["properties"]
            assert tool["inputSchema"]["required"] == ["repository_root", "query"]
            if harness == "dsh":
                assert '"minimum"' not in json.dumps(tools)
                assert '"maximum"' not in json.dumps(tools)
            assert result["isError"] is False, result
            body = json.loads(result["content"][0]["text"])
            packet = assert_content_packet(body, repository)
            op = next(op for op in packet["recipe"]["operations"] if op["op"] == "fff_symbol")
            assert op["reused"] is (index > 0)
            index += 1
            event = EventLog().get(body["event_id"])
            assert body["harness_id"] == harness
            assert event.source == f"harness:{harness}"
            assert event.session_id == "search-session"
            assert isinstance(event.payload, dict)
            assert event.payload["task_id"] == f"mcp-{harness}"
            print("MCP_NATIVE_PROOF", json.dumps({"harness": harness, "shared_only": shared_only,
                                                "tools": sorted(names), "response": body}))
    assert len(file_search._POOL) == 1


def test_mcp_http_errors_are_explicit_not_worker_uncertainty(service, repository):
    for harness in ("codex", "claude-code", "dsh"):
        _, result = mcp_exchange(service, harness, True, request_for(repository, limit=999))
        assert result["isError"] is True
        assert result["content"][0]["text"].startswith("{"), result
        body = json.loads(result["content"][0]["text"])
        assert body["ok"] is False
        assert body["error"] == "invalid_file_search_request"
        assert body["http_status"] == 400
    assert not file_search._POOL
    assert not EventLog().events_path.exists()
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))  # Owned, non-listening port: no probe/release race.
        url = f"http://127.0.0.1:{reserved.getsockname()[1]}"
        _, result = mcp_exchange(url, "codex", True, request_for(repository))
        assert result["isError"] is True
        body = json.loads(result["content"][0]["text"])
        assert body["ok"] is False
        assert body["error"] == "file_search_unavailable"
        assert "fallback" in body["instruction"].lower()
        assert "packet" not in body
        assert "execution_status" not in body
