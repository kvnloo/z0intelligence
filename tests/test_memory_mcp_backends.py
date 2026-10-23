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
    assert hit["locator"] == f"agentsview:{SESSION}:7"
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
    assert rows[0]["native"] == {"session_id": SESSION, "ordinal": 7}
    assert any("%3A" in url for url in native["calls"])
    with pytest.raises(backend.BackendUnavailable, match="unsupported_native_identity"):
        backend._native_id("../outside", 7)
    with pytest.raises(backend.BackendUnavailable, match="unsupported_native_identity"):
        backend._native_id(SESSION, -1)
