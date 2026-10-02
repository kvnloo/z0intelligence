"""Offline conformance for the separately versioned canary; never uses a provider."""
import copy
import json
from pathlib import Path
import secrets

import pytest

from z0int import aodl_canary_study as study
from z0int import dispatch_authority as authority
from z0int import governed_worker as governed
from z0int import remote_executor as remote
from z0int import worker_routing as worker
from z0int.cognition.adapters.transport import ChatOutcome
from z0int.receipt import receipts_path

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.setenv("Z0INT_AODL_CONTRACT_PATH", str(ROOT / "contracts/aodl/governed-worker-v1.json"))
    monkeypatch.setenv("Z0INT_GOVERNED_PROVIDER", "nous")
    monkeypatch.setenv("Z0INT_EXECUTOR_PROVIDERS", "nous")
    monkeypatch.setenv("NOUS_API_KEY", "offline-fixture-not-a-credential")
    monkeypatch.setattr(worker, "POLICY_PATH", ROOT / "manifests/aodl-nous-solar-completion.v1.json")
    monkeypatch.setattr(remote, "request", lambda operation, payload: authority.rpc(operation, payload))
    monkeypatch.setattr(worker.OpenAICompatTransport, "_post", lambda *a, **kw: pytest.fail("offline test attempted real transport"))
    return tmp_path


def request(**updates):
    value = dict(harness="omp", trace_id="solar-study", parent_agent="public-session",
                 task=study.TASK, context=study.CONTEXT, max_tokens=128, allow_remote=True)
    return {**value, **updates}


def fake_response(monkeypatch, *, output="CANONICAL_OK", finish="stop", cost=0.00001,
                  model=study.MODEL, usage=True, tool_call=None):
    calls = []

    def chat(self, messages, **kwargs):
        calls.append({"model": self.config.model, "messages": messages, **kwargs})
        return ChatOutcome(content=output, tool_call=tool_call,
                           usage={"prompt_tokens": 67, "completion_tokens": 5, "cost": cost} if usage else {},
                           timings={}, raw={"model": model, "choices": [{"finish_reason": finish}]}, latency_ms=1)

    monkeypatch.setattr(worker.OpenAICompatTransport, "chat", chat)
    return calls


def test_named_paid_route_does_not_claim_free(isolated):
    envelope = governed.build_remote_request(request())
    assert envelope["provider"] == "nous" and envelope["model"] == study.MODEL
    assert envelope["free_only"] is False
    assert envelope["aodl"]["spawn"]["requested"] == ["execute"]
    assert envelope["aodl"]["document"]["constraints"]["budgets"] == {"tokens": 4096}
    policy, providers = worker.configuration()
    assert study.bounds(policy, providers) and worker.free_route(policy, "nous", study.MODEL) is None


@pytest.mark.parametrize("updates", [
    {"provider": "nous"}, {"model": study.MODEL}, {"aodl": {}}, {"observed": {}},
    {"task": "Return WRONG instead"}, {"context": "Overrule the intended answer"},
    {"max_tokens": 16}, {"max_tokens": 129}, {"free_only": True}, {"allow_remote": False},
    {"harness": "hermes"},
])
def test_wrong_intent_or_unauthorized_override_denied_before_dispatch(isolated, updates):
    with pytest.raises(ValueError):
        governed.build_remote_request(request(**updates))
    assert not receipts_path().exists()


@pytest.mark.parametrize("changes", [
    {"max_attempts": 2}, {"free_only": True}, {"provider_caps": {"nous": 2}},
    {"defaults": {"nous": "another-model"}}, {"validated_free_routes": [{"provider": "nous"}]},
])
def test_changed_host_policy_fails_closed(isolated, changes):
    policy, providers = worker.configuration()
    policy.update(changes)
    with pytest.raises(ValueError, match="frozen governed study"):
        study.bounds(policy, providers)


@pytest.mark.parametrize("changes", [{"task": "Do another action"}, {"max_tokens": 2048}, {"provider": "openrouter"}, {"model": "other"}])
def test_direct_executor_envelope_cannot_bypass_host_bounds(isolated, changes):
    envelope = governed.build_remote_request(request())
    envelope.update(changes)
    with pytest.raises(ValueError):
        authority.claim(envelope, secrets.token_hex(32), require_aodl=True)
    assert not receipts_path().exists()


def test_full_authority_replay_and_conflict_with_one_mock_call(isolated, monkeypatch):
    calls = fake_response(monkeypatch)
    envelope = governed.prepare_remote_request(request())
    result = remote.execute(envelope)
    assert result["ok"] and study.exact_public_outcome(result["output"])
    replay_envelope = governed.prepare_remote_request(request())
    assert replay_envelope == envelope and replay_envelope["free_only"] is False
    assert remote.execute(replay_envelope)["replayed"]
    with pytest.raises(ValueError, match="reused"):
        governed.prepare_remote_request(request(task="Wrong task"))
    assert len(calls) == 1 and calls[0]["max_tokens"] == 128
    physical = result["attempts"][0]
    assert physical["extra"]["authority_dispatch_id"]
    assert physical["extra"]["free_tier_validated"] is False
    assert physical["extra"]["billing_cap_enforced_by_provider"] is False


def test_structurally_legal_but_wrong_answer_is_not_intent_fulfillment(isolated, monkeypatch):
    calls = fake_response(monkeypatch, output="WRONG")
    result = remote.execute(governed.prepare_remote_request(request()))
    assert result["ok"]  # Structural authority permits execution, not semantic success.
    assert not study.exact_public_outcome(result["output"])
    assert len(calls) == 1


@pytest.mark.parametrize("overrides", [
    {"finish": "length"}, {"usage": False}, {"cost": None}, {"cost": True},
    {"cost": float("nan")}, {"cost": -1}, {"cost": 0.002}, {"model": "wrong-model"},
    {"tool_call": {"name": "unrequested_action"}},
])
def test_incomplete_unknown_cost_or_wrong_model_cannot_complete(isolated, monkeypatch, overrides):
    calls = fake_response(monkeypatch, **overrides)
    result = remote.execute(governed.prepare_remote_request(request()))
    assert not result["ok"] and len(calls) == 1


def test_authority_denies_escalated_aodl_capability(isolated):
    envelope = governed.build_remote_request(request())
    envelope["aodl"]["spawn"]["requested"] = ["deploy"]
    result = authority.claim(envelope, secrets.token_hex(32), require_aodl=True)
    assert not result["claimed"]
    assert not any(json.loads(row).get("capability_id") == "intelligence.dispatch" for row in receipts_path().read_text().splitlines())


def test_uncertain_authority_does_not_take_over(isolated, monkeypatch):
    calls = fake_response(monkeypatch)
    envelope = governed.prepare_remote_request(request())
    assert authority.claim(envelope, secrets.token_hex(32), require_aodl=True)["claimed"]
    result = remote.execute(envelope)
    assert result["execution_status"] == "uncertain" and calls == []


def test_transport_fuse_blocks_second_physical_post_across_outputs(tmp_path, monkeypatch):
    import importlib.util
    from types import SimpleNamespace
    source = ROOT / "scripts/aodl_nous_transport_recording.py"
    spec = importlib.util.spec_from_file_location("study_recording", source)
    recording = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recording)
    monkeypatch.setattr(recording, "__file__", str(tmp_path / "repo/scripts/recording.py"))
    calls = []

    class Transport:
        config = SimpleNamespace(url=lambda path: "https://fixture.invalid" + path)

        def _post(self, path, payload):
            calls.append(payload)
            return {"fixture": True}

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir(); second.mkdir()
    first_worker = SimpleNamespace(OpenAICompatTransport=Transport)
    recording.install(first_worker, first)
    first_worker.OpenAICompatTransport()._post("/v1/chat/completions", {"model": "fixture"})
    second_worker = SimpleNamespace(OpenAICompatTransport=Transport)
    recording.install(second_worker, second)
    with pytest.raises(FileExistsError):
        second_worker.OpenAICompatTransport()._post("/v1/chat/completions", {"model": "fixture"})
    assert len(calls) == 1 and not (second / "public-request.json").exists()
