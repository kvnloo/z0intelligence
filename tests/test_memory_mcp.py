"""Observable security contract for the read-only memory MCP facade.

All records and credentials below are synthetic. Backends never access a service.
"""

from __future__ import annotations

import json

import pytest

from z0int.memory_mcp import MemoryServer


def _grant(source="qmd", collection="sessions", scope="work"):
    return {
        "source": source,
        "collection": collection,
        "scope": scope,
        "privacy": ["private"],
    }


def _record(**changes):
    record = {
        "source": "qmd",
        "collection": "sessions",
        "scope": "work",
        "privacy": "private",
        "host": "fixture-host",
        "date": "2026-10-22",
        "text": "Allowed synthetic memory.",
        "locator": "fixture:record-1",
        "title": "Synthetic memory",
    }
    record.update(changes)
    return record


def _serialized(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class _Backend:
    def __init__(self, records):
        self.records = records
        self.calls = []
        self.down = set()

    def __call__(self, source, query, grants, limit, timeout_s):
        self.calls.append((source, query, grants, limit, timeout_s))
        if source in self.down:
            raise OSError("Synthetic backend unavailable")
        return [dict(record) for record in self.records if record["source"] == source]


@pytest.fixture()
def make_server(tmp_path):
    serial = 0

    def make(*, records=None, allow=None, limits=None, backend=None, clock=None):
        nonlocal serial
        root = tmp_path / str(serial)
        serial += 1
        root.mkdir()
        policy = {
            "version": 1,
            "allow": [_grant()] if allow is None else allow,
            "limits": {
                "result_bytes": 4096,
                "total_bytes": 32768,
                "calls_per_minute": 30,
                **(limits or {}),
            },
            "timeout_seconds": 3,
        }
        policy_path = root / "policy.json"
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        backend = backend if backend is not None else _Backend(
            [_record()] if records is None else records
        )
        clock = clock if clock is not None else _Clock()
        server = MemoryServer(
            policy_path,
            root / "state",
            backend_search=backend,
            clock=clock,
        )
        return server, policy_path, backend, clock

    return make


def _rpc(server, method, params=None):
    message = {"jsonrpc": "2.0", "id": 7, "method": method}
    if params is not None:
        message["params"] = params
    reply = server.handle(message)
    assert reply["jsonrpc"] == "2.0"
    assert reply["id"] == 7
    return reply


def _call(server, name, arguments):
    return _rpc(server, "tools/call", {"name": name, "arguments": arguments})


def _assert_rpc_denied(reply):
    assert "error" in reply or reply.get("result", {}).get("isError") is True


def test_allowed_search_and_fetch_use_injected_backend(make_server):
    server, _, backend, _ = make_server()
    result = server.search("synthetic query", limit=1)
    assert len(result["results"]) == 1
    hit = result["results"][0]
    assert hit["text"] == "Allowed synthetic memory."
    assert hit["source"] == "qmd"
    assert isinstance(hit["id"], str) and hit["id"]
    assert result["untrusted"] is True
    fetched = server.fetch(hit["id"])
    assert fetched["record"] == hit
    assert fetched["untrusted"] is True
    assert len(backend.calls) == 1
    source, query, grants, limit, timeout_s = backend.calls[0]
    assert source == "qmd"
    assert query == "synthetic query"
    assert grants == [_grant()]
    assert limit == 1
    assert 0 < timeout_s <= 3


@pytest.mark.parametrize(
    "changes",
    [
        {"collection": "not-allowed"},
        {"collection": "sessions-extra"},
        {"scope": "not-allowed"},
        {"scope": ""},
        {"privacy": "public"},
        {"privacy": "personal"},
        {"privacy": "secret"},
        {"privacy": "unknown"},
        {"source": "tencentdb"},
    ],
)
def test_search_filters_each_authorization_dimension(make_server, changes):
    denied = _record(text="DENIED_SYNTHETIC_RECORD", locator="fixture:denied", **changes)

    def backend(source, query, grants, limit, timeout_s):
        # An untrusted backend can return rows outside its requested grants.
        return [_record(), denied]

    server, _, _, _ = make_server(backend=backend)
    result = server.search("authorization")
    assert [row["text"] for row in result["results"]] == ["Allowed synthetic memory."]
    assert "DENIED_SYNTHETIC_RECORD" not in _serialized(result).decode("utf-8")


def test_empty_allowlist_never_calls_backend(make_server):
    server, _, backend, _ = make_server(allow=[])
    result = server.search("not authorized")
    assert not result.get("results")
    assert backend.calls == []


def test_grants_do_not_form_a_collection_scope_cross_product(make_server):
    grants = [
        _grant(collection="first", scope="scope-one"),
        _grant(collection="second", scope="scope-two"),
    ]
    records = [
        _record(collection="first", scope="scope-one", text="Allowed pair"),
        _record(collection="first", scope="scope-two", text="Forbidden crossed pair"),
    ]
    server, _, _, _ = make_server(allow=grants, records=records)
    assert [row["text"] for row in server.search("pairs")["results"]] == ["Allowed pair"]


@pytest.mark.parametrize("privacy", ["public", "personal", "private"])
def test_explicit_privacy_grants_are_exact(make_server, privacy):
    grant = _grant()
    grant["privacy"] = [privacy]
    records = [
        _record(privacy=value, text=f"Classification {value}", locator=f"fixture:{value}")
        for value in ("public", "personal", "private", "secret")
    ]
    server, _, _, _ = make_server(allow=[grant], records=records)
    assert [row["privacy"] for row in server.search("classification")["results"]] == [privacy]


@pytest.mark.parametrize(
    "replacement",
    [
        [],
        [_grant(collection="other")],
        [_grant(scope="other")],
        [{**_grant(), "privacy": ["public"]}],
        [_grant(source="tencentdb")],
    ],
)
def test_fetch_rechecks_current_policy_after_id_issuance(make_server, replacement):
    server, policy_path, backend, _ = make_server()
    identifier = server.search("before revocation")["results"][0]["id"]
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["allow"] = replacement
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    result = server.fetch(identifier)
    assert result["error"] == "policy_denied"
    assert "record" not in result
    assert "Allowed synthetic memory." not in _serialized(result).decode("utf-8")
    assert len(backend.calls) == 1


@pytest.mark.parametrize("policy_state", ["missing", "malformed", "secret-grant"])
def test_fetch_fails_closed_when_current_policy_is_invalid(make_server, policy_state):
    server, policy_path, _, _ = make_server()
    identifier = server.search("before invalid policy")["results"][0]["id"]
    if policy_state == "missing":
        policy_path.unlink()
    elif policy_state == "malformed":
        policy_path.write_text("{not-json", encoding="utf-8")
    else:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
        policy["allow"][0]["privacy"] = ["secret"]
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
    result = server.fetch(identifier)
    assert result["error"] == "policy_denied"
    assert "record" not in result


def test_forged_ids_cannot_fetch_records(make_server):
    server, _, backend, _ = make_server()
    identifier = server.search("issue id")["results"][0]["id"]
    offset = len(identifier) // 2
    replacement = "A" if identifier[offset] != "A" else "B"
    forged = identifier[:offset] + replacement + identifier[offset + 1:]
    for value in (forged, identifier + "x", "fixture:record-1", "../policy.json", ""):
        result = server.fetch(value)
        assert result["error"] in {"invalid_id", "invalid_arguments"}
        assert "record" not in result
    assert len(backend.calls) == 1
    assert server.fetch(identifier)["record"]["id"] == identifier


def test_ids_are_not_reusable_across_server_instances(make_server):
    first, policy_path, backend, clock = make_server()
    second = MemoryServer(
        policy_path,
        policy_path.parent / "second-state",
        backend_search=backend,
        clock=clock,
    )
    first_id = first.search("same memory")["results"][0]["id"]
    second_id = second.search("same memory")["results"][0]["id"]
    assert first_id != second_id
    assert second.fetch(first_id)["error"] == "invalid_id"
    assert first.fetch(second_id)["error"] == "invalid_id"


@pytest.mark.parametrize(
    ("secret_text", "secrets"),
    [
        ("OPENAI_API_KEY=sk-" + "a1B2" * 12, ["sk-" + "a1B2" * 12]),
        ("Standalone key: sk-" + "c3D4" * 12, ["sk-" + "c3D4" * 12]),
        ("Authorization: Bearer synthetic-auth-marker-123456789", ["synthetic-auth-marker-123456789"]),
        (
            ".env\nDATABASE_PASSWORD=synthetic-env-password-12345\nAPI_TOKEN=synthetic-env-token-67890",
            ["synthetic-env-password-12345", "synthetic-env-token-67890"],
        ),
        (
            "-----BEGIN PRIVATE KEY-----\nU1lOVEhFVElDX1BFTV9TRUNSRVRfTUFSS0VS\n-----END PRIVATE KEY-----",
            ["U1lOVEhFVElDX1BFTV9TRUNSRVRfTUFSS0VS"],
        ),
    ],
)
def test_credentials_are_redacted_before_search_and_fetch(make_server, secret_text, secrets):
    text = f"Safe context before.\n{secret_text}\nSafe context after."
    server, _, _, _ = make_server(records=[_record(text=text)])
    result = server.search("redaction")
    assert len(result["results"]) == 1
    hit = result["results"][0]
    assert "Safe context before." in hit["text"]
    assert "Safe context after." in hit["text"]
    fetched = server.fetch(hit["id"])
    assert fetched["record"] == hit
    for response in (result, fetched):
        encoded = _serialized(response).decode("utf-8")
        for secret in secrets:
            assert secret not in encoded


def test_cached_record_is_a_redacted_snapshot_not_a_backend_refetch(make_server):
    server, _, backend, _ = make_server(
        records=[_record(text="Before.\nAPI_KEY=synthetic-original-secret-12345\nAfter.")]
    )
    hit = server.search("snapshot")["results"][0]
    backend.records[0]["text"] = "MUTATED_BACKEND_PRIVATE_CONTENT"
    fetched = server.fetch(hit["id"])
    assert fetched["record"] == hit
    assert "synthetic-original-secret-12345" not in _serialized(fetched).decode("utf-8")
    assert "MUTATED_BACKEND_PRIVATE_CONTENT" not in _serialized(fetched).decode("utf-8")
    assert len(backend.calls) == 1


def test_utf8_result_cap_includes_metadata_and_id(make_server):
    original = "é中🙂" * 2000
    cap = 1024
    server, _, _, _ = make_server(
        records=[_record(text=original)],
        limits={"result_bytes": cap, "total_bytes": 8192},
    )
    result = server.search("unicode")
    assert result["results"]
    for hit in result["results"]:
        assert len(_serialized(hit)) <= cap
        assert hit["text"]
        assert "é中🙂" in hit["text"]
        assert hit["text"] != original
        assert "\ufffd" not in hit["text"]
        assert hit["text"].encode("utf-8").decode("utf-8") == hit["text"]
        fetched = server.fetch(hit["id"])
        assert fetched["record"] == hit
        assert len(_serialized(fetched["record"])) <= cap


def test_total_cap_covers_entire_serialized_search_and_fetch_payload(make_server):
    records = [
        _record(text=f"{index}: " + "é中🙂" * 500, locator=f"fixture:unicode-{index}")
        for index in range(20)
    ]
    cap = 4096
    server, _, _, _ = make_server(
        records=records,
        limits={"result_bytes": 1024, "total_bytes": cap},
    )
    result = server.search("total bytes", limit=10)
    assert 0 < len(result["results"]) < len(records)
    assert len(_serialized(result)) <= cap
    for hit in result["results"]:
        assert len(_serialized(hit)) <= 1024
        fetched = server.fetch(hit["id"])
        assert fetched["record"] == hit
        assert len(_serialized(fetched)) <= cap


def test_backend_outage_does_not_hide_other_authorized_results(make_server):
    backend = _Backend([_record(source="tencentdb", text="Available backend memory")])
    backend.down.add("qmd")
    server, _, _, _ = make_server(
        allow=[_grant(), _grant(source="tencentdb")],
        backend=backend,
    )
    result = server.search("partial outage")
    assert [row["text"] for row in result["results"]] == ["Available backend memory"]
    assert result["backends"]["qmd"] == "unavailable"
    assert result["backends"]["tencentdb"] == "available"
    assert {call[0] for call in backend.calls} == {"qmd", "tencentdb"}


def test_search_and_fetch_share_rate_limit_and_recover_with_clock(make_server):
    server, _, backend, clock = make_server(limits={"calls_per_minute": 2})
    identifier = server.search("first call")["results"][0]["id"]
    assert server.fetch(identifier)["record"]["id"] == identifier
    assert server.search("blocked search")["error"] == "rate_limited"
    assert server.fetch(identifier)["error"] == "rate_limited"
    assert len(backend.calls) == 1
    clock.now += 61
    assert server.search("new window")["results"]
    assert len(backend.calls) == 2


def test_audit_has_hashes_and_counts_without_raw_queries_or_records(make_server):
    query = "SYNTHETIC_RAW_QUERY_DO_NOT_LOG"
    raw_text = "SYNTHETIC_RAW_CONTENT_DO_NOT_LOG"
    locator = "fixture:SYNTHETIC_RAW_LOCATOR_DO_NOT_LOG"
    server, policy_path, _, _ = make_server(
        records=[_record(text=raw_text, locator=locator)]
    )
    hit = server.search(query)["results"][0]
    server.fetch(hit["id"])
    audit_path = policy_path.parent / "state" / "audit.jsonl"
    audit = audit_path.read_text(encoding="utf-8")
    for forbidden in (query, raw_text, locator, hit["id"]):
        assert forbidden not in audit
    events = [json.loads(line) for line in audit.splitlines()]
    assert len(events) >= 2
    assert {"search", "fetch"} <= {event["tool"] for event in events}
    search_event = next(event for event in events if event["tool"] == "search")
    assert isinstance(search_event["query_hmac"], str)
    assert len(search_event["query_hmac"]) >= 32
    assert search_event["count"] == 1
    assert isinstance(search_event["bytes"], int) and search_event["bytes"] > 0


def test_mcp_lists_exactly_two_read_only_tools_with_closed_schemas(make_server):
    server, _, backend, _ = make_server()
    tools = _rpc(server, "tools/list")["result"]["tools"]
    assert len(tools) == 2
    by_name = {tool["name"]: tool for tool in tools}
    assert set(by_name) == {"search", "fetch"}
    for name, fields, required in (
        ("search", {"query", "limit"}, {"query"}),
        ("fetch", {"id"}, {"id"}),
    ):
        schema = by_name[name]["inputSchema"]
        assert schema["type"] == "object"
        assert set(schema["properties"]) == fields
        assert set(schema["required"]) == required
        assert schema["additionalProperties"] is False
    assert backend.calls == []


def test_mcp_search_and_fetch_expose_the_same_bounded_records(make_server):
    server, _, _, _ = make_server()
    search_reply = _call(server, "search", {"query": "mcp", "limit": 1})
    assert "error" not in search_reply
    assert not search_reply["result"].get("isError")
    search = json.loads(search_reply["result"]["content"][0]["text"])
    hit = search["results"][0]
    fetch_reply = _call(server, "fetch", {"id": hit["id"]})
    assert "error" not in fetch_reply
    assert not fetch_reply["result"].get("isError")
    fetched = json.loads(fetch_reply["result"]["content"][0]["text"])
    assert fetched["record"] == hit


@pytest.mark.parametrize("name", ["write", "store", "delete", "update", "search_backend"])
def test_mcp_rejects_write_and_unexposed_tools(make_server, name):
    server, _, backend, _ = make_server()
    _assert_rpc_denied(_call(server, name, {"text": "Never stored"}))
    assert backend.calls == []


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("search", {"query": "safe", "source": "qmd"}),
        ("search", {"query": "safe", "collection": "sessions"}),
        ("search", {"query": "safe", "scope": "work"}),
        ("search", {"query": "safe", "privacy": ["secret"]}),
        ("search", {"query": "safe", "write": True}),
        ("search", {"query": "safe", "timeout_seconds": 300}),
        ("fetch", {"id": "unissued", "path": "/tmp/anything"}),
        ("fetch", {"id": "unissued", "query": "safe"}),
    ],
)
def test_mcp_rejects_extra_tool_arguments_before_backend_access(make_server, name, arguments):
    server, _, backend, _ = make_server()
    _assert_rpc_denied(_call(server, name, arguments))
    assert backend.calls == []


def test_mcp_rejects_extra_dispatch_and_list_params(make_server):
    server, _, backend, _ = make_server()
    reply = _rpc(
        server,
        "tools/call",
        {"name": "search", "arguments": {"query": "safe"}, "write": True},
    )
    _assert_rpc_denied(reply)
    _assert_rpc_denied(_rpc(server, "tools/list", {"write": True}))
    assert backend.calls == []


@pytest.mark.parametrize("limit", [0, -1, 11, 20, True, 1.5, "1"])
def test_search_rejects_limits_outside_integer_one_to_ten(make_server, limit):
    server, _, backend, _ = make_server()
    assert server.search("invalid limit", limit=limit)["error"] == "invalid_arguments"
    assert backend.calls == []


def test_issued_id_expires_with_the_injected_clock(make_server):
    server, _, _, clock = make_server()
    identifier = server.search("expiring record")["results"][0]["id"]
    clock.now += 601
    result = server.fetch(identifier)
    assert result["error"] == "policy_denied"
    assert "record" not in result


@pytest.mark.parametrize("tool", ["search", "fetch"])
def test_audit_write_failure_prevents_record_disclosure(make_server, tool):
    server, policy_path, _, _ = make_server()
    identifier = None
    if tool == "fetch":
        identifier = server.search("issue before audit failure")["results"][0]["id"]
    audit_path = policy_path.parent / "state" / "audit.jsonl"
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    if audit_path.exists():
        audit_path.unlink()
    # A directory cannot be opened as an append-only audit file, even as root.
    audit_path.mkdir()
    result = server.search("must not disclose") if tool == "search" else server.fetch(identifier)
    assert result == {"error": "audit_unavailable"}


@pytest.mark.parametrize("invalid_call", ["search", "fetch", "unknown-tool", "extra-argument"])
def test_invalid_calls_consume_the_shared_rate_budget(make_server, invalid_call):
    server, _, backend, _ = make_server(limits={"calls_per_minute": 1})
    if invalid_call == "search":
        assert server.search("")["error"] == "invalid_arguments"
    elif invalid_call == "fetch":
        assert server.fetch("")["error"] == "invalid_id"
    elif invalid_call == "unknown-tool":
        _assert_rpc_denied(_call(server, "write", {"text": "not stored"}))
    else:
        _assert_rpc_denied(_call(server, "search", {"query": "safe", "write": True}))
    assert server.search("valid but rate limited")["error"] == "rate_limited"
    assert backend.calls == []
