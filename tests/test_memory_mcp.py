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
           session_id="codex:synthetic-session", message_id=None,
           locator=None, **extras):
    message_id = 1000 + ordinal if message_id is None else message_id
    native = {"session_id": session_id, "ordinal": ordinal, "id": message_id}
    result = {
        "source": source, "collection": collection, "scope": scope,
        "privacy": privacy, "host": "synthetic-host", "date": "2026-10-22",
        "text": text, "locator": locator or
        f"agentsview:{session_id}:message:{message_id}:ordinal:{ordinal}",
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
            if (row["source"] == source and
                    row["native"].get("session_id") == identity["native"]["session_id"] and
                    row["native"].get("ordinal") == identity["native"]["ordinal"]):
                # Native ordinal lookup can return another stable ID with identical text.
                return record(**{
                    "text": row["text"], "source": row["source"],
                    "scope": row["scope"], "collection": row["collection"],
                    "privacy": row["privacy"], "ordinal": row["native"]["ordinal"],
                    "session_id": row["native"]["session_id"],
                    "message_id": row["native"].get("id"),
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
    assert hit["locator"] == "agentsview:codex:synthetic-session:message:1003:ordinal:3"
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


@pytest.mark.parametrize("tamper", [
    "sibling_session", "missing_session", "missing_message_id",
    "zero_id", "boolean_id", "overflow_id", "ordinal_mismatch",
])
def test_facade_search_denies_unstable_or_foreign_native_identity(factory, tamper):
    row = record(text="SIBLING_PAYLOAD_NEVER_VISIBLE")
    native = row["native"]
    if tamper == "sibling_session":
        native["session_id"] = "codex:sibling-session"
    elif tamper == "missing_session":
        native.pop("session_id")
    elif tamper == "missing_message_id":
        native.pop("id")
    elif tamper == "zero_id":
        native["id"] = 0
    elif tamper == "boolean_id":
        native["id"] = True
    elif tamper == "overflow_id":
        native["id"] = 1 << 63
    elif tamper == "ordinal_mismatch":
        native["ordinal"] = 4
    server, _, _, _ = factory(rows=[row])
    reply = server.search("foreign native")
    assert reply == {"error": "denied"}
    assert "SIBLING_PAYLOAD" not in serialized(reply).decode()
    assert row["locator"] not in serialized(reply).decode()
    assert "synthetic-host" not in serialized(reply).decode()


@pytest.mark.parametrize("tamper", ["sibling", "missing_session", "missing_id"])
def test_facade_fetch_denies_sibling_or_missing_identity_without_metadata(factory, tamper):
    server, _, _, _ = factory()
    token = server.search("before native tamper")["results"][0]["id"]
    replaced = record(text="SIBLING_PAYLOAD_NEVER_VISIBLE")
    replaced["host"] = "SIBLING_PRIVATE_HOST"
    if tamper == "sibling":
        replaced["native"]["session_id"] = "codex:sibling-session"
    elif tamper == "missing_session":
        replaced["native"].pop("session_id")
    else:
        replaced["native"].pop("id")
    server.backend_fetch = lambda *args: replaced
    reply = server.fetch(token)
    assert reply == {"error": "denied"}
    assert "record" not in reply and "context_packet" not in reply
    assert "SIBLING_PAYLOAD" not in serialized(reply).decode()
    assert "SIBLING_PRIVATE_HOST" not in serialized(reply).decode()


def test_facade_identical_text_reused_ordinal_cannot_replace_native_message(factory):
    original = record(text="identical evidence bytes")
    server, _, native, _ = factory(rows=[original])
    hit = server.search("exact identity")["results"][0]
    native.records[0]["native"]["id"] += 1
    # Text and SHA-256 are unchanged. Source-native message ID is not.
    replacement = record(text=original["text"], message_id=1004)
    assert replacement["source_version"] == hit["source_version"]
    assert replacement["locator"] != hit["locator"]
    denied = server.fetch(hit["id"])
    assert denied == {"error": "denied"}
    assert "record" not in denied and "context_packet" not in denied


def test_long_native_locator_survives_original_content_hash(factory):
    session = "codex:" + "a" * 175
    original = record(session_id=session, message_id=(1 << 63) - 1)
    server, _, _, _ = factory(rows=[original])
    hit = server.search("full locator")["results"][0]
    assert hit["locator"] == original["locator"]
    assert str((1 << 63) - 1) in hit["locator"]
    assert hit["source_version"] == original["source_version"]
    assert hit["locator"] in serialized(server.search("same original")["context_packet"]).decode()
    hydrated = server.fetch(hit["id"])
    assert hydrated["record"]["locator"] == original["locator"]
    assert hydrated["context_packet"]["evidence"][0]["locator"] == original["locator"]

def outcome_rows(server):
    """Read the actual private JSONL audit, not a stubbed audit callback."""
    return [json.loads(line) for line in
            (server.state_dir / "audit.jsonl").read_text().splitlines()]


def test_query_outcomes_record_real_hit_rows_without_evidence(factory):
    server, _, _, _ = factory(rows=[record(text="SYNTHETIC_PRIVATE_ORIGINAL")])
    query = "SYNTHETIC_PRIVATE_QUERY"
    found = server.search(query)
    fetched = server.fetch(found["results"][0]["id"])
    rows = outcome_rows(server)
    assert [row["outcome"] for row in rows] == ["hit", "hit"]
    assert [row["tool"] for row in rows] == ["search", "fetch"]
    assert [row["count"] for row in rows] == [1, 1]
    assert [row["bytes"] for row in rows] == [
        len(serialized(found)), len(serialized(fetched))]
    for row in rows:
        assert set(row) == {
            "schema", "timestamp", "tool", "query_hmac", "count", "bytes",
            "denied_count", "unavailable_count", "decision", "outcome",
        }
        assert row["schema"] == "z0int.memory_query_outcome.v1"
        assert row["timestamp"].endswith("Z")
        assert len(row["query_hmac"]) == 64
        assert row["denied_count"] == row["unavailable_count"] == 0
        assert row["decision"] == "allowed"
    assert rows[0]["query_hmac"] == server._hash("query", query)
    assert rows[1]["query_hmac"] == server._hash("query", found["results"][0]["id"])
    audit = (server.state_dir / "audit.jsonl").read_text()
    for forbidden in (query, "SYNTHETIC_PRIVATE_ORIGINAL",
                      found["results"][0]["locator"], found["results"][0]["id"]):
        assert forbidden not in audit


def test_query_outcome_empty_authorized_search_is_miss_not_outage(factory):
    server, _, _, _ = factory(rows=[])
    reply = server.search("no matching synthetic message")
    assert reply["status"] == "empty"
    assert reply["backends"] == {"agentsview": "available"}
    row = outcome_rows(server)[-1]
    assert row["tool"] == "search" and row["outcome"] == "miss"
    assert (row["count"], row["denied_count"], row["unavailable_count"]) == (0, 0, 0)


def test_query_outcome_policy_denials_are_not_misses(factory):
    chatgpt, _, source, _ = factory(principal="chatgpt")
    assert chatgpt.search("no grant")["status"] == "denied"
    assert outcome_rows(chatgpt)[-1]["outcome"] == "denied"
    assert source.search_calls == []

    filtered, _, _, _ = factory(rows=[record(scope="sibling-project",
                                              text="SYNTHETIC_SIBLING_SECRET")])
    result = filtered.search("filtered native candidate")
    assert result["status"] == "empty"  # Preserve the existing public response.
    assert result["results"] == []
    row = outcome_rows(filtered)[-1]
    assert row["outcome"] == "denied"
    assert row["denied_count"] == 1 and row["count"] == 0
    assert "SYNTHETIC_SIBLING_SECRET" not in (filtered.state_dir / "audit.jsonl").read_text()


def test_query_outcome_search_and_fetch_outages_are_unavailable(factory):
    server, _, native, _ = factory()
    native.down = True
    lost = server.search("source offline")
    assert lost["status"] == "unavailable"
    assert lost["backends"] == {"agentsview": "unavailable"}
    native.down = False
    token = server.search("source online")["results"][0]["id"]
    native.down = True
    assert server.fetch(token) == {"error": "unavailable"}
    rows = outcome_rows(server)
    assert [row["outcome"] for row in rows] == ["unavailable", "hit", "unavailable"]
    assert rows[0]["unavailable_count"] == 1
    assert rows[-1]["decision"] == "unavailable"
    assert "SYNTHETIC_SECRET_BACKEND_ERROR" not in (server.state_dir / "audit.jsonl").read_text()


def test_query_outcome_changed_source_and_expired_handle_are_stale(factory):
    server, _, native, clock = factory(rows=[record(text="version-one")])
    token = server.search("source revision")["results"][0]["id"]
    native.records[0]["text"] = "version-two"
    assert server.fetch(token) == {"error": "stale"}
    latest = server.search("latest revision")["results"][0]["id"]
    clock.now += 601
    assert server.fetch(latest) == {"error": "stale"}
    assert [row["outcome"] for row in outcome_rows(server)] == [
        "hit", "stale", "hit", "stale"]


def test_query_outcome_shared_rate_limit_is_not_miss(factory):
    server, _, _, _ = factory(limits={"calls_per_minute": 1})
    token = server.search("first")["results"][0]["id"]
    assert server.fetch(token) == {"error": "rate_limited"}
    rows = outcome_rows(server)
    assert [row["outcome"] for row in rows] == ["hit", "rate_limited"]
    assert rows[-1]["tool"] == "fetch"
    assert rows[-1]["decision"] == "rate_limited"
    assert rows[-1]["count"] == 0


def test_query_outcome_invalid_arguments_handles_and_rpc_are_invalid(factory):
    server, _, _, _ = factory()
    assert server.search("", limit=0) == {"error": "invalid_arguments"}
    assert server.fetch("../not-a-handle") == {"error": "invalid_id"}
    reply = server.handle({
        "jsonrpc": "2.0", "id": 9, "method": "tools/call",
        "params": {"name": "erase", "arguments": {}},
    })
    assert reply["result"]["isError"] is True
    rows = outcome_rows(server)
    assert [(row["tool"], row["outcome"]) for row in rows] == [
        ("search", "invalid"), ("fetch", "invalid"), ("invalid", "invalid")]
    assert [row["decision"] for row in rows] == [
        "invalid_arguments", "invalid_id", "invalid_arguments"]


def test_query_outcome_unknown_internal_error_defaults_to_error(factory):
    server, _, _, _ = factory()
    coded = {"error": "synthetic_unknown_error_code"}
    assert server._audit("fetch", "opaque synthetic handle", coded) == coded
    row = outcome_rows(server)[-1]
    assert row["outcome"] == "error"
    assert row["decision"] == "synthetic_unknown_error_code"
    assert row["count"] == 0


def test_query_outcome_failed_audit_blocks_search_before_disclosure(factory):
    server, _, _, _ = factory(rows=[record(text="SYNTHETIC_PRIVATE_ORIGINAL")])
    (server.state_dir / "audit.jsonl").mkdir()
    reply = server.search("SYNTHETIC_PRIVATE_QUERY")
    assert reply == {"error": "audit_unavailable"}
    assert "record" not in reply and "results" not in reply
    assert "SYNTHETIC_PRIVATE_ORIGINAL" not in serialized(reply).decode()

def test_query_outcome_fetch_policy_revocation_logs_denied(factory):
    server, path, backend, _ = factory()
    token = server.search("before policy revocation")["results"][0]["id"]
    replace_policy(path, lambda p: p["principals"]["operator"].clear())
    assert server.fetch(token) == {"error": "denied"}
    row = outcome_rows(server)[-1]
    assert row["tool"] == "fetch" and row["outcome"] == "denied"
    assert row["decision"] == "denied" and row["count"] == 0
    assert not backend.fetch_calls

