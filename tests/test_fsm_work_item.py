"""Work-item statechart: model, Bend parity, replay checker, export."""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from z0int.fsm import bend_parity, export, replay
from z0int.fsm.work_item import (
    Actor, Avail, Event, Item, Phase, Receipt, Role, astep, needs_kevin, run, step,
)

SOL = Actor("w1", Role.SOL)
CI = Actor("ci", Role.CI)
OPS = Actor("orch", Role.OPUS)
KEVIN = Actor("kevin", Role.OWNER)


def happy() -> list[Event]:
    return [Event("Claim", SOL), Event("Start", SOL), Event("Push", SOL, sha="h1"),
            Event("Submit", SOL), Event("Verify", CI, receipt=Receipt("h1", True)),
            Event("Integrate", OPS, sha="i1", receipt=Receipt("i1", True)),
            Event("Deploy", CI, sha="i1"), Event("Accept", KEVIN), Event("Merge", CI)]


def test_happy_path_reaches_merged_with_owner_acceptance():
    s = run(happy())
    assert s.phase is Phase.MERGED and s.ap == KEVIN and s.ig == "i1"


@pytest.mark.parametrize("i,bad,why", [
    (0, Event("Claim", Actor("nim", Role.FREE)), "L6 free model cannot claim/implement"),
    (0, Event("Claim", Actor("gpt", Role.CHAT)), "L6 chat app cannot implement"),
    (4, Event("Verify", Actor("w1", Role.CI), receipt=Receipt("h1", True)), "L2 author cannot verify"),
    (4, Event("Verify", CI, receipt=Receipt("OTHER", True)), "L3 receipt for another sha"),
    (5, Event("Integrate", OPS, sha="i1", receipt=Receipt("h1", True)), "L3 receipt not for integration head"),
    (5, Event("Integrate", Actor("w1", Role.OPUS), sha="i1", receipt=Receipt("i1", True)), "L2 author integrates"),
    (6, Event("Deploy", CI, sha="stale"), "L4 served head != integrated head"),
    (7, Event("Accept", Actor("w1", Role.OWNER)), "L2 author accepts"),
    (7, Event("Accept", OPS), "L1 only the owner accepts"),
    (7, Event("Merge", CI), "L1 merge before acceptance"),
])
def test_each_law_refuses_its_bug(i, bad, why):
    tape = happy()
    before = run(tape[:i])
    after = step(before, bad)
    good_next = step(before, tape[i])
    assert good_next != before, "control: the legal move must advance"
    if bad.kind == "Verify" and bad.actor == CI:
        assert after.phase is Phase.IMPLEMENTING, why  # unbound receipt sends work back
    else:
        assert after == before, why


def test_drift_undeploys():
    s = run(happy()[:7])
    assert s.phase is Phase.DEPLOYED
    assert step(s, Event("Observe", sha="other")).phase is Phase.INTEGRATED
    assert step(s, Event("Observe", sha="i1")) == s


def test_interrupted_resumes_with_head_or_abandons():
    s = run(happy()[:3] + [Event("Interrupt")])
    assert s.phase is Phase.INTERRUPTED and s.hd == "h1"
    r = step(s, Event("Resume", Actor("w2", Role.OPUS)))
    assert r.phase is Phase.IMPLEMENTING and r.hd == "h1" and r.au is not None and r.au.id == "w2"
    assert step(s, Event("Abandon", KEVIN)).phase is Phase.ABANDONED
    assert step(s, Event("Resume", Actor("j", Role.JEV))) == s


def test_owner_off_holds_and_never_blocks_others():
    deployed = run(happy()[:7])
    assert needs_kevin(deployed)
    assert astep(Avail.OFF, deployed, Event("Accept", KEVIN)) == deployed  # held in queue
    assert astep(Avail.LOW_ENERGY, deployed, Event("Accept", KEVIN)).phase is Phase.ACCEPTED
    other = run(happy()[:4])
    for av in Avail:  # a non-owner move is the same whatever Kevin's state
        assert astep(av, other, Event("Verify", CI, receipt=Receipt("h1", True))).phase is Phase.DRAFT_PR


# ---------------------------------------------------------------- Bend parity

BEND = Path(bend_parity.BEND)
needs_bend = pytest.mark.skipif(not BEND.exists(), reason="bend CLI not installed")


@needs_bend
def test_python_matches_bend_on_seeded_tapes():
    res = bend_parity.run_parity(seed=7, n=400, max_len=24)
    assert res["equal"], res
    assert len(res["final_phases"]) == 12, res["final_phases"]  # every phase reached


@needs_bend
def test_parity_harness_can_fail(monkeypatch):
    # test-only sabotage: Python lets authors verify their own work.
    # Production (fsm.bend) is the reference; this path is knowingly wrong.
    real = bend_parity.step

    def sabotaged(s, e):
        if s.phase is Phase.VERIFYING and e.kind == "Verify" and e.actor and e.actor.role is Role.CI:
            if e.receipt and e.receipt.passed and e.receipt.sha == s.hd:
                return Item(Phase.DRAFT_PR, au=s.au, hd=s.hd)
        return real(s, e)

    monkeypatch.setattr(bend_parity, "run", lambda es: _fold(sabotaged, es))
    assert not bend_parity.run_parity(seed=7, n=400, max_len=24)["equal"]


def _fold(f, es):
    s = Item()
    for e in es:
        s = f(s, e)
    return s


# ---------------------------------------------------------------- replay checker

def _pr(n, base, state="MERGED", checks=(), merged="2026-10-20T16:42:00Z", login="kvnloo"):
    return {"number": n, "title": f"pr {n}", "state": state, "isDraft": False, "headRefName": f"b{n}",
            "baseRefName": base, "headRefOid": f"{n:040d}", "mergedAt": merged if state == "MERGED" else None,
            "createdAt": "2026-10-20T16:00:00Z", "closedAt": None, "author": {"login": login},
            "mergedBy": {"login": login}, "mergeCommit": {"oid": f"{n + 500:040d}"}, "reviews": [],
            "labels": [], "statusCheckRollup": list(checks), "url": f"https://x/{n}"}


def _codes(t):
    return sorted((f.law, f.code, f.severity) for f in t.findings)


def test_replay_flags_merge_without_acceptance_and_late_tests():
    ok = {"name": "test", "workflowName": "CI", "conclusion": "SUCCESS", "completedAt": "2026-10-20T16:41:00Z"}
    late = dict(ok, completedAt="2026-10-20T16:43:00Z")
    label = {"name": "label", "workflowName": "Auto-label PRs", "conclusion": "SUCCESS",
             "completedAt": "2026-10-20T16:41:00Z"}
    ts = replay.pr_traces("o/r", [_pr(1, "main", checks=[ok]), _pr(2, "integrate/x", checks=[late, label]),
                                  _pr(3, "integrate/x", checks=[label]), _pr(4, "integrate/x", checks=[ok])],
                          "main", {}, None, fetch_merge_checks=False)
    by = {t.meta["number"]: t for t in ts}
    assert (1, "merged_without_owner_acceptance", "violation") in _codes(by[1])
    assert (3, "integrated_without_head_receipt", "violation") in _codes(by[2])  # tests finished after merge
    assert (3, "integrated_without_head_receipt", "violation") in _codes(by[3])  # label-only is no receipt
    # passing receipt, but merged by the author's (shared owner) account: not decidable
    assert (2, "self_integrated", "indeterminate") in _codes(by[4])


def _git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), *a], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def test_replay_reproduces_stale_service(tmp_path, monkeypatch):
    repo = tmp_path / "svc"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "a")
    _git(repo, "branch", "integrated")
    _git(repo, "checkout", "-q", "integrated")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "b")
    _git(repo, "checkout", "-q", "main")
    monkeypatch.chdir(repo)  # this process plays the running service
    cfg = [{"name": "web", "port": 1, "repo": "o/r", "integration_ref": "integrated"}]
    [t] = replay.service_traces(cfg, {1: os.getpid()}, {})
    assert t.final.phase is Phase.INTEGRATED
    f = [f for f in t.findings if f.code == "service_stale"][0]
    assert f.law == 4 and f.evidence["behind"] == 1 and f.evidence["ahead"] == 0
    assert any(f.code == "integration_ref_local_only" for f in t.findings)
    _git(repo, "merge", "-q", "--ff-only", "integrated")  # redeploy the integrated head
    [t2] = replay.service_traces(cfg, {1: os.getpid()}, {})
    assert t2.final.phase is Phase.DEPLOYED and not any(f.law == 4 for f in t2.findings)


def test_replay_flags_dropped_interrupted_worker(tmp_path):
    now = dt.datetime(2026, 10, 21, 3, 0, tzinfo=dt.timezone.utc)
    d = tmp_path / "deleg_a"
    d.mkdir()
    (d / "task-0.log").write_text("goal: build the thing\nstarted: 2026-10-20 10:00:00\n"
                                  "worktree add -b feat/thing\nend status=interrupted exit_reason=interrupted\n")
    e = tmp_path / "deleg_b"
    e.mkdir()
    (e / "task-0.log").write_text("goal: other\nstarted: 2026-10-20 10:00:00\nend status=interrupted\n")
    f = tmp_path / "deleg_c"
    f.mkdir()
    (f / "task-0.log").write_text("goal: RESUME other work\nstarted: 2026-10-20 11:00:00\n"
                                  "context: continue deleg_b's worktree\nend status=completed\n")
    old = (now - dt.timedelta(hours=5)).timestamp()
    for p in tmp_path.glob("*/task-0.log"):
        os.utime(p, (old, old))
    ts = {t.id: t for t in replay.worker_traces(tmp_path, {}, now, ttl_hours=2)}
    assert [x.code for x in ts["worker:deleg_a/task-0"].findings] == ["interrupted_dropped"]
    assert ts["worker:deleg_b/task-0"].final.phase is Phase.IMPLEMENTING  # resumed by deleg_c
    assert "worker:deleg_c/task-0" not in ts


# ---------------------------------------------------------------- export

def test_mermaid_and_overlay_cover_the_statechart():
    m = export.mermaid()
    for p in Phase:
        assert p.value in m
    ov = export.overlay({"items": [], "generated_at": "t"}, {"receipt_id": "r", "verdict": "pass"})
    assert ov["schema"] == "z0.fsm.work-item.overlay/v0"
    assert {s["id"] for s in ov["statechart"]["states"]} == {p.value for p in Phase}
    assert [l["id"] for l in ov["statechart"]["laws"]] == [1, 2, 3, 4, 5, 6, 7]
    assert ov["proof"]["verdict"] == "pass"
    json.dumps(ov)


def test_committed_diagram_is_current():
    mmd = Path(__file__).resolve().parents[1] / "docs" / "fsm" / "work-item.mmd"
    assert mmd.read_text() == export.mermaid(), "regenerate: python -m z0int.fsm.export --mermaid docs/fsm/work-item.mmd"
