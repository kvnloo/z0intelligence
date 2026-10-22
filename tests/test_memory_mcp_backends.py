"""Synthetic adapter contracts; no production stores or credentials are read."""
import json
from pathlib import Path
import sys
import time

import pytest

from z0int import memory_mcp_backends as backends
from z0int.memory_mcp import MemoryServer


def grant(source, collection, scope):
    return {"source": source, "collection": collection, "scope": scope, "privacy": ["private"]}


def test_agentsview_reads_own_auth_and_configured_port_and_checks_project(tmp_path, monkeypatch):
    config = tmp_path / ".agentsview"
    config.mkdir()
    (config / "config.toml").write_text('port = 19481\nauth_token = "synthetic-backend-token"\n')
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    calls = []

    def http(port, path, deadline, **kwargs):
        calls.append((port, path, kwargs))
        return {"matches": [
            {"project": "allowed", "snippet": "Permitted fixture", "session_id": "fixture", "ordinal": 1},
            {"project": "denied", "snippet": "Never returned"},
        ]}

    monkeypatch.setattr(backends, "_loopback_json", http)
    result = backends.search_backend("agentsview", "harmless", [grant("agentsview", "history", "allowed")], 10, 1)
    assert len(result) == 1 and result[0]["scope"] == "allowed"
    assert calls[0][0] == 19481
    assert "reveal=false" in calls[0][1]
    assert calls[0][2]["headers"]["Authorization"] == "Bearer synthetic-backend-token"


def test_ctx_refuses_fuzzy_workspace_results_without_exact_metadata(monkeypatch):
    observed = []

    def command(argv, deadline, **kwargs):
        observed.append((argv, kwargs))
        return {"results": [{"snippet": "Unscoped fixture", "ctx_event_id": "fixture"}]}

    monkeypatch.setattr(backends, "_bounded_command", command)
    with pytest.raises(backends.BackendUnavailable, match="exact_scope_metadata"):
        backends.search_backend("ctx", "--index=untrusted", [grant("ctx", "history", "/exact/work")], 1, 1)
    argv, kwargs = observed[0]
    assert argv[-2:] == ["--", "--index=untrusted"]
    assert argv[argv.index("--refresh") + 1] == "off"
    assert argv[argv.index("--backend") + 1] == "lexical"
    assert kwargs["env"]["CTX_DATA_ROOT"] == backends.CTX_DATA_ROOT


def test_qmd_rechecks_collection_and_preserves_option_boundary(monkeypatch):
    observed = []

    def command(argv, deadline, **kwargs):
        observed.append(argv)
        return [
            {"file": "qmd://allowed/fixture.md", "snippet": "Allowed fixture"},
            {"file": "qmd://denied/fixture.md", "snippet": "Denied fixture"},
            {"file": "file:///etc/passwd", "snippet": "Denied locator"},
        ]

    monkeypatch.setattr(backends, "_bounded_command", command)
    rows = backends.search_backend("qmd", "--full", [grant("qmd", "allowed", "qmd://allowed")], 10, 1)
    assert [row["text"] for row in rows] == ["Allowed fixture"]
    assert observed[0][-2:] == ["--", "--full"]


@pytest.mark.parametrize("collection", ["atomic", "conversation"])
def test_tencentdb_requires_all_returned_tenancy_fields(monkeypatch, collection):
    key = "items" if collection == "atomic" else "messages"
    scope = "team=t;agent=a;user=u"
    calls = []

    def http(port, path, deadline, **kwargs):
        calls.append((port, path, kwargs))
        return {"code": 0, "data": {key: [{"id": "fixture", "content": "Missing native tenancy"}]}}

    monkeypatch.setattr(backends, "_loopback_json", http)
    with pytest.raises(backends.BackendUnavailable, match="exact_scope_metadata"):
        backends.search_backend("tencentdb", "harmless", [grant("tencentdb", collection, scope)], 1, 1)
    assert calls[0][0] == 8420
    assert calls[0][1] == f"/v3/{collection}/search"
    assert {k: calls[0][2]["body"][k] for k in ("team_id", "agent_id", "user_id")} == {"team_id": "t", "agent_id": "a", "user_id": "u"}


def test_tencentdb_denies_cross_tenant_rows(monkeypatch):
    monkeypatch.setattr(backends, "_loopback_json", lambda *args, **kwargs: {"code": 0, "data": {"items": [
        {"team_id": "t", "agent_id": "a", "user_id": "other", "content": "Forbidden"},
        {"team_id": "t", "agent_id": "a", "user_id": "u", "content": "Permitted", "id": "fixture"},
    ]}})
    rows = backends.search_backend("tencentdb", "harmless", [grant("tencentdb", "atomic", "team=t;agent=a;user=u")], 10, 1)
    assert [row["text"] for row in rows] == ["Permitted"]


@pytest.mark.parametrize("label", ["secret", "unknown"])
def test_native_privacy_is_not_relabelled_private(monkeypatch, label):
    monkeypatch.setattr(backends, "_bounded_command", lambda *args, **kwargs: [
        {"file": "qmd://allowed/fixture.md", "snippet": "Forbidden", "privacy": label},
    ])
    assert backends.search_backend("qmd", "harmless", [grant("qmd", "allowed", "qmd://allowed")], 1, 1) == []


def test_redaction_precedes_any_backend_truncation(tmp_path, monkeypatch):
    # A token crossing the former 16 KiB adapter crop must never survive as a prefix.
    text = "Safe prose. " * 1365 + "sk-" + "a" * 100
    monkeypatch.setattr(backends, "_bounded_command", lambda *args, **kwargs: [
        {"file": "qmd://allowed/fixture.md", "snippet": text},
    ])
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"version": 1, "allow": [grant("qmd", "allowed", "qmd://allowed")], "limits": {"result_bytes": 20480}}))
    server = MemoryServer(policy, tmp_path / "state")
    response = server.search("harmless")
    assert response["results"]
    assert "sk-" not in json.dumps(response)


def test_subprocess_output_limit_is_enforced():
    with pytest.raises(backends.BackendUnavailable, match="output_limit"):
        backends._bounded_command([sys.executable, "-c", "import sys; sys.stdout.write('x'*262145)"], time.monotonic() + 2)


def test_subprocess_deadline_is_enforced():
    with pytest.raises(backends.BackendUnavailable, match="timeout"):
        backends._bounded_command([sys.executable, "-c", "import time; time.sleep(2)"], time.monotonic() + 0.05)
