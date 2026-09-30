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
    assert p["resource_posture"] == {"available": False, "error": "RuntimeError", "enforce": False}


def test_enforce_true_burn_hands_back_to_parent():
    with patch.object(P, "shadow_annotation", return_value=ann(enforce=True)):
        p = r.posture_annotate(plan())
    assert p["candidates"] == [] and p["resource_posture"]["enforced"] is True
    assert p["skipped"] == [{"provider": "cerebras", "reason": "posture_burn_parent_should_absorb"}]
    policy, providers = r.configuration()
    with patch.object(r, "receipts_path", return_value=pathlib.Path("/tmp/unused")):
        out = r.execute_plan({"parent_agent": "p"}, policy, providers, p, receipt_sink=append_receipt)
    assert out["ok"] is False and out["requires_parent"] is True and "posture BURN" in out["refusal_reason"]


def test_enforce_true_but_agreeing_posture_changes_nothing():
    with patch.object(P, "shadow_annotation", return_value=ann(posture="OFFLOAD", enforce=True, agrees=True)):
        p = r.posture_annotate(plan())
    assert p["candidates"] == plan()["candidates"]


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
