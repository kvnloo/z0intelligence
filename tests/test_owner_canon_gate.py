"""owner-canon-gate: twin parity with the Bend laws, and one case per law (block + allow)."""
import importlib.util
import itertools
import json
import sys
import time
from pathlib import Path

import pytest

P = Path(__file__).resolve().parents[1] / "plugins" / "owner-canon-gate"
spec = importlib.util.spec_from_file_location("owner_canon_gate", P / "__init__.py")
G = importlib.util.module_from_spec(spec)
sys.modules["owner_canon_gate"] = G
spec.loader.exec_module(G)
K = G.K


def test_twin_matches_laws_exhaustively():
    # LAWS.bend: each guard with trigger True{} implies the clearance; trigger False is always admitted
    for law, (_, pred) in K.RULES.items():
        assert pred(False, False) and pred(False, True) and pred(True, True) and not pred(True, False), law
    for bits in itertools.product([False, True], repeat=6):
        pairs = list(zip(bits[::2], bits[1::2]))
        assert K.admits(*bits) == all(K.guard(t, c) for t, c in pairs)
    assert K.admits(*([False] * 6))  # ordinary_call_admitted


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    G._last_limited.clear()
    return tmp_path


def blocked(tool, **args):
    r = G.on_pre_tool_call(tool_name=tool, args=args)
    return bool(r and r.get("action") == "block")





def test_gui_focus():
    assert blocked("terminal", command="xdotool key super+1")
    assert blocked("terminal", command="adb -s S shell input tap 10 10")
    assert not blocked("terminal", command="adb -s S shell input --display 7 tap 10 10")
    assert not blocked("terminal", command="xvfb-run -a xdotool key a")






def test_quota_backoff():
    args = {"command": "curl https://api.example.com/v1"}
    assert not blocked("terminal", **args)
    G.on_post_tool_call(tool_name="terminal", args=args, result="HTTP 429 Too Many Requests, Retry-After: 30")
    assert blocked("terminal", **args)
    G._last_limited[G._key("terminal", args)] = 0.5  # back-off elapsed
    assert not blocked("terminal", **args)


def claim_rows(home):
    f = home / "owner-canon-gate" / "claim-receipts.jsonl"
    return [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []


def test_done_claims_are_dereferenced(home):
    assert not blocked("message_agent", target="ayo", message="Done, all fixed.")  # no typed claim: flag
    assert claim_rows(home) == []
    made_up = f"Done: commit 6a7b5a8, receipt {home}/missing.json"
    assert blocked("message_agent", target="ayo", message=made_up)  # the regex gate passed this
    (home / "missing.json").write_text("{}")
    G.claimcheck._cache.clear()
    assert not blocked("message_agent", target="ayo", message=made_up)
    assert blocked("send_message", text="pushed 8b96606")  # typed claim, nothing to check
    assert not blocked("message_agent", message="unverified: gh unreachable, pushed 8b96606")
    assert not blocked("message_agent", message="local branch ayo/Z11, not pushed")
    rows = claim_rows(home)
    assert [r["ok"] for r in rows] == [False, True, False] and all("message" not in r for r in rows)
    assert not any(r["ok"] is None for r in rows) and "6a7b5a8" not in json.dumps(rows[2])
    r = G.on_pre_tool_call(tool_name="message_agent", args={"message": "pushed 8b96606"})
    assert "pushed: no locator" in r["message"] and "unverified:" in r["message"]
    (home / "owner-canon-gate.json").write_text('{"mode": {"done_untyped": "enforce"}}')
    assert blocked("message_agent", target="ayo", message="Done, all fixed.")
    (home / "owner-canon-gate.json").write_text('{"mode": {"done_needs_receipt": "flag"}}')
    assert not blocked("send_message", text="pushed 8b96606")


def test_kanban_complete_is_gated(home):
    assert blocked("kanban_complete", task_id="t1", summary="pushed and PR opened")
    assert [r["type"] for r in claim_rows(home)] == ["pr", "pushed"]
    assert blocked("mcp__kanban_complete", summary="ok", result="45 tests pass (reported)")
    assert not blocked("kanban_complete", summary="Reviewed the plan; two questions filed.")


def test_every_message_call_leaves_one_outcome_line(home):
    """F24: block -> resend is measurable per session without reading any message text."""
    say = lambda msg: G.on_pre_tool_call(tool_name="message_agent", args={"message": msg}, session_id="s9")  # noqa: E731
    say("pushed 8b96606")
    say("unverified: pushed 8b96606")
    say("Done, all fixed.")
    say("Two questions about the plan.")
    rows = [json.loads(x) for x in (home / "owner-canon-gate" / "claim-calls.jsonl").read_text().splitlines()]
    assert [(r["outcome"], r["typed"], r["unverified_lines"]) for r in rows] == [
        ("block", True, 0), ("pass", False, 1), ("flag", False, 0), ("pass", False, 0)]
    assert {r["session_id"] for r in rows} == {"s9"} and all("message" not in r for r in rows)
    assert claim_rows(home)[0]["session_id"] == "s9"  # tool-path claim rows used to carry null


def test_infra_failure_and_checker_bug_flag_only(home, monkeypatch):
    monkeypatch.setattr(G.claimcheck, "RUN", lambda cmd, env=None: (None, b"", "TimeoutExpired"))
    assert not blocked("message_agent", message="pushed kvnloo/x@8b96606")
    assert claim_rows(home)[-1]["ok"] is False and claim_rows(home)[-1]["detail"].startswith("infra")
    monkeypatch.setattr(G.claimcheck, "check", lambda *a, **k: 1 / 0)
    assert not blocked("message_agent", message="Done. pushed kvnloo/x@8b96606")


def test_reply_footer(home):
    assert G.reply_footer(response_text="Here is the plan for tomorrow.") is None
    (home / "r.json").write_text("{}")
    assert G.reply_footer(response_text=f"Done. Receipt {home}/r.json") is None
    out = G.reply_footer(response_text=f"Done. Receipt {home}/r.json\npushed 8b96606 and 45 tests pass", session_id="s1")
    assert out.startswith(f"Done. Receipt {home}/r.json\npushed 8b96606 and 45 tests pass\n\nChecks: 1 verified · 2 NOT verified (")
    assert "pushed: no locator" in out and "tests: no locator" in out
    assert G.reply_footer(response_text=out) is None  # never stacks a second footer
    assert claim_rows(home)[-1]["tool"] == "reply" and claim_rows(home)[-1]["session_id"] == "s1"
    hooks = {}
    G.register(type("Ctx", (), {"register_hook": lambda self, name, fn: hooks.setdefault(name, fn)})())
    assert hooks["transform_llm_output"] is G.reply_footer


def test_ordinary_calls_pass():
    for cmd in ("ls -la", "git status", "python3 -m pytest -q", "rm file.txt", "hermes doctor"):
        assert not blocked("terminal", command=cmd), cmd
    assert not blocked("read_file", path="/etc/hosts")


KS = G.KS


def test_standing_twin_matches_laws_exhaustively():
    for law, (_, pred) in KS.RULES.items():
        assert pred(False, False) and pred(False, True) and pred(True, True) and not pred(True, False), law
    for bits in itertools.product([False, True], repeat=6):
        pairs = list(zip(bits[::2], bits[1::2]))
        assert KS.admits(*bits) == all(KS.guard(t, c) for t, c in pairs)
    assert KS.admits(False, False, True, True, False, False)  # receipted_delete_admitted (Kevin 23:25)


def test_gpt_outside_fx_blocked_and_via_fx_allowed():
    assert blocked("terminal", command="codex exec --model gpt-6.1-terra 'x'")
    assert blocked("delegate_task", goal="x", model="gpt-6.1-sol")
    assert not blocked("terminal", command="fx ask --json --model gpt-6.1-terra 'x'")


def test_prose_naming_gpt_is_not_a_dispatch():
    assert not blocked("message_agent", target="ayo", message="ran fx gpt-6.1-terra, see /a/b/c")
    assert not blocked("write_file", path="/x/notes.md", content="GPT models only via fx (gpt-6.1-terra)")


def test_delete_outside_scratch_blocked():
    assert blocked("terminal", command="rm -rf /mnt/zer0models/models/foo")
    assert blocked("terminal", command="git worktree remove /mnt/zer0models/z0-wt/x")


def test_delete_in_scratch_allowed(monkeypatch):
    monkeypatch.setenv("TMPDIR", "/home/kvn/.hermes/profiles/z0i/cache/scratch")
    assert not blocked("terminal", command="rm -rf /tmp/foo")
    assert not blocked("terminal", command="rm -rf $TMPDIR/foo")
    assert not blocked("terminal", command="rm -rf ~/.cache/pip/x")


def test_var_and_tilde_resolved_before_judging(monkeypatch, tmp_path):
    monkeypatch.setenv("TMPDIR", "/home/kvn/.hermes/profiles/z0i/cache/scratch")
    assert not blocked("terminal", command="B=$TMPDIR/z0v2-base; rm -rf $B")          # inline assignment -> scratch
    assert blocked("terminal", command="B=/mnt/zer0models/models; rm -rf $B/old")      # inline assignment -> real data
    monkeypatch.setenv("TARGET", "/mnt/zer0models/models/old")
    assert blocked("terminal", command="rm -rf \"$TARGET\"")                          # env of the call
    assert blocked("terminal", command="rm -rf ~/projects")                             # ~ expands to home, not scratch
    assert not blocked("terminal", command="rm -rf /tmp/$UNSET_VAR_X/y")               # unresolvable, scratch prefix: flag
    assert blocked("terminal", command="rm -rf $UNSET_VAR_X/y")                        # unresolvable, no scratch prefix


def test_creator_finished_receipt_satisfies_delete(home):
    d = home / "owner-canon-gate" / "creator-receipts"; d.mkdir(parents=True)
    tgt = "/mnt/zer0models/z0-wt/old-wt"
    assert blocked("terminal", command=f"rm -rf {tgt}")
    (d / "r.jsonl").write_text('{"path": "%s", "kib": 10, "creator": "session local-abc", "why_finished": "merged"}\n' % tgt)
    assert not blocked("terminal", command=f"rm -rf {tgt}")
    assert blocked("terminal", command="rm -rf /mnt/zer0models/z0-wt/other-wt")  # receipt names one target only


def test_receipt_without_creator_does_not_count(home):
    d = home / "owner-canon-gate" / "creator-receipts"; d.mkdir(parents=True)
    (d / "r.jsonl").write_text('{"path": "/mnt/zer0models/z0-wt/x", "why": "old"}\n')
    assert blocked("terminal", command="rm -rf /mnt/zer0models/z0-wt/x")


def test_public_needs_owner_ok(home):
    cmd = "gh pr create --repo NousResearch/hermes-agent --title t --body b"
    assert blocked("terminal", command=cmd)
    assert not blocked("terminal", command="gh issue create --repo kvnloo/oss-factory --title t")
    (home / "owner-canon-approvals.json").write_text('{"public": ["NousResearch/hermes-agent --title t"]}')
    assert not blocked("terminal", command=cmd)


def test_approvals_file_protected():
    assert blocked("write_file", path="/x/owner-canon-approvals.json", content="{}")
    assert blocked("terminal", command="echo {} > ~/.hermes/owner-canon-approvals.json")



def test_gui_words_need_word_boundaries():
    # TERM=xterm-... once matched the xte pattern and blocked a headless private tmux session
    assert not blocked("terminal", command="env TERM=xterm-256color tmux -L a03 new-session -d bun Term.ts")
    assert blocked("terminal", command="x" + "te 'mousemove 0 0'")


def test_approvals_guard_judges_the_target_path_not_the_text():
    name = "owner-canon-" + "approvals.json"
    assert not blocked("patch", path="/x/tests/test_gate.py", old_string="a " + name, new_string="b " + name)
    assert blocked("patch", path="/x/" + name, old_string="{}", new_string='{"public": ["x"]}')


@pytest.fixture
def reuse_inventory(home):
    row = {"id": "repo:demo", "name": "Demo_Tool", "where": "/existing/demo-tool",
           "seen_epoch": time.time()}
    (home / "EXISTS.jsonl").write_text(json.dumps(row) + "\n")
    return row


def test_reuse_clone_flag_and_enforce(home, reuse_inventory, caplog):
    command = "git clone https://example.com/owner/demo-tool.git"
    assert G.on_pre_tool_call("terminal", {"command": command}) is None
    assert "reuse_before_create" in caplog.text
    row = reuse_inventory
    assert row["id"] in caplog.text and row["where"] in caplog.text
    log = home / "owner-canon-gate" / "exists-injections.jsonl"
    assert json.loads(log.read_text().splitlines()[-1])["slot"] == "action"
    (home / "owner-canon-gate.json").write_text('{"mode": {"reuse_before_create": "enforce"}}')
    result = G.on_pre_tool_call("terminal", {"command": command})
    assert result["action"] == "block"
    assert str(row["seen_epoch"]) in result["message"]
    assert not blocked("terminal", command=command + " # exists-ack=repo:demo reason=isolated compatibility test")
    assert not blocked("terminal", command=command + " # reuse /existing/demo-tool")
    assert blocked("terminal", command=command + " # exists-ack=repo:demo")
    assert blocked("terminal", command=command + " # exists-ack=repo:demo-other reason=test")


@pytest.mark.parametrize("command", [
    "git clone --depth 1 https://example.com/owner/demo-tool.git local-copy",
    "pip install demo-tool==1.2", "uv pip install demo-tool", "uv tool install demo-tool",
    "npm install demo-tool@1.2", "cargo install demo-tool", "pacman -S demo-tool", "yay -S demo-tool",
    "python -m venv /venvs/demo-tool", "gh repo create owner/demo-tool --private",
    "systemctl --user enable demo-tool.service", "systemctl --user link /units/demo-tool.timer",
    "mkdir -p ~/.agents/skills/demo-tool", "mkdir ~/bin/demo-tool",
])
def test_reuse_shell_targets(command, reuse_inventory):
    assert "reuse_before_create" in dict(G.decide("terminal", {"command": command})[1])


@pytest.mark.parametrize("tool,path", [
    ("write_file", "/skills/demo-tool/SKILL.md"), ("patch", "/skills/demo-tool/SKILL.md"),
    ("write_file", "/units/demo-tool.service"), ("patch", "/units/demo-tool.timer"),
])
def test_reuse_file_targets(tool, path, reuse_inventory):
    assert "reuse_before_create" in dict(G.decide(tool, {"path": path})[1])


@pytest.mark.parametrize("command", [
    "pip install unrelated", "pip install demo-tool-extra", "mkdir /tmp/demo-tool",
    "echo 'git clone https://example.com/demo-tool.git'", "git status",
])
def test_reuse_unrelated(command, reuse_inventory):
    assert G.decide("terminal", {"command": command}) == (False, [])


def test_reuse_stale_missing_and_invalid_fail_open(home, reuse_inventory):
    (home / "owner-canon-gate.json").write_text('{"mode": {"reuse_before_create": "enforce"}}')
    inventory = home / "EXISTS.jsonl"
    row = reuse_inventory | {"seen_epoch": time.time() - 21601}
    inventory.write_text(json.dumps(row) + "\n")
    command = "git clone https://example.com/demo-tool.git"
    assert G.on_pre_tool_call("terminal", {"command": command}) is None
    inventory.unlink()
    assert G.on_pre_tool_call("terminal", {"command": command}) is None
    inventory.write_text('not json\n' + json.dumps(row | {"seen_epoch": "invalid"}) + '\n')
    assert G.on_pre_tool_call("terminal", {"command": command}) is None


@pytest.fixture
def done_inventory(home, monkeypatch):
    import subprocess

    repo = home / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "current", str(repo)], check=True)
    (repo / "tracked.py").write_text("before\n")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.py"], check=True)
    monkeypatch.chdir(repo)
    row = {"id": "plan:demo", "kind": "plan", "scope": str(repo), "repo": str(repo),
           "branch": "other", "paths": ["tracked.py"], "seen_epoch": time.time()}
    (home / "DONE.jsonl").write_text(json.dumps(row) + "\n")
    return row


@pytest.mark.parametrize("tool,args,slot", [
    ("write_file", {"path": "PLAN.md"}, "plan"),
    ("patch", {"path": "docs/ROADMAP-next"}, "plan"),
    ("write_file", {"path": "docs/replan-notes.md"}, "plan"),
    ("kanban", {"action": "create", "title": "demo"}, "plan"),
    ("patch", {"path": "tracked.py"}, "edit"),
    ("write_file", {"path": "tracked.py"}, "edit"),
])
def test_done_flags_only(home, done_inventory, caplog, tool, args, slot):
    (home / "owner-canon-gate.json").write_text('{"mode": {"reuse_before_create": "enforce"}}')
    assert G.on_pre_tool_call(tool, args, session_id="done-test") is None
    assert "reuse_before_create" in caplog.text and done_inventory["id"] in caplog.text
    log = home / "owner-canon-gate" / "done-injections.jsonl"
    rec = json.loads(log.read_text().splitlines()[-1])
    assert (rec["slot"], rec["decision"], rec["session_id"]) == (slot, "flag", "done-test")
    assert not (home / "owner-canon-gate" / "exists-injections.jsonl").exists()


def test_done_task_row_explicit_scope_outside_git(home, done_inventory, monkeypatch, caplog):
    monkeypatch.chdir(home)
    row = done_inventory | {"kind": "task", "scope": "demo"}
    (home / "DONE.jsonl").write_text(json.dumps(row) + "\n")
    (home / "PLAN.md").touch()
    assert G.on_pre_tool_call("patch", {"path": "PLAN.md", "scope": "demo"}) is None
    assert row["id"] in caplog.text


@pytest.mark.parametrize("change,path", [
    ({}, "unrelated.md"),
    ({"scope": "elsewhere"}, "PLAN.md"),
    ({"kind": "fix"}, "PLAN.md"),
    ({"repo": "elsewhere"}, "tracked.py"),
    ({"branch": "current"}, "tracked.py"),
    ({"paths": ["other.py"]}, "tracked.py"),
    ({"seen_epoch": time.time() - 15 * 86400}, "tracked.py"),
    ({"paths": ["untracked.py"]}, "untracked.py"),
])
def test_done_nonmatches(home, done_inventory, caplog, change, path):
    Path("untracked.py").touch()
    (home / "DONE.jsonl").write_text(json.dumps(done_inventory | change) + "\n")
    assert G.on_pre_tool_call("write_file", {"path": path}) is None
    assert "reuse_before_create" not in caplog.text
    assert not (home / "owner-canon-gate" / "done-injections.jsonl").exists()


@pytest.mark.parametrize("state", ["missing", "stale", "invalid", "off"])
def test_done_fail_open(home, done_inventory, caplog, state):
    import os

    inventory = home / "DONE.jsonl"
    if state == "missing":
        inventory.unlink()
    elif state == "stale":
        os.utime(inventory, (time.time() - 21601,) * 2)
    elif state == "invalid":
        inventory.write_text('not json\nnull\n{}\n')
    else:
        (home / "owner-canon-gate.json").write_text('{"mode": {"reuse_before_create": "off"}}')
    assert G.on_pre_tool_call("write_file", {"path": "PLAN.md"}) is None
    assert "reuse_before_create" not in caplog.text
    assert not (home / "owner-canon-gate" / "done-injections.jsonl").exists()


def test_reuse_message_limit_and_off(home, reuse_inventory):
    rows = [reuse_inventory | {"id": f"repo:{i}", "where": f"/existing/{i}"} for i in range(4)]
    (home / "EXISTS.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    (home / "owner-canon-gate.json").write_text('{"mode": {"reuse_before_create": "enforce"}}')
    result = G.on_pre_tool_call("terminal", {"command": "pip install demo-tool"})
    assert result["action"] == "block"
    assert all(row["id"] in result["message"] for row in rows[:3])
    assert rows[3]["id"] not in result["message"]
    (home / "owner-canon-gate.json").write_text('{"mode": {"reuse_before_create": "off"}}')
    assert G.on_pre_tool_call("terminal", {"command": "pip install demo-tool"}) is None
