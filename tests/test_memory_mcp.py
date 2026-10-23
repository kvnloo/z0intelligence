"""Read-only facade security contract; all evidence and credentials are synthetic."""
from __future__ import annotations

import hashlib
import json

import pytest

from z0int.memory_mcp import MemoryServer, load_policy


def grant(source="agentsview", collection="history", scope="project-A"):
    return {"source": source, "collection": collection, "scope": scope,
            "privacy": ["private"]}


def record(*, text="Safe original text", source="agentsview", scope="project-A",
           collection="history", privacy="private", ordinal=3,
           locator=None, **extras):
    native = {"session_id": "codex:synthetic-session", "ordinal": ordinal}
    result = {
        "source": source, "collection": collection, "scope": scope,
        "privacy": privacy, "host": "synthetic-host", "date": "2026-10-22",
        "text": text, "locator": locator or f"agentsview:{native['session_id']}:{ordinal}",
        "native": native,
        "source_version": "sha256:" + hashlib.sha256(text.encode()).hexdigest(),
    }
    result.update(extras)
    return result


def serialized(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class NativeFixture:
    """The original changes independently of search previews; no cached hydration."""
    def __init__(self, records):
        self.records = records
        self.search_calls = []
        self.fetch_calls = []
        self.down = False

    def search(self, source, query, grants, limit, timeout):
        self.search_calls.append((source, query, grants, limit, timeout))
        if self.down:
            raise OSError("SYNTHETIC_SECRET_BACKEND_ERROR")
        return [dict(row) for row in self.records if row["source"] == source]

    def fetch(self, source, identity, grant_, timeout):
        self.fetch_calls.append((source, identity, grant_, timeout))
        if self.down:
            raise OSError("SYNTHETIC_SECRET_BACKEND_ERROR")
        for row in self.records:
            if row["native"] == identity["native"] and row["source"] == source:
                # Calculate the native revision on *every* source read.
                return record(**{
                    "text": row["text"], "source": row["source"],
                    "scope": row["scope"], "collection": row["collection"],
                    "privacy": row["privacy"], "ordinal": row["native"]["ordinal"],
                    "locator": row["locator"],
                })
        raise OSError("synthetic missing original")


@pytest.fixture()
def factory(tmp_path):
    count = 0

    def make(*, rows=None, operator=None, chatgpt=None, principal="operator",
             limits=None, fixture=None, clock=None):
        nonlocal count
        count += 1
        directory = tmp_path / str(count)
        directory.mkdir()
        policy_path = directory / "policy.json"
        policy = {
            "version": 2,
            "principals": {
                "operator": [grant()] if operator is None else operator,
                "chatgpt": [] if chatgpt is None else chatgpt,
            },
            "limits": {"result_bytes": 4096, "total_bytes": 32768,
                       "calls_per_minute": 30, **(limits or {})},
            "timeout_seconds": 3,
        }
        policy_path.write_text(json.dumps(policy))
        backend = fixture or NativeFixture([record()] if rows is None else rows)
        clock_ = clock or Clock()
        server = MemoryServer(policy_path, directory / "state", principal=principal,
                              backend_search=backend.search, backend_fetch=backend.fetch,
                              clock=clock_)
        return server, policy_path, backend, clock_

    return make


def replace_policy(path, fn):
    policy = json.loads(path.read_text())
    fn(policy)
    path.write_text(json.dumps(policy))


def test_search_proves_content_revision_and_fetch_reads_original_again(factory):
    server, _, source, _ = factory()
    found = server.search("workstream", 1)
    assert found["status"] == "ok"
    hit = found["results"][0]
    assert hit["mode"] == "preview"
    assert hit["source_version"] == record()["source_version"]
    assert hit["locator"] == "agentsview:codex:synthetic-session:3"
    assert hit["source_version"] in serialized(found).decode()
    fetched = server.fetch(hit["id"])
    assert fetched["status"] == "ok"
    assert fetched["record"]["mode"] == "original_read"
    assert fetched["record"]["text"] == "Safe original text"
    assert fetched["record"]["source_version"] == hit["source_version"]
    assert len(source.search_calls) == 1
    assert len(source.fetch_calls) == 1


def test_chatgpt_cannot_borrow_operator_grants(factory):
    server, _, backend, _ = factory(principal="chatgpt")
    found = server.search("operator only")
    assert found["status"] == "denied"
    assert found["results"] == [] and found["backends"] == {}
    assert not backend.search_calls
    authorized, _, native, _ = factory(principal="chatgpt", chatgpt=[grant()])
    hit = authorized.search("explicit chatgpt grant")["results"][0]
    assert authorized.fetch(hit["id"])["status"] == "ok"
    assert native.fetch_calls
    with pytest.raises(ValueError):
        MemoryServer(authorized.policy_path, authorized.state_dir, principal="unknown")


@pytest.mark.parametrize("change", [
    {"scope": "project-B"}, {"scope": ""},
    {"collection": "history-backup"}, {"privacy": "secret"},
    {"privacy": "public"}, {"source": "tencentdb"},
])
def test_sibling_unknown_and_classified_rows_never_leak(factory, change):
    denied = record(text="SIBLING-SYNTHETIC-SECRET", ordinal=7, **change)
    server, _, _, _ = factory(rows=[record(), denied])
    reply = serialized(server.search("scope check")).decode()
    assert "Safe original text" in reply
    assert "SIBLING-SYNTHETIC-SECRET" not in reply
    assert "history-backup" not in reply
    assert "project-B" not in reply


def test_grant_pairs_are_not_cross_product(factory):
    server, _, _, _ = factory(
        operator=[grant(scope="A"), grant(collection="another", scope="B")],
        rows=[record(scope="B", text="forbidden-crossed-pair"),
              record(scope="A", text="allowed-pair")],
    )
    output = serialized(server.search("pairs"))
    assert b"allowed-pair" in output
    assert b"forbidden-crossed-pair" not in output


@pytest.mark.parametrize("new_grants", [
    [], [grant(scope="other")], [grant(collection="other")],
    [{"source": "agentsview", "collection": "history",
      "scope": "project-A", "privacy": ["public"]}],
])
def test_revocation_between_search_and_fetch_denies_without_source_read(factory, new_grants):
    server, policy_path, source, _ = factory()
    issued = server.search("before revoke")["results"][0]["id"]
    replace_policy(policy_path, lambda p: p["principals"]["operator"].__setitem__(
        slice(None), new_grants))
    reply = server.fetch(issued)
    assert reply == {"error": "denied"} or reply.get("error") == "audit_unavailable"
    assert not source.fetch_calls
    assert b"Safe original" not in serialized(reply)


def test_principal_revocation_does_not_affect_other_principal(factory):
    server, path, _, _ = factory(principal="chatgpt", chatgpt=[grant()])
    token = server.search("chatgpt")["results"][0]["id"]
    replace_policy(path, lambda p: p["principals"]["chatgpt"].clear())
    assert server.fetch(token)["error"] == "denied"
    operator, _, _, _ = factory(principal="operator", chatgpt=[])
    assert operator.search("operator")["status"] == "ok"


def test_same_date_same_size_source_replacement_is_stale(factory):
    server, _, native, _ = factory(rows=[record(text="answer-v1")])
    token = server.search("version")["results"][0]["id"]
    native.records[0]["text"] = "answer-v2"  # Same byte count, same date/locator.
    reply = server.fetch(token)
    assert reply["error"] == "stale"
    assert "record" not in reply
    assert b"answer-v1" not in serialized(reply)
    assert b"answer-v2" not in serialized(reply)


def test_source_outage_is_unavailable_not_cached_hit(factory):
    server, _, native, _ = factory()
    token = server.search("outage")["results"][0]["id"]
    native.down = True
    result = server.fetch(token)
    assert result == {"error": "unavailable"}
    assert "SYNTHETIC_SECRET_BACKEND_ERROR" not in serialized(result).decode()


def test_expired_and_forged_handle_cannot_read_original(factory):
    server, _, native, clock = factory()
    token = server.search("ttl")["results"][0]["id"]
    assert server.fetch(token[:-1] + ("A" if token[-1] != "A" else "B"))["error"] == "invalid_id"
    assert server.fetch("../path")["error"] == "invalid_id"
    assert server.fetch("z" * 89)["error"] == "invalid_id"
    clock.now += 601
    assert server.fetch(token)["error"] == "stale"
    assert not native.fetch_calls


def test_cached_metadata_only_no_snapshot_content(factory):
    server, _, native, _ = factory(rows=[record(text="SECRET ORIGINAL SYNTHETIC")])
    token = server.search("metadata")["results"][0]["id"]
    assert token in server.cache
    assert "SECRET ORIGINAL SYNTHETIC" not in repr(server.cache)
    native.records[0]["text"] = "ANOTHER SOURCE ORIGINAL"
    assert server.fetch(token)["error"] == "stale"


@pytest.mark.parametrize("secret", [
    "OPENAI_API_KEY=sk-" + "aBcD" * 12,
    "Authorization: Bearer synthetic-token-123456789",
    "-----BEGIN PRIVATE KEY-----\nc2VjcmV0\n-----END PRIVATE KEY-----",
])
def test_redaction_before_clipping_on_original_reads(factory, secret):
    text = "safe start\n" + secret + "\nsafe end"
    server, _, _, _ = factory(rows=[record(text=text)])
    found = server.search("redact")["results"][0]
    output = serialized(server.fetch(found["id"])).decode()
    assert secret not in output and secret not in serialized(found).decode()
    assert "safe start" in output
    assert "safe end" in output


def test_preview_is_not_mislabeled_complete_original(factory):
    server, _, _, _ = factory(rows=[record(text="é中🙂" * 4000)],
                              limits={"result_bytes": 1024, "total_bytes": 8192})
    found = server.search("utf8")["results"][0]
    assert found["mode"] == "preview"
    assert found["text_truncated"] is True
    assert len(serialized(found)) <= 1024
    original = server.fetch(found["id"])["record"]
    assert original["mode"] == "original_read"
    assert original["text_truncated"] is True
    assert len(serialized(original)) <= 1024
    assert "\ufffd" not in original["text"]


def test_unsupported_backend_is_explicitly_unavailable(factory):
    server, _, fixture, _ = factory(operator=[grant("ctx", "history", "/project")])
    # The fake backend is not the real dispatcher, so verify facade outage semantics.
    fixture.down = True
    result = server.search("ctx")
    assert result["status"] == "unavailable"
    assert result["backends"] == {"ctx": "unavailable"}
    assert not result["results"]


def test_policy_malformed_or_v1_legacy_does_not_start_or_grant(factory):
    server, policy_path, _, _ = factory()
    token = server.search("policy")["results"][0]["id"]
    policy_path.write_text(json.dumps({"version": 1, "allow": [grant()]}))
    assert server.fetch(token)["error"] == "denied"
    with pytest.raises(ValueError):
        load_policy(policy_path)
    with pytest.raises(ValueError):
        MemoryServer(policy_path, policy_path.parent / "other-state", principal="operator")


def test_rpc_has_only_two_readonly_closed_tools(factory):
    server, _, backend, _ = factory()
    result = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/list"})
    schemas = {row["name"]: row for row in result["result"]["tools"]}
    assert set(schemas) == {"search", "fetch"}
    for name, expected in (("search", {"query", "limit"}), ("fetch", {"id"})):
        assert set(schemas[name]["inputSchema"]["properties"]) == expected
        assert schemas[name]["inputSchema"]["additionalProperties"] is False
        assert schemas[name]["annotations"]["readOnlyHint"] is True
    for forbidden in ("write", "update", "refresh", "import", "setup", "search_backend"):
        result = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                "params": {"name": forbidden, "arguments": {}}})
        assert result["result"]["isError"] is True
    assert backend.search_calls == []


def test_rpc_round_trip_and_forbidden_argument_shapes(factory):
    server, _, backend, _ = factory()
    reply = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "search", "arguments": {"query": "allowed"}}})
    hit = json.loads(reply["result"]["content"][0]["text"])["results"][0]
    reply = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                           "params": {"name": "fetch", "arguments": {"id": hit["id"]}}})
    assert json.loads(reply["result"]["content"][0]["text"])["status"] == "ok"
    for args in ({"query": "x", "scope": "unknown"},
                 {"query": "x", "principal": "operator"},
                 {"query": "x", "path": "/etc/passwd"},
                 {"query": "x", "url": "http://127.0.0.1"}):
        reply = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": "search", "arguments": args}})
        assert reply["result"]["isError"] is True
    assert len(backend.search_calls) == 1


def test_rate_limit_shared_by_search_and_fetch(factory):
    server, _, _, clock = factory(limits={"calls_per_minute": 2})
    token = server.search("once")["results"][0]["id"]
    assert server.fetch(token)["status"] == "ok"
    assert server.search("over")["error"] == "rate_limited"
    assert server.fetch(token)["error"] == "rate_limited"
    clock.now += 61
    assert server.search("again")["status"] == "ok"


def test_audit_never_exposes_source_text_native_locator_query_or_token(factory):
    server, path, _, _ = factory(rows=[record(text="ULTRA_PRIVATE_SYNTHETIC")])
    query = "PRIVATE_QUERY_SYNTHETIC"
    hit = server.search(query)["results"][0]
    server.fetch(hit["id"])
    audit = (path.parent / "state" / "audit.jsonl").read_text()
    for item in (query, "ULTRA_PRIVATE_SYNTHETIC", hit["locator"], hit["id"]):
        assert item not in audit
    assert '"query_hmac":' in audit and '"tool":"fetch"' in audit


def test_audit_failure_blocks_any_disclosure(factory):
    server, path, _, _ = factory()
    token = server.search("before")["results"][0]["id"]
    audit_path = path.parent / "state" / "audit.jsonl"
    audit_path.unlink()
    audit_path.mkdir()
    assert server.fetch(token) == {"error": "audit_unavailable"}


def test_policy_rejects_unknown_scope_before_server_or_reads(factory):
    server, path, fixture, _ = factory()
    replace_policy(path, lambda p: p["principals"]["operator"][0].__setitem__(
        "scope", "unknown"))
    with pytest.raises(ValueError):
        load_policy(path)
    assert server.search("never disclose")["error"] == "denied"
    assert fixture.search_calls == []


def test_revoke_during_exact_source_io_is_rechecked_before_disclosure(factory):
    server, path, source, _ = factory()
    handle = server.search("before concurrent policy change")["results"][0]["id"]
    real_fetch = source.fetch

    def revoke_on_read(name, identity, principal_grant, timeout):
        value = real_fetch(name, identity, principal_grant, timeout)
        replace_policy(path, lambda p: p["principals"]["operator"].clear())
        return value

    server.backend_fetch = revoke_on_read
    reply = server.fetch(handle)
    assert reply["error"] == "denied"
    assert "record" not in reply
    assert len(source.fetch_calls) == 1


def test_inconsistent_digest_row_not_issued(factory):
    mismatch = record(text="Original fixture")
    mismatch["source_version"] = "sha256:" + "0" * 64
    server, _, _, _ = factory(rows=[mismatch])
    reply = server.search("tampered digest")
    assert reply["status"] == "empty"
    assert reply["results"] == []
