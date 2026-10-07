"""Hermes adapter tests using the canonical, read-only scoped memory resolver."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

PLUGIN_PATH = (
    Path(__file__).resolve().parents[1]
    / "harness-adapters"
    / "hermes-z0intelligence"
    / "__init__.py"
)


@pytest.fixture
def plugin():
    spec = importlib.util.spec_from_file_location("hermes_context_adapter_test", PLUGIN_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _native_identity(*, event_id: str, content: str):
    from z0int.memory_contract import EventIdentity

    return EventIdentity.from_source(
        source_system="codex",
        source_session="client-a",
        source_event_id=event_id,
        payload_hash="sha256:" + hashlib.sha256(content.encode()).hexdigest(),
    )


def _admit_user_claim(
    *, scope, event_id: str, value: str, observed_at: str, correction_of: str | None = None,
):
    from z0int.memory.claims import NativeUserClaimAttestation, admit_user_claim

    content = f"User decision: {value}"
    identity = _native_identity(event_id=event_id, content=content)

    def source_reader(locator: str):
        return {
            "role": "user",
            "content": content,
            "identity": identity.to_dict(),
            "scope": scope.to_dict(),
            "admitted_claims": (
                NativeUserClaimAttestation(
                    identity=identity,
                    scope=scope,
                    role="user",
                    subject="deployment",
                    predicate="strategy",
                    value=value,
                    observed_at=observed_at,
                ),
            ),
        }

    return admit_user_claim(
        identity=identity,
        locator=f"agentsview:client-a#{event_id}",
        scope=scope,
        subject="deployment",
        predicate="strategy",
        value=value,
        observed_at=observed_at,
        source_reader=source_reader,
        correction_of=correction_of,
    )


def _scope():
    from z0int.memory_contract import MemoryScope

    return MemoryScope(user="u1", project="p1", repo="org/repo", task="deploy-7")


def _decode_context(context: str) -> dict:
    prefix, separator, body = context.partition("\n")
    assert separator and prefix.startswith("Canonical z0int scoped-memory evidence packet.")
    return json.loads(body)


def test_registered_scope_opt_in_resolves_correction_for_a_fresh_client(
    plugin, monkeypatch, tmp_path,
):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "z0int"))
    monkeypatch.setenv("Z0INT_PYTHON", __import__("sys").executable)
    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "references")
    scope = _scope()
    first = _admit_user_claim(
        scope=scope,
        event_id="source-1",
        value="blue-green",
        observed_at="2026-10-01T00:00:00Z",
    )
    correction = _admit_user_claim(
        scope=scope,
        event_id="source-2",
        value="canary",
        observed_at="2026-10-02T00:00:00Z",
        correction_of=first.payload["claim_id"],
    )

    from z0int.memory.claims import record_claim
    from z0int.memory_contract import BitemporalClaim, MemoryScope

    record_claim(
        BitemporalClaim(
            claim_id="late-model-assertion",
            scope=scope,
            subject="deployment",
            predicate="strategy",
            value="rolling",
            status="observed",
            observed_at="2026-10-03T00:00:00Z",
            recorded_at="2099-01-01T00:00:00Z",
            origin_trust="explicit_user",  # The typed writer downgrades this claim.
        )
    )
    sibling = MemoryScope(user="u1", project="p1", repo="org/repo", task="deploy-8")
    _admit_user_claim(
        scope=sibling,
        event_id="source-sibling",
        value="sibling-secret",
        observed_at="2026-10-04T00:00:00Z",
    )

    from z0int.memory.event_log import EventLog

    event_path = EventLog().events_path
    before = event_path.read_bytes()
    captured_body_payloads = []
    body_writer_errors = []
    append_body_witness = plugin._append_body_witness

    def capture_and_append_body_witness(payload):
        captured_body_payloads.append(payload)
        try:
            append_body_witness(payload)
        except Exception as exc:
            body_writer_errors.append(
                f"{type(exc).__name__}: {exc}; stderr={getattr(exc, 'stderr', '')}"
            )
            raise

    monkeypatch.setattr(plugin, "_append_body_witness", capture_and_append_body_witness)
    settings = plugin._context_memory_settings(
        scope.to_dict(), subject="deployment", predicate="strategy"
    )
    assert settings is not None
    result = plugin.before_turn(
        session_id="hermes-client-b",
        turn_id="turn-b-1",
        task_id="native-hermes-task-b",
        user_message="continue the deployment decision",
        _memory_settings=settings,
    )
    after = event_path.read_bytes()

    assert after == before, "the Hermes context adapter is read-only"
    assert result and "context" in result
    context = _decode_context(result["context"])
    packet = context["packet"]
    measurements = packet
    assert measurements["scope"] == scope.to_dict()
    assert measurements["coverage"] == "complete"
    assert measurements["authority"] == "untrusted_evidence_only"
    assert packet["source_epochs"]["eventlog.scoped_claims"].startswith("sha256:")
    assert packet["source_epochs"]["eventlog.memory_snapshot"] == packet["memory_snapshot_id"]
    assert context["packet_sha256"]
    selected = measurements["selected_claims"]
    assert [claim["claim_id"] for claim in selected if claim["subject"] == "deployment"] == [
        correction.payload["claim_id"]
    ]
    winner = next(claim for claim in selected if claim["subject"] == "deployment")
    assert winner["value"] == "canary" and winner["origin_trust"] == "explicit_user"
    history = {row["claim_id"]: row for row in measurements["claim_history"]}
    assert history[first.payload["claim_id"]]["superseded_by"] == correction.payload["claim_id"]
    assert history["late-model-assertion"]["origin_trust"] == "model_assertion"
    assert history["late-model-assertion"]["superseded_by"] == correction.payload["claim_id"]
    assert any("rolling" in text for text in measurements["resolved_lower_trust_conflicts"])
    assert "sibling-secret" not in result["context"]
    assert context["runtime_provenance"]["adapter_source_sha256"]
    assert context["runtime_provenance"]["plugin_manifest_sha256"]

    # The body observer joins this exact opted-in turn, preserves Hermes' native task id
    # separately from the stable memory-scope task, and records no serialized prompt body.
    current_user_content = "continue the deployment decision\n\n" + result["context"]
    exact_body = json.dumps(
        {"model": "test-model", "messages": [{"role": "user", "content": current_user_content}]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    exact_bytes = exact_body.encode("utf-8")
    plugin.observe_provider_request_body(
        plugin.INSTANCE_ID,
        settings,
        session_id="hermes-client-b",
        task_id="native-hermes-task-b",
        turn_id="turn-b-1",
        api_request_id="turn-b-1:api:0",
        api_call_count=1,
        retry_count=0,
        request_index=1,
        provider="openai-compat",
        model="test-model",
        api_mode="chat_completions",
        platform="cli",
        streaming=True,
        body_available=True,
        serialized_body=exact_body,
        body_sha256=hashlib.sha256(exact_bytes).hexdigest(),
        body_byte_count=len(exact_bytes),
    )
    assert not body_writer_errors, body_writer_errors
    witness = captured_body_payloads[-1]
    assert witness["witness"]["status"] == "context_included_verified"
    assert witness["witness"]["body_sha256"] == hashlib.sha256(exact_bytes).hexdigest()
    assert witness["witness"]["body_byte_count"] == len(exact_bytes)
    assert witness["nativeTrace"]["task_id"] == "native-hermes-task-b"
    assert witness["memory_task_id"] == "deploy-7"
    assert witness["source_epochs"] == packet["source_epochs"]
    assert witness["packet_sha256"] == context["packet_sha256"]
    assert witness["memory_use_receipt"]["snapshot_id"] == packet["memory_snapshot_id"]
    assert "serialized_body" not in json.dumps(witness)
    assert exact_body not in json.dumps(witness)

    # A matching substring in an arbitrary request metadata field cannot prove injection.
    stripped_body = json.dumps(
        {
            "metadata": {"debug": context["packet_sha256"] + result["context"]},
            "messages": [{"role": "user", "content": "continue the deployment decision"}],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    stripped_bytes = stripped_body.encode("utf-8")
    plugin.observe_provider_request_body(
        plugin.INSTANCE_ID,
        settings,
        session_id="hermes-client-b",
        task_id="native-hermes-task-b",
        turn_id="turn-b-1",
        api_request_id="turn-b-1:api:0",
        api_call_count=1,
        retry_count=0,
        request_index=2,
        provider="openai-compat",
        model="test-model",
        api_mode="chat_completions",
        platform="cli",
        streaming=True,
        body_available=True,
        serialized_body=stripped_body,
        body_sha256=hashlib.sha256(stripped_bytes).hexdigest(),
        body_byte_count=len(stripped_bytes),
    )
    assert captured_body_payloads[-1]["witness"]["status"] == "context_not_in_current_user_message"

    # If a provider-specific transformation makes the final human user row unbindable,
    # an older row that happens to match the captured pre-hook text cannot be a fallback.
    unbindable_current_body = json.dumps(
        {
            "messages": [
                {"role": "user", "content": "continue the deployment decision\n\n" + result["context"]},
                {"role": "assistant", "content": "earlier answer"},
                {"role": "user", "content": "provider-transformed current input"},
            ]
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    unbindable_current_bytes = unbindable_current_body.encode("utf-8")
    plugin.observe_provider_request_body(
        plugin.INSTANCE_ID,
        settings,
        session_id="hermes-client-b",
        task_id="native-hermes-task-b",
        turn_id="turn-b-1",
        api_request_id="turn-b-1:api:0",
        api_call_count=1,
        retry_count=0,
        request_index=5,
        provider="openai-compat",
        model="test-model",
        api_mode="chat_completions",
        platform="cli",
        streaming=True,
        body_available=True,
        serialized_body=unbindable_current_body,
        body_sha256=hashlib.sha256(unbindable_current_bytes).hexdigest(),
        body_byte_count=len(unbindable_current_bytes),
    )
    assert captured_body_payloads[-1]["witness"]["status"] == "current_user_message_unavailable"

    # An independent reader can follow the DecisionReceipt's canonical EventLog reference
    # and confirm that both durable records carry the same packet fingerprint and receipt.
    body_event = next(
        event for event in EventLog().iter_events(require_complete=True)
        if event.event_type == "hermes.provider_request_body"
        and event.payload["witness"].get("body_sha256") == hashlib.sha256(exact_bytes).hexdigest()
    )
    from z0int.receipt import receipts_path

    receipt_rows = [
        json.loads(line)
        for line in receipts_path().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    matching_receipts = [
        receipt for receipt in receipt_rows
        if receipt.get("capability_id") == "hermes.provider_request_body"
        and receipt.get("extra", {}).get("provider_request_body", {}).get("body_sha256")
        == hashlib.sha256(exact_bytes).hexdigest()
    ]
    assert len(matching_receipts) == 1
    receipt_extra = matching_receipts[0]["extra"]
    assert receipt_extra["nativeTrace"] == body_event.payload["nativeTrace"]
    assert receipt_extra["event_ref"] == {
        "event_id": body_event.event_id,
        "event_type": body_event.event_type,
        "checksum": body_event.checksum,
    }
    assert receipt_extra["packet_sha256"] == context["packet_sha256"]
    assert receipt_extra["memory"]["snapshot_id"] == packet["memory_snapshot_id"]
    assert body_event.payload["packet_sha256"] == context["packet_sha256"]
    assert exact_body not in json.dumps(body_event.payload)
    assert exact_body not in json.dumps(receipt_extra)

    tool_result_only = json.dumps(
        {"messages": [{
            "role": "user",
            "content": [{"type": "tool_result", "content": result["context"]}],
        }]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    tool_result_bytes = tool_result_only.encode("utf-8")
    plugin.observe_provider_request_body(
        plugin.INSTANCE_ID,
        settings,
        session_id="hermes-client-b",
        task_id="native-hermes-task-b",
        turn_id="turn-b-1",
        api_request_id="turn-b-1:api:0",
        api_call_count=1,
        retry_count=0,
        request_index=4,
        provider="openai-compat",
        model="test-model",
        api_mode="chat_completions",
        platform="cli",
        streaming=True,
        body_available=True,
        serialized_body=tool_result_only,
        body_sha256=hashlib.sha256(tool_result_bytes).hexdigest(),
        body_byte_count=len(tool_result_bytes),
    )
    assert captured_body_payloads[-1]["witness"]["status"] == "current_user_message_unavailable"

    # A prior user row can contain this turn's matching bytes; the stripped current
    # user message must still be unavailable as proof of this turn's injection.
    prior_only_body = json.dumps(
        {
            "messages": [
                {"role": "user", "content": result["context"]},
                {"role": "assistant", "content": "earlier answer"},
                {"role": "user", "content": "continue the deployment decision"},
                {"role": "tool", "content": result["context"]},
            ]
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    prior_only_bytes = prior_only_body.encode("utf-8")
    plugin.observe_provider_request_body(
        plugin.INSTANCE_ID,
        settings,
        session_id="hermes-client-b",
        task_id="native-hermes-task-b",
        turn_id="turn-b-1",
        api_request_id="turn-b-1:api:0",
        api_call_count=1,
        retry_count=0,
        request_index=3,
        provider="openai-compat",
        model="test-model",
        api_mode="chat_completions",
        platform="cli",
        streaming=True,
        body_available=True,
        serialized_body=prior_only_body,
        body_sha256=hashlib.sha256(prior_only_bytes).hexdigest(),
        body_byte_count=len(prior_only_bytes),
    )
    assert captured_body_payloads[-1]["witness"]["status"] == "context_not_in_current_user_message"


def test_provider_body_unavailable_remains_explicit_and_is_not_hash_invented(plugin, monkeypatch):
    settings = plugin._context_memory_settings({"user": "u1"})
    assert settings is not None
    payloads = []
    monkeypatch.setattr(plugin, "_append_body_witness", payloads.append)
    plugin._remember_turn_context(
        owner_id="owner-a",
        session_id="session-a",
        task_id="native-task-a",
        turn_id="turn-a",
        settings=settings,
        status="context_injected",
        rendered_context="Canonical scoped block",
        resolved=None,
    )
    plugin.observe_provider_request_body(
        "owner-a",
        settings,
        session_id="session-a",
        task_id="native-task-a",
        turn_id="turn-a",
        api_request_id="request-a",
        request_index=1,
        body_available=False,
        unavailable_reason="request_body_not_buffered",
        serialized_body=None,
        body_sha256=None,
        body_byte_count=None,
    )
    witness = payloads[-1]["witness"]
    assert witness["status"] == "body_unavailable"
    assert witness["reason"] == "request_body_not_buffered"
    assert witness["body_sha256"] is None
    assert witness["body_byte_count"] is None


def test_scope_absent_or_malformed_keeps_only_legacy_automatic_path(plugin, monkeypatch):
    calls = []

    def fake_invoke(operation, value):
        calls.append((operation, value))
        if operation == "event":
            return {"action": "context", "context": "legacy automatic result", "receipt_id": "r1"}
        return {"ok": True}

    monkeypatch.setattr(plugin, "invoke", fake_invoke)
    monkeypatch.setattr(
        plugin,
        "_invoke_context_memory",
        lambda _settings: pytest.fail("malformed or absent scope must not resolve memory"),
    )

    assert plugin.before_turn(session_id="s", turn_id="t", user_message="hello") == {
        "context": "legacy automatic result"
    }
    assert [op for op, _ in calls] == ["event", "consume"]
    assert calls[0][1]["session_id"] == "s"

    registered_hooks = []
    ctx = SimpleNamespace(
        get_config=lambda key: (
            {"user": "u1", "task": "task-b"}
            if key == "context_memory_scope"
            else None
        ),
        register_hook=lambda name, callback: (
            registered_hooks.append(name), setattr(ctx, "callback", callback)
        ),
    )
    plugin.register(ctx)
    assert registered_hooks == ["pre_llm_call"], "malformed scope must not register a body observer"
    assert ctx.callback(session_id="s2", turn_id="t2", user_message="hello") == {
        "context": "legacy automatic result"
    }
    assert [op for op, _ in calls[-2:]] == ["event", "consume"]


def test_missing_scope_never_widens_and_packet_gaps_or_contradictions_are_omitted(
    plugin, monkeypatch, tmp_path,
):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "z0int"))
    from z0int.context_resolve import InformationNeed, resolve_context

    from z0int.memory_contract import MemoryScope

    packet = resolve_context(
        needs=[InformationNeed(id="m", description="memory", kind="memory", required=True)],
        allow_memory=True,
        memory_scope=None,
        allow_fff=False,
        allow_qmd=False,
        use_cache=False,
    )
    settings = plugin._context_memory_settings(
        MemoryScope(user="u1", project="p1").to_dict()
    )
    assert settings
    assert plugin._memory_packet_context(
        {"packet": packet.to_dict(), "packet_sha256": "0" * 64, "runtime_provenance": {}},
        settings,
    ) is None

    bad = {
        "packet": {
            "schema": "z0int.context_resolve.v1",
            "task_id": settings.scope.get("task"),
            "unresolved_gaps": ["explicit memory scope is required"],
            "contradictions": [],
            "recipe": None,
            "measurements": {},
        },
        "packet_sha256": "0" * 64,
        "runtime_provenance": {},
    }
    assert plugin._memory_packet_context(bad, settings) is None


def test_oversized_packet_is_omitted_instead_of_spilled_or_truncated(plugin, monkeypatch):
    monkeypatch.setattr(plugin, "_spill_safe_context_limit", lambda: 32)
    settings = plugin._context_memory_settings({"user": "u1"})
    assert settings
    # A complete packet is intentionally too large for Hermes' hook budget.
    # It must not become a preview/path that looks like a usable receipt.
    packet = {
        "schema": "z0int.context_resolve.v1",
        "task_id": None,
        "needs": [],
        "contradictions": [],
        "unresolved_gaps": [],
        "recipe": {
            "capability_id": "context_resolve",
            "source_epochs": {
                "eventlog.scoped_claims": "sha256:" + "a" * 64,
                "eventlog.memory_snapshot": "snapshot-test",
            },
            "operations": [{
                "op": "memory_claims", "need": "hermes_scoped_memory",
                "status": "ready", "coverage": "complete", "required": True,
                "authority": "untrusted_evidence_only",
                "generation": "sha256:" + "a" * 64,
                "snapshot_id": "snapshot-test",
            }],
        },
        "measurements": {
            "coverage": "complete",
            "memory_scope": {"user": "u1", "level": "user"},
            "memory_coverage": "complete",
            "memory_authority": "untrusted_evidence_only",
            "memory_source_revision": "sha256:" + "a" * 64,
            "coverage_operations": [{
                "op": "memory_claims", "need": "hermes_scoped_memory",
                "coverage": "complete", "status": "ready",
                "authority": "untrusted_evidence_only", "required": True,
            }],
            "selected_claims": [{
                "claim_id": "claim-1", "subject": "s", "predicate": "p", "value": "x" * 500,
                "origin_trust": "explicit_user", "evidence_event_uids": ["event-1"],
            }],
            "claim_history": [{"claim_id": "claim-1", "current": True, "origin_trust": "explicit_user"}],
            "memory_use_receipt": {
                "included_claim_ids": ["claim-1"], "evidence_event_uids": ["event-1"],
                "capability_ids": ["eventlog.scoped_claims"], "snapshot_id": "snapshot-test",
            },
            "memory_snapshot": {"snapshot_id": "snapshot-test"},
            "memory_snapshot_id": "snapshot-test",
        },
        "evidence": [{
            "source_id": "agentsview:client-a#event-1",
            "source_version": "sha256:" + "e" * 64,
            "locator": "agentsview:client-a#event-1",
            "trust_class": "conversation",
            "observed_at": "2026-10-01T00:00:00Z",
            "note": "source event event-1; ledger event 4",
        }],
    }
    packet["measurements"]["memory_snapshot"].update({
        "snapshot_id": "snapshot-test",
        "scope": {"user": "u1", "level": "user"},
        "source_revisions": {"eventlog.scoped_claims": "sha256:" + "a" * 64},
        "claim_ids": ["claim-1"],
        "evidence_event_uids": ["event-1"],
    })
    packet["measurements"]["memory_use_receipt"]["snapshot_id"] = "snapshot-test"
    runtime = {
        "source_root": str(PLUGIN_PATH.parents[2]),
        "python_executable": "/test/python",
        "python_version": "3.11.0",
        "adapter_source_sha256": plugin._sha256_file(PLUGIN_PATH),
        "plugin_manifest_sha256": plugin._sha256_file(PLUGIN_PATH.parent / "plugin.yaml"),
        "resolver_source_sha256": plugin._sha256_file(PLUGIN_PATH.parents[2] / "src" / "z0int" / "context_resolve.py"),
        "memory_contract_source_sha256": plugin._sha256_file(PLUGIN_PATH.parents[2] / "src" / "z0int" / "memory_contract.py"),
        "memory_claims_source_sha256": plugin._sha256_file(PLUGIN_PATH.parents[2] / "src" / "z0int" / "memory" / "claims.py"),
    }
    resolved = {
        "packet": packet,
        "packet_sha256": hashlib.sha256(
            json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
        ).hexdigest(),
        "runtime_provenance": runtime,
    }
    monkeypatch.setattr(plugin, "_spill_safe_context_limit", lambda: 9_000)
    assert plugin._memory_packet_context(resolved, settings) is not None

    # A self-consistent packet hash does not make a disconnected resolver
    # generation valid: the operation must point at the same scoped-claim epoch.
    operation = packet["recipe"]["operations"][0]
    original_generation = operation["generation"]
    operation["generation"] = "sha256:" + "f" * 64
    resolved["packet_sha256"] = hashlib.sha256(
        json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    assert plugin._memory_packet_context(resolved, settings) is None

    operation["generation"] = original_generation
    resolved["packet_sha256"] = hashlib.sha256(
        json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()
    monkeypatch.setattr(plugin, "_spill_safe_context_limit", lambda: 32)
    assert plugin._memory_packet_context(resolved, settings) is None


def test_malformed_packet_or_resolver_failure_keeps_legacy_context(plugin, monkeypatch, tmp_path):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "z0int"))
    settings = plugin._context_memory_settings({"user": "u1"})
    assert settings
    monkeypatch.setattr(plugin, "invoke", lambda _operation, _value: {"action": "native"})
    monkeypatch.setattr(plugin, "_invoke_context_memory", lambda _settings: (_ for _ in ()).throw(TimeoutError()))
    assert plugin.before_turn(
        session_id="hermes-b",
        turn_id="turn-b",
        user_message="question",
        _memory_settings=settings,
    ) is None


def test_child_resolver_does_not_inject_claims_from_incomplete_ledger(plugin, monkeypatch, tmp_path):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "z0int"))
    monkeypatch.setenv("Z0INT_PYTHON", sys.executable)
    monkeypatch.setenv("Z0INT_MEMORY_SOURCE_INGEST", "references")
    scope = _scope()
    _admit_user_claim(
        scope=scope,
        event_id="source-1",
        value="blue-green",
        observed_at="2026-10-01T00:00:00Z",
    )

    from z0int.memory.event_log import EventLog

    event_path = EventLog().events_path
    with event_path.open("ab") as events:
        events.write(b'{"event_id":2,"partial":')

    settings = plugin._context_memory_settings(
        scope.to_dict(), subject="deployment", predicate="strategy"
    )
    assert settings is not None
    resolver_calls = []
    actual_resolver = plugin._invoke_context_memory

    def observe_resolver(resolver_settings):
        resolver_calls.append(resolver_settings)
        return actual_resolver(resolver_settings)

    monkeypatch.setattr(plugin, "invoke", lambda _operation, _value: {})
    monkeypatch.setattr(plugin, "_invoke_context_memory", observe_resolver)

    result = plugin.before_turn(
        session_id="hermes-client-b",
        turn_id="turn-b-1",
        user_message="continue the deployment decision",
        _memory_settings=settings,
    )

    assert resolver_calls == [settings]
    assert result is None
