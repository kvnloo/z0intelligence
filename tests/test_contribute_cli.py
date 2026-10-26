"""CW4: `z0int contribute` S0-S7 on a throwaway checkout (W1 loop with a scripted human yes).

No network: GitHub is the in-process fake, subprocess steps (doctor, onboard,
pytest subset, verifier) use a fake runner, and origin/fork are local bare repos.
"""
from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from z0int.contribute import cli as K
from z0int.contribute import priorities as P
from z0int.contribute import schemas as S
from z0int.contribute import smoke
from z0int.contribute import state as St
from z0int.contribute.gh import GH

from contrib_fake_github import REPO, ROOT, FakeGitHub

NOW = _dt.datetime(2026, 10, 26, 12, 0, 0, tzinfo=_dt.timezone.utc)
TASK = "t-24-rlcd-alignbench"
GIT_ID = ["-c", "user.name=Test", "-c", "user.email=test@invalid", "-c", "commit.gpgsign=false"]


def git(cwd, *args):
    out = subprocess.run(["git", *GIT_ID, *args], cwd=cwd, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.fixture(scope="module")
def eval_output():
    env = smoke.offline_env(ROOT)
    p = subprocess.run([sys.executable, *smoke.EVAL_ARGV], cwd=ROOT, env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout


@pytest.fixture
def world(tmp_path, monkeypatch, eval_output):
    monkeypatch.setenv("Z0INT_HOME", str(tmp_path / "home" / ".z0int"))
    monkeypatch.delenv("Z0_HOME", raising=False)
    work = tmp_path / "work"
    for rel in (".z0", "schemas"):
        shutil.copytree(ROOT / rel, work / rel)
    (work / "tests" / "fixtures").mkdir(parents=True)
    shutil.copy(ROOT / smoke.GOLDEN, work / smoke.GOLDEN)
    doc = P.load_priorities(ROOT)
    for t in doc["tasks"]:
        for field in ("verifier", "smoke"):
            for rel in P.verifier_paths(t.get(field)):
                p = work / rel
                if "." in p.name:
                    p.parent.mkdir(parents=True, exist_ok=True)
                    p.write_text("# placeholder\n")
                else:
                    p.mkdir(parents=True, exist_ok=True)
                    (p / ".keep").write_text("")
    (work / "README.md").write_text("fixture\n")
    git(tmp_path, "init", "-q", "-b", "master", str(work))
    git(work, "add", "-A")
    git(work, "commit", "-q", "-m", "fixture")
    origin = tmp_path / "origin.git"
    fork = tmp_path / "fork.git"
    git(tmp_path, "init", "-q", "--bare", str(origin))
    git(tmp_path, "init", "-q", "--bare", str(fork))
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "-q", "origin", "master")
    git(work, "fetch", "-q", "origin")
    git(work, "remote", "set-head", "origin", "master")

    calls: list[list[str]] = []

    def runner(argv, cwd, env):
        calls.append(list(argv))
        joined = " ".join(argv)
        if "contrastive" in joined:
            return 0, eval_output
        if "doctor" in joined:
            return 0, '{"ok": true}'
        if "onboard" in joined:
            return 0, '{"ok": true}'
        if "pytest" in joined and "contrib_smoke" in joined:
            return 0, "40 passed"
        if argv[:2] == ["bash", "-c"]:
            return 0, "verifier ok"
        raise AssertionError(f"unexpected subprocess {argv}")

    fake = FakeGitHub(doc["tasks"], now=lambda: NOW)
    fake.add_issue(24)
    env = K.Env(root=work, gh=GH(REPO, runner=fake.cli("alice")), runner=runner, now=lambda: NOW, tty=False,
                harness="claude-code", poll_seconds=0, sleep=lambda s: None,
                profile_probe=lambda: {"schema": "z0.contribute.profile.v1", "tier": "T0",
                                       "hw": {"os": "linux", "arch": "x86_64", "ram_gib": 16,
                                              "gpu": {"class": "none", "vram_gib": 0}},
                                       "toolchains": ["python", "node"], "harnesses": ["claude"]})
    return {"env": env, "work": work, "origin": origin, "fork": fork, "fake": fake, "calls": calls,
            "tmp": tmp_path}


def _ok(out):
    assert S.validate(out, "contribute.state") == [], out
    return out


def test_w1_full_loop_with_scripted_yes(world):
    env, fake, work = world["env"], world["fake"], world["work"]

    # S0..S3 in one call; stops at the first human gate
    out = _ok(K.cmd_run(env))
    assert out["state"] == "S3_suggested" and out["next_action"]["kind"] == "ask_human"
    assert out["onboard_mode"] == "repo_only" and K.FULL_PATH_HINT in out["warnings"]
    sug = out["data"]["suggestion"]
    assert len(sug["alternatives"]) == 2 and sug["engine"]["llm"] == "none"
    assert fake.writes == []                                   # A5: nothing posted
    again = _ok(K.cmd_run(env))                                # idempotent at the gate
    assert again["state"] == "S3_suggested" and again["next_action"]["kind"] == "ask_human"

    # S4: local lease; the public claim is only shown, not posted
    out = _ok(K.cmd_claim(env, TASK))
    assert out["state"] == "S4_claimed" and out["next_action"]["kind"] == "ask_human"
    assert out["needs_human"] == [f"post claim comment on #24 (public post): /claim {TASK} lease=72h harness=claude-code hw=cpu"]
    assert fake.writes == []

    # S5: the default run makes the worktree from origin/master (C0)
    out = _ok(K.cmd_run(env))
    assert out["state"] == "S5_working"
    wt = Path(out["data"]["worktree"])
    assert out["data"]["base"] == "master" and out["data"]["branch"] == f"contrib/{TASK}-anon"
    assert git(wt, "rev-parse", "HEAD") == git(work, "rev-parse", "origin/master")
    # run from inside the task worktree, the CLI still binds S0-S3 to the checkout it came from
    assert K.find_repo_root(wt) == work.resolve() and K.find_repo_root(work) == work.resolve()

    # evidence refuses a dirty tree, then binds to the exact HEAD
    (wt / "README.md").write_text("changed\n")
    assert _ok(K.cmd_evidence(env, TASK))["next_action"]["kind"] == "blocked"
    git(wt, "commit", "-q", "-am", "contrib change")
    out = _ok(K.cmd_evidence(env, TASK))
    assert out["state"] == "S6_evidenced"
    head = git(wt, "rev-parse", "HEAD")

    # S7 needs the public claim for an exclusive task
    out = _ok(K.cmd_pr(env, TASK))
    assert out["next_action"]["kind"] == "ask_human" and "claim" in out["next_action"]["why"]
    out = _ok(K.cmd_claim(env, TASK, post=True, yes=True))
    assert out["data"]["claim"] == "won"
    assert [a[:2] for who, a in fake.writes if who == "alice"] == [["issue", "comment"]]

    out = _ok(K.cmd_pr(env, TASK))
    assert out["state"] == "S7_pr_ready" and out["next_action"]["kind"] == "ask_human"
    body = Path(out["data"]["body_path"]).read_text()
    assert "Credit: @alice" in body and "Refs #24" in body and f"head_revision: {head}" in body
    for leak in ("toolchains", "ram_gib", "instruction_files", "harnesses", "languages", "@invalid"):
        assert leak not in body
    spec = importlib.util.spec_from_file_location("check_receipt", ROOT / ".github" / "scripts" / "check-receipt.py")
    cr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cr)
    assert cr.check(body, head) == []                          # receipt.yml CI would pass

    # --open without a yes asks; nothing is pushed
    out = _ok(K.cmd_pr(env, TASK, open_=True))
    assert out["next_action"]["kind"] == "ask_human"
    # a push target that is origin is refused
    git(wt, "remote", "add", "upstreamish", "https://github.com/kvnloo/z0intelligence.git")
    out = _ok(K.cmd_pr(env, TASK, open_=True, yes=True, remote="upstreamish"))
    assert out["next_action"]["kind"] == "blocked" and "never pushes to origin" in out["next_action"]["why"]

    # the human's yes: push to the fork, open a draft PR against master
    git(wt, "remote", "add", "fork", str(world["fork"]))
    out = _ok(K.cmd_pr(env, TASK, open_=True, yes=True))
    assert out["next_action"]["kind"] == "done"
    pr = [a for who, a in fake.writes if who == "alice" and a[:2] == ["pr", "create"]]
    assert len(pr) == 1 and "--draft" in pr[0]
    assert pr[0][pr[0].index("--base") + 1] == "master"
    assert pr[0][pr[0].index("--head") + 1] == f"alice:contrib/{TASK}-anon"
    assert git(world["fork"], "rev-parse", f"refs/heads/contrib/{TASK}-anon") == head
    origin_heads = git(world["origin"], "for-each-ref", "--format=%(refname)", "refs/heads")
    assert origin_heads.split() == ["refs/heads/master"]       # never pushed to origin


def test_new_commit_in_worktree_drops_back_to_s5(world):
    env = world["env"]
    K.cmd_run(env)
    K.cmd_claim(env, TASK)
    wt = Path(K.cmd_run(env)["data"]["worktree"])
    git(wt, "commit", "-q", "--allow-empty", "-m", "a")
    assert K.cmd_evidence(env, TASK)["state"] == "S6_evidenced"
    git(wt, "commit", "-q", "--allow-empty", "-m", "b")
    assert K.cmd_status(env)["state"] == "S5_working"


def test_checkout_sha_change_invalidates_receipts(world):
    env, work = world["env"], world["work"]
    assert K.cmd_run(env)["state"] == "S3_suggested"
    git(work, "commit", "-q", "--allow-empty", "-m", "pull")
    assert K.cmd_status(env)["state"] == "none"
    n = len(world["calls"])
    assert K.cmd_run(env)["state"] == "S3_suggested"          # re-runs S0-S2 on the new sha
    assert len(world["calls"]) > n


def test_claim_refuses_invented_or_excluded_tasks(world):
    env = world["env"]
    for task in ("t-made-up", "t-234-tensor-unlock", "t-94-governed-canary"):
        out = _ok(K.cmd_claim(env, task))
        assert out["next_action"]["kind"] == "blocked"
    assert St.active_leases(NOW) == []


def test_claim_post_respects_github_post_never(world):
    env = world["env"]
    St.load_prefs()
    (St.home() / "prefs.yaml").write_text("github_post: never\n")
    out = _ok(K.cmd_claim(env, TASK, post=True, yes=True))
    assert out["next_action"]["kind"] == "ask_human" and world["fake"].writes == []


def test_smoke_failure_blocks(world):
    env = world["env"]
    orig = env.runner
    env.runner = lambda argv, cwd, e: (0, '{"different": 1}') if "contrastive" in " ".join(argv) else orig(argv, cwd, e)
    out = _ok(K.cmd_run(env))
    assert out["next_action"]["kind"] == "blocked" and "eval_fixture" in out["next_action"]["why"]


def test_feedback_task_check(world):
    env = world["env"]
    out = _ok(K.cmd_feedback(env))
    path = Path(out["data"]["path"])
    assert out["next_action"]["kind"] == "ask_human"
    assert _ok(K.cmd_feedback(env, check=str(path)))["next_action"]["kind"] == "blocked"   # empty template
    doc = json.loads(path.read_text())
    doc["steps"] = [{"step": "bootstrap", "minutes": 4, "friction": 1, "note": "venv took a while"}]
    path.write_text(json.dumps(doc))
    assert _ok(K.cmd_feedback(env, check=str(path)))["next_action"]["kind"] == "done"


def test_main_json_shape_for_every_subcommand(world, capsys):
    env = world["env"]
    for argv in (["--json"], ["status", "--json"], ["suggest", "--json"], ["claim", TASK, "--json"],
                 ["start", TASK, "--json", "--no-baseline"], ["release", TASK, "--json"], ["feedback", "--json"]):
        K.main(argv, env=env)
        out = json.loads(capsys.readouterr().out)
        assert S.validate(out, "contribute.state") == [], argv


def test_llm_flag_never_accepts_grok_or_cursor():
    for bad in ("grok", "cursor"):
        with pytest.raises(SystemExit):
            K.build_parser().parse_args(["--llm", bad])
