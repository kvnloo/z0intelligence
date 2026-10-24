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


@pytest.mark.parametrize("confidence,returned_model,expected_status", [
    (0.99, "synthetic-06b", 0), (0.2, "synthetic-06b", 1),
    (0.99, "different-model", 1),
])
def test_scoped_policy_binds_actual_endpoint_without_caller_scope(
    isolated_policy, capsys, monkeypatch, confidence, returned_model, expected_status,
):
    from z0int.cognition.adapters.transport import ChatOutcome
    body = policy_body("live")
    rule = body["families"].pop("omp.tool")
    rule.update(family="omp.tool", scope={k: rule["arm"][k] for k in ("model", "quant", "engine", "backend", "device")})
    body["families"]["omp.tool@synthetic"] = rule
    endpoint = {**rule["arm"], "url": "http://127.0.0.1:12345/v1", "evidence": ["e" * 64]}
    body["serving"] = [endpoint]
    _, version = publish(isolated_policy, body)
    runtime._write_health(endpoint["url"], {
        "scope_sha256": runtime._scope_hash(endpoint), "status": "healthy",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "model": endpoint["model"], "revision": "synthetic-revision",
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
    assert calls == [("http://127.0.0.1:12345", "synthetic-06b")]
    assert result["policy_version"] == version
    assert result["policy_family"] == "omp.tool@synthetic"
    assert result["selected_arm"] == {k: (returned_model if k == "model" else endpoint[k])
                                      for k in ("model", "quant", "engine", "backend", "device")}
    assert result["response_model"] == returned_model
    assert result["response_revision"] == "synthetic-revision"
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
