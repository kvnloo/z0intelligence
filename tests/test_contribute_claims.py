"""CW5: claim protocol laws, the claim bot, and the C1 race on a fake GitHub (spec §2.4, §4.3 C1)."""
from __future__ import annotations

import datetime as _dt
import json
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from z0int.contribute import claims as C
from z0int.contribute import priorities as P
from z0int.contribute import state as St

from contrib_fake_github import FakeGitHub, REPO, ROOT  # noqa: E402

NOW = _dt.datetime(2026, 10, 26, 12, 0, 0, tzinfo=_dt.timezone.utc)
TRUSTED = C.trusted_authors(REPO)
BOT = C.BOT_LOGIN


@pytest.fixture(scope="module")
def tasks():
    return P.load_priorities(ROOT)["tasks"]


def _c(login, body):
    return {"author": {"login": login}, "body": body}


def _marker(task, login, hours=72, status="active", now=NOW):
    return C.format_marker(task, login, now + _dt.timedelta(hours=hours), status)


# ------------------------------------------------------------ parsing / markers

def test_parse_command():
    c = C.parse_command("/claim t-139-e2e-trace lease=72h harness=claude-code hw=cpu")
    assert (c.verb, c.task, c.lease_h, c.params["harness"]) == ("claim", "t-139-e2e-trace", 72, "claude-code")
    assert C.parse_command("/claim t-1 lease=500h").lease_h == 72          # capped
    assert C.parse_command("/claim renew").verb == "renew"
    assert C.parse_command("/claim release t-1").task == "t-1"
    assert C.parse_command("/claim rejected: @x claimed to capacity") is None  # bot reply never re-triggers
    assert C.parse_command("please /claim t-1") is None
    assert C.parse_command("/claim") is None


def test_claim_text_is_exact():
    assert C.claim_text("t-139-e2e-trace", harness="claude-code", hw="cpu") == \
        "/claim t-139-e2e-trace lease=72h harness=claude-code hw=cpu"


def test_markers_only_from_trusted_authors():
    forged = _c("mallory", f"I claim it {_marker('t-24-rlcd-alignbench', 'mallory')}")
    real = _c(BOT, f"Claim accepted {_marker('t-24-rlcd-alignbench', 'alice')}")
    act = C.active_leases([forged, real], TRUSTED, now=NOW, task="t-24-rlcd-alignbench")
    assert set(act) == {"alice"}


def test_expired_and_released_leases_are_inactive():
    cs = [_c(BOT, _marker("t-1", "a", hours=-1)), _c(BOT, _marker("t-1", "b")),
          _c(BOT, _marker("t-1", "b", hours=0, status="released"))]
    assert C.active_leases(cs, TRUSTED, now=NOW, task="t-1") == {}


# ------------------------------------------------------------ bot decisions

def _decide(tasks, body, login="alice", issue=24, labels=("claimable",), comments=(), recent=False):
    return C.decide(C.parse_command(body), login=login, issue=issue, labels=labels, comments=list(comments),
                    tasks=tasks, now=NOW, trusted=TRUSTED, renewal_has_recent_commit=recent)


def test_bot_accepts_a_valid_claim_with_label_and_one_marker_comment(tasks):
    d = _decide(tasks, "/claim t-24-rlcd-alignbench lease=72h")
    assert d.accept and d.add_label == "claimed"
    assert len(C.MARKER_RE.findall(d.comment)) == 1
    lease = json.loads(C.MARKER_RE.search(d.comment).group(1))
    assert lease == {"task": "t-24-rlcd-alignbench", "login": "alice", "expires": "2026-10-29T12:00:00Z",
                     "status": "active"}


@pytest.mark.parametrize("why,kw", [
    ("not claimable", dict(labels=())),
    ("blocked", dict(labels=("claimable", "blocked"))),
    ("wrong issue", dict(issue=139)),
    ("capacity", dict(comments=[_c(BOT, _marker("t-24-rlcd-alignbench", "bob"))])),
    ("already mine", dict(comments=[_c(BOT, _marker("t-24-rlcd-alignbench", "alice"))])),
])
def test_bot_rejects(tasks, why, kw):
    d = _decide(tasks, "/claim t-24-rlcd-alignbench", **kw)
    assert not d.accept and d.add_label is None
    assert d.comment.startswith("/claim rejected: @alice ")


def test_bot_rejects_non_newcomer_and_unknown_tasks(tasks):
    assert not _decide(tasks, "/claim t-234-tensor-unlock", issue=234).accept
    assert not _decide(tasks, "/claim t-made-up-task").accept
    assert not _decide(tasks, "/claim t-z0evals-memory-cohort", issue=24).accept


def test_parallel_task_accepts_up_to_max_n(tasks):
    t = "t-231-local-inference-profile"
    cs = [_c(BOT, _marker(t, f"u{i}")) for i in range(5)]
    assert _decide(tasks, f"/claim {t}", issue=231, comments=cs).accept          # 6th of parallel(6)
    cs.append(_c(BOT, _marker(t, "u5")))
    assert not _decide(tasks, f"/claim {t}", issue=231, comments=cs).accept      # 7th


def test_renewal_needs_a_recent_fork_commit(tasks):
    cs = [_c(BOT, _marker("t-24-rlcd-alignbench", "alice", hours=2))]
    assert not _decide(tasks, "/claim renew", comments=cs, recent=False).accept
    d = _decide(tasks, "/claim renew", comments=cs, recent=True)
    assert d.accept and "2026-10-29T12:00:00Z" in d.comment and d.add_label is None


def test_release_writes_a_released_marker(tasks):
    cs = [_c(BOT, _marker("t-24-rlcd-alignbench", "alice"))]
    d = _decide(tasks, "/claim release", comments=cs)
    assert d.accept and '"status":"released"' in d.comment
    assert C.active_leases(cs + [_c(BOT, d.comment)], TRUSTED, now=NOW, task="t-24-rlcd-alignbench") == {}


def test_bot_ignores_itself_pull_requests_and_chatter(tasks):
    bot = __import__("contrib_fake_github").load_claim_bot()
    calls = []
    gh = lambda a: calls.append(a) or "[]"  # noqa: E731
    for event in (
        {"comment": {"user": {"login": BOT}, "body": "/claim t-24-rlcd-alignbench"}, "issue": {"number": 24}},
        {"comment": {"user": {"login": "a"}, "body": "/claim t-24-rlcd-alignbench"},
         "issue": {"number": 24, "pull_request": {}}},
        {"comment": {"user": {"login": "a"}, "body": "nice issue"}, "issue": {"number": 24}},
    ):
        assert bot.handle(event, gh=gh, repo=REPO, tasks=tasks, now=NOW)["action"] == "ignored"
    assert calls == []


# ------------------------------------------------------------ C1: race on the fake GitHub

def test_c1_two_contributors_race_exactly_one_wins(tasks, tmp_path, monkeypatch):
    """Two contributors (separate hosts) claim the same exclusive task at once."""
    from z0int.contribute import cli as K
    from z0int.contribute.gh import GH

    gh = FakeGitHub(tasks, now=lambda: NOW)
    gh.add_issue(24)
    root = ROOT
    out: dict[str, dict] = {}
    barrier = threading.Barrier(2)
    homes = {u: tmp_path / u for u in ("alice", "bob")}

    def contributor(login):
        # each contributor is a separate host with its own private ~/.z0int
        env = K.Env(root=root, gh=GH(REPO, runner=gh.cli(login)), now=lambda: NOW, tty=False,
                    harness="claude-code", poll_seconds=0, sleep=lambda s: None,
                    profile_probe=lambda: {"schema": "z0.contribute.profile.v1", "tier": "T0",
                                           "hw": {"os": "linux", "arch": "x86_64", "ram_gib": 16,
                                                  "gpu": {"class": "none", "vram_gib": 0}},
                                           "toolchains": ["python"], "harnesses": []})
        barrier.wait()
        with St.use_home(homes[login]):
            St.write_json(St.home() / "suggestion.json", {"alternatives": [
                {"task": "t-24-rlcd-alignbench"}, {"task": "t-14-observer-eval-receipts"}]})
            out[login] = K.cmd_claim(env, "t-24-rlcd-alignbench", post=True, yes=True)
            out[login + ":lease"] = St.read_lease("t-24-rlcd-alignbench", NOW)

    threads = [threading.Thread(target=contributor, args=(u,)) for u in ("alice", "bob")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    claims = [c for c in gh.issues[24]["comments"] if c["body"].startswith("/claim t-24")]
    assert len(claims) == 2                                     # both really posted
    results = sorted(r["action"] for r in gh.bot_results)
    assert results == ["accepted", "rejected"]                  # exactly one winner
    winner = next(r["login"] for r in gh.bot_results if r["action"] == "accepted")
    loser = "bob" if winner == "alice" else "alice"
    assert out[winner]["data"]["claim"] == "won" and out[winner + ":lease"] is not None
    assert out[loser]["data"]["claim"] == "lost" and out[loser + ":lease"] is None
    assert out[loser]["next_action"]["kind"] == "ask_human"
    assert out[loser]["data"]["next_alternative"] == "t-14-observer-eval-receipts"
    assert "claimed" in gh.issues[24]["labels"]
    active = C.active_leases(gh.issues[24]["comments"], TRUSTED, now=NOW, task="t-24-rlcd-alignbench")
    assert list(active) == [winner]
    # the only origin writes are the two human-approved claim comments, the label and two bot comments
    bot_writes = [a for who, a in gh.writes if who == BOT]
    assert sorted(a[1] for a in bot_writes) == ["comment", "comment", "edit"]


# ------------------------------------------------------------ claim-bot.py wiring (subprocess, fake gh binary)

FAKE_GH = r"""#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
log = os.environ["FAKE_GH_LOG"]
with open(log, "a") as fh:
    fh.write(json.dumps(args) + "\n")
if args[:2] == ["issue", "view"]:
    print(json.dumps({"labels": [{"name": "claimable"}]}))
elif args and args[0] == "api" and args[1].endswith("/comments"):
    print(json.dumps([[{"user": {"login": "someone"}, "body": "hi"}]]))
elif args[:2] in (["issue", "edit"], ["issue", "comment"]):
    pass
else:
    sys.stderr.write("unexpected %r\n" % args); sys.exit(3)
"""


def test_claim_bot_script_end_to_end_with_fake_gh(tmp_path):
    gh = tmp_path / "bin" / "gh"
    gh.parent.mkdir()
    gh.write_text(FAKE_GH)
    gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"comment": {"user": {"login": "alice"}, "body": "/claim t-24-rlcd-alignbench"},
                                 "issue": {"number": 24}}))
    log = tmp_path / "gh.log"
    env = dict(os.environ, PATH=f"{gh.parent}{os.pathsep}{os.environ['PATH']}", GITHUB_EVENT_PATH=str(event),
               GITHUB_REPOSITORY=REPO, FAKE_GH_LOG=str(log), PYTHONPATH=str(ROOT / "src"))
    p = subprocess.run([sys.executable, str(ROOT / ".github" / "scripts" / "claim-bot.py")], env=env,
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    assert json.loads(p.stdout)["action"] == "accepted"
    calls = [json.loads(x) for x in log.read_text().splitlines()]
    writes = [c for c in calls if c[:2] in (["issue", "edit"], ["issue", "comment"])]
    assert [c[:2] for c in writes] == [["issue", "edit"], ["issue", "comment"]]
    assert writes[0][-2:] == ["--add-label", "claimed"]
    assert "<!-- z0-claim " in writes[1][-1]
