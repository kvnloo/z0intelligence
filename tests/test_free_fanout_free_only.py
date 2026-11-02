"""The free-fanout probe is free-only whatever the policy says (PR #141).

No provider is contacted: the transport class is replaced by a recorder, admission
is a stub and every socket connection fails the test.
"""
import copy
import json
import socket
import sys
import types

import pytest

from z0int import free_fanout as ff
from z0int import worker_routing as wr

SLOTS = [
    ("nous", "inclusionai/ling-3.0-flash-sante:free"),
    ("vercel", "openai/gpt-oss-20b"),
    ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"),
    ("groq", "openai/gpt-oss-20b"),
    ("nvidia", "openai/gpt-oss-20b"),
]
VALIDATED = ("nous", "groq")
REFUSED = {"vercel", "openrouter", "nvidia"}


class HttpError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def test_fanout_plan_loads_kerdoios_builder_and_preserves_shape(monkeypatch, tmp_path):
    calls = []

    def build_fanout(tasks, *, workers):
        calls.append((tasks, workers))
        return {"slots": [{"slot": 0, "provider": "nous", "model": "free"}]}

    package = types.ModuleType("kerdoios")
    package.__path__ = []
    module = types.ModuleType("kerdoios.free_fanout")
    module.build_fanout = build_fanout
    monkeypatch.setitem(sys.modules, "kerdoios", package)
    monkeypatch.setitem(sys.modules, "kerdoios.free_fanout", module)
    monkeypatch.setattr(ff, "KERDOIOS_ROOT", tmp_path)

    assert ff.fanout_plan(1) == {
        "slots": [{"slot": 0, "provider": "nous", "model": "free"}]
    }
    assert calls == [([], 1)]
    assert sys.path[0] == str(tmp_path)


def test_fanout_plan_propagates_builder_errors(monkeypatch):
    module = types.ModuleType("kerdoios.free_fanout")

    def unavailable(tasks, *, workers):
        raise RuntimeError("no free providers")

    module.build_fanout = unavailable
    monkeypatch.setitem(sys.modules, "kerdoios.free_fanout", module)

    with pytest.raises(RuntimeError, match="no free providers"):
        ff.fanout_plan()


def test_plan_for_builds_single_candidate_and_rejects_incomplete_slot():
    assert ff._plan_for({"provider": "groq", "model": "free-model"}) == {
        "candidates": [{"provider": "groq", "model": "free-model"}],
        "source": "z0int.free_fanout",
        "category": "probe",
        "reason": "free fanout probe",
        "harness": "omp",
        "caller_trace_id": "free-fanout",
    }
    with pytest.raises(KeyError, match="model"):
        ff._plan_for({"provider": "groq"})


@pytest.fixture
def probe(tmp_path, monkeypatch):
    """Install the fakes. Returns (calls, statuses, policy, set_plan)."""
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.setattr("z0int.quota_budget.project", lambda *args, **kwargs: None)

    def no_network(sock, address):
        raise AssertionError(f"connection attempted: {address!r}")

    def no_oauth(provider):
        raise AssertionError("hermes oauth must not be resolved in tests")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(wr, "resolve_hermes_oauth", no_oauth)

    calls, statuses = [], {}

    class RecordingTransport:
        quota_headers = {}

        def __init__(self, config):
            self.provider = config.base_url.rsplit("/", 1)[-1]
            self.model = config.model

        def chat(self, messages, **kwargs):
            calls.append(self.provider)
            status = statuses.get(self.provider, 200)
            if status != 200:
                raise HttpError(status)
            return types.SimpleNamespace(
                content="pong", tool_call=None,
                usage={"prompt_tokens": 2, "completion_tokens": 1, "cost": 0},
                raw={"model": self.model, "id": "fake", "choices": [{"finish_reason": "stop"}]},
            )

    monkeypatch.setattr(wr, "OpenAICompatTransport", RecordingTransport)

    class AdmitAll:
        def acquire(self, provider, call_id, context):
            return {"admitted": True, "token": call_id, "inflight_at_admission": 0, "cap": 1, "capped": False, "reason": None}

        def release(self, *args, **kwargs):
            pass

    monkeypatch.setattr("z0int.provider_saturation.LocalAdmission", AdmitAll)

    policy, providers = copy.deepcopy(wr.configuration())
    policy["free_only"] = False
    policy["free_policy_exclusions"] = {}
    policy["sidestep_order"] = [{"provider": p, "model": m} for p, m in SLOTS]
    policy["validated_free_routes"] = [
        {"provider": p, "model": m, "validated": True, "price_usd": 0, "evidence_sha256": "fixture"}
        for p, m in SLOTS + [("cursor", "cursor-fixture")] if p in VALIDATED + ("cursor",)
    ]
    providers = {name: {"auth": "none", "base_url": f"http://fake.invalid/{name}", "models": []}
                 for name in [p for p, _ in SLOTS] + ["cursor"]}
    monkeypatch.setattr(ff, "configuration", lambda: (policy, providers))

    def set_plan(slots):
        plan = {"slots": [{"slot": i, "provider": p, "model": m} for i, (p, m) in enumerate(slots)]}
        monkeypatch.setattr(ff, "fanout_plan", lambda workers=5: plan)

    set_plan(SLOTS)
    return calls, statuses, policy, set_plan


def test_slot_without_validated_zero_cost_evidence_is_refused_and_never_called(probe):
    calls, _, policy, _ = probe
    assert policy["free_only"] is False
    report = ff.run()
    assert sorted(calls) == sorted(VALIDATED)
    rows = {row["provider"]: row for row in report["slots"]}
    for provider in REFUSED:
        assert rows[provider]["ok"] is False
        assert rows[provider]["error"] == "no_validated_free_route"
    for provider in VALIDATED:
        assert rows[provider]["ok"] is True
        assert [(a["provider"], a["status"], a["sidestep"]) for a in rows[provider]["attempts"]] == [(provider, "completed", False)]
    assert report["working"] == 2
    assert report["cursor_used"] is False


def test_402_inside_a_slot_sidesteps_only_to_a_validated_free_route(probe):
    calls, statuses, _, set_plan = probe
    set_plan(SLOTS[:1])
    statuses["nous"] = 402
    report = ff.run(1)
    assert calls == ["nous", "groq"]
    assert report["slots"][0]["ok"] is True
    assert report["slots"][0]["provider"] == "groq"


def test_no_validated_slot_prints_refusals_and_exits_nonzero(probe, capsys):
    calls, _, policy, _ = probe
    policy["validated_free_routes"] = []
    assert ff.main([]) == 1
    assert calls == []
    report = json.loads(capsys.readouterr().out)
    assert report["working"] == 0
    assert report["cursor_used"] is False
    assert report["slots"][1] == {"slot": 1, "ok": False, "provider": "vercel",
                                  "model": "openai/gpt-oss-20b", "error": "no_validated_free_route"}


def test_blocked_provider_slot_is_not_executed(probe):
    calls, _, policy, _ = probe
    _, providers = ff.configuration()
    row = ff._one({"slot": 0, "provider": "cursor", "model": "cursor-fixture"}, policy, providers)
    assert calls == []
    assert row == {"slot": 0, "ok": False, "provider": "cursor", "error": "cursor_or_paid_blocked"}


def test_plan_naming_a_blocked_provider_is_refused_whole(probe):
    calls, _, _, set_plan = probe
    set_plan(SLOTS[:1] + [("cursor", "cursor-fixture")])
    with pytest.raises(RuntimeError, match="paid parent"):
        ff.run(2)
    assert calls == []


def test_cursor_used_is_computed_from_recorded_attempts(probe, monkeypatch):
    _, _, _, set_plan = probe
    set_plan(SLOTS[:1])
    monkeypatch.setattr(ff, "execute_plan", lambda *args, **kwargs: {
        "ok": True, "provider": "cursor", "model": "m", "attempts": [{"provider": "cursor", "model": "m", "extra": {}}]})
    assert ff.run(1)["cursor_used"] is True


def test_one_maps_attempt_rows_without_leaking_transport_payload(probe, monkeypatch):
    _, _, policy, _ = probe
    _, providers = ff.configuration()
    monkeypatch.setattr(ff, "execute_plan", lambda *args, **kwargs: {
        "ok": False,
        "provider": "nous",
        "model": "free-model",
        "refusal_reason": "exhausted",
        "attempts": [
            {
                "provider": "nous",
                "model": "free-model",
                "extra": {
                    "status": "failed",
                    "http_status": 429,
                    "finish_reason": "length",
                    "sidestep": True,
                    "response_body": "must not escape",
                },
            },
            {"provider": "groq", "model": "fallback", "extra": None},
        ],
    })

    row = ff._one(
        {"slot": 3, "provider": "nous", "model": SLOTS[0][1]}, policy, providers
    )

    assert row == {
        "slot": 3,
        "ok": False,
        "provider": "nous",
        "model": "free-model",
        "refusal_reason": "exhausted",
        "attempts": [
            {
                "provider": "nous",
                "model": "free-model",
                "status": "failed",
                "http_status": 429,
                "finish": "length",
                "sidestep": True,
            },
            {
                "provider": "groq",
                "model": "fallback",
                "status": None,
                "http_status": None,
                "finish": None,
                "sidestep": None,
            },
        ],
    }


def test_one_maps_execution_exception_to_stable_error_name(probe, monkeypatch):
    _, _, policy, _ = probe
    _, providers = ff.configuration()

    class ProviderUnavailable(Exception):
        pass

    def fail(*args, **kwargs):
        raise ProviderUnavailable("secret provider details")

    monkeypatch.setattr(ff, "execute_plan", fail)
    row = ff._one(
        {"slot": 2, "provider": "nous", "model": SLOTS[0][1]}, policy, providers
    )
    assert row == {
        "slot": 2,
        "ok": False,
        "provider": "nous",
        "error": "ProviderUnavailable",
    }


def test_a_malformed_slot_is_reported_and_costs_no_other_slot(probe):
    calls, _, policy, _ = probe
    _, providers = ff.configuration()
    row = ff._one({"slot": 1, "provider": "vercel"}, policy, providers)
    assert calls == []
    assert row == {"slot": 1, "ok": False, "provider": "vercel", "error": "KeyError"}
