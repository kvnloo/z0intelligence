"""Synthetic policy consumers; no model servers, providers or private traces."""
import argparse
import copy
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from z0int import policy_runtime as runtime
from z0int.cognition import cli
from z0int.cognition.escalation import EscalationPolicy, EscalationSignals


@pytest.fixture(autouse=True)
def isolated_policy(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.setattr(runtime, "_cache_key", None)
    monkeypatch.setattr(runtime, "_cache_policy", None)
    monkeypatch.setattr(runtime, "_health", {})
    monkeypatch.setattr(runtime, "_health_pending", set())
    return tmp_path


def policy_body(stage="canary", share=0.5):
    return {
        "schema": "z0.decision_policy.v1",
        "compiled_at": datetime.now(timezone.utc).isoformat(),
        "findings_head": "f" * 64,
        "compiler": "z0int.policy.compile@synthetic",
        "families": {"omp.tool": {
            "stage": stage,
            "arm": {"kind": "slm", "model": "synthetic-06b", "quant": "Q4_0",
                    "engine": "synthetic-engine", "backend": "cpu", "device": "synthetic-device"},
            "threshold": 0.95, "abstain_to": "frontier", "canary_share": share,
            "evidence": ["e" * 64], "rule_id": "F-omp.tool-1",
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
            "demoted_reason": None,
        }},
        "serving": [], "changes": [],
    }


def publish(home, body):
    body = copy.deepcopy(body)
    body.pop("version", None)
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    body["version"] = digest
    path = home / "policy" / "decision-policy.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False))
    return path, digest


def test_canary_deterministic_and_matches_hash(isolated_policy):
    _, version = publish(isolated_policy, policy_body())
    policy = runtime.current()
    assert policy.version == policy.sha256 == version
    results = []
    for n in range(100):
        trace = f"synthetic-trace-{n}"
        expected = int.from_bytes(hashlib.sha256(trace.encode()).digest()[:8], "big") < 0.5 * (1 << 64)
        result = policy.credited_families(trace)
        assert result == policy.credited_families(trace) == runtime.credited_families(trace)
        assert bool(result) == expected
        results.append(bool(result))
    assert any(results) and not all(results)


@pytest.mark.parametrize(("stage", "share", "credited"), [
    ("none", 1.0, False), ("shadow", 1.0, False),
    ("canary", 0.0, False), ("canary", 1.0, True), ("live", 0.0, True),
])
def test_stage_credit_boundaries(isolated_policy, stage, share, credited):
    publish(isolated_policy, policy_body(stage, share))
    assert bool(runtime.credited_families("fixed-trace")) is credited


def test_tamper_rejected_even_with_unchanged_mtime(isolated_policy):
    path, _ = publish(isolated_policy, policy_body())
    assert runtime.current().version != "none"
    stat = path.stat()
    body = json.loads(path.read_text())
    body["families"]["omp.tool"]["canary_share"] = 1.0
    path.write_text(json.dumps(body))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    policy = runtime.current()
    assert policy.version == "none"
    assert "hash_mismatch" in policy.reason
    assert policy.stage("omp.tool") == "none"
    assert not policy.credited_families("fixed-trace")


def test_missing_policy_none_and_stamped(isolated_policy):
    policy = runtime.current()
    assert policy.reason == "policy_missing"
    assert runtime.stage("omp.tool") == "none"
    assert runtime.serving_for("any") == []
    row = runtime.stamp({}, "omp.tool")
    assert row == {
        "policy_version": "none", "policy_sha256": "none", "rule_id": "none",
        "stage": "none", "family": "omp.tool", "policy_reason": "policy_missing",
        "serving_healthy": None, "policy_family": "omp.tool",
        "synthetic": False,
    }


@pytest.mark.parametrize("bad", ["not-json", "null", "[]", '{"schema":"wrong"}'])
def test_corrupt_policy_never_throws(isolated_policy, bad):
    path = isolated_policy / "policy" / "decision-policy.json"
    path.parent.mkdir()
    path.write_text(bad)
    assert runtime.current().version == "none"
    assert runtime.current().stamp({}, "omp.tool")["stage"] == "none"


def test_valid_hash_cannot_bypass_schema(isolated_policy):
    body = policy_body()
    body["families"]["omp.tool"]["canary_share"] = 2.0
    publish(isolated_policy, body)
    assert runtime.current().version == "none"


def test_expired_rule_loses_credit_on_cached_policy(isolated_policy, monkeypatch):
    body = policy_body("live")
    publish(isolated_policy, body)
    policy = runtime.current()
    assert policy.stage("omp.tool") == "live"
    after = datetime.now(timezone.utc) + timedelta(days=2)

    class Later(datetime):
        @classmethod
        def now(cls, tz=None):
            return after

    monkeypatch.setattr(runtime, "datetime", Later)
    assert runtime.current() is policy
    assert policy.stage("omp.tool") == "none"
    assert not policy.credited_families("trace")
    assert policy.stamp({}, "omp.tool")["policy_reason"] == "evidence_expired"


def test_scoped_rule_never_generalizes(isolated_policy):
    body = policy_body("live")
    rule = body["families"].pop("omp.tool")
    rule.update(family="omp.tool", scope={"device": "synthetic-device", "quant": "Q4_0"})
    body["families"]["omp.tool@synthetic"] = rule
    publish(isolated_policy, body)
    policy = runtime.current()
    assert policy.credited_families("trace") == ("omp.tool",)
    assert not policy.for_scope({"device": "synthetic-device", "quant": "Q8_0"}).credited_families("trace")
    scoped = policy.for_scope({"device": "synthetic-device", "quant": "Q4_0"})
    assert scoped.credited_families("trace") == ("omp.tool",)
    assert scoped.stamp({}, "omp.tool")["rule_id"] == rule["rule_id"]
    assert scoped.stamp({}, "omp.tool")["policy_family"] == "omp.tool@synthetic"


def run_cli(home, capsys, monkeypatch, credits=()):
    request = {
        "trace_id": "synthetic-cli-trace", "credited_families": list(credits),
        "actions": [{"action_id": "read", "kind": "tool", "tool": "read", "family": "omp.tool"}],
        "authority": ["read"], "budget_units": 8,
    }
    path = home / "request.json"
    path.write_text(json.dumps(request))
    captured = []
    cascade_type = cli.CognitionCascade

    class RecordingCascade(cascade_type):
        def __init__(self, **kwargs):
            captured.extend(kwargs["policy"].thresholds.credited_families)
            super().__init__(**kwargs)

    monkeypatch.setattr(cli, "CognitionCascade", RecordingCascade)
    args = argparse.Namespace(input=str(path), surface=False, catalog=None, max_cost_class=None,
                              shadow=[], json=True)
    assert cli._cmd_decide(args) == 0
    return json.loads(capsys.readouterr().out), captured


def test_payload_override_recorded_on_every_receipt(isolated_policy, capsys, monkeypatch):
    body, credited = run_cli(isolated_policy, capsys, monkeypatch, ("omp.tool",))
    assert credited == ["omp.tool"]
    rows = [body, body["escalation"], *body["attempts"], *body["receipts"]]
    assert body["receipts"]
    for row in rows:
        assert row["payload_override"] == ["omp.tool"]
        assert row["policy_version"] == row["policy_sha256"] == "none"
        assert row["rule_id"] == row["stage"] == "none"
        assert row["policy_reason"] == "policy_missing"
        assert "serving_healthy" in row


def test_policy_credit_union_and_receipts_always_stamped(isolated_policy, capsys, monkeypatch):
    _, version = publish(isolated_policy, policy_body("live"))
    body, credited = run_cli(isolated_policy, capsys, monkeypatch, ("extra.family", "omp.tool"))
    assert credited == ["extra.family", "omp.tool"]
    assert body["trace_id"] == "synthetic-cli-trace"
    for row in [body, body["escalation"], *body["attempts"], *body["receipts"]]:
        assert row["policy_version"] == row["policy_sha256"] == version
        assert row["stage"] == "none"
        assert row["policy_stage"] == "live"
        assert row["policy_reason"] == "no_serving_for_scope"
        assert row["rule_id"] == "F-omp.tool-1"
        assert row["payload_override"] == ["extra.family"]


def test_escalation_revision_and_frontier_abstain_confidence(isolated_policy):
    decision = EscalationPolicy().decide(EscalationSignals(
        legal_action_count=3, backend_confidence=0.2, specialist_abstained=True,
        latency_budget_ms=1, families=("omp.tool",),
    ))
    assert decision.tier == "remote_frontier"
    assert decision.escalation_version == "escalation-v1"
    assert decision.policy_version == "none"
    assert decision.to_dict()["abstain"] == {
        "confidence": 0.2, "threshold": 0.65, "reason": "bounded_scorer_uncertain",
    }
    assert "serving_healthy" in decision.to_dict()


def test_health_probe_nonblocking_and_cached(isolated_policy, monkeypatch):
    body = policy_body("live")
    body["serving"] = [{"device": "synthetic-device", "model": "synthetic-06b",
                        "engine": "synthetic-engine", "backend": "cpu", "quant": "Q4_0",
                        "url": "http://127.0.0.1:12345/v1", "evidence": ["e" * 64]}]
    publish(isolated_policy, body)
    starts = []

    class DeferredThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
        def start(self):
            starts.append(self.kwargs)

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self):
            return b'{"data":[{"id":"synthetic-06b","revision":"synthetic-revision"}]}'

    calls = []
    def open_health(url, timeout):
        calls.append((url, timeout))
        return Response()

    monkeypatch.setattr(runtime.threading, "Thread", DeferredThread)
    monkeypatch.setattr(runtime, "urlopen", open_health)
    policy = runtime.current()
    assert policy.stamp({}, "omp.tool")["serving_healthy"] is None
    assert policy.stamp({}, "omp.tool")["serving_healthy"] is None
    assert len(starts) == 1 and starts[0]["daemon"] is True
    assert calls == []
    runtime._probe_health(body["serving"][0]["url"], body["serving"][0])
    assert calls == [("http://127.0.0.1:12345/v1/models", 1.0)]
    assert policy.stamp({}, "omp.tool")["serving_healthy"] is True
    assert len(starts) == 1


def test_shadow_persisted_receipt_stamped(isolated_policy):
    from z0int.cognition.shadow import run_shadow
    result = run_shadow({
        "trace_id": "synthetic-shadow", "state": "synthetic", "shadows": [],
        "actions": [{"action_id": "read", "kind": "tool", "tool": "read", "family": "omp.tool"}],
        "authority": ["read"], "budget_units": 8,
    })
    assert result["ok"] is True
    row = json.loads(open(result["receipts_path"]).readline())
    assert row["policy_version"] == "none"
    assert row["policy_reason"] == "policy_missing"
    assert row["escalation"]["policy_sha256"] == "none"
    assert "serving_healthy" in row


@pytest.mark.parametrize("expected_alias", ["synthetic-06b", "synthetic model/+alias"])
@pytest.mark.parametrize("confidence,returned_model,expected_status", [
    (0.99, "synthetic-06b", 0), (0.2, "synthetic-06b", 1),
    (0.99, "different-model", 1),
])
def test_scoped_policy_binds_actual_endpoint_without_caller_scope(
    isolated_policy, capsys, monkeypatch, confidence, returned_model, expected_status, expected_alias,
):
    from z0int.cognition.adapters.transport import ChatOutcome
    from urllib.parse import quote
    if returned_model == "synthetic-06b":
        returned_model = expected_alias
    body = policy_body("live")
    rule = body["families"].pop("omp.tool")
    rule.update(family="omp.tool", scope={k: rule["arm"][k] for k in ("model", "quant", "engine", "backend", "device")})
    body["families"]["omp.tool@synthetic"] = rule
    endpoint = {**rule["arm"], "url": "http://127.0.0.1:12345/v1", "evidence": ["e" * 64]}
    endpoint.update(expected_model_id=expected_alias, engine_build="synthetic-build",
                    model_path="/synthetic/model.gguf", model_sha256="a" * 64)
    body["serving"] = [endpoint]
    _, version = publish(isolated_policy, body)
    runtime._write_health(endpoint["url"], {
        "scope_sha256": runtime._scope_hash(endpoint), "status": "healthy",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "model": expected_alias, "revision": None,
    })
    calls = []

    def answer(transport, messages, **kwargs):
        calls.append((transport.config.base_url, transport.config.model))
        return ChatOutcome(
            content=json.dumps({"action_id": "read", "confidence": confidence}),
            tool_call=None, usage={}, timings={}, latency_ms=1.0,
            raw={"model": returned_model, "revision": "synthetic-revision"},
        )

    monkeypatch.setattr("z0int.cognition.adapters.transport.OpenAICompatTransport.chat", answer)
    props_calls = []
    class PropsResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self):
            return json.dumps({"model_alias": expected_alias, "build_info": "synthetic-build",
                               "model_path": "/synthetic/model.gguf"}).encode()
    def props(url, timeout):
        props_calls.append((url, timeout))
        return PropsResponse()
    monkeypatch.setattr("z0int.cognition.adapters.policy.urlopen", props)
    # A role registry is not an authority for this decision, even if configured.
    monkeypatch.setattr(cli.LocalModelRegistry, "from_environment",
                        lambda: pytest.fail("policy arm must not use default registry routes"))
    request = {
        "trace_id": "synthetic-bound-trace", "state": "pick a legal tool",
        "actions": [
            {"action_id": "read", "kind": "tool", "tool": "read", "family": "omp.tool"},
            {"action_id": "glob", "kind": "tool", "tool": "glob", "family": "omp.tool"},
        ],
        "authority": ["read"], "budget_units": 8,
    }
    path = isolated_policy / "request.json"
    path.write_text(json.dumps(request))
    args = argparse.Namespace(input=str(path), surface=False, catalog=None, max_cost_class=None,
                              shadow=[], json=True)
    assert cli._cmd_decide(args) == expected_status
    result = json.loads(capsys.readouterr().out)
    assert calls == [("http://127.0.0.1:12345", expected_alias)]
    assert props_calls == [("http://127.0.0.1:12345/props?model=" + quote(expected_alias, safe=""), 1.5)]
    assert result["policy_version"] == version
    assert result["policy_family"] == "omp.tool@synthetic"
    assert result["selected_arm"] == {k: endpoint[k] for k in ("model", "quant", "engine", "backend", "device")}
    assert result["response_model"] == returned_model
    assert result["response_revision"] is None
    assert result["observed_identity"] == {"model_id": returned_model, "engine_build": "synthetic-build",
                                           "model_path": "/synthetic/model.gguf"}
    assert result["serving_healthy"] is True
    if confidence < rule["threshold"]:
        assert result["route"] == "remote_frontier"
        assert result["abstain"]["confidence"] == confidence
        assert result["abstain"]["threshold"] == rule["threshold"]
        assert result["receipts"][-1]["abstain"] == result["abstain"]
    elif expected_status == 0:
        assert result["selected_action"] == "read"
        assert result["stage"] == "live"
        assert result["receipts"][0]["selected_arm"] == result["selected_arm"]


def test_full_scope_endpoint_match_includes_quant(isolated_policy):
    body = policy_body("live")
    body["serving"] = [{**body["families"]["omp.tool"]["arm"],
                       "quant": "Q8_0", "url": "http://127.0.0.1:12345/v1", "evidence": ["e" * 64]}]
    publish(isolated_policy, body)
    assert runtime.current().endpoint_for("omp.tool") is None


def test_shared_health_survives_process_cache_and_refreshes_stale_scope(isolated_policy, monkeypatch):
    endpoint = {**policy_body("live")["families"]["omp.tool"]["arm"],
                "url": "http://127.0.0.1:12345/v1"}
    calls = []

    class Response:
        status = 200
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self):
            return b'{"data":[{"id":"synthetic-06b","revision":"synthetic-revision"}]}'

    def answer(url, timeout):
        calls.append((url, timeout))
        return Response()

    monkeypatch.setattr(runtime, "urlopen", answer)
    assert runtime.serving_health(endpoint["url"], endpoint, probe=True) is True
    assert calls == [("http://127.0.0.1:12345/v1/models", 1.5)]
    saved = json.loads((isolated_policy / "policy" / "serving-health.json").read_text())
    entry = saved["endpoints"][endpoint["url"]]
    assert entry["scope_sha256"] == runtime._scope_hash(endpoint)
    assert entry["model"] == endpoint["model"]
    assert entry["revision"] == "synthetic-revision"
    runtime._health.clear()
    assert runtime.serving_health(endpoint["url"], endpoint, probe=True) is True
    assert len(calls) == 1
    entry["checked_at"] = (datetime.now(timezone.utc) - timedelta(seconds=61)).isoformat()
    runtime._write_health(endpoint["url"], entry)
    runtime._health.clear()
    assert runtime.serving_health(endpoint["url"], endpoint, probe=True) is True
    assert len(calls) == 2
    other_quant = {**endpoint, "quant": "Q8_0"}
    runtime._health.clear()
    assert runtime.serving_health(endpoint["url"], other_quant, probe=True) is True
    assert len(calls) == 3
    updated = json.loads((isolated_policy / "policy" / "serving-health.json").read_text())
    assert updated["endpoints"][endpoint["url"]]["scope_sha256"] == runtime._scope_hash(other_quant)


def test_unknown_shared_health_falls_back_with_reason(isolated_policy, capsys, monkeypatch):
    body = policy_body("live")
    endpoint = {**body["families"]["omp.tool"]["arm"],
                "url": "http://127.0.0.1:12345/v1", "evidence": ["e" * 64]}
    endpoint.update(expected_model_id="synthetic-06b", engine_build="synthetic-build",
                    model_path="/synthetic/model.gguf", model_sha256="a" * 64)
    body["serving"] = [endpoint]
    publish(isolated_policy, body)
    runtime._write_health(endpoint["url"], {
        "scope_sha256": runtime._scope_hash(endpoint), "status": "unknown",
        "checked_at": datetime.now(timezone.utc).isoformat(), "model": None, "revision": None,
    })
    monkeypatch.setattr(runtime, "urlopen", lambda *args, **kwargs: pytest.fail("fresh cache must not reprobe"))
    result, _ = run_cli(isolated_policy, capsys, monkeypatch)
    assert result["stage"] == "none"
    assert result["policy_stage"] == "live"
    assert result["policy_reason"] == "serving_health_unknown"
    assert result["serving_healthy"] is None


def test_compiled_none_demoted_reason_stays_on_receipt(isolated_policy):
    body = policy_body("none")
    body["families"]["omp.tool"]["demoted_reason"] = "evidence_expired"
    publish(isolated_policy, body)
    row = runtime.stamp({}, "omp.tool")
    assert row["stage"] == "none"
    assert row["policy_reason"] == "evidence_expired"


def test_synthetic_rule_taint_reaches_nested_and_fallback_rows(isolated_policy):
    body = policy_body("shadow")
    body["families"]["omp.tool"]["synthetic"] = True
    publish(isolated_policy, body)
    row = {
        "receipts": [{}], "attempts": [{}], "shadow": [{}], "escalation": {},
    }
    cli._stamp_decision(row, runtime.current(), ("omp.tool",), (), {})
    for emitted in [row, row["escalation"], *row["receipts"], *row["attempts"], *row["shadow"]]:
        assert emitted["synthetic"] is True
    assert runtime.stamp({}, "unknown-family")["synthetic"] is True
    assert runtime.stamp({"synthetic": False}, "omp.tool")["synthetic"] is True


def test_payload_synthetic_taint_does_not_need_a_policy(isolated_policy, capsys, monkeypatch):
    original = cli._graph_from_payload
    def synthetic_graph(payload):
        payload["synthetic"] = True
        return original(payload)
    monkeypatch.setattr(cli, "_graph_from_payload", synthetic_graph)
    row, _ = run_cli(isolated_policy, capsys, monkeypatch)
    for emitted in [row, row["escalation"], *row["receipts"], *row["attempts"]]:
        assert emitted["synthetic"] is True


@pytest.mark.parametrize("field,value,reason", [
    ("build_info", "different-build", "endpoint_build_mismatch"),
    ("model_path", "/synthetic/different.gguf", "endpoint_path_mismatch"),
])
def test_endpoint_observed_props_mismatch_abstains(isolated_policy, monkeypatch, field, value, reason):
    from z0int.cognition.actions import compile_actions
    from z0int.cognition.adapters.local_slm import ToolDecisionRequest
    from z0int.cognition.adapters.policy import PolicyArmBackend
    from z0int.cognition.adapters.transport import ChatOutcome
    endpoint = {**policy_body("live")["families"]["omp.tool"]["arm"],
                "url": "http://127.0.0.1:12345/v1", "expected_model_id": "synthetic-06b",
                "engine_build": "synthetic-build", "model_path": "/synthetic/model.gguf",
                "model_sha256": "a" * 64}
    legal = compile_actions(graph=cli._graph_from_payload({"actions": [
        {"action_id": "read", "kind": "tool", "family": "omp.tool"},
        {"action_id": "glob", "kind": "tool", "family": "omp.tool"},
    ]}), granted_capabilities=(), authority=("read",), budget_units=8)
    monkeypatch.setattr("z0int.cognition.adapters.transport.OpenAICompatTransport.chat",
                        lambda *args, **kwargs: ChatOutcome(
                            content='{"action_id":"read","confidence":0.99}', tool_call=None,
                            usage={}, timings={}, latency_ms=1.0, raw={"model": "synthetic-06b"},
                        ))
    observed = {"model_alias": "synthetic-06b", "build_info": "synthetic-build",
                "model_path": "/synthetic/model.gguf", field: value}
    class Props:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self):
            return json.dumps(observed).encode()
    monkeypatch.setattr("z0int.cognition.adapters.policy.urlopen", lambda *args, **kwargs: Props())
    decision = PolicyArmBackend(endpoint, 0.95).decide(ToolDecisionRequest(state="synthetic", legal=legal))
    assert decision.abstained is True
    assert decision.selected_action is None
    assert decision.diagnostics["reason"] == reason
    assert decision.diagnostics["response_revision"] is None


def capacity_rule(body, allowed=4, scope=None):
    scope = scope or {k: body["families"]["omp.tool"]["arm"][k]
                      for k in ("model", "quant", "engine", "backend", "device")}
    key = json.dumps(scope, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    body["option_capacity"] = {key: {
        "allowed_k": allowed, "finding_ids": ["e" * 64], "wilson_lower": 0.99,
        "task": ["T1", "T2", "T3", "T4"], "bar": 0.95, "expires_at": "2099-01-01T00:00:00+00:00",
    }}
    return scope, key


def test_option_capacity_exact_scope_expiry_and_default(isolated_policy):
    body = policy_body("shadow")
    scope, key = capacity_rule(body, 4)
    publish(isolated_policy, body)
    assert runtime.current().allowed_k(scope) == 4
    assert runtime.allowed_k({**scope, "quant": "Q8_0"}) == 2
    assert runtime.allowed_k({**scope, "device": "other-device"}) == 2
    assert runtime.allowed_k({"model": scope["model"]}) == 2
    body["option_capacity"][key]["expires_at"] = "2000-01-01T00:00:00+00:00"
    publish(isolated_policy, body)
    assert runtime.allowed_k(scope) == 2


@pytest.mark.parametrize("field,value", [
    ("allowed_k", True), ("allowed_k", 0), ("finding_ids", []), ("task", []),
    ("wilson_lower", 0.94), ("bar", "0.95"), ("expires_at", "broken"),
])
def test_invalid_capacity_grant_cannot_raise_binary_bound(isolated_policy, field, value):
    body = policy_body()
    scope, key = capacity_rule(body, 8)
    body["option_capacity"][key][field] = value
    publish(isolated_policy, body)
    assert runtime.allowed_k(scope) == 2


def test_missing_policy_blocks_wide_model_request_before_transport(isolated_policy):
    from z0int.cognition.actions import compile_actions
    from z0int.cognition.adapters.local_slm import LocalSLMBackend, ToolDecisionRequest
    from z0int.cognition.adapters.transport import ServerConfig
    scope = {k: policy_body()["families"]["omp.tool"]["arm"][k]
             for k in ("model", "quant", "engine", "backend", "device")}
    legal = compile_actions(graph=cli._graph_from_payload({"actions": [
        {"action_id": name, "kind": "tool"} for name in ("read", "glob", "grep")
    ]}), authority=("read",), budget_units=8)
    class NoSend:
        def chat(self, *args, **kwargs):
            pytest.fail("over-cap request must not reach model transport")
    backend = LocalSLMBackend(backend_id="synthetic", config=ServerConfig("http://fixture", "served-alias"),
                              scope=scope, transport=NoSend())
    request = ToolDecisionRequest(state="synthetic", legal=legal, budget={"allowed_k": 999})
    decision = backend.decide(request)
    assert decision.abstained is True
    assert decision.parse_error == "option_capacity_exceeded"
    assert request.metadata == decision.diagnostics["request_metadata"]
    row = decision.to_dict()
    assert row["slm_requested"] is False
    assert row["option_count"] == 0
    assert row["attempted_option_count"] == 3
    assert row["allowed_k"] == 2
    assert row["model_id"] == scope["model"] and row["quant"] == scope["quant"]
    assert row["scope"] == scope


def test_qualified_request_stamps_actual_not_maximum_width(isolated_policy):
    from z0int.cognition.actions import compile_actions
    from z0int.cognition.adapters.local_slm import LocalSLMBackend, ToolDecisionRequest
    from z0int.cognition.adapters.transport import ChatOutcome, ServerConfig
    body = policy_body("shadow")
    scope, _ = capacity_rule(body, 8)
    publish(isolated_policy, body)
    legal = compile_actions(graph=cli._graph_from_payload({"actions": [
        {"action_id": name, "kind": "tool"} for name in ("read", "glob", "grep"),
    ]}), authority=("read",), budget_units=8)
    calls = []
    class Transport:
        def chat(self, messages, **kwargs):
            calls.append(kwargs)
            return ChatOutcome('{"action_id":"read","confidence":0.99}', None, {}, {}, {}, 1.0)
    backend = LocalSLMBackend(backend_id="synthetic", config=ServerConfig("http://fixture", "served-alias"),
                              scope=scope, transport=Transport(), policy=runtime.current())
    request = ToolDecisionRequest(state="synthetic", legal=legal)
    decision = backend.decide(request)
    assert decision.selected_action == "read"
    assert [tool["function"]["name"] for tool in calls[0]["tools"]] == list(legal.ids())
    wire = calls[0]["extra_body"]["metadata"]
    assert wire["option_count"] == "3" and wire["allowed_k"] == "8"
    assert wire["model_id"] == scope["model"] and wire["quant"] == scope["quant"]
    assert json.loads(wire["scope"]) == scope
    assert request.metadata["option_count"] == decision.to_dict()["option_count"] == 3
    assert decision.to_dict()["scope"] == scope


def test_cli_capacity_refusal_is_frontier_receipt_not_fake_abstain(isolated_policy, capsys, monkeypatch):
    body = policy_body("live")
    arm = body["families"]["omp.tool"]["arm"]
    endpoint = {**arm, "url": "http://127.0.0.1:12345/v1", "evidence": ["e" * 64],
                "expected_model_id": "synthetic-06b", "engine_build": "synthetic-build",
                "model_path": "/synthetic/model.gguf", "model_sha256": "a" * 64}
    body["serving"] = [endpoint]
    publish(isolated_policy, body)
    runtime._write_health(endpoint["url"], {"scope_sha256": runtime._scope_hash(endpoint), "status": "healthy",
        "checked_at": datetime.now(timezone.utc).isoformat(), "model": endpoint["expected_model_id"], "revision": None})
    monkeypatch.setattr("z0int.cognition.adapters.transport.OpenAICompatTransport.chat",
                        lambda *args, **kwargs: pytest.fail("must not send over-cap request"))
    monkeypatch.setattr("z0int.cognition.adapters.policy.urlopen",
                        lambda *args, **kwargs: pytest.fail("blocked decision needs no props request"))
    request = {"trace_id": "synthetic-capacity-refusal", "authority": ["read"], "budget_units": 8,
        "actions": [{"action_id": name, "family": "omp.tool"} for name in ("read", "glob", "grep")]}
    path = isolated_policy / "request.json"
    path.write_text(json.dumps(request))
    args = argparse.Namespace(input=str(path), surface=False, catalog=None, max_cost_class=None, shadow=[], json=True)
    assert cli._cmd_decide(args) == 1
    row = json.loads(capsys.readouterr().out)
    assert row["reason"] == row["policy_reason"] == "option_capacity_exceeded"
    assert row["route"] == "remote_frontier" and row["stage"] == "none" and row["policy_stage"] == "live"
    assert row["option_count"] == 0 and row["attempted_option_count"] == 3 and row["slm_requested"] is False
    assert row["allowed_k"] == 2 and "abstain" not in row
    assert row["model_id"] == arm["model"] and row["quant"] == arm["quant"]
    assert row["scope"] == {k: arm[k] for k in ("model", "quant", "engine", "backend", "device")}
    assert row["receipts"][-1]["reason"] == "option_capacity_exceeded"


def test_timed_out_model_row_retains_actual_request_capacity_metadata(isolated_policy):
    import time
    from z0int.cognition.actions import compile_actions
    from z0int.cognition.adapters.local_slm import LocalSLMBackend, ToolDecisionRequest
    from z0int.cognition.adapters.transport import ChatOutcome, ServerConfig
    from z0int.cognition.shadow import ShadowSpec, _decision_rows
    scope = {k: policy_body()["families"]["omp.tool"]["arm"][k]
             for k in ("model", "quant", "engine", "backend", "device")}
    legal = compile_actions(graph=cli._graph_from_payload({"actions": [
        {"action_id": name, "kind": "tool"} for name in ("read", "glob"),
    ]}), authority=("read",), budget_units=8)
    class Slow:
        def chat(self, messages, **kwargs):
            time.sleep(0.25)
            return ChatOutcome('{"action_id":"read","confidence":0.99}', None, {}, {}, {}, 250.0)
    backend = LocalSLMBackend(backend_id="synthetic", config=ServerConfig("http://fixture", "served-alias"),
                              scope=scope, transport=Slow())
    rows = _decision_rows([(ShadowSpec(label="synthetic", model_id=scope["model"]), backend, None)],
                         frozenset(legal.ids()), ToolDecisionRequest(state="synthetic", legal=legal), timeout_s=0.1)
    assert rows[0]["error"] == "shadow_timeout"
    assert rows[0]["slm_requested"] is True and rows[0]["option_count"] == 2
    assert rows[0]["model_id"] == scope["model"] and rows[0]["quant"] == scope["quant"]
    assert rows[0]["scope"] == scope and rows[0]["allowed_k"] == 2
