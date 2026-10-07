from __future__ import annotations

import json
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

from z0int.cli import main
from z0int.context_resolve import ContextPacket
from z0int.memory_contract import BitemporalClaim, MemoryScope


def _seed_claim(
    scope: MemoryScope,
    *,
    claim_id: str,
    value: str,
    subject: str = "deployment",
    predicate: str = "strategy",
) -> None:
    from z0int.memory.claims import record_claim

    record_claim(
        BitemporalClaim(
            claim_id=claim_id,
            scope=scope,
            subject=subject,
            predicate=predicate,
            value=value,
            status="observed",
            observed_at="2026-10-01T00:00:00Z",
            recorded_at="2026-10-01T00:00:00Z",
        )
    )


def _run_cli(monkeypatch, *args: str) -> dict:
    output = StringIO()
    with redirect_stdout(output):
        result = main(["context", "resolve", "--json", "--no-qmd", *args])
    assert result == 0
    return json.loads(output.getvalue())


def test_explicit_scope_returns_claim_snapshot_receipt_and_recipe(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "home"))
    scope = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    _seed_claim(scope, claim_id="claim-a", value="canary")
    _seed_claim(scope, claim_id="claim-other", subject="database", predicate="engine", value="postgres")
    scope_file = tmp_path / "scope.json"
    scope_file.write_text(json.dumps(scope.to_dict()), encoding="utf-8")

    packet = _run_cli(
        monkeypatch,
        "--allow-memory",
        "--memory-scope",
        str(scope_file),
        "--memory-subject",
        "deployment",
        "--memory-predicate",
        "strategy",
    )

    measurements = packet["measurements"]
    assert [claim["claim_id"] for claim in measurements["selected_claims"]] == ["claim-a"]
    assert measurements["selected_claims"][0]["value"] == "canary"
    assert measurements["memory_snapshot"]["snapshot_id"] == measurements["memory_snapshot_id"]
    assert measurements["memory_use_receipt"]["snapshot_id"] == measurements["memory_snapshot_id"]
    assert packet["recipe"] is not None
    assert any(key.startswith("eventlog.scoped_claims") for key in packet["recipe"]["source_epochs"])
    assert (
        packet["recipe"]["source_epochs"]["eventlog.scoped_claims"]
        == measurements["memory_source_revision"]
    )
    memory_operation = next(
        op for op in packet["recipe"]["operations"] if op["op"].startswith("memory")
    )
    assert memory_operation["coverage"] == "complete"
    assert memory_operation["authority"] == "untrusted_evidence_only"
    assert not any("TencentDB" in item for item in packet["contradictions"])


def test_explicit_scope_excludes_sibling_task_claims(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "home"))
    task_a = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-a")
    task_b = MemoryScope(user="u1", project="p1", repo="org/repo", task="task-b")
    _seed_claim(task_a, claim_id="claim-a", value="canary")
    _seed_claim(task_b, claim_id="claim-b", value="secret-sibling-value")
    scope_file = tmp_path / "scope.json"
    scope_file.write_text(json.dumps(task_a.to_dict()), encoding="utf-8")

    packet = _run_cli(
        monkeypatch,
        "--allow-memory",
        "--memory-scope",
        str(scope_file),
    )

    encoded = json.dumps(packet)
    assert "claim-a" in encoded
    assert "claim-b" not in encoded
    assert "secret-sibling-value" not in encoded


def test_memory_opt_in_without_scope_is_a_required_fail_closed_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "home"))

    packet = _run_cli(monkeypatch, "--allow-memory")

    assert any("memory" in gap and "scope" in gap for gap in packet["unresolved_gaps"])
    memory_operation = next(
        op for op in packet["recipe"]["operations"] if op["op"].startswith("memory")
    )
    assert memory_operation["required"] is True
    assert memory_operation["coverage"] == "unavailable"
    assert memory_operation["reason"] == "missing_scope"
    assert packet["measurements"]["coverage"] == "unavailable"
    assert "no memory resolver configured" not in json.dumps(packet)


def test_scope_file_is_not_read_without_memory_opt_in(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "home"))
    existing = tmp_path / "input.md"
    existing.write_text("ordinary path evidence", encoding="utf-8")

    packet = _run_cli(
        monkeypatch,
        "--path",
        str(existing),
        "--memory-scope",
        str(tmp_path / "does-not-exist.json"),
    )

    assert packet["evidence"][0]["locator"] == str(existing)
    assert not any(need["kind"] == "memory" for need in packet["needs"])


def test_scope_and_repository_orientation_options_are_passed_as_typed_inputs(tmp_path):
    scope_file = tmp_path / "scope.json"
    scope_file.write_text(
        json.dumps({"user": "u1", "project": "p1", "repo": "org/repo", "task": "task-a"}),
        encoding="utf-8",
    )
    roots = [tmp_path / "checkout-a", tmp_path / "checkout-b"]

    with mock.patch("z0int.context_resolve.resolve_context", return_value=ContextPacket()) as resolver:
        output = StringIO()
        with redirect_stdout(output):
            result = main(
                [
                    "context",
                    "resolve",
                    "--allow-memory",
                    "--memory-scope",
                    str(scope_file),
                    "--query",
                    "show deployment context",
                    "--canonical-repo",
                    "org/repo",
                    "--registry-path",
                    str(tmp_path / "registry.yaml"),
                    "--candidate-root",
                    str(roots[0]),
                    "--candidate-root",
                    str(roots[1]),
                    "--registry-format",
                    "generated",
                ]
            )

    assert result == 0
    kwargs = resolver.call_args.kwargs
    assert kwargs["memory_scope"] == MemoryScope(
        user="u1", project="p1", repo="org/repo", task="task-a"
    )
    assert kwargs["canonical_repo"] == "org/repo"
    assert kwargs["registry_path"] == str(tmp_path / "registry.yaml")
    assert kwargs["candidate_roots"] == [str(root) for root in roots]
    assert kwargs["registry_format"] == "generated"
    assert kwargs["allow_memory"] is True
    assert any(need.kind == "memory" and need.required for need in kwargs["needs"])
    assert any(
        need.kind == "natural_language" and need.description == "show deployment context"
        for need in kwargs["needs"]
    )


def test_invalid_explicit_scope_fails_before_resolver_call(tmp_path):
    scope_file = tmp_path / "scope.json"
    scope_file.write_text('{"user": "u1", "task": "task-a"}', encoding="utf-8")
    errors = StringIO()

    with mock.patch("z0int.context_resolve.resolve_context") as resolver:
        with redirect_stdout(StringIO()), mock.patch("sys.stderr", errors):
            result = main(
                [
                    "context",
                    "resolve",
                    "--allow-memory",
                    "--memory-scope",
                    str(scope_file),
                ]
            )

    assert result == 2
    assert "invalid --memory-scope" in errors.getvalue()
    resolver.assert_not_called()
