"""worker_routing x resource posture: annotate only (shadow), fail-open, enforce only when configured."""
import pathlib
from unittest.mock import patch

from z0int import dispatch_authority
from z0int import posture as P
from z0int import worker_routing as r
from z0int.dispatch_authority import append_receipt


def ann(posture="BURN", enforce=False, agrees=False):
    return {"schema": P.SCHEMA, "available": True, "factory_posture": posture, "prefer": ["claude"], "avoid": [],
            "revision": "abc", "route_kind": "offload", "agrees": agrees, "enforce": enforce, "action": "spend"}


def plan():
    return {"candidates": [{"provider": "cerebras", "model": "m"}], "skipped": [], "category": "general",
            "reason": "task category general", "policy_revision": "x"}


def test_shadow_annotation_does_not_change_plan():
    with patch.object(P, "shadow_annotation", return_value=ann()):
        p = r.posture_annotate(plan())
    assert p["candidates"] == plan()["candidates"] and p["skipped"] == []
    assert p["resource_posture"]["factory_posture"] == "BURN" and p["resource_posture"]["agrees"] is False
    assert "enforced" not in p["resource_posture"]


def test_fail_open_when_posture_raises():
    with patch.object(P, "shadow_annotation", side_effect=RuntimeError("boom")):
        p = r.posture_annotate(plan())
    assert p["candidates"] == plan()["candidates"]
    ann_ = p["resource_posture"]
    assert {k: ann_[k] for k in ("available", "error", "enforce")} == {"available": False, "error": "RuntimeError",
                                                                          "enforce": False}
    assert ann_["would"]["action"] == "none" and "enforced" not in ann_


# --- v1 enforce path (designed, OFF by default) --------------------------------------------------------


def policy_fixture():
    policy = {"max_attempts": 3, "free_only": True, "free_provider_order": ["openrouter"], "baseline": {},
              "defaults": {"openrouter": "nemo:free", "groot": "qwen3-8b-q4km", "local": "qwen3-0.6b-q8",
                           "cerebras": "m"},
              "provider_caps": {"openrouter": 1, "groot": 2, "local": 1, "cerebras": 32},
              "local_order": ["groot", "local"],
              "validated_free_routes": [
                  {"provider": "openrouter", "model": "nemo:free", "validated": True, "price_usd": 0, "evidence_sha256": "a"},
                  {"provider": "local", "model": "qwen3-0.6b-q8", "validated": True, "price_usd": 0, "evidence_sha256": "b"},
                  {"provider": "groot", "model": "qwen3-8b-q4km", "validated": True, "price_usd": 0, "evidence_sha256": "c"},
                  {"provider": "groot", "model": "qwen3-4b-q4km", "validated": True, "price_usd": 0, "evidence_sha256": "d"}]}
    providers = {"openrouter": {"cohort": "existing-api"}, "groot": {"cohort": "local", "auth": "none"},
                 "local": {"cohort": "local", "auth": "none"}, "cerebras": {"cohort": "trial-credit"}}
    return policy, providers


def always(provider, config):
    return True


def claude_plan(**kw):
    return {**plan(), "harness": "claude-code", "candidates": [{"provider": "openrouter", "model": "nemo:free"}], **kw}


def test_enforced_burn_returns_bounded_work_to_the_burning_parent():
    pol, prov = policy_fixture()
    with patch.object(P, "shadow_annotation", return_value=ann(enforce=True)):
        p = r.posture_annotate(claude_plan(), policy=pol, providers=prov, available_fn=always)
    ann_ = p["resource_posture"]
    assert p["candidates"] == [] and ann_["enforced"] is True and ann_["enforced_action"] == "return_to_parent"
    assert p["skipped"] == [{"provider": "openrouter", "reason": "posture_burn_parent_should_absorb"}]
    with patch.object(r, "receipts_path", return_value=pathlib.Path("/nonexistent/unused")):
        out = r.execute_plan({"parent_agent": "p"}, pol, prov, p, receipt_sink=append_receipt)
    assert out["ok"] is False and out["requires_parent"] is True and "posture BURN" in out["refusal_reason"]


def test_burn_does_not_hand_back_when_the_parents_pool_is_not_burning():
    pol, prov = policy_fixture()
    # codex parent while only claude burns: returning work to codex burns nothing that perishes.
    with patch.object(P, "shadow_annotation", return_value=ann(enforce=True)):
        p = r.posture_annotate({**claude_plan(), "harness": "codex"}, policy=pol, providers=prov, available_fn=always)
    assert p["candidates"] == [{"provider": "openrouter", "model": "nemo:free"}] and "enforced" not in p["resource_posture"]
    assert p["resource_posture"]["would"]["reason"] == "parent_pool_not_burning:codex"


def test_local_only_tasks_never_leave_the_host_under_burn():
    pol, prov = policy_fixture()
    local_plan = {**claude_plan(), "category": "local", "candidates": [{"provider": "groot", "model": "qwen3-8b-q4km"}]}
    with patch.object(P, "shadow_annotation", return_value=ann(enforce=True)):
        p = r.posture_annotate(local_plan, policy=pol, providers=prov, available_fn=always)
    assert p["candidates"] == [{"provider": "groot", "model": "qwen3-8b-q4km"}]
    assert p["resource_posture"]["would"]["reason"] == "local_only_task_stays_local"


def test_enforced_offload_prefers_zero_cost_routes_local_first():
    pol, prov = policy_fixture()
    with patch.object(P, "shadow_annotation", return_value=ann(posture="OFFLOAD", enforce=True, agrees=True)):
        p = r.posture_annotate(claude_plan(), policy=pol, providers=prov, available_fn=always)
    assert p["candidates"] == [{"provider": "groot", "model": "qwen3-8b-q4km"},
                               {"provider": "local", "model": "qwen3-0.6b-q8"},
                               {"provider": "openrouter", "model": "nemo:free"}]
    assert p["resource_posture"]["enforced_action"] == "prefer_zero_cost"
    # every candidate is still a validated $0 route: execute_plan's re-check passes
    assert all(r.free_route(pol, c["provider"], c["model"]) for c in p["candidates"])


def test_offload_skips_unavailable_capped_or_unvalidated_routes():
    pol, prov = policy_fixture()
    pol["provider_caps"]["local"] = 0
    pol["defaults"]["openrouter"] = "not-validated"
    with patch.object(P, "shadow_annotation", return_value=ann(posture="OFFLOAD", enforce=True, agrees=True)):
        p = r.posture_annotate(claude_plan(candidates=[]), policy=pol, providers=prov,
                               available_fn=lambda prov_, cfg: prov_ != "groot")
    assert p["resource_posture"]["would"] == {"action": "none", "reason": "no_usable_zero_cost_route",
                                              "changes_plan": False, "candidates": []}
    assert p["candidates"] == []


def test_shadow_records_the_counterfactual_but_changes_nothing():
    pol, prov = policy_fixture()
    for posture, action in (("BURN", "return_to_parent"), ("OFFLOAD", "prefer_zero_cost"), ("BALANCED", "none"),
                            ("RESERVE", "none")):
        with patch.object(P, "shadow_annotation", return_value=ann(posture=posture, enforce=False)):
            p = r.posture_annotate(claude_plan(), policy=pol, providers=prov, available_fn=always)
        assert p["candidates"] == claude_plan()["candidates"] and p["skipped"] == []
        assert p["resource_posture"]["would"]["action"] == action and "enforced" not in p["resource_posture"]


def test_unavailable_posture_never_enforces():
    pol, prov = policy_fixture()
    bad = {**ann(enforce=True), "available": False}
    with patch.object(P, "shadow_annotation", return_value=bad):
        p = r.posture_annotate(claude_plan(), policy=pol, providers=prov, available_fn=always)
    assert p["candidates"] == claude_plan()["candidates"] and "enforced" not in p["resource_posture"]


def test_route_worker_annotates_every_plan():
    seen = {}

    def fake_run(request, work):
        return work(lambda row: row)

    def fake_execute(args, policy, providers, plan, *, receipt_sink):
        seen["plan"] = plan
        return {"ok": True}

    with patch.object(P, "shadow_annotation", return_value=ann()), patch.object(dispatch_authority, "run", fake_run), \
            patch.object(r, "execute_plan", fake_execute), patch.object(r, "available", return_value=True):
        r.route_worker({"task": "Summarize a paragraph", "parent_agent": "p", "trace_id": "t1"})
    assert seen["plan"]["resource_posture"]["factory_posture"] == "BURN"
    assert seen["plan"]["candidates"], "shadow mode must not remove candidates"


def test_default_config_is_shadow(tmp_path, monkeypatch):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path))
    monkeypatch.setenv("Z0INT_POSTURE_CODEXBAR", str(tmp_path / "missing.json"))
    a = P.shadow_annotation()
    assert a["available"] is True and a["enforce"] is False
