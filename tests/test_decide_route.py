"""The shadow role API uses the host's pure route entry point, never live signals."""
import copy
import json

import pytest

from z0int import decide as D, decide_shadow as S, intelligence as I


ROLES = D.load_roles()


def signals():
    return {"roles": copy.deepcopy(ROLES), "burn": {"lanes": {
        name: {"band": "on-pace", "pace_ratio": 0.9, "max_workflow_agents": 4,
               "effort": "medium"}
        for name in ("claude", "claude2", "claude3", "codex")}},
        "saturation": {}, "free_inventory": {"disabled": [], "paid": []}}


@pytest.fixture(autouse=True)
def no_live_readers(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Pure decisions must not acquire live signals or credentials")
    for name in ("routing_snapshot", "configuration", "ensure_credential", "create_backend"):
        monkeypatch.setattr(I, name, forbidden)
    monkeypatch.setattr(S, "live_signals", forbidden)
    monkeypatch.setattr(S, "tap", forbidden)


@pytest.mark.parametrize("role", sorted(ROLES["roles"]))
@pytest.mark.parametrize("privacy", sorted(ROLES["privacy"]))
def test_role_route_matches_existing_api_without_mutating_inputs(role, privacy):
    req, snapshot = {"role": role, "privacy": privacy}, signals()
    before = copy.deepcopy((req, snapshot))
    expected = D.decide(req, snapshot)
    assert I.route(req, snapshot) == expected
    assert (req, snapshot) == before
    assert expected["receipt_id"]


def test_decide_is_an_adapter_to_route(monkeypatch):
    req, snapshot = {"role": "task", "privacy": "public"}, signals()
    expected = {"kind": "HOLD", "reason": "sentinel"}
    calls = []
    def route(args, facts):
        calls.append((args, facts))
        return expected
    monkeypatch.setattr(I, "route", route)
    assert D.decide(req, snapshot) is expected
    assert calls == [(req, snapshot)]


def test_shadow_rows_reach_the_same_route_entry_point(monkeypatch):
    calls = []
    real = I.route
    def route(args, facts):
        calls.append(args)
        return real(args, facts)
    monkeypatch.setattr(I, "route", route)
    req = {"role": "task", "privacy": "public", "executors": ["fx"]}
    rows = S.shadow_rows([{"source": "fx", "id": "synthetic", "ts": 1,
                          "req": req, "actual": {"provider": "openai-codex", "model": "gpt-6.1-terra"},
                          "tokens": {}}], signals())
    assert len(calls) == 1 and calls[0] == req
    assert rows[0]["agree"] is True


def test_mixed_role_and_capability_request_is_rejected():
    with pytest.raises(ValueError, match="must not be mixed"):
        I.route({"role": "task", "privacy": "public", "function": "cheap_bounded_worker"}, signals())


def test_capability_route_still_uses_supplied_snapshot():
    registry = json.loads(I.REGISTRY.read_text())
    snapshot = {"registry": registry, "registry_sha256": "synthetic-registry",
                "policy": {"free_only": False}, "providers": {}, "available_providers": set()}
    req = {"harness": "codex", "trace_id": "synthetic", "parent_agent": "test",
           "function": "cheap_bounded_worker", "task": "two plus two",
           "expected_parent_tokens": 10, "expected_parent_ms": 100}
    before = copy.deepcopy((req, snapshot))
    decision = I.route(req, snapshot)
    assert decision["kind"] == "PARENT_ONLY" and "1347" in decision["reason"]
    assert decision["registry_sha256"] == "synthetic-registry"
    assert (req, snapshot) == before


def test_role_route_rejects_unknown_privacy():
    with pytest.raises(ValueError, match="Unknown privacy"):
        I.route({"role": "task", "privacy": "unclassified"}, signals())


def test_role_decisions_cannot_be_dispatched(monkeypatch):
    monkeypatch.setattr(I, "execute", lambda *a, **k: pytest.fail("Role execution is forbidden"))
    with pytest.raises(ValueError, match="Unknown request fields"):
        I.dispatch({"role": "task", "privacy": "public"})


@pytest.mark.parametrize("args", [None, [], "role", 4])
def test_invalid_request_keeps_capability_validation(args):
    with pytest.raises(ValueError, match="Unknown request fields"):
        I.route(args, {})
