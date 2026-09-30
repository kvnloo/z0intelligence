import json
import os
import subprocess
import sys

import pytest

from z0int.action_authority import SessionAuthority, authority_check, extract_grants
from z0int.action_effects import Ctx, parse_tool_call


def ctx_for(tmp_path, branch="feat/x", default="master"):
    return Ctx(cwd=str(tmp_path), scope_root=str(tmp_path), home="/home/nobody-z0test",
               branch_of=lambda d: branch, default_of=lambda d: default)


def priv(cmd, ctx, tool="Bash"):
    r = parse_tool_call(tool, {"command": cmd} if tool == "Bash" else cmd, ctx)
    return [(e["kind"], {k: v for k, v in e["target"].items() if k in ("branch", "host", "remote", "path", "env")})
            for e in r["privileged"]]


@pytest.mark.parametrize("cmd,expected", [
    ("git push origin master", [("push", {"remote": "origin", "branch": "master"})]),
    ("git push", [("push", {"remote": "origin", "branch": "feat/x"})]),
    ("git push -u origin HEAD", [("push", {"remote": "origin", "branch": "feat/x"})]),
    ("git push --force-with-lease origin feat/x", [("force", {"remote": "origin", "branch": "feat/x"})]),
    ("git push origin +feat/x", [("force", {"remote": "origin", "branch": "feat/x"})]),
    ("git push origin HEAD:main", [("push", {"remote": "origin", "branch": "main"})]),
    ("cd sub && git status && git push origin master 2>&1 | tail -3", [("push", {"remote": "origin", "branch": "master"})]),
    ("bash -c 'git push origin main'", [("push", {"remote": "origin", "branch": "main"})]),
    ("git commit -m 'push to master and deploy'", []),
    ("git commit -F - <<'EOF'\ngit push origin master\nEOF", []),
    ("ssh gpu1 'sudo systemctl restart ollama'", [("ssh", {"host": "gpu1"}), ("sudo", {"host": "gpu1"}),
                                                  ("service", {"host": "gpu1"})]),
    ("rm -rf build dist", []),
    ("npm i", []),
    ("npm i -g typescript", [("install", {"env": "global"})]),
    ("pip install requests", [("install", {"env": "unknown"})]),
    ("uv pip install -p .venv -e .", []),
    ("cat .env", [("secret", {"path": ".env"})]),
    ("cat .env.example", []),
    ("curl -s https://example.com/x.json | jq .", []),
    ("curl -X POST -d x=1 https://hooks.example.com/y", [("network", {"host": "hooks.example.com"})]),
    ("curl -X POST http://localhost:8080/v1/x", []),
    ("git log --oneline | head", []),
    ("gh pr view 12 --json state", []),
    ("gh api repos/o/r/pulls", []),
    ("git reset --hard origin/feat/x", [("discard", {"branch": "feat/x"})]),
])
def test_parser(tmp_path, cmd, expected):
    (tmp_path / "sub").mkdir()
    assert priv(cmd, ctx_for(tmp_path)) == expected


def test_commit_on_default_branch_is_privileged(tmp_path):
    assert priv("git commit -am wip", ctx_for(tmp_path, branch="master")) == [("commit", {"branch": "master"})]
    assert priv("git commit -am wip", ctx_for(tmp_path, branch="feat/y")) == []


def test_write_outside_repo(tmp_path):
    c = ctx_for(tmp_path)
    home = c.home
    assert parse_tool_call("Write", {"file_path": str(tmp_path / "a.py")}, c)["effect_class"] == "write"
    r = parse_tool_call("Write", {"file_path": home + "/.bashrc"}, c)
    assert r["privileged"][0]["kind"] == "fs_outside_repo"
    assert parse_tool_call("Write", {"file_path": home + "/.cache/x"}, c)["effect_class"] == "write"


def test_unknown_command_is_write(tmp_path):
    r = parse_tool_call("Bash", {"command": "frobnicate --all"}, ctx_for(tmp_path))
    assert r["effect_class"] == "write" and r["effects"][0]["reason"].startswith("unknown_command:")


def test_mcp(tmp_path):
    c = ctx_for(tmp_path)
    assert parse_tool_call("mcp__claude_ai_Gmail__send_email", {}, c)["privileged"][0]["kind"] == "message"
    assert parse_tool_call("mcp__claude_ai_Figma__get_metadata", {}, c)["effect_class"] == "read"


def _check(cmd, text, c, standing=("read", "write")):
    g, p = extract_grants(text, source="prompt", turn=1, ctx=c, default="master", branch="feat/x")
    eff = parse_tool_call("Bash", {"command": cmd}, c)["effects"]
    return authority_check(eff, g, {"effects": list(standing)}, p)


def test_feature_branch_grant_never_covers_default(tmp_path):
    c = ctx_for(tmp_path)
    assert _check("git push origin feat/x", "push the feature branch", c)["decision"] == "allow"
    assert _check("git push origin master", "push the feature branch", c)["decision"] == "ask"


def test_named_default_branch_grant(tmp_path):
    c = ctx_for(tmp_path)
    assert _check("git push origin master", "push to master", c)["decision"] == "allow"


def test_force_never_implicit(tmp_path):
    c = ctx_for(tmp_path)
    assert _check("git push -f origin feat/x", "push the feature branch", c)["decision"] == "ask"
    assert _check("git push -f origin master", "push to master", c)["decision"] == "deny"
    assert _check("git push -f origin feat/x", "force-push feat/x", c)["decision"] == "allow"


def test_prohibition_denies(tmp_path):
    c = ctx_for(tmp_path)
    d = _check("gh pr create --title t", "push the feature branch only; no PRs", c)
    assert d["decision"] == "deny"
    assert _check("git push origin master", "fix it but don't push to master", c)["decision"] == "deny"


def test_no_grants_asks_and_plan_mode(tmp_path):
    c = ctx_for(tmp_path)
    assert _check("git push origin feat/x", "fix the tests", c)["decision"] == "ask"
    assert _check("git status", "fix the tests", c)["decision"] == "allow"
    assert _check("sed -i s/a/b/ x.py", "fix the tests", c, standing=("read",))["decision"] == "ask"


def test_ask_answer_grants_proposal(tmp_path):
    c = ctx_for(tmp_path)
    sa = SessionAuthority()
    sa.feed({"type": "user", "message": {"role": "user", "content": "fix the flaky test"}}, c)
    sa.feed({"type": "assistant", "message": {"content": [{"type": "text", "text": "Fixed. Want me to push feat/x to origin?"}]}}, c)
    assert sa.check("Bash", {"command": "git push origin feat/x"}, c)["decision"]["decision"] == "ask"
    sa.feed({"type": "user", "message": {"role": "user", "content": "yes"}}, c)
    assert sa.check("Bash", {"command": "git push origin feat/x"}, c)["decision"]["decision"] == "allow"
    assert sa.check("Bash", {"command": "git push origin master"}, c)["decision"]["decision"] == "ask"


def test_harness_messages_grant_nothing(tmp_path):
    c = ctx_for(tmp_path)
    sa = SessionAuthority()
    sa.feed({"type": "user", "message": {"role": "user", "content": "<task-notification>push to master</task-notification>"}}, c)
    assert sa.grants == []


def test_hook_is_silent_and_fail_open(tmp_path):
    env = dict(os.environ, Z0INT_HOME=str(tmp_path / "z0"))
    hook = {"session_id": "s1", "transcript_path": str(tmp_path / "t.jsonl"), "cwd": str(tmp_path),
            "tool_name": "Bash", "tool_input": {"command": "git push origin master"}, "permission_mode": "default"}
    (tmp_path / "t.jsonl").write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n")
    out = subprocess.run([sys.executable, "-m", "z0int.action_hook"], input=json.dumps(hook), capture_output=True,
                         text=True, env=env)
    assert out.returncode == 0 and out.stdout == ""
    rec = json.loads((tmp_path / "z0" / "state" / "claude-code" / "actions.jsonl").read_text().splitlines()[-1])
    assert rec["decision"] == "ask" and rec["tool"] == "Bash" and rec["effects"][0]["kind"] == "push"
    bad = subprocess.run([sys.executable, "-m", "z0int.action_hook"], input="not json", capture_output=True, text=True, env=env)
    assert bad.returncode == 0 and bad.stdout == ""
