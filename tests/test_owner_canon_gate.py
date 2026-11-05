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
    for bits in itertools.product([False, True], repeat=12):
        pairs = list(zip(bits[::2], bits[1::2]))
        assert K.admits(*bits) == all(K.guard(t, c) for t, c in pairs)
    assert K.admits(*([False] * 12))  # ordinary_call_admitted


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    G._last_limited.clear()
    return tmp_path


def blocked(tool, **args):
    r = G.on_pre_tool_call(tool_name=tool, args=args)
    return bool(r and r.get("action") == "block")


@pytest.mark.parametrize("cmd", [
    "hermes -p z0i --oneshot 'x' -m gpt-6.1-terra",
    "codex exec --model gpt-5.5 'fix it'",
])
def test_gpt_outside_fx_blocked(cmd):
    assert blocked("terminal", command=cmd)


def test_gpt_via_fx_allowed():
    assert not blocked("terminal", command="fx ask --json --model gpt-6.1-terra 'brief'")
    assert blocked("delegate_task", tasks=[{"goal": "x"}], model="gpt-6.1-sol")
    assert not blocked("delegate_task", tasks=[{"goal": "x"}])


def test_prose_naming_gpt_is_not_a_dispatch():
    # regression: a report to another agent that names the fx route was blocked as a GPT dispatch
    msg = "E6 runs on fx gpt-6.1-terra; codex window 4%; hermes -m was never used, model=gpt-6.1-terra"
    assert not blocked("message_agent", target="ayo", message=msg)
    assert not blocked("write_file", path="/tmp/x.md", content=msg)


def test_gui_focus():
    assert blocked("terminal", command="xdotool key super+1")
    assert blocked("terminal", command="adb -s S shell input tap 10 10")
    assert not blocked("terminal", command="adb -s S shell input --display 7 tap 10 10")
    assert not blocked("terminal", command="xvfb-run -a xdotool key a")


def test_delete():
    assert blocked("terminal", command="rm -rf /mnt/zer0models/models/foo")
    assert blocked("terminal", command="git branch -D feature/x")
    assert not blocked("terminal", command="rm -rf $TMPDIR/probe")
    assert not blocked("terminal", command="rm -rf /home/kvn/.hermes/profiles/z0i/cache/scratch/x")


def test_delete_with_creator_receipt(tmp_path):
    rc = tmp_path / "creator.done"
    rc.write_text("{}")
    assert not blocked("terminal", command=f"rm -rf /mnt/zer0models/x  # canon-creator-receipt={rc}")
    assert blocked("terminal", command="rm -rf /mnt/zer0models/x  # canon-creator-receipt=/nope")


def test_public(home):
    assert blocked("terminal", command="gh pr create --repo NousResearch/hermes-agent --title t --body b")
    assert not blocked("terminal", command="gh pr create --repo kvnloo/z0intelligence --title t --body b")
    assert blocked("terminal", command="npm publish")
    (home / "owner-canon-approvals.json").write_text('{"public": ["NousResearch/hermes-agent --title t"]}')
    assert not blocked("terminal", command="gh pr create --repo NousResearch/hermes-agent --title t --body b")


def test_approvals_file_protected():
    assert blocked("write_file", path="/x/owner-canon-approvals.json", content="{}")


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


def test_pre_llm_call_flags_gpt_session():
    assert G.on_pre_llm_call(model="gpt-6.1-terra")
    assert G.on_pre_llm_call(model="claude-opus-5-5") is None
