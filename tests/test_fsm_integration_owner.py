"""Loop 3 safety, differential sabotage, and read-only collection boundaries."""
import itertools
import json
import subprocess
from pathlib import Path
import pytest
from z0int.fsm import integrate as C
from z0int.fsm import integration_owner as F
from z0int.fsm.integration_parity import run_parity


def test_full_path_and_drift_revokes_acceptance():
    s = "Branch"
    for e in [{"tag":"Draft"}, {"tag":"Queue"},
              {"tag":"Integrate","lease":True,"stack":True,"independent":True},
              {"tag":"Deploy","equal":True}, {"tag":"Accept","owner":True}]:
        s = F.step(s, e)
    assert s == "OwnerAccepted"
    assert F.step(s, {"tag":"Merge"}) == "Merged"
    assert F.step(s, {"tag":"Observe","equal":False}) == "OnIntegrationBranch"


def test_merge_cannot_bypass_owner():
    for s in F.STATES:
        assert F.step(s, {"tag":"Merge"}) == ("Merged" if s == "OwnerAccepted" else s)
    assert F.step("Deployed", {"tag":"Accept","owner":False}) == "Deployed"


@pytest.mark.parametrize("lease,stack,independent", itertools.product((False,True), repeat=3))
def test_integrate_needs_all_three(lease,stack,independent):
    assert F.step("Queued", dict(tag="Integrate",lease=lease,stack=stack,independent=independent)) == ("OnIntegrationBranch" if lease and stack and independent else "Queued")


def test_deadline_parking_and_lease_boundaries():
    assert not F.overdue(86399, False, False)
    assert F.overdue(86400, False, False)
    assert not F.overdue(86400, False, True)
    assert F.acquire((1,100),2,200,99) == (1,100)
    assert F.acquire((1,100),2,200,100) == (2,200)
    assert F.acquire(None,2,100,100) is None
    assert F.step("DraftPR", dict(tag="Park",reason=True,future=False)) == "DraftPR"
    with pytest.raises(ValueError):
        F.step("DraftPR", dict(tag="Park",reason="yes",future=True))


def test_bend_parity_every_prefix():
    r = run_parity(seed=3)
    assert r["equal"], r
    assert r["prefixes"] == 16225


def test_differential_harness_detects_test_owned_sabotage():
    # Bend is the production reference. This test-only runner is knowingly wrong.
    def sabotaged(s,e):
        return "Merged" if e["tag"] == "Merge" else F.step(s,e)
    r = run_parity(seed=3,stepper=sabotaged)
    assert not r["equal"]
    assert r["initial"] == "Branch" and r["tape"] == [{"tag":"Merge"}]


def test_parking_is_expiring_owner_decision_bound_to_head():
    r = dict(head="abc",reason="not this cycle",expires_at="2026-10-22T00:00:00Z",decided_by="Kevin")
    now = C.epoch("2026-10-21T00:00:00Z")
    assert C.parking_valid(r,"abc",now)
    assert not C.parking_valid(r,"def",now)
    assert not C.parking_valid(r,"abc",now+86400)
    assert not C.parking_valid({**r,"decided_by":"Sol"},"abc",now)


def test_current_parent_not_creation_base_is_compared(monkeypatch):
    parent = dict(number=1,isDraft=False,headRefName="parent",baseRefName="main",headRefOid="new-parent",baseRefOid="main-sha",createdAt="2026-10-19T00:00:00Z",mergeable="MERGEABLE",url="https://example/1",isCrossRepository=False)
    child = {**parent,"number":2,"isDraft":True,"headRefName":"child","baseRefName":"parent","headRefOid":"child-sha","baseRefOid":"old-parent"}
    monkeypatch.setattr(C,"command",lambda args,cwd=None: json.dumps([parent,child]))
    monkeypatch.setattr(C,"gh_api",lambda path:{"sha":"integration-sha"})
    calls = []
    def contains(repo,ancestor,descendant):
        calls.append((ancestor,descendant))
        return False
    monkeypatch.setattr(C,"contains",contains)
    r=C.collect_repo(dict(repo="a/b",integration_branch="integrate"),{},C.epoch("2026-10-21T00:00:00Z"))
    assert ("new-parent","old-parent") in calls
    assert r["changes"][0]["stack_consistent"] is False
    assert any(v["code"]=="STACK_BASE_STALE" for v in r["violations"])
    assert not r["needs_kevin"]


def test_checkout_match_does_not_attest_running_code(monkeypatch):
    monkeypatch.setattr(C,"git",lambda path,*args: "" if args[0]=="status" else "abc")
    s=C.inspect_service(dict(id="s",checkout="x"),dict(integration_branch="i",integration_head="abc"))
    assert s["checkout_matches_integration"]
    assert s["runtime_sha"] is None and "I2_RUNTIME_UNATTESTED" in s["violations"]


def test_collection_errors_fail_closed(monkeypatch):
    def failed(args,cwd=None):
        raise C.EvidenceError("offline")
    monkeypatch.setattr(C,"command",failed)
    r=C.collect_repo(dict(repo="a/b"),{},0)
    assert r["errors"] and r["changes"] == []


def test_duplicate_live_leases_are_not_silently_collapsed():
    leases=[dict(repo="a/b",owner=o,expires_at="2026-10-22T00:00:00Z") for o in ["a","b"]]
    assert len(C.leases_for(leases,"a/b",C.epoch("2026-10-21T00:00:00Z"))) == 2


def test_compare_api_direction(monkeypatch):
    C.contains.cache_clear()
    seen=[]
    monkeypatch.setattr(C,"gh_api",lambda path: seen.append(path) or {"status":"ahead"})
    assert C.contains("a/b","ancestor","descendant")
    assert seen == ["repos/a/b/compare/ancestor...descendant"]
    C.contains.cache_clear()
