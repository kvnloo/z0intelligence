"""Ops plane (O1-O9): the gates' decision functions, their IO wrappers on scratch
repos, the 2026-10-21 incident tapes, and Bend parity (groot only)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from z0int.fsm import guard as G
from z0int.fsm import ops as P
from z0int.fsm import ops_parity, ops_replay

ROOT = Path(__file__).resolve().parents[1]
TAPES = sorted((ROOT / "tests/fixtures/ops_tapes").glob("*.json"))
needs_bend = pytest.mark.skipif(not Path(ops_parity.BEND).exists() and not shutil.which("bend"),
                                reason="bend not installed (Bend/Lean live on groot only)")


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "STATE", tmp_path / "state")
    monkeypatch.setattr(G, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(G, "CONFIG", tmp_path / "config")
    for g in G.DEFAULT_MODES:
        monkeypatch.delenv(f"Z0_GUARD_{g}", raising=False)
    monkeypatch.delenv("Z0_ROLLOUT_TOKEN", raising=False)


def git(d, *a, env=None, check=True):
    return subprocess.run(["git", "-C", str(d), *a], capture_output=True, text=True, check=check, env=env)


def repo(tmp_path, name="r"):
    d = tmp_path / name
    d.mkdir()
    git(d, "init", "-q", "-b", "main")
    git(d, "config", "user.email", "t@localhost")
    git(d, "config", "user.name", "t")
    (d / "a.txt").write_text("1\n")
    git(d, "add", "-A")
    git(d, "commit", "-qm", "one")
    return d


# ------------------------------------------------------------------ modes

def test_default_modes_enforce_only_pure_refusals():
    assert {g for g, m in G.DEFAULT_MODES.items() if m == "enforce"} == {"O1", "O2", "O4", "O6"}
    assert all(G.DEFAULT_MODES[g] == "report" for g in ("O3", "O5", "O7", "O8", "O9"))


def test_mode_env_and_file_override(monkeypatch, tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/modes.json").write_text(json.dumps({"O7": "enforce"}))
    assert G.mode("O7") == "enforce"
    monkeypatch.setenv("Z0_GUARD_O7", "off")
    assert G.mode("O7") == "off"


# ------------------------------------------------------------------ O1

def test_o1_decisions():
    for c in P.HEAVY_CLASSES:
        assert not P.admits(P.Lane.GROOT, c)
        assert P.place(c, True, True) is P.Lane.MBP
        assert P.place(c, False, True) is P.Lane.PHONE
        assert P.place(c, False, False) is None          # waits, never groot
    for c in P.GROOT_CLASSES:
        assert P.admits(P.Lane.GROOT, c) and P.place(c, False, False) is P.Lane.GROOT


@pytest.mark.parametrize("cmd,cls", [
    ("bun test app/src bridge/src shared", P.Cls.TEST),
    ("export PATH=x; bun run typecheck > log", P.Cls.TYPECHECK),
    ("bunx tsc --noEmit -p app", P.Cls.TYPECHECK),
    ("bunx expo export --platform android", P.Cls.BUNDLE),
    ("bun scripts/mutate.ts --threshold 80", P.Cls.MUTATION),
    ("./gradlew assembleDebug", P.Cls.GRADLE),
    ("python3 -m pytest -q", P.Cls.TEST),
    ("node shoot.mjs http://x out tag  # playwright", P.Cls.SHOT),
    ("bend PROOF.bend --verdict", P.Cls.BEND),
    ("fleet-build -- bun test", None),
    ("git status", None),
])
def test_o1_classifier(cmd, cls):
    assert P.classify_command(cmd) is cls


def test_o1_run_routes_heavy_to_fleet_build(tmp_path, monkeypatch):
    fake = tmp_path / "fleet-build"
    log = tmp_path / "argv"
    fake.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {log}\n")
    fake.chmod(0o755)
    env = {**os.environ, "Z0_GUARD_HOST": "groot", "Z0_GUARD_FLEET_BUILD": str(fake),
           "Z0_GUARD_STATE": str(tmp_path / "s"), "PYTHONPATH": str(ROOT / "src")}
    r = subprocess.run([sys.executable, "-m", "z0int.fsm.guard", "run", "typecheck", "--", "bunx", "tsc", "--noEmit"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert log.read_text().split() == ["--light", "--wait", "1800", "--", "bunx", "tsc", "--noEmit"]
    # a groot class runs locally
    r = subprocess.run([sys.executable, "-m", "z0int.fsm.guard", "run", "bend", "--", "echo", "local"],
                       env=env, capture_output=True, text=True)
    assert r.stdout.strip() == "local"
    # report mode runs locally but records the violation
    r = subprocess.run([sys.executable, "-m", "z0int.fsm.guard", "run", "test", "--", "echo", "here"],
                       env={**env, "Z0_GUARD_O1": "report"}, capture_output=True, text=True)
    assert "here" in r.stdout and "would-refuse" in r.stdout


def test_o1_tiny_overrun_is_refused(monkeypatch):
    monkeypatch.setattr(P, "TINY_SECONDS", 1)
    monkeypatch.setenv("Z0_GUARD_HOST", "groot")
    code = G.cmd_run(Namespace(cls="tiny", cmd=["--", "sleep", "3"], wait=0, fleet_arg=[]))
    assert code == 124


def test_o1_unclassifiable_auto_is_refused():
    assert G.cmd_run(Namespace(cls="auto", cmd=["--", "echo", "x"], wait=0, fleet_arg=[])) == 2


# ------------------------------------------------------------------ O2

@pytest.fixture
def served(tmp_path, monkeypatch):
    main = repo(tmp_path, "main")
    wt = tmp_path / "served"
    git(main, "worktree", "add", "-q", "-b", "integrate", str(wt))
    cand = tmp_path / "cand"
    git(main, "worktree", "add", "-q", "-b", "cand", str(cand))
    (cand / "a.txt").write_text("2\n")
    git(cand, "commit", "-qam", "two")
    binp = tmp_path / "z0-guard"
    binp.write_text(f"#!/bin/sh\nPYTHONPATH={ROOT / 'src'} Z0_GUARD_STATE={tmp_path / 'state'} exec {sys.executable} "
                    f"-m z0int.fsm.guard \"$@\"\n")
    binp.chmod(0o755)
    monkeypatch.setenv("Z0_GUARD_BIN", str(binp))
    G.cmd_served(Namespace(action="mark", worktree=str(wt), unit="scratch-metro.service", port=1, health=None,
                           deeplink=None, serial=None, scratch=True))
    G.cmd_served(Namespace(action="install-hooks", worktree=str(wt), unit=None, port=None, health=None,
                           deeplink=None, serial=None, scratch=True))
    return main, wt, cand


def test_o2_decision():
    for op in P.GitOp:
        assert P.wt_allows(False, op, False)
        assert P.wt_allows(True, op, True) == (op in (P.GitOp.MERGE_FF, P.GitOp.ROLLBACK))
        assert not P.wt_allows(True, op, False)


def test_o2_hooks_refuse_edits_in_served_worktree(served, tmp_path):
    main, wt, cand = served
    (wt / "a.txt").write_text("edit\n")
    assert git(wt, "commit", "-qam", "in place", check=False).returncode != 0          # commit
    git(wt, "checkout", "--", "a.txt")
    assert git(wt, "checkout", "-q", "-b", "other", check=False).returncode != 0      # checkout
    (wt / "a.txt").write_text("edit\n")
    assert git(wt, "stash", check=False).returncode != 0                              # stash
    git(wt, "checkout", "--", "a.txt")
    assert git(wt, "merge", "--ff-only", "cand", check=False).returncode != 0         # ff outside txn
    assert git(wt, "rev-parse", "HEAD").stdout == git(main, "rev-parse", "main").stdout
    # the unserved candidate worktree is unaffected
    (cand / "b.txt").write_text("x")
    git(cand, "add", "b.txt")
    assert git(cand, "commit", "-qm", "free").returncode == 0
    # the reversible switch
    env = {**os.environ, "Z0_GUARD_O2": "off"}
    assert git(wt, "merge", "--ff-only", "cand", env=env).returncode == 0


def test_o2_rollout_token_allows_ff_and_its_rollback_only(served):
    main, wt, cand = served
    gd = G.git_dir(str(wt))
    prev = git(wt, "rev-parse", "HEAD").stdout.strip()
    tok = "t0ken"
    (gd / "z0-rollout.lock").write_text(json.dumps({"token_sha256": __import__("hashlib").sha256(tok.encode()).hexdigest(),
                                                    "prev": prev}))
    env = {**os.environ, "Z0_ROLLOUT_TOKEN": tok}
    assert git(wt, "merge", "--ff-only", "cand", env=env).returncode == 0
    (wt / "a.txt").write_text("edit\n")
    assert git(wt, "commit", "-qam", "x", env=env, check=False).returncode != 0      # still no commits
    git(wt, "checkout", "--", "a.txt")
    assert git(wt, "reset", "-q", "--keep", prev, env=env).returncode == 0            # rollback to prev
    bad = {**os.environ, "Z0_ROLLOUT_TOKEN": "wrong"}
    assert git(wt, "merge", "--ff-only", "cand", env=bad, check=False).returncode != 0


def test_o2_uninstall_restores(served):
    main, wt, cand = served
    G.cmd_served(Namespace(action="uninstall-hooks", worktree=str(wt), unit=None, port=None, health=None,
                           deeplink=None, serial=None, scratch=True))
    (wt / "a.txt").write_text("edit\n")
    assert git(wt, "commit", "-qam", "allowed now").returncode == 0


# ------------------------------------------------------------------ O3

def test_o3_admission_table():
    ok = P.Req(clean=True, ff=True, gated=False, receipt=False, call=False, idle=True)
    assert P.admission(ok) is None
    assert P.rollout(ok, True) is P.Out.PROMOTED and P.rollout(ok, False) is P.Out.ROLLED_BACK
    assert P.admission(P.Req(True, True, True, False, False, True)) is P.Out.REFUSED     # gated, no receipt
    assert P.admission(P.Req(False, True, False, False, False, True)) is P.Out.REFUSED   # served build
    assert P.admission(P.Req(True, False, False, False, False, True)) is P.Out.REFUSED   # not ff
    assert P.admission(P.Req(True, True, False, False, True, True)) is P.Out.DEFERRED    # owner call
    assert P.admission(P.Req(True, True, False, False, False, False)) is P.Out.DEFERRED  # busy


def _rollout(wt, cand, **kw):
    return G.cmd_rollout(Namespace(worktree=str(wt), ref="cand", candidate=str(cand), gated=kw.get("gated", False),
                                   review_receipt=kw.get("receipt"), check="web", dry_run=False))


@pytest.fixture
def fake_service(monkeypatch):
    calls = {"restarts": 0}
    monkeypatch.setattr(G, "restart_unit", lambda u: calls.__setitem__("restarts", calls["restarts"] + 1) or True)
    monkeypatch.setattr(G, "wait_http", lambda url, t: (True, "ok"))
    monkeypatch.setattr(G, "service_idle", lambda port: True)
    monkeypatch.setattr(G, "owner_call_live", lambda: False)
    return calls


def test_o3_rollout_promotes_after_passing_check(served, fake_service, monkeypatch):
    main, wt, cand = served
    monkeypatch.setattr(G, "device_launch_check", lambda *a: (True, {"mode": "test"}))
    assert _rollout(wt, cand) == 0
    assert git(wt, "rev-parse", "HEAD").stdout == git(cand, "rev-parse", "HEAD").stdout
    assert fake_service["restarts"] == 1
    assert not (G.git_dir(str(wt)) / "z0-rollout.lock").exists()


def test_o3_rollout_rolls_back_failed_check(served, fake_service, monkeypatch):
    main, wt, cand = served
    prev = git(wt, "rev-parse", "HEAD").stdout
    monkeypatch.setattr(G, "device_launch_check", lambda *a: (False, {"logcat_errors": ["FATAL EXCEPTION"]}))
    assert _rollout(wt, cand) == 1
    assert git(wt, "rev-parse", "HEAD").stdout == prev
    assert fake_service["restarts"] == 2


def test_o3_rollout_defers_during_owner_call(served, fake_service, monkeypatch):
    main, wt, cand = served
    prev = git(wt, "rev-parse", "HEAD").stdout
    monkeypatch.setattr(G, "owner_call_live", lambda: True)
    G.cmd_served(Namespace(action="mark", worktree=str(wt), unit="u", port=1, health=None, deeplink="exp://x",
                           serial="S", scratch=False))
    monkeypatch.setattr(G, "device_launch_check", lambda *a: pytest.fail("device check ran during a call"))
    assert G.cmd_rollout(Namespace(worktree=str(wt), ref="cand", candidate=str(cand), gated=False,
                                   review_receipt=None, check="adb", dry_run=False)) == 75
    assert git(wt, "rev-parse", "HEAD").stdout == prev and fake_service["restarts"] == 0
    assert "deferred" in (G.STATE / "pending-rollouts.jsonl").read_text()


def test_o3_rollout_refuses_served_build_and_missing_review(served, fake_service, tmp_path):
    main, wt, cand = served
    assert G.cmd_rollout(Namespace(worktree=str(wt), ref="cand", candidate=str(wt), gated=False,
                                   review_receipt=None, check="web", dry_run=False)) == G.EXIT_REFUSED
    assert _rollout(wt, cand, gated=True) == G.EXIT_REFUSED
    sha = git(cand, "rev-parse", "HEAD").stdout.strip()
    rc = tmp_path / "review.json"
    rc.write_text(json.dumps({"sha": sha, "verdict": "approve", "reviewer": "orchestrator"}))
    assert G.review_receipt_ok(str(rc), sha) and not G.review_receipt_ok(str(rc), "0" * 40)


def test_o3_web_check_refused_for_owner_device():
    ok, ev = G.device_launch_check({"unit": "u", "port": 1, "owner_device": True}, 0, Path("/tmp"), "web")
    assert not ok and "refused" in ev["error"]


# ------------------------------------------------------------------ O4

@pytest.fixture
def vis(monkeypatch):
    table = {"kvnloo/taste-skill": P.Vis.PUBLIC, "kvnloo/taste-data": P.Vis.PRIVATE}
    monkeypatch.setattr(G, "gh_visibility", lambda slug, ttl=0: table.get(slug, P.Vis.UNKNOWN))


def _push(d, remote, url):
    sha = git(d, "rev-parse", "HEAD").stdout.strip()
    return G.prepush(remote, url, f"refs/heads/main {sha} refs/heads/main {G.ZERO}\n", str(d))


def test_o4_decision():
    assert not P.push_allows(True, P.Vis.PUBLIC) and not P.push_allows(True, P.Vis.UNKNOWN)
    assert P.push_allows(True, P.Vis.PRIVATE) and all(P.push_allows(False, v) for v in P.Vis)


def test_o4_marked_dir_and_attr_and_glob(tmp_path, vis, monkeypatch):
    d = repo(tmp_path)
    git(d, "remote", "add", "origin", "https://github.com/kvnloo/taste-skill.git")
    assert _push(d, "origin", "https://github.com/kvnloo/taste-skill.git") == 0          # ordinary
    (d / "corpus").mkdir()
    (d / "corpus/.z0-private").write_text("")
    (d / "corpus/p.json").write_text("{}")
    git(d, "add", "-A"); git(d, "commit", "-qm", "private")
    assert _push(d, "origin", "https://github.com/kvnloo/taste-skill.git") == G.EXIT_REFUSED
    monkeypatch.setenv("Z0_GUARD_O4", "off")
    assert _push(d, "origin", "https://github.com/kvnloo/taste-skill.git") == 0          # switch
    monkeypatch.delenv("Z0_GUARD_O4")
    e = repo(tmp_path, "e")
    (e / ".gitattributes").write_text("secret.txt z0-private\n")
    (e / "secret.txt").write_text("s")
    git(e, "add", "-A"); git(e, "commit", "-qm", "attr")
    assert _push(e, "public", "https://github.com/kvnloo/taste-skill.git") == G.EXIT_REFUSED
    assert _push(e, "unknown", "https://example.com/x.git") == G.EXIT_REFUSED            # unknown fails closed
    assert _push(e, "local", str(tmp_path / "bare.git")) == 0                           # a local path is private
    f = repo(tmp_path, "f")
    (f / "taste-data").mkdir(); (f / "taste-data/x.json").write_text("{}")
    git(f, "add", "-A"); git(f, "commit", "-qm", "glob")
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config/private-paths").write_text("taste-data/*\n")
    assert _push(f, "public", "https://github.com/kvnloo/taste-skill.git") == G.EXIT_REFUSED


def test_o4_private_origin_to_public_remote(tmp_path, vis):
    d = repo(tmp_path)
    git(d, "remote", "add", "origin", "https://github.com/kvnloo/taste-data.git")
    assert _push(d, "public", "https://github.com/kvnloo/taste-skill.git") == G.EXIT_REFUSED
    assert _push(d, "origin", "https://github.com/kvnloo/taste-data.git") == 0


# ------------------------------------------------------------------ O5

@pytest.mark.parametrize("utterance,intent", [
    ("Wait, what do you mean you can't open it? You don't need to, like, you're trying to change something on my phone? You need to use the backend", False),
    ("No, no, like, just check directly through the bridge. Not on my phone", False),
    ("did my message go through?", False),
    ("open Spotify and tap play", True),
    ("go to settings and turn on bluetooth", True),
])
def test_o5_intent(utterance, intent):
    assert P.owner_phone_intent(utterance) is intent


def test_o5_decisions():
    d = G.phone_decision("tap", utterance="open Spotify", expected_app="a", foreground_app="b", call=False)
    assert not d["allowed"] and d["reasons"] == ["foreground_mismatch"]
    assert G.phone_decision("tap", utterance="open Spotify", expected_app="a", foreground_app="a", call=True)["allowed"]
    assert not G.phone_decision("install", package="app.lawnchair.play.debug", call=True)["allowed"]
    assert G.phone_decision("install", package="app.lawnchair.play.debug", call=False)["allowed"]
    assert G.phone_decision("restart", package="com.other", call=True)["allowed"]


def test_o5_unknown_call_state_fails_closed(monkeypatch):
    monkeypatch.setattr(G, "owner_call_live", lambda: None)
    assert not G.phone_decision("install", package="app.lawnchair.play.debug")["allowed"]


def test_o5_call_live_reads_broker(tmp_path, monkeypatch):
    import sqlite3
    import time
    db = tmp_path / "broker.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE sessions(id TEXT, updated REAL, ended INTEGER)")
        c.execute("INSERT INTO sessions VALUES ('a', ?, 0)", (time.time(),))
    monkeypatch.setattr(G, "BROKER_DB", db)
    assert G.owner_call_live() is True
    with sqlite3.connect(db) as c:
        c.execute("UPDATE sessions SET ended=1")
    assert G.owner_call_live() is False


# ------------------------------------------------------------------ O6

def test_o6():
    pre = "a/A:b/B:c/C"
    assert P.a11y_ok(P.parse_a11y(pre), P.parse_a11y("c/C:b/B:a/A:d/D"))
    assert not P.a11y_ok(P.parse_a11y(pre), P.parse_a11y("a/A"))
    assert P.a11y_ok(P.parse_a11y(pre), P.parse_a11y("a/A:b/B"), P.parse_a11y("c/C"))
    assert P.parse_a11y("null") == [] and P.parse_a11y("'x/Y'") == ["x/Y"]
    assert G.cmd_a11y(Namespace(pre=pre, post="a/A", removed="")) == 5
    assert G.cmd_a11y(Namespace(pre=pre, post=pre, removed="")) == 0


# ------------------------------------------------------------------ O7

def test_o7_flags(tmp_path, capsys):
    assert P.provider_flags("xai-oauth", "grok-4.6") == (True, False)
    assert P.provider_flags(None, "openai-codex/gpt-6.1-sol") == (False, True)
    assert P.provider_flags(None, "anthropic/claude-opus-5-5") == (False, False)
    assert P.design_role("visual design") and P.design_role("ux") and not P.design_role("task")
    omp = tmp_path / "omp.yml"
    omp.write_text("modelRoles:\n  task: xai-oauth/grok-4.6:auto\n  design: openai-codex/gpt-6.1-sol\n"
                   "  default: openai-codex/gpt-6.1-sol\nlive:\n  provider: grok\n  api_key: SUPERSECRETVALUE\n")
    hz = tmp_path / "h.yaml"
    hz.write_text("model:\n  provider: xai-oauth\n  default: grok-4.6\nproviders:\n  xai:\n    api_key: SUPERSECRETVALUE\n")
    f = G.routing_findings(omp, [hz], tmp_path / "none.yml")
    wheres = sorted(x["where"] for x in f)
    assert wheres == ["live.provider", "model.provider", "modelRoles.design", "modelRoles.task"]
    code = G.cmd_routing(Namespace(omp=str(omp), dash=str(tmp_path / "none.yml"), hermes=[str(hz)]))
    out = capsys.readouterr().out
    assert code == 0 and "would-refuse" in out and "SUPERSECRETVALUE" not in out


# ------------------------------------------------------------------ O8

def test_o8_notifies_exactly_once_per_episode():
    sent = []
    st, lim = {}, 1800
    seq = [(True, 0), (False, 100), (False, 1000), (False, 1901), (False, 2500), (False, 9000),
           (True, 9100), (False, 9200), (False, 11001)]
    for ok, t in seq:
        st, ev = G.health_step(st, "s22", ok, t, lim, deliver=lambda txt: sent.append(txt) or {"delivered": True},
                               gate_mode="enforce")
    assert len(sent) == 2                                   # one per unhealthy episode
    st, ev = G.health_step({}, "n", False, 0, 10, deliver=lambda t: pytest.fail("report mode delivered"),
                           gate_mode="report")
    st, ev = G.health_step(st, "n", False, 11, 10, deliver=lambda t: pytest.fail("report mode delivered"),
                           gate_mode="report")
    assert ev and ev["delivery"]["via"] == "report-only"


# ------------------------------------------------------------------ O9

def test_o9_restart_marks_interrupted_never_failed(tmp_path):
    d1, d2 = tmp_path / "20261021-a", tmp_path / "20261021-b"
    d1.mkdir(); d2.mkdir()
    (d2 / "REPORT.md").write_text("done")
    prev = {"gateway_invocation": "g1", "hermes_head": "h1",
            "units": {"sol-a.service": ["hermes", "chat", "--query-file", str(d1 / "PROMPT.txt")],
                      "sol-b.service": ["hermes", "chat", "--query-file", str(d2 / "PROMPT.txt")],
                      "sol-c.service": ["hermes", "chat", "--query-file", str(tmp_path / "c/PROMPT.txt")]}}
    ident = {"gateway_invocation": "g1", "hermes_head": "h2"}
    recs = G.interrupted_records(prev, {"sol-c.service": []}, ident, "t")
    assert [r["worker"] for r in recs] == ["sol-run/a"]
    assert recs[0]["action"] == "interrupted" and "sol-a-resume" in recs[0]["resume"]
    assert G.interrupted_records(prev, {}, {"gateway_invocation": "g1", "hermes_head": "h1"}, "t") == []
    assert P.rstep(P.RSt("active", 1), "gw-restart", 1).kind == "interrupted"
    assert P.rstep(P.RSt("interrupted", 1), "error").kind == "interrupted"


# ------------------------------------------------------------------ incident tapes

@pytest.mark.parametrize("tape", TAPES, ids=[t.name for t in TAPES])
def test_incident_tape_is_refused_or_flagged(tape, vis):
    r = ops_replay.replay(str(tape))
    assert r["summary"]["ok"], r["summary"]


def test_all_nine_laws_have_a_tape():
    assert {json.loads(t.read_text())["law"] for t in TAPES} == {f"O{i}" for i in range(1, 10)}


# ------------------------------------------------------------------ Bend parity (groot)

@needs_bend
def test_parity_with_bend_book():
    r = ops_parity.run_parity(seed=1, n_rand=60)
    assert r["equal"], r


@needs_bend
def test_parity_catches_a_sabotaged_gate(monkeypatch):
    monkeypatch.setattr(P, "push_allows", lambda private, vis: True)    # O4 broken
    cs = [c for c in ops_parity.cases(0, 5) if c[0] == "O4 push_allows"]
    assert not ops_parity.run_parity(cs=cs)["equal"]
