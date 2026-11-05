"""owner-canon-gate: twin parity with the Bend laws, and one case per law (block + allow)."""
import importlib.util
import itertools
import sys
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


def test_done_claim_flag_then_enforce(home):
    assert not blocked("message_agent", target="ayo", message="Done, all fixed.")  # default: flag
    (home / "owner-canon-gate.json").write_text('{"mode": {"done_needs_receipt": "enforce"}}')
    assert blocked("message_agent", target="ayo", message="Done, all fixed.")
    assert not blocked("message_agent", target="ayo", message="Done: commit 6a7b5a8, receipt /a/b/c.json")


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

